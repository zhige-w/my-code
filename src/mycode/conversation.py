"""单批工具执行、单次总结及会话历史生命周期。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from copy import deepcopy
from dataclasses import replace

from mycode.errors import ProviderError
from mycode.models import (
    Message,
    ResponseCompleted,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolFinished,
    ToolResult,
    ToolResultError,
    ToolStarted,
    TurnCompleted,
)
from mycode.providers.base import Provider


class Conversation:
    def __init__(self, provider: Provider, executor=None) -> None:
        self._provider = provider
        self._executor = executor
        self._history: list[Message] = []
        self._busy = False
        self._closed = False
        self._phase = "idle"
        self._tool_history_committed = False
        self._tool_results = []

    @property
    def history(self) -> tuple[Message, ...]:
        return deepcopy(tuple(self._history))

    @property
    def tool_results(self):
        return deepcopy(tuple(self._tool_results))

    @property
    def phase(self):
        return self._phase

    @property
    def tool_history_committed(self):
        return self._tool_history_committed

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
        self._phase = "initial"
        self._tool_history_committed = False
        self._tool_results = []
        user = Message("user", user_text)
        response = None
        batch_started = False
        cancellation = None
        current_active = False
        definitions = self._executor.registry.definitions() if self._executor else ()

        def commit_batch():
            if not self.tool_history_committed:
                self._history.extend(
                    deepcopy(
                        (
                            user,
                            response,
                            Message("tool", "", tool_results=tuple(self._tool_results)),
                        )
                    )
                )
                self._tool_history_committed = True

        try:
            # 两个阶段显式编排；这里不根据模型结果进入循环。
            options = {"tools": definitions, "allow_tools": True} if self._executor else {}
            async with aclosing(self._provider.stream((*self.history, user), **options)) as events:
                async for event in events:
                    if response is not None:
                        raise ProviderError("protocol", "模型完成后返回了额外事件。")
                    if isinstance(event, ResponseCompleted):
                        response = event.message
                    else:
                        yield event
            if response is None or response.role != "assistant":
                raise ProviderError("protocol", "回复未完整结束或角色异常，本轮未计入历史。")
            calls = response.tool_calls
            if not calls:
                self._history.extend(deepcopy((user, response)))
                yield TurnCompleted(response)
                return
            if not self._executor:
                raise ProviderError("protocol", "当前会话没有工具执行器。")
            ids = [c.id for c in calls]
            if any(not i.strip() for i in ids) or len(set(ids)) != len(ids):
                raise ProviderError("protocol", "工具调用 ID 缺失或重复，本批未执行。")
            # 批次入口提供取消检查点；进入后始终补齐结果再提交历史。
            await asyncio.sleep(0)
            self._phase = "tools"
            batch_started = True
            for call in calls:
                yield ToolStarted(call.id, call.name)
                current_active = True
                result = await self._executor.execute(call)
                self._tool_results.append(result)
                current_active = False
                yield ToolFinished(result)
                await asyncio.sleep(0)
            commit_batch()
            self._phase = "summary"
            summary = None
            async with aclosing(
                self._provider.stream(self.history, tools=definitions, allow_tools=False)
            ) as events:
                async for event in events:
                    if summary is not None:
                        raise ProviderError("protocol", "总结完成后返回了额外事件。")
                    if isinstance(event, ResponseCompleted):
                        summary = event.message
                    elif isinstance(event, (TextDelta, ThinkingDelta)):
                        yield replace(event, phase="summary")
            if summary is None or summary.role != "assistant" or summary.tool_calls:
                raise ProviderError("protocol", "总结不完整或返回了禁用的工具调用。")
            self._history.append(deepcopy(summary))
            yield TurnCompleted(summary)
        except asyncio.CancelledError as exc:
            cancellation = exc
            raise
        finally:
            if batch_started and not self.tool_history_committed:
                for index, call in enumerate(response.tool_calls[len(self._tool_results) :]):
                    data = getattr(cancellation, "result_data", {}) if index == 0 else {}
                    if index == 0 and current_active and call.name in {"write_file", "edit_file"}:
                        data = {**data, "outcome_unknown": True}
                    frame = getattr(cancellation, "result_frame", None) if index == 0 else None
                    if frame:
                        error = frame.get("error")
                        result = ToolResult(
                            call.id,
                            call.name,
                            "error" if error else "success",
                            frame.get("data", {}),
                            ToolResultError(error["code"], error["message"]) if error else None,
                            truncated=frame.get("truncated", False),
                        )
                    else:
                        message = (
                            "执行已取消；操作可能已生效，请重新读取确认。"
                            if data.get("outcome_unknown")
                            else "执行已取消。"
                        )
                        result = ToolResult(
                            call.id,
                            call.name,
                            "cancelled",
                            data,
                            ToolResultError("cancelled", message),
                        )
                    self._tool_results.append(result)
                commit_batch()
            self._busy = False
            self._phase = "idle"

    async def aclose(self) -> None:
        if not self._closed:
            await self._provider.aclose()
            self._closed = True
            self._history.clear()
