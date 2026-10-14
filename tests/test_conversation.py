import asyncio
import json
from dataclasses import replace

import pytest
from conftest import (
    FakeProvider,
    SSEStream,
    anthropic_parts,
    anthropic_tool_parts,
    openai_parts,
    openai_tool_parts,
)
from test_agent_loop import SequenceProvider, call

from mycode.agent.events import ToolFinished, ToolStarted
from mycode.conversation import Conversation
from mycode.errors import ProviderError
from mycode.models import Message, NativeContent, ResponseCompleted, ToolCall
from mycode.providers.anthropic import AnthropicProvider
from mycode.providers.openai import OpenAIProvider
from mycode.tools import create_tool_registry
from mycode.tools.base import ToolContext
from mycode.tools.executor import ToolExecutor


class BatchProvider(SequenceProvider):
    def __init__(self, calls, *, summary="ok", summary_wait=False):
        self.calls = tuple(calls)
        self.summary = summary
        self.summary_wait = summary_wait
        super().__init__([])

    async def stream(self, messages, **options):
        self.requests.append((tuple(messages), options))
        if len(self.requests) == 1:
            yield ResponseCompleted(Message("assistant", "", tool_calls=self.calls))
        else:
            yield __import__("mycode.models", fromlist=["TextDelta"]).TextDelta("总结中")
            if self.summary_wait:
                self.waiting.set()
                await asyncio.Event().wait()
            if self.summary == "error":
                raise ProviderError("connection", "模拟失败")
            yield ResponseCompleted(Message("assistant", "总结中"))


def tools_conversation(tmp_path, calls, **options):
    provider = BatchProvider(calls, **options)
    return Conversation(
        provider, ToolExecutor(create_tool_registry(), ToolContext(tmp_path))
    ), provider


async def collect(conversation, text="任务"):
    return [e async for e in conversation.stream_turn(text)]


async def test_agent_entry_history_and_reset():
    provider = FakeProvider()
    c = Conversation(provider)
    events = await collect(c, "口令：青竹742")
    assert events[-1].reason == "completed" and not c.busy
    await collect(c, "口令是什么")
    assert provider.requests[1] == (
        Message("user", "口令：青竹742"),
        Message("assistant", "你好"),
        Message("user", "口令是什么"),
    )
    await c.aclose()
    await c.aclose()
    assert not c.history and provider.closed


async def test_agent_entry_native_copies():
    native = NativeContent("anthropic", [{"type": "text", "text": "你好"}])
    provider = SequenceProvider([Message("assistant", "你好", native)])
    c = Conversation(provider)
    await collect(c)
    native.blocks[0].clear()
    snapshot = c.history
    snapshot[1].native_content.blocks[0].clear()
    assert c.history[1].native_content.blocks[0]["text"] == "你好"


async def test_agent_entry_blank_closed_and_busy():
    provider = FakeProvider(wait=True)
    c = Conversation(provider)
    with pytest.raises(ProviderError):
        await collect(c, " ")
    task = asyncio.create_task(collect(c))
    await provider.started.wait()
    with pytest.raises(ProviderError):
        await collect(c)
    c.cancel()
    assert (await task)[-1].reason == "cancelled"
    assert not c.history
    await c.aclose()
    with pytest.raises(ProviderError):
        await collect(c)


async def test_agent_entry_error_and_recovery():
    provider = FakeProvider(error=ProviderError("connection", "网络异常"))
    c = Conversation(provider)
    assert (await collect(c))[-1].reason == "stream_error"
    assert not c.history and not c.busy
    provider.error = None
    assert (await collect(c))[-1].reason == "completed"


async def test_plan_mode_latest_and_execute(tmp_path):
    responses = [
        Message("assistant", "计划一"),
        Message("assistant", "计划二"),
        call("write_file", '{"path":"x","content":"执行"}'),
        Message("assistant", "完成"),
    ]
    provider = SequenceProvider(responses)
    c = Conversation(provider, ToolExecutor(create_tool_registry(), ToolContext(tmp_path)))
    c.set_mode("plan")
    await collect(c)
    await collect(c)
    assert c.mode == "plan" and c.latest_plan.reply.text == "计划二"
    assert {t.name for t in provider.requests[0][1]["tools"]} == {
        "read_file",
        "find_files",
        "search_code",
    }
    assert "禁止" in provider.requests[0][1]["system_prompt"]
    events = [e async for e in c.execute_plan()]
    assert events[-1].reason == "completed" and c.mode == "execute"
    assert (
        "计划二" in provider.requests[2][0][-1].text and len(provider.requests[2][1]["tools"]) == 6
    )
    assert (tmp_path / "x").read_text() == "执行"


async def test_plan_mode_no_plan_busy_and_failed_plan():
    provider = SequenceProvider(
        [Message("assistant", "计划"), ProviderError("connection", "失败"), "wait"]
    )
    c = Conversation(provider)
    c.set_mode("plan")
    with pytest.raises(ProviderError):
        _ = [e async for e in c.execute_plan()]
    assert not provider.requests and c.mode == "execute"
    c.set_mode("plan")
    await collect(c)
    await collect(c)
    assert c.latest_plan.reply.text == "计划"
    task = asyncio.create_task(collect(c))
    await provider.waiting.wait()
    with pytest.raises(ProviderError):
        c.set_mode("execute")
    c.cancel()
    await task
    assert c.mode == "plan" and c.latest_plan.reply.text == "计划"


async def test_plan_mode_reject_side_effects(tmp_path):
    c, provider = tools_conversation(
        tmp_path, [ToolCall("a", "write_file", '{"path":"x","content":"x"}')]
    )
    c.set_mode("plan")
    events = await collect(c)
    assert [e.result.error.code for e in events if isinstance(e, ToolFinished)] == [
        "mode_restricted"
    ]
    assert not (tmp_path / "x").exists() and c.mode == "plan"


