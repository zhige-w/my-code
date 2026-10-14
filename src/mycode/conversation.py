"""会话、模式与单运行生命周期。"""

import asyncio
from contextlib import aclosing
from copy import deepcopy

from mycode.agent.events import RunFinished
from mycode.agent.history import HistoryStore
from mycode.agent.loop import AgentLoop
from mycode.agent.mode import PlanSnapshot
from mycode.agent.options import AgentOptions
from mycode.errors import ProviderError
from mycode.models import Message
from mycode.tools.scheduler import ToolScheduler


class Conversation:
    def __init__(self, provider, executor=None, *, options=None):
        self._provider = provider
        self._executor = executor
        self._history = HistoryStore()
        self.options = options or AgentOptions()
        self._loop = AgentLoop(
            provider,
            ToolScheduler(executor, self.options.max_read_concurrency) if executor else None,
            self.options,
        )
        self._busy = self._closed = self._reading = False
        self._stream = None
        self._cancel = asyncio.Event()
        self._done = asyncio.Event()
        self._done.set()
        self._close_done = asyncio.Event()
        self._mode = "execute"
        self._latest_plan = None

    @property
    def history(self):
        return self._history.snapshot()

    @property
    def busy(self):
        return self._busy

    @property
    def mode(self):
        return self._mode

    @property
    def latest_plan(self):
        return deepcopy(self._latest_plan)

    def set_mode(self, mode):
        if self._busy or self._closed:
            raise ProviderError("response", "运行期间或关闭后不能切换模式。")
        if mode not in {"plan", "execute"}:
            raise ValueError("未知模式。")
        self._mode = mode

    def cancel(self):
        if self._busy:
            self._cancel.set()

    async def stream_turn(self, user_text):
        if self._closed or self._busy or not user_text.strip():
            raise ProviderError("response", "会话已关闭、当前任务未结束或输入为空。")
        self._busy = True
        self._done.clear()
        self._cancel = asyncio.Event()
        mode = self._mode
        stream = self._loop.run(user_text, history=self._history, mode=mode, cancel=self._cancel)
        self._stream = stream
        try:
            while True:
                self._reading = True
                try:
                    event = await anext(stream)
                except StopAsyncIteration:
                    break
                finally:
                    self._reading = False
                if isinstance(event, RunFinished):
                    if (
                        mode == "plan"
                        and event.reason == "completed"
                        and event.message.text.strip()
                    ):
                        self._latest_plan = PlanSnapshot(Message("user", user_text), event.message)
                    self._busy = False
                    self._done.set()
                yield event
        finally:
            await stream.aclose()
            if self._stream is stream:
                self._stream = None
                self._busy = False
                self._done.set()

    async def execute_plan(self):
        self.set_mode("execute")
        plan = self.latest_plan
        if plan is None:
            raise ProviderError("response", "没有可执行计划，请先 /plan 规划或直接提交任务。")
        async with aclosing(
            self.stream_turn("请立即执行以下最近计划，使用工具完成并验证：\n" + plan.reply.text)
        ) as events:
            async for event in events:
                yield event

    async def aclose(self):
        if self._closed:
            await self._close_done.wait()
            return
        self._closed = True
        try:
            self.cancel()
            if self._stream is not None and not self._reading:
                await self._stream.aclose()
                self._busy = False
                self._done.set()
            await self._done.wait()
            await self._provider.aclose()
            self._history.clear()
            self._latest_plan = None
        finally:
            self._close_done.set()
