"""OpenAI Chat Completions SSE 适配。"""

from collections.abc import AsyncIterator, Sequence

import httpx
from openai import AsyncOpenAI

from mycode.config import ProviderConfig
from mycode.errors import ProviderError, map_provider_error
from mycode.models import Message, StreamEvent, TextDelta, TurnCompleted
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

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[StreamEvent]:
        text = ""
        finished = False
        try:
            response = await self._client.chat.completions.create(
                model=self._config.model,
                messages=[{"role": m.role, "content": m.text} for m in messages],
                stream=True,
            )
            async with response:
                async for chunk in response:
                    for choice in chunk.choices:
                        if choice.index != 0:
                            raise ProviderError("protocol", "模型返回了不支持的多候选响应。")
                        if finished:
                            raise ProviderError("protocol", "模型在完成后返回了额外内容。")
                        delta = choice.delta
                        if delta.tool_calls or delta.function_call:
                            raise ProviderError(
                                "response", "本轮仅支持文本对话，无法处理工具调用。"
                            )
                        if delta.content:
                            text += delta.content
                            yield TextDelta(delta.content)
                        if choice.finish_reason is not None:
                            if choice.finish_reason != "stop":
                                message = (
                                    "回复达到输出上限，内容不完整，请缩短问题后重试。"
                                    if choice.finish_reason == "length"
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
        yield TurnCompleted(Message("assistant", text))

    async def aclose(self) -> None:
        await self._client.close()
