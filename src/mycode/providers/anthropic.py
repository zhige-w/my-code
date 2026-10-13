"""Anthropic Messages SSE 与手动 extended thinking 适配。"""

from collections.abc import AsyncIterator, Sequence
from copy import deepcopy

import httpx
from anthropic import AsyncAnthropic

from mycode.config import ProviderConfig
from mycode.errors import ProviderError, map_provider_error
from mycode.models import (
    Message,
    NativeContent,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    TurnCompleted,
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

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[StreamEvent]:
        blocks = []
        active = set()
        started = False
        finished = False
        reason = None
        text = ""
        params = {}
        if self._config.thinking:
            params["thinking"] = {"type": "enabled", "budget_tokens": 2048}
        history = []
        for message in messages:
            native = message.native_content
            content = (
                deepcopy(native.blocks)
                if native is not None and native.protocol == "anthropic"
                else message.text
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
                    elif kind == "content_block_start":
                        if not started or event.index != len(blocks) or active:
                            raise ProviderError("protocol", "响应内容块顺序异常。")
                        block = event.content_block.model_dump(mode="json", exclude_none=True)
                        if block["type"] not in {"text", "thinking", "redacted_thinking"}:
                            raise ProviderError(
                                "response", "本轮仅支持文本对话，无法处理该内容类型。"
                            )
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
                        elif delta.type in {"text_delta", "thinking_delta", "signature_delta"}:
                            raise ProviderError("protocol", "响应增量与内容块类型不匹配。")
                        else:
                            raise ProviderError("protocol", "模型返回了不支持的内容增量。")
                    elif kind == "content_block_stop":
                        if event.index not in active:
                            raise ProviderError("protocol", "响应内容块重复或提前关闭。")
                        active.remove(event.index)
                    elif kind == "message_delta":
                        if event.delta.stop_reason is not None:
                            reason = event.delta.stop_reason
                    elif kind == "message_stop":
                        if not started or active or not blocks:
                            raise ProviderError("protocol", "响应内容块不完整。")
                        if reason not in {"end_turn", "stop_sequence"}:
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
        yield TurnCompleted(Message("assistant", text, NativeContent("anthropic", blocks)))

    async def aclose(self) -> None:
        await self._client.close()
