"""实时转发增量，并在流关闭后提供完整响应。"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass

from mycode.errors import ProviderError
from mycode.models import Message, ProviderUsage, ResponseCompleted, TextDelta, TokenUsage


@dataclass(frozen=True)
class CollectedResponse:
    message: Message
    usage: TokenUsage


class StreamCollector:
    async def collect(self, events, *, cancel):
        completed = None
        text = ""
        usage = TokenUsage()
        reader = None
        cancelled = asyncio.create_task(cancel.wait())
        try:
            while True:
                if cancel.is_set():
                    raise asyncio.CancelledError()
                reader = asyncio.create_task(anext(events))
                await asyncio.wait((reader, cancelled), return_when=asyncio.FIRST_COMPLETED)
                if cancel.is_set():
                    raise asyncio.CancelledError()
                try:
                    event = reader.result()
                except StopAsyncIteration:
                    break
                if completed is not None:
                    raise ProviderError("protocol", "模型完成后返回了额外事件。")
                if isinstance(event, ResponseCompleted):
                    completed = event.message
                else:
                    if isinstance(event, TextDelta):
                        text += event.text
                    elif isinstance(event, ProviderUsage):
                        usage = event.usage
                    yield event
            if completed is None or completed.role != "assistant" or completed.text != text:
                raise ProviderError("protocol", "响应未完整结束或文本、角色不一致。")
        finally:
            for task in (reader, cancelled):
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await task
            await events.aclose()
        yield CollectedResponse(completed, usage)
