"""Anthropic Messages SSE 与手动 extended thinking 适配。"""

import json
from collections.abc import AsyncIterator, Sequence
from copy import deepcopy

import httpx
from anthropic import AsyncAnthropic

from mycode.config import ProviderConfig
from mycode.errors import ProviderError, map_provider_error
from mycode.models import (
    Message,
    NativeContent,
    ProviderEvent,
    ProviderUsage,
    ResponseCompleted,
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    ToolCall,
)
from mycode.providers.base import REQUEST_TIMEOUT


class AnthropicProvider:
    def __init__(self, config: ProviderConfig, *, http_client: httpx.AsyncClient | None = None):
        self._config = config
        self._client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
            timeout=REQUEST_TIMEOUT,
            http_client=http_client,
        )

    async def stream(
        self, messages: Sequence[Message], *, tools=(), allow_tools=False, system_prompt=""
    ) -> AsyncIterator[ProviderEvent]:
        blocks = []
        arguments = {}
        calls = []
        active = set()
        started = False
        finished = False
        reason = None
        text = ""
        params = {}
        input_tokens = output_tokens = None
        if system_prompt:
            params["system"] = system_prompt
        if self._config.thinking:
            params["thinking"] = {"type": "enabled", "budget_tokens": 2048}
        if tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
            params["tool_choice"] = {"type": "auto" if allow_tools else "none"}
        history = []
        for message in messages:
            if message.role == "tool":
                history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.call_id,
                                "content": r.to_json(),
                                "is_error": r.status != "success",
                            }
                            for r in message.tool_results
                        ],
                    }
                )
                continue
            native = message.native_content
            content = (
                deepcopy(native.blocks)
                if native is not None and native.protocol == "anthropic"
                else message.text
            )
            if message.tool_calls and native is None:
                content = [{"type": "text", "text": message.text}] if message.text else []
                for call in message.tool_calls:
                    try:
                        parsed = json.loads(call.arguments_json)
                        if not isinstance(parsed, dict):
                            parsed = {}
                    except ValueError:
                        parsed = {}
                    content.append(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": parsed}
                    )
            history.append({"role": message.role, "content": content})
        try:
            response = await self._client.messages.create(
                model=self._config.model,
                max_tokens=8192,
                messages=history,
                stream=True,
                **params,
            )
            async with response:
                async for event in response:
                    kind = event.type
                    if kind == "ping":
                        continue
                    if finished:
                        raise ProviderError("protocol", "模型在完成后返回了额外事件。")
                    if kind == "message_start":
                        if started:
                            raise ProviderError("protocol", "响应流重复开始。")
                        started = True
                        usage = event.message.usage
                        input_tokens = getattr(usage, "input_tokens", None)
                        output_tokens = getattr(usage, "output_tokens", None)
                        yield ProviderUsage(TokenUsage(input_tokens, output_tokens))
                    elif kind == "content_block_start":
                        if not started or event.index != len(blocks) or active:
                            raise ProviderError("protocol", "响应内容块顺序异常。")
                        block = event.content_block.model_dump(mode="json", exclude_none=True)
                        if block["type"] not in {
                            "text",
                            "thinking",
                            "redacted_thinking",
                            "tool_use",
                        }:
                            raise ProviderError(
                                "response", "本轮仅支持文本对话，无法处理该内容类型。"
                            )
                        if block["type"] == "tool_use":
                            if not allow_tools:
                                raise ProviderError(
                                    "protocol", "模型在禁用工具阶段返回了工具调用。"
                                )
                            arguments[event.index] = ""
                        blocks.append(block)
                        active.add(event.index)
                        if block["type"] == "text" and block["text"]:
                            text += block["text"]
                            yield TextDelta(block["text"])
                        elif block["type"] == "thinking" and block["thinking"]:
                            yield ThinkingDelta(block["thinking"])
                    elif kind == "content_block_delta":
                        if event.index not in active:
                            raise ProviderError("protocol", "响应增量没有对应的开放内容块。")
                        block = blocks[event.index]
                        delta = event.delta
                        if delta.type == "text_delta" and block["type"] == "text":
                            block["text"] += delta.text
                            text += delta.text
                            if delta.text:
                                yield TextDelta(delta.text)
                        elif delta.type == "thinking_delta" and block["type"] == "thinking":
                            block["thinking"] += delta.thinking
                            if delta.thinking:
                                yield ThinkingDelta(delta.thinking)
                        elif delta.type == "signature_delta" and block["type"] == "thinking":
                            block["signature"] = block.get("signature", "") + delta.signature
                        elif delta.type == "input_json_delta" and block["type"] == "tool_use":
                            arguments[event.index] += delta.partial_json
                        elif delta.type in {"text_delta", "thinking_delta", "signature_delta"}:
                            raise ProviderError("protocol", "响应增量与内容块类型不匹配。")
                        else:
                            raise ProviderError("protocol", "模型返回了不支持的内容增量。")
                    elif kind == "content_block_stop":
                        if event.index not in active:
                            raise ProviderError("protocol", "响应内容块重复或提前关闭。")
                        block = blocks[event.index]
                        if block["type"] == "tool_use":
                            raw = arguments[event.index] or json.dumps(block.get("input", {}))
                            calls.append(ToolCall(block.get("id", ""), block.get("name", ""), raw))
                            try:
                                value = json.loads(raw)
                                json.dumps(value, allow_nan=False)
                                block["input"] = value if isinstance(value, dict) else {}
                            except ValueError:
                                block["input"] = {}
                        active.remove(event.index)
                    elif kind == "message_delta":
                        usage = event.usage
                        if getattr(usage, "input_tokens", None) is not None:
                            input_tokens = usage.input_tokens
                        if getattr(usage, "output_tokens", None) is not None:
                            output_tokens = usage.output_tokens
                        yield ProviderUsage(TokenUsage(input_tokens, output_tokens))
                        if event.delta.stop_reason is not None:
                            reason = event.delta.stop_reason
                    elif kind == "message_stop":
                        if not started or active or not blocks:
                            raise ProviderError("protocol", "响应内容块不完整。")
                        if not (
                            (reason in {"end_turn", "stop_sequence"} and not calls)
                            or (reason == "tool_use" and calls)
                        ):
                            message = (
                                "回复达到输出上限，内容不完整，请缩短问题后重试。"
                                if reason == "max_tokens"
                                else "模型未正常完成文本回复，请重新提问。"
                            )
                            raise ProviderError("response", message)
                        finished = True
            if not finished:
                raise ProviderError("protocol", "响应流提前结束，回复未完成。")
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_error(exc) from None
        if any(not call.name for call in calls):
            raise ProviderError("protocol", "工具调用名称缺失。")
        yield ResponseCompleted(
            Message("assistant", text, NativeContent("anthropic", blocks), tuple(calls))
        )

    async def aclose(self) -> None:
        await self._client.close()
