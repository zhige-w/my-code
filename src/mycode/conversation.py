"""当前会话的成功历史与单次请求状态。"""

from collections.abc import AsyncIterator
from contextlib import aclosing
from copy import deepcopy

from mycode.errors import ProviderError
from mycode.models import Message, StreamEvent, TurnCompleted
from mycode.providers.base import Provider


class Conversation:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider
        self._history: list[Message] = []
        self._busy = False
        self._closed = False

    @property
    def history(self) -> tuple[Message, ...]:
        return deepcopy(tuple(self._history))

    @property
    def busy(self) -> bool:
        return self._busy

    async def stream_turn(self, user_text: str) -> AsyncIterator[StreamEvent]:
        if self._closed:
            raise ProviderError("response", "会话已关闭。")
        if self._busy:
            raise ProviderError("response", "当前回复尚未结束，请稍后发送。")
        if not user_text.strip():
            raise ProviderError("response", "请输入非空问题。")
        self._busy = True
        user = Message("user", user_text)
        completed = False
        try:
            snapshot = (*self.history, user)
            async with aclosing(self._provider.stream(snapshot)) as events:
                async for event in events:
                    if completed:
                        raise ProviderError("protocol", "模型完成后返回了额外事件。")
                    if isinstance(event, TurnCompleted):
                        if event.message.role != "assistant":
                            raise ProviderError("protocol", "模型回复角色不正确。")
                        # 此处没有 await，完整消息对一起提交。
                        self._history.extend((deepcopy(user), deepcopy(event.message)))
                        completed = True
                    yield event
            if not completed:
                raise ProviderError("protocol", "回复未完整结束，本轮未计入历史。")
        finally:
            self._busy = False

    async def aclose(self) -> None:
        if not self._closed:
            await self._provider.aclose()
            self._closed = True
            self._history.clear()
