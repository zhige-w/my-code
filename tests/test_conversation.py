import asyncio
from dataclasses import replace

import pytest
from conftest import FakeProvider, SSEStream, anthropic_parts, openai_parts

from mycode.conversation import Conversation
from mycode.errors import ProviderError
from mycode.models import Message, NativeContent, ResponseCompleted, TextDelta, TurnCompleted
from mycode.providers.anthropic import AnthropicProvider
from mycode.providers.openai import OpenAIProvider


async def test_history_success_and_reset():
    provider = FakeProvider()
    conversation = Conversation(provider)
    events = conversation.stream_turn("口令：青竹742")
    assert await anext(events) == TextDelta("你好")
    assert conversation.busy and conversation.history == ()
    completion = await anext(events)
    assert isinstance(completion, TurnCompleted)
    assert len(conversation.history) == 2
    assert [e async for e in events] == []
    assert not conversation.busy
    _ = [e async for e in conversation.stream_turn("口令是什么")]
    assert provider.requests[1] == (
        Message("user", "口令：青竹742"),
        Message("assistant", "你好"),
        Message("user", "口令是什么"),
    )
    assert Conversation(FakeProvider()).history == ()
    await conversation.aclose()
    await conversation.aclose()
    assert conversation.history == () and provider.closed


async def test_history_native_copies():
    blocks = [{"type": "text", "text": "answer"}]
    native = NativeContent("anthropic", blocks)
    blocks[0]["text"] = "external"
    assert native.blocks[0]["text"] == "answer"

    class NativeProvider(FakeProvider):
        async def stream(self, messages):
            yield ResponseCompleted(Message("assistant", "answer", native))

    conversation = Conversation(NativeProvider())
    _ = [e async for e in conversation.stream_turn("hello")]
    native.blocks[0]["text"] = "changed"
    snapshot = conversation.history
    snapshot[1].native_content.blocks[0]["text"] = "changed again"
    assert conversation.history[1].native_content.blocks[0]["text"] == "answer"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_history_real_adapters(make_http, config, protocol):
    if protocol == "openai":
        parts = openai_parts
        kind = OpenAIProvider
    else:
        parts = anthropic_parts
        kind = AnthropicProvider
    client, requests = make_http([SSEStream(parts()), SSEStream(parts())])
    provider = kind(replace(config, protocol=protocol), http_client=client)
    conversation = Conversation(provider)
    _ = [e async for e in conversation.stream_turn("first")]
    _ = [e async for e in conversation.stream_turn("second")]
    import json

    messages = json.loads(requests[1].content)["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "first"
    assert messages[-1]["content"] == "second"
    await conversation.aclose()


async def test_cancel_failure_and_recovery():
    provider = FakeProvider()
    conversation = Conversation(provider)
    _ = [e async for e in conversation.stream_turn("successful")]
    good_history = conversation.history
    provider.wait = True
    events = conversation.stream_turn("cancelled")
    assert await anext(events) == TextDelta("你好")
    pending = asyncio.create_task(anext(events))
    await asyncio.sleep(0)
    with pytest.raises(ProviderError):
        _ = [e async for e in conversation.stream_turn("duplicate")]
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not conversation.busy and provider.stream_closed
    assert conversation.history == good_history
    provider.wait = False
    provider.error = ProviderError("connection", "网络异常")
    with pytest.raises(ProviderError):
        _ = [e async for e in conversation.stream_turn("failed")]
    assert not conversation.busy and conversation.history == good_history
    provider.error = None
    _ = [e async for e in conversation.stream_turn("recovered")]
    assert provider.requests[-1] == (*good_history, Message("user", "recovered"))


async def test_consumer_exit_and_committed_cancel():
    provider = FakeProvider()
    conversation = Conversation(provider)
    events = conversation.stream_turn("early")
    await anext(events)
    await events.aclose()
    assert provider.stream_closed and not conversation.busy
    assert conversation.history == ()
    events = conversation.stream_turn("committed")
    await anext(events)
    assert isinstance(await anext(events), TurnCompleted)
    await events.aclose()
    assert len(conversation.history) == 2 and not conversation.busy


async def test_blank_closed_and_missing_completion():
    conversation = Conversation(FakeProvider())
    with pytest.raises(ProviderError):
        _ = [e async for e in conversation.stream_turn(" \n")]
    assert not conversation.busy
    await conversation.aclose()
    with pytest.raises(ProviderError):
        _ = [e async for e in conversation.stream_turn("hello")]

    class Incomplete(FakeProvider):
        async def stream(self, messages):
            yield TextDelta("partial")

    incomplete = Conversation(Incomplete())
    with pytest.raises(ProviderError):
        _ = [e async for e in incomplete.stream_turn("hello")]
    assert incomplete.history == () and not incomplete.busy


class BatchProvider:
    def __init__(self, calls, *, summary="ok", summary_wait=False):
        self.calls = tuple(calls)
        self.requests = []
        self.options = []
        self.summary = summary
        self.summary_wait = summary_wait
        self.waiting = asyncio.Event()

    async def stream(self, messages, **options):
        self.requests.append(tuple(messages))
        self.options.append(options)
        if len(self.requests) == 1:
            yield ResponseCompleted(Message("assistant", "准备", tool_calls=self.calls))
        else:
            yield TextDelta("总结中")
            if self.summary_wait:
                self.waiting.set()
                await asyncio.Event().wait()
            if self.summary == "error":
                raise ProviderError("connection", "模拟失败")
            if self.summary == "tool":
                yield ResponseCompleted(Message("assistant", "", tool_calls=self.calls))
            else:
                yield ResponseCompleted(Message("assistant", "总结中"))

    async def aclose(self):
        pass


def tools_conversation(tmp_path, calls, **options):
    from mycode.tools import create_tool_registry
    from mycode.tools.base import ToolContext
    from mycode.tools.executor import ToolExecutor

    p = BatchProvider(calls, **options)
    return Conversation(p, ToolExecutor(create_tool_registry(), ToolContext(tmp_path))), p


async def test_tool_batch(tmp_path):
    from mycode.models import ToolCall, ToolFinished, ToolStarted

    calls = [
        ToolCall("a", "write_file", '{"path":"a","content":"青竹742"}'),
        ToolCall("b", "read_file", '{"path":"a"}'),
        ToolCall("c", "read_file", "{"),
        ToolCall("d", "missing", "{}"),
    ]
    conversation, provider = tools_conversation(tmp_path, calls)
    events = [e async for e in conversation.stream_turn("操作")]
    assert [e.call_id for e in events if isinstance(e, ToolStarted)] == ["a", "b", "c", "d"]
    results = [e.result for e in events if isinstance(e, ToolFinished)]
    assert "青竹742" in results[1].data["content"]
    assert results[2].error.code == "invalid_arguments"
    assert results[3].error.code == "unknown_tool"
    assert len(provider.requests) == 2 and not provider.options[1]["allow_tools"]
    assert any(isinstance(e, TextDelta) and e.phase == "summary" for e in events)
    assert len(conversation.history) == 4 and conversation.tool_history_committed
    assert len(conversation.history[2].tool_results) == 4
    snapshot = conversation.history
    snapshot[2].tool_results[0].data.clear()
    assert conversation.history[2].tool_results[0].data


@pytest.mark.parametrize("ids", [("",), ("a", "a")])
async def test_tool_batch_invalid_ids(tmp_path, ids):
    from mycode.models import ToolCall

    calls = [ToolCall(i, "write_file", '{"path":"bad","content":"bad"}') for i in ids]
    conversation, provider = tools_conversation(tmp_path, calls)
    with pytest.raises(ProviderError):
        _ = [e async for e in conversation.stream_turn("操作")]
    assert not (tmp_path / "bad").exists() and not conversation.history
    assert len(provider.requests) == 1


@pytest.mark.parametrize("summary", ["error", "tool"])
async def test_tool_summary_failure_history(tmp_path, summary):
    from mycode.models import ToolCall

    conversation, p = tools_conversation(
        tmp_path, [ToolCall("a", "write_file", '{"path":"a","content":"ok"}')], summary=summary
    )
    with pytest.raises(ProviderError):
        _ = [e async for e in conversation.stream_turn("操作")]
    assert len(conversation.history) == 3 and (tmp_path / "a").read_text() == "ok"
    p.summary = "ok"
    _ = [e async for e in conversation.stream_turn("引用")]
    assert p.requests[-1][2].tool_results[0].status == "success"
    assert len(p.requests) == 3


async def test_tool_cancel_during_execution(tmp_path):
    import json

    from mycode.models import ToolCall, ToolStarted

    calls = [
        ToolCall("a", "write_file", '{"path":"a","content":"ok"}'),
        ToolCall(
            "b", "execute_command", json.dumps({"command": "echo $$ > pid; sleep 20; touch late"})
        ),
        ToolCall("c", "write_file", '{"path":"never","content":"bad"}'),
    ]
    c, p = tools_conversation(tmp_path, calls)
    events = c.stream_turn("操作")
    while True:
        e = await anext(events)
        if isinstance(e, ToolStarted) and e.call_id == "b":
            break
    pending = asyncio.create_task(anext(events))
    for _ in range(100):
        if (tmp_path / "pid").exists():
            break
        await asyncio.sleep(0.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not c.busy and c.tool_history_committed
    assert [r.status for r in c.history[2].tool_results] == ["success", "cancelled", "cancelled"]
    assert not (tmp_path / "never").exists() and not (tmp_path / "late").exists()
    assert len(p.requests) == 1


async def test_tool_cancel_summary_and_consumer_close(tmp_path):
    from mycode.models import ToolCall, ToolStarted

    calls = [ToolCall("a", "write_file", '{"path":"a","content":"ok"}')]
    c, p = tools_conversation(tmp_path, calls, summary_wait=True)

    async def consume():
        return [e async for e in c.stream_turn("操作")]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(p.waiting.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(c.history) == 3 and c.tool_history_committed and not c.busy
    c, p = tools_conversation(tmp_path, calls)
    events = c.stream_turn("关闭")
    assert isinstance(await anext(events), ToolStarted)
    await events.aclose()
    assert c.history[2].tool_results[0].status == "cancelled"
    assert not c.busy


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_tool_batch_real_adapters(tmp_path, make_http, config, protocol):
    import json

    from conftest import anthropic_tool_parts, openai_tool_parts

    from mycode.tools import create_tool_registry
    from mycode.tools.base import ToolContext
    from mycode.tools.executor import ToolExecutor

    calls = [
        ("a", "write_file", '{"path":"a","content":"中文"}'),
        ("b", "read_file", '{"path":"a"}'),
    ]
    parts = openai_tool_parts if protocol == "openai" else anthropic_tool_parts
    ordinary = openai_parts if protocol == "openai" else anthropic_parts
    kind = OpenAIProvider if protocol == "openai" else AnthropicProvider
    client, requests = make_http(
        [SSEStream(parts(calls)), SSEStream(ordinary()), SSEStream(ordinary())]
    )
    provider = kind(replace(config, protocol=protocol), http_client=client)
    c = Conversation(provider, ToolExecutor(create_tool_registry(), ToolContext(tmp_path)))
    _ = [e async for e in c.stream_turn("写后读")]
    assert (tmp_path / "a").read_text() == "中文" and len(requests) == 2
    _ = [e async for e in c.stream_turn("引用")]
    body = json.loads(requests[-1].content)
    assert "中文" in json.dumps(body, ensure_ascii=False)
    assert len(requests) == 3
    await c.aclose()


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("mode", ["duplicate_id", "missing_id", "truncated", "cancel"])
async def test_tool_batch_protocol_no_side_effect(tmp_path, make_http, config, protocol, mode):
    from conftest import anthropic_tool_parts, openai_tool_parts

    from mycode.tools import create_tool_registry
    from mycode.tools.base import ToolContext
    from mycode.tools.executor import ToolExecutor

    calls = [("a" if mode != "missing_id" else "", "write_file", '{"path":"bad","content":"bad"}')]
    if mode == "duplicate_id":
        calls.append(calls[0])
    if protocol == "openai":
        parts = openai_tool_parts(calls, finish=None if mode == "truncated" else "tool_calls")
        kind = OpenAIProvider
    else:
        parts = anthropic_tool_parts(calls, stop=mode != "truncated")
        kind = AnthropicProvider
    stream = SSEStream(parts, gate_after=2 if mode == "cancel" else None)
    client, _ = make_http([stream])
    c = Conversation(
        kind(replace(config, protocol=protocol), http_client=client),
        ToolExecutor(create_tool_registry(), ToolContext(tmp_path)),
    )

    async def consume():
        return [e async for e in c.stream_turn("操作")]

    if mode == "cancel":
        task = asyncio.create_task(consume())
        await asyncio.wait_for(stream.waiting.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(ProviderError):
            await consume()
    assert c.history == () and not (tmp_path / "bad").exists()
    await c.aclose()


async def test_tool_cancel_between_calls(tmp_path):
    from mycode.models import ToolCall, ToolFinished

    c, p = tools_conversation(
        tmp_path,
        [
            ToolCall("a", "write_file", '{"path":"a","content":"ok"}'),
            ToolCall("b", "write_file", '{"path":"never","content":"bad"}'),
        ],
    )
    events = c.stream_turn("操作")
    while not isinstance(await anext(events), ToolFinished):
        pass
    await events.aclose()
    assert c.history[2].tool_results[0].status == "success"
    assert c.history[2].tool_results[1].status == "cancelled"
    assert not c.history[2].tool_results[1].data.get("outcome_unknown")
    assert not (tmp_path / "never").exists()


async def test_tool_first_response_extra_event(tmp_path):
    from mycode.models import ToolCall

    calls = [ToolCall("a", "write_file", '{"path":"bad","content":"bad"}')]
    c, p = tools_conversation(tmp_path, calls)

    async def invalid(messages, **options):
        yield ResponseCompleted(Message("assistant", "", tool_calls=tuple(calls)))
        yield TextDelta("late")

    p.stream = invalid
    with pytest.raises(ProviderError):
        _ = [e async for e in c.stream_turn("操作")]
    assert not (tmp_path / "bad").exists() and c.history == ()


@pytest.mark.parametrize("failed", [False, True])
async def test_tool_cancel_known_worker_result(tmp_path, failed):
    from mycode.models import ToolCall

    c, p = tools_conversation(tmp_path, [ToolCall("a", "write_file", '{"path":"a","content":"x"}')])

    async def known(call):
        error = asyncio.CancelledError()
        error.result_frame = {
            "data": {"known": True},
            "error": {"code": "file_changed", "message": "已变化"} if failed else None,
        }
        raise error

    c._executor.execute = known
    with pytest.raises(asyncio.CancelledError):
        _ = [e async for e in c.stream_turn("操作")]
    result = c.history[2].tool_results[0]
    assert result.status == ("error" if failed else "success")
    assert result.data == {"known": True}
