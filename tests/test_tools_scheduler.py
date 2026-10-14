import asyncio
import json

import pytest

from mycode.errors import ProviderError
from mycode.models import ToolCall, ToolFinished
from mycode.tools import create_tool_registry
from mycode.tools.base import ToolContext, ToolDefinition, ToolOutput, schema
from mycode.tools.executor import ToolExecutor
from mycode.tools.registry import ToolRegistry
from mycode.tools.scheduler import BatchCompleted, ToolScheduler


def scheduler(tmp_path):
    return ToolScheduler(ToolExecutor(create_tool_registry(), ToolContext(tmp_path)))


@pytest.mark.parametrize(
    "bad",
    [
        ToolCall("", "write_file", "{}"),
        ToolCall("b", "read_file", "{"),
        ToolCall("b", "read_file", "[]"),
        ToolCall("b", "read_file", "{}"),
    ],
)
def test_preflight_no_side_effect(tmp_path, bad):
    s = scheduler(tmp_path)
    with pytest.raises(ProviderError):
        s.validate_batch(
            (ToolCall("a", "write_file", '{"path":"x","content":"x"}'), bad), mode="execute"
        )
    assert not (tmp_path / "x").exists()


def test_preflight_unknown_rejected_and_ids(tmp_path):
    s = scheduler(tmp_path)
    batch = s.validate_batch(
        (ToolCall("a", "missing", "{}"), ToolCall("b", "write_file", "{")), mode="plan"
    )
    assert [i.kind for i in batch.items] == ["unknown", "rejected"]
    with pytest.raises(ProviderError):
        s.validate_batch(
            (ToolCall("a", "read_file", "{}"), ToolCall("a", "missing", "{}")), mode="execute"
        )


async def test_serial_results_real_files(tmp_path):
    s = scheduler(tmp_path)
    calls = (
        ToolCall("a", "write_file", '{"path":"x","content":"中文"}'),
        ToolCall("b", "read_file", '{"path":"x"}'),
        ToolCall("c", "missing", "{}"),
    )
    events = [
        e
        async for e in s.execute_batch(
            s.validate_batch(calls, mode="execute"), cancel=asyncio.Event()
        )
    ]
    assert isinstance(events[-1], BatchCompleted)
    assert [r.call_id for r in events[-1].results] == ["a", "b", "c"]
    assert "中文" in events[-1].results[1].data["content"]
    assert events[-1].results[-1].error.code == "unknown_tool"


class Controlled:
    def __init__(self, name, state, read_only=True):
        self.definition = ToolDefinition(
            name, "测试", schema({"n": {"type": "integer"}}, ["n"]), read_only
        )
        self.state = state

    async def execute(self, arguments, context):
        n = arguments["n"]
        state = self.state
        state["active"].add(n)
        state["max"] = max(state["max"], len(state["active"]))
        state["starts"].append(n)
        state["changed"].set()
        try:
            await state["gates"][n].wait()
            return ToolOutput({"n": n})
        finally:
            state["active"].remove(n)


def controlled(tmp_path, size=6):
    state = {
        "active": set(),
        "max": 0,
        "starts": [],
        "changed": asyncio.Event(),
        "gates": [asyncio.Event() for _ in range(size)],
    }
    registry = ToolRegistry()
    registry.register(Controlled("read", state))
    registry.register(Controlled("write", state, False))
    return ToolScheduler(ToolExecutor(registry, ToolContext(tmp_path))), state


async def wait_starts(state, count):
    async with asyncio.timeout(3):
        while len(state["starts"]) < count:
            state["changed"].clear()
            if len(state["starts"]) < count:
                await state["changed"].wait()


async def test_concurrency_limit_order_and_barrier(tmp_path):
    s, state = controlled(tmp_path, 8)
    calls = tuple(
        ToolCall(str(n), "read" if n != 6 else "write", json.dumps({"n": n})) for n in range(8)
    )
    events = []

    async def consume():
        async for event in s.execute_batch(
            s.validate_batch(calls, mode="execute"), cancel=asyncio.Event()
        ):
            events.append(event)

    task = asyncio.create_task(consume())
    await wait_starts(state, 4)
    assert state["starts"] == [0, 1, 2, 3] and state["max"] == 4
    state["gates"][3].set()
    await wait_starts(state, 5)
    state["gates"][4].set()
    await wait_starts(state, 6)
    assert 6 not in state["starts"]
    for n in range(6):
        state["gates"][n].set()
    await wait_starts(state, 7)
    assert state["active"] == {6}
    state["gates"][6].set()
    await wait_starts(state, 8)
    assert state["active"] == {7}
    state["gates"][7].set()
    await task
    assert [r.call_id for r in events[-1].results] == list(map(str, range(8)))
    assert [e.result.call_id for e in events if isinstance(e, ToolFinished)][0] == "3"


async def test_cleanup_queue_and_pending(tmp_path):
    s, state = controlled(tmp_path)
    calls = tuple(ToolCall(str(n), "read", json.dumps({"n": n})) for n in range(6))
    cancel = asyncio.Event()

    async def consume():
        return [
            e async for e in s.execute_batch(s.validate_batch(calls, mode="execute"), cancel=cancel)
        ]

    task = asyncio.create_task(consume())
    await wait_starts(state, 4)
    cancel.set()
    events = await asyncio.wait_for(task, 3)
    assert not state["active"] and state["starts"] == [0, 1, 2, 3]
    assert len(events[-1].results) == 6
    assert all(r.status == "cancelled" for r in s.results)


async def test_cleanup_backpressure_close(tmp_path):
    s, state = controlled(tmp_path, 30)
    for gate in state["gates"]:
        gate.set()
    calls = tuple(ToolCall(str(n), "read", json.dumps({"n": n})) for n in range(30))
    stream = s.execute_batch(s.validate_batch(calls, mode="execute"), cancel=asyncio.Event())
    await anext(stream)
    await asyncio.sleep(0.05)
    await asyncio.wait_for(stream.aclose(), 3)
    assert not state["active"] and len(s.results) == 30
    assert len(state["starts"]) < 30
    assert len({r.call_id for r in s.results}) == 30


@pytest.mark.parametrize("known", [False, True])
async def test_cleanup_known_commit_result(tmp_path, known):
    s = scheduler(tmp_path)
    started = asyncio.Event()

    async def execute(call):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            if known:
                error.result_frame = {"data": {"known": True}, "error": None}
            raise

    s.executor.execute = execute
    cancel = asyncio.Event()
    batch = s.validate_batch(
        (ToolCall("a", "write_file", '{"path":"x","content":"x"}'),), mode="execute"
    )

    async def consume():
        return [e async for e in s.execute_batch(batch, cancel=cancel)]

    task = asyncio.create_task(consume())
    await started.wait()
    cancel.set()
    await task
    assert s.results[0].status == ("success" if known else "cancelled")
    assert s.results[0].data == ({"known": True} if known else {"outcome_unknown": True})


async def test_cleanup_unexpected_tool_cancellation(tmp_path):
    s = scheduler(tmp_path)

    async def cancelled(call):
        raise asyncio.CancelledError()

    s.executor.execute = cancelled
    batch = s.validate_batch((ToolCall("a", "read_file", '{"path":"x"}'),), mode="execute")
    _ = [e async for e in s.execute_batch(batch, cancel=asyncio.Event())]
    assert s.failure and s.results[0].status == "cancelled"
