"""OpenAI Chat Completions 文本与工具调用 SSE 适配。"""

from collections.abc import AsyncIterator, Sequence

import httpx
from openai import AsyncOpenAI

from mycode.config import ProviderConfig
from mycode.errors import ProviderError, map_provider_error
from mycode.models import Message, ProviderEvent, ResponseCompleted, TextDelta, ToolCall
from mycode.providers.base import REQUEST_TIMEOUT


class OpenAIProvider:
    def __init__(self, config: ProviderConfig, *, http_client: httpx.AsyncClient | None = None):
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
            timeout=REQUEST_TIMEOUT,
            http_client=http_client,
        )

    async def stream(
        self, messages: Sequence[Message], *, tools=(), allow_tools=False
    ) -> AsyncIterator[ProviderEvent]:
        text, finished, calls = "", False, {}
        history = []
        for message in messages:
            if message.role == "tool":
                history.extend(
                    {"role": "tool", "tool_call_id": r.call_id, "content": r.to_json()}
                    for r in message.tool_results
                )
            else:
                item = {"role": message.role, "content": message.text}
                if message.tool_calls:
                    item["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments_json},
                        }
                        for c in message.tool_calls
                    ]
                history.append(item)
        params = {}
        if tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            params["tool_choice"] = "auto" if allow_tools else "none"
        try:
            response = await self._client.chat.completions.create(
                model=self._config.model, messages=history, stream=True, **params
            )
            async with response:
                async for chunk in response:
                    for choice in chunk.choices:
                        if choice.index != 0 or finished:
                            raise ProviderError("protocol", "响应候选或结束顺序异常。")
                        delta = choice.delta
                        if delta.function_call:
                            raise ProviderError("protocol", "不支持旧式 function_call。")
                        if delta.tool_calls:
                            if not allow_tools:
                                raise ProviderError(
                                    "protocol", "模型在禁用工具阶段返回了工具调用。"
                                )
                            for part in delta.tool_calls:
                                if part.index is None or part.index < 0:
                                    raise ProviderError("protocol", "工具调用索引无效。")
                                call = calls.setdefault(
                                    part.index, {"id": "", "name": "", "args": ""}
                                )
                                if part.id:
                                    call["id"] += part.id
                                if part.type and part.type != "function":
                                    raise ProviderError("protocol", "不支持该工具调用类型。")
                                if part.function:
                                    call["name"] += part.function.name or ""
                                    call["args"] += part.function.arguments or ""
                        if delta.content:
                            text += delta.content
                            yield TextDelta(delta.content)
                        reason = choice.finish_reason
                        if reason is not None:
                            if reason == "length":
                                raise ProviderError(
                                    "response", "回复达到输出上限，内容不完整，请缩短问题后重试。"
                                )
                            if not (
                                (reason == "stop" and not calls)
                                or (reason == "tool_calls" and calls)
                            ):
                                raise ProviderError("protocol", "模型结束原因与工具调用不一致。")
                            finished = True
            if not finished:
                raise ProviderError("protocol", "响应流提前结束，回复未完成。")
            if calls and sorted(calls) != list(range(len(calls))):
                raise ProviderError("protocol", "工具调用索引不连续。")
            tool_calls = tuple(
                ToolCall(c["id"], c["name"], c["args"]) for _, c in sorted(calls.items())
            )
            if any(not c.name for c in tool_calls):
                raise ProviderError("protocol", "工具调用名称缺失。")
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_error(exc) from None
        yield ResponseCompleted(Message("assistant", text, tool_calls=tool_calls))

    async def aclose(self) -> None:
        await self._client.close()