async def test_cleanup_explicit_and_early_exit(tmp_path):
    c, p = tools_conversation(
        tmp_path, [ToolCall("a", "write_file", '{"path":"x","content":"x"}')], summary_wait=True
    )
    task = asyncio.create_task(collect(c))
    await asyncio.wait_for(p.waiting.wait(), 3)
    c.cancel()
    events = await task
    assert events[-1].reason == "cancelled" and events[-1].history_updated
    assert len(c.history) == 3 and (tmp_path / "x").exists()
    p.summary_wait = False
    assert (await collect(c))[-1].reason == "completed"
    c, _ = tools_conversation(
        tmp_path, [ToolCall("a", "execute_command", '{"command":"sleep 20"}')]
    )
    events = c.stream_turn("任务")
    while not isinstance(await anext(events), ToolStarted):
        pass
    await events.aclose()
    assert not c.busy and c.history[2].tool_results[0].status == "cancelled"


async def test_cleanup_aclose_during_read():
    provider = FakeProvider(wait=True)
    c = Conversation(provider)
    task = asyncio.create_task(collect(c))
    await provider.started.wait()
    await asyncio.wait_for(c.aclose(), 3)
    assert (await task)[-1].reason == "cancelled"
    assert provider.closed and not c.busy


async def test_cleanup_external_cancel(tmp_path):
    c, p = tools_conversation(
        tmp_path, [ToolCall("a", "write_file", '{"path":"x","content":"x"}')], summary_wait=True
    )
    task = asyncio.create_task(collect(c))
    await p.waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(c.history) == 3 and not c.busy


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_agent_entry_real_adapters_multibatch(tmp_path, make_http, config, protocol):
    tool_parts = openai_tool_parts if protocol == "openai" else anthropic_tool_parts
    ordinary = openai_parts if protocol == "openai" else anthropic_parts
    kind = OpenAIProvider if protocol == "openai" else AnthropicProvider
    calls = [("a", "write_file", '{"path":"x","content":"中文"}')]
    client, requests = make_http(
        [
            SSEStream(tool_parts(calls)),
            SSEStream(tool_parts([("a", "read_file", '{"path":"x"}')])),
            SSEStream(ordinary()),
            SSEStream(ordinary()),
        ]
    )
    c = Conversation(
        kind(replace(config, protocol=protocol), http_client=client),
        ToolExecutor(create_tool_registry(), ToolContext(tmp_path)),
    )
    events = await collect(c)
    assert events[-1].reason == "completed" and len(requests) == 3
    second = json.loads(requests[1].content)
    if protocol == "openai":
        assert second["messages"][-1]["tool_call_id"] == "a"
        assert second["tool_choice"] == "auto"
    else:
        assert second["messages"][-1]["content"][0]["tool_use_id"] == "a"
        assert second["tool_choice"]["type"] == "auto"
    await collect(c, "引用")
    assert "中文" in requests[-1].content.decode()
    await c.aclose()


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("mode", ["duplicate_id", "missing_id", "truncated", "cancel"])
async def test_agent_entry_protocol_no_side_effect(tmp_path, make_http, config, protocol, mode):
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
    task = asyncio.create_task(collect(c))
    if mode == "cancel":
        await stream.waiting.wait()
        c.cancel()
    events = await task
    assert events[-1].reason in {"cancelled", "stream_error", "invalid_response"}
    assert not c.history and not (tmp_path / "bad").exists()
    await c.aclose()


async def test_cleanup_concurrent_close_waits():
    gate = asyncio.Event()
    entered = asyncio.Event()

    class Closing(FakeProvider):
        async def aclose(self):
            entered.set()
            await gate.wait()
            self.closed = True

    p = Closing()
    c = Conversation(p)
    first = asyncio.create_task(c.aclose())
    await entered.wait()
    second = asyncio.create_task(c.aclose())
    await asyncio.sleep(0)
    assert not second.done()
    gate.set()
    await asyncio.gather(first, second)
    assert p.closed


@pytest.mark.parametrize(
    "second", [Message("assistant", ""), call("write_file", '{"path":"x","content":"x"}')]
)
async def test_plan_mode_empty_and_limit_do_not_replace(tmp_path, second):
    from mycode.agent.options import AgentOptions

    provider = SequenceProvider([Message("assistant", "已有计划"), second])
    c = Conversation(
        provider,
        ToolExecutor(create_tool_registry(), ToolContext(tmp_path)),
        options=AgentOptions(max_iterations=1),
    )
    c.set_mode("plan")
    await collect(c)
    await collect(c)
    assert c.latest_plan.reply.text == "已有计划" and c.mode == "plan"
    assert not (tmp_path / "x").exists()


async def test_plan_mode_all_side_effects_and_no_unknown_limit(tmp_path):
    calls = Message(
        "assistant",
        "",
        tool_calls=(
            ToolCall("a", "write_file", '{"path":"x","content":"x"}'),
            ToolCall("b", "edit_file", '{"path":"x","old_text":"x","new_text":"y"}'),
            ToolCall("c", "execute_command", '{"command":"touch x"}'),
        ),
    )
    p = SequenceProvider([calls, calls, calls, Message("assistant", "计划")])
    c = Conversation(p, ToolExecutor(create_tool_registry(), ToolContext(tmp_path)))
    c.set_mode("plan")
    events = await collect(c)
    assert events[-1].reason == "completed" and len(p.requests) == 4
    results = [e.result for e in events if isinstance(e, ToolFinished)]
    assert len(results) == 9 and all(r.error.code == "mode_restricted" for r in results)
    assert not list(tmp_path.iterdir())
