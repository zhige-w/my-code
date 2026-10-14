import asyncio

import pytest

from mycode.agent.collector import CollectedResponse, StreamCollector
from mycode.errors import ProviderError
from mycode.models import Message, ResponseCompleted, TextDelta


async def test_dual_path_realtime():
    release = asyncio.Event()

    async def stream():
        yield TextDelta("首段")
        await release.wait()
        yield TextDelta("末段")
        yield ResponseCompleted(Message("assistant", "首段末段"))

    events = StreamCollector().collect(stream(), cancel=asyncio.Event())
    assert await anext(events) == TextDelta("首段")
    release.set()
    rest = [e async for e in events]
    assert isinstance(rest[-1], CollectedResponse)
    assert rest[-1].message.text == "首段末段"


@pytest.mark.parametrize("mode", ["missing", "extra", "duplicate", "role", "text"])
async def test_dual_path_invalid(mode):
    async def stream():
        yield TextDelta("x")
        if mode != "missing":
            yield ResponseCompleted(
                Message("user" if mode == "role" else "assistant", "bad" if mode == "text" else "x")
            )
        if mode == "extra":
            yield TextDelta("late")
        if mode == "duplicate":
            yield ResponseCompleted(Message("assistant", "x"))

    with pytest.raises(ProviderError):
        _ = [e async for e in StreamCollector().collect(stream(), cancel=asyncio.Event())]


@pytest.mark.parametrize("close", [False, True])
async def test_cleanup_stream(close):
    closed = asyncio.Event()
    cancel = asyncio.Event()

    async def stream():
        try:
            yield TextDelta("x")
            await asyncio.Event().wait()
        finally:
            closed.set()

    events = StreamCollector().collect(stream(), cancel=cancel)
    await anext(events)
    if close:
        await events.aclose()
    else:
        pending = asyncio.create_task(anext(events))
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert closed.is_set()
