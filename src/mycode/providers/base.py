"""模型供应商的统一异步接口。"""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

import httpx

from mycode.models import Message, StreamEvent

REQUEST_TIMEOUT = httpx.Timeout(connect=10, read=120, write=10, pool=10)


class Provider(Protocol):
    def stream(self, messages: Sequence[Message]) -> AsyncIterator[StreamEvent]: ...

    async def aclose(self) -> None: ...
