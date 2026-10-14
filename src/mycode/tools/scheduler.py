"""整批预检、只读并发与副作用屏障。"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass

from jsonschema import ValidationError

from mycode.errors import ProviderError
from mycode.models import ToolCall, ToolFinished, ToolResult, ToolResultError, ToolStarted


@dataclass(frozen=True)
class PreparedCall:
    index: int
    call: ToolCall
    kind: str
    arguments: dict | None = None
    immediate_result: ToolResult | None = None


@dataclass(frozen=True)
class PreparedBatch:
    items: tuple[PreparedCall, ...]
    contains_unknown: bool


@dataclass(frozen=True)
class BatchCompleted:
    results: tuple[ToolResult, ...]


def cancelled_result(call, error=None, *, active=False):
    frame = getattr(error, "result_frame", None)
    if frame:
        failure = frame.get("error")
        return ToolResult(
            call.id,
            call.name,
            "error" if failure else "success",
            frame.get("data", {}),
            ToolResultError(failure["code"], failure["message"]) if failure else None,
            frame.get("truncated", False),
        )
    data = getattr(error, "result_data", {})
    if active and call.name in {"write_file", "edit_file"}:
        data = {**data, "outcome_unknown": True}
    message = (
        "执行已取消；操作可能已生效，请重新读取确认。"
        if data.get("outcome_unknown")
        else "执行已取消。"
    )
    return ToolResult(call.id, call.name, "cancelled", data, ToolResultError("cancelled", message))


class ToolScheduler:
    def __init__(self, executor, max_read_concurrency=4):
        self.executor = executor
        self.max_read_concurrency = max_read_concurrency
        self.results = ()
        self.failure = None

    def validate_batch(self, calls, *, mode):
        ids = [c.id for c in calls]
        if any(not i.strip() for i in ids) or len(set(ids)) != len(ids):
            raise ProviderError("invalid_response", "工具调用 ID 缺失或重复，本批未执行。")
        items = []
        for index, call in enumerate(calls):
            try:
                tool = self.executor.registry.get(call.name)
            except KeyError:
                code, kind = "unknown_tool", "unknown"
            else:
                if mode == "plan" and not tool.definition.read_only:
                    code, kind = "mode_restricted", "rejected"
                else:
                    try:
                        arguments = self.executor.validate_arguments(call)
                    except (ValueError, ValidationError):
                        raise ProviderError(
                            "invalid_response", "工具参数无效，本批未执行。"
                        ) from None
                    items.append(
                        PreparedCall(
                            index,
                            call,
                            "read_only" if tool.definition.read_only else "side_effect",
                            arguments,
                        )
                    )
                    continue
            message = f"未知工具：{call.name}" if kind == "unknown" else "规划模式禁止执行此工具。"
            result = ToolResult(call.id, call.name, "error", {}, ToolResultError(code, message))
            items.append(PreparedCall(index, call, kind, immediate_result=result))
        return PreparedBatch(tuple(items), any(i.kind == "unknown" for i in items))

    async def execute_batch(self, batch, *, cancel):
        queue = asyncio.Queue(maxsize=max(8, 2 * self.max_read_concurrency))
        slots = [None] * len(batch.items)
        emitted = set()
        self.results = ()
        self.failure = None

        async def call_one(item):
            started = False
            try:
                await queue.put(ToolStarted(item.call.id, item.call.name))
                started = True
                slots[item.index] = await self.executor.execute(item.call)
            except asyncio.CancelledError as exc:
                if slots[item.index] is None:
                    slots[item.index] = cancelled_result(item.call, exc, active=started)
                raise
            await queue.put(ToolFinished(slots[item.index]))

        async def drive():
            active = set()
            try:
                index = 0
                while index < len(batch.items) and not cancel.is_set():
                    item = batch.items[index]
                    if item.kind == "read_only":
                        stop = index
                        while stop < len(batch.items) and batch.items[stop].kind == "read_only":
                            stop += 1
                        while index < stop or active:
                            while index < stop and len(active) < self.max_read_concurrency:
                                if cancel.is_set():
                                    raise asyncio.CancelledError()
                                active.add(asyncio.create_task(call_one(batch.items[index])))
                                index += 1
                            done, _ = await asyncio.wait(
                                active, return_when=asyncio.FIRST_COMPLETED
                            )
                            for task in done:
                                active.remove(task)
                                task.result()
                    else:
                        if item.immediate_result is not None:
                            slots[index] = item.immediate_result
                            await queue.put(ToolFinished(slots[index]))
                        else:
                            task = asyncio.create_task(call_one(item))
                            active.add(task)
                            await task
                            active.remove(task)
                        index += 1
            except asyncio.CancelledError:
                if not cancel.is_set():
                    self.failure = "工具执行意外中断，已停止并清理。"
            except Exception:
                self.failure = "工具调度异常，已停止并清理。"
            finally:
                for task in active:
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
                for item in batch.items:
                    if slots[item.index] is None:
                        slots[item.index] = cancelled_result(item.call)
                self.results = tuple(slots)

        runner = asyncio.create_task(drive())
        watcher = asyncio.create_task(cancel.wait())
        reader = None
        try:
            while not runner.done() or not queue.empty():
                reader = asyncio.create_task(queue.get())
                await asyncio.wait((runner, reader, watcher), return_when=asyncio.FIRST_COMPLETED)
                if cancel.is_set():
                    runner.cancel()
                    await runner
                    break
                if reader.done():
                    event = reader.result()
                    if isinstance(event, ToolFinished):
                        emitted.add(event.result.call_id)
                    yield event
                else:
                    reader.cancel()
                    with suppress(asyncio.CancelledError):
                        await reader
            await runner
            for result in self.results:
                if result.call_id not in emitted:
                    yield ToolFinished(result)
            yield BatchCompleted(self.results)
        finally:
            runner.cancel()
            watcher.cancel()
            if reader is not None:
                reader.cancel()
            await asyncio.gather(
                runner, watcher, *([reader] if reader else []), return_exceptions=True
            )
