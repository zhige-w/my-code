import asyncio

import pytest

from mycode.agent.events import RunFinished, ToolStarted, UsageSummary, UsageUpdated
from mycode.agent.history import HistoryStore
from mycode.agent.loop import AgentLoop
from mycode.agent.options import AgentOptions
from mycode.errors import ProviderError
from mycode.models import Message, ProviderUsage, ResponseCompleted, TextDelta, TokenUsage, ToolCall
from mycode.tools import create_tool_registry
from mycode.tools.base import ToolContext
from mycode.tools.executor import ToolExecutor
from mycode.tools.scheduler import ToolScheduler


def test_contracts_defaults_and_events():
    assert AgentOptions() == AgentOptions(20, 3, 4)
    assert TokenUsage().input_tokens is None
    assert RunFinished("r", 0, "cancelled", None, UsageSummary()).iteration == 0


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "4"])
def test_contracts_invalid(value):
    with pytest.raises(ValueError):
        AgentOptions(max_iterations=value)
    if value != 0 or type(value) is not int:
        with pytest.raises(ValueError):
            TokenUsage(value)


class SequenceProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed = False
        self.waiting = asyncio.Event()

    async def stream(self, messages, **options):
        self.requests.append((tuple(messages), options))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if response == "wait":
            self.waiting.set()
            await asyncio.Event().wait()
        if isinstance(response, tuple):
            message, usage = response
            for value in usage:
                yield ProviderUsage(value)
        else:
            message = response
        if message.text:
            yield TextDelta(message.text)
        yield ResponseCompleted(message)

    async def aclose(self):
        self.closed = True


def call(name="read_file", raw='{"path":"x"}', id="a"):
    return Message("assistant", "", tool_calls=(ToolCall(id, name, raw),))


def agent(tmp_path, responses, options=None):
    provider = SequenceProvider(responses)
    executor = ToolExecutor(create_tool_registry(), ToolContext(tmp_path))
    loop = AgentLoop(provider, ToolScheduler(executor), options or AgentOptions())
    return loop, provider, HistoryStore()


async def run_agent(loop, history, cancel=None):
    events = [
        e
        async for e in loop.run(
            "任务", history=history, mode="execute", cancel=cancel or asyncio.Event()
        )
    ]
    assert isinstance(events[-1], RunFinished)
    assert sum(isinstance(e, RunFinished) for e in events) == 1
    return events


async def test_react_multiple_batches(tmp_path):
    loop, provider, history = agent(
        tmp_path,
        [call("write_file", '{"path":"x","content":"中文"}'), call(), Message("assistant", "完成")],
    )
    events = await run_agent(loop, history)
    assert events[-1].reason == "completed" and len(provider.requests) == 3
    assert "中文" in history.snapshot()[4].tool_results[0].data["content"]
    assert sum(m.role == "user" for m in history.snapshot()) == 1
    assert all(options["allow_tools"] for _, options in provider.requests)


@pytest.mark.parametrize("terminal", [False, True])
async def test_stopping_iteration_twenty(tmp_path, terminal):
    responses = [call("missing", "{}")] * 19
    responses.append(Message("assistant", "完成") if terminal else call("missing", "{}"))
    loop, provider, history = agent(tmp_path, responses, AgentOptions(unknown_tool_batch_limit=30))
    events = await run_agent(loop, history)
    assert len(provider.requests) == 20
    assert events[-1].reason == ("completed" if terminal else "iteration_limit")
    assert len(history.snapshot()) == (40 if terminal else 39)


async def test_stopping_unknown_reset(tmp_path):
    sequence = [
        call("missing", "{}"),
        call("missing", "{}"),
        call(),
        call("missing", "{}"),
        call("missing", "{}"),
        call("missing", "{}"),
    ]
    loop, provider, history = agent(tmp_path, sequence)
    events = await run_agent(loop, history)
    assert events[-1].reason == "unknown_tool_limit" and len(provider.requests) == 6
    assert len(history.snapshot()) == 13


async def test_stopping_invalid_and_stream_error(tmp_path):
    for response, reason in [
        (call("read_file", "{"), "invalid_response"),
        (ProviderError("connection", "错误"), "stream_error"),
    ]:
        loop, provider, history = agent(tmp_path, [response])
        events = await run_agent(loop, history)
        assert events[-1].reason == reason and history.snapshot() == ()
        assert len(provider.requests) == 1


async def test_usage_progress_snapshot_and_missing(tmp_path):
    loop, _, history = agent(
        tmp_path, [(call(), [TokenUsage(10, 0), TokenUsage(10, 5)]), Message("assistant", "完成")]
    )
    events = await run_agent(loop, history)
    assert events[-1].usage.known_input_tokens == 10
    assert events[-1].usage.known_output_tokens == 5
    assert not events[-1].usage.input_complete
    assert events[-1].usage.requests_started == 2
    assert any(isinstance(e, UsageUpdated) for e in events)


async def test_lifecycle_cancel_and_recover_history(tmp_path):
    loop, provider, history = agent(
        tmp_path, [call("write_file", '{"path":"x","content":"x"}'), "wait"]
    )
    cancel = asyncio.Event()
    task = asyncio.create_task(run_agent(loop, history, cancel))
    await asyncio.wait_for(provider.waiting.wait(), 3)
    cancel.set()
    events = await task
    assert events[-1].reason == "cancelled" and len(history.snapshot()) == 3
    assert (tmp_path / "x").read_text() == "x"


async def test_lifecycle_consumer_closes_active_batch(tmp_path):
    loop, _, history = agent(tmp_path, [call("execute_command", '{"command":"sleep 20"}')])
    events = loop.run("任务", history=history, mode="execute", cancel=asyncio.Event())
    while not isinstance(await anext(events), ToolStarted):
        pass
    await events.aclose()
    assert history.snapshot()[2].tool_results[0].status == "cancelled"


async def test_stopping_internal_failure_and_before_request(tmp_path):
    loop, provider, history = agent(tmp_path, [RuntimeError("内部错误")])
    assert (await run_agent(loop, history))[-1].reason == "internal_error"
    assert len(provider.requests) == 1
    loop, provider, history = agent(tmp_path, [])
    cancel = asyncio.Event()
    cancel.set()
    assert (await run_agent(loop, history, cancel))[-1].iteration == 0
    assert not provider.requests


async def test_stopping_unknown_same_batch_and_restricted(tmp_path):
    missing = Message(
        "assistant", "", tool_calls=tuple(ToolCall(str(n), "missing", "{}") for n in range(4))
    )
    loop, provider, history = agent(tmp_path, [missing, Message("assistant", "完成")])
    assert (await run_agent(loop, history))[-1].reason == "completed"
    assert len(provider.requests) == 2


async def test_react_plain_and_failure_then_next_batch(tmp_path):
    loop, provider, history = agent(tmp_path, [Message("assistant", "纯文本")])
    assert (await run_agent(loop, history))[-1].reason == "completed"
    assert len(provider.requests) == 1
    loop, provider, history = agent(
        tmp_path,
        [call(), call("write_file", '{"path":"x","content":"调整"}'), Message("assistant", "完成")],
    )
    await run_agent(loop, history)
    assert history.snapshot()[2].tool_results[0].error.code == "not_found"
    assert (tmp_path / "x").read_text() == "调整"


@pytest.mark.parametrize("error", [False, True])
async def test_usage_progress_failed_and_reset(tmp_path, error):
    class Partial(SequenceProvider):
        async def stream(self, messages, **options):
            self.requests.append((messages, options))
            yield ProviderUsage(TokenUsage(12, None))
            if error:
                raise ProviderError("connection", "失败")
            self.waiting.set()
            await asyncio.Event().wait()

    loop, _, history = agent(tmp_path, [])
    provider = Partial([])
    loop.provider = provider
    cancel = asyncio.Event()
    task = asyncio.create_task(run_agent(loop, history, cancel))
    if not error:
        await provider.waiting.wait()
        cancel.set()
    result = (await task)[-1]
    assert result.usage.known_input_tokens == 12 and not result.usage.output_complete
    assert result.usage.requests_started == 1
    loop.provider = SequenceProvider([Message("assistant", "新请求")])
    result = (await run_agent(loop, history))[-1]
    assert result.usage.known_input_tokens == 0 and result.usage.requests_with_usage == 0


async def test_lifecycle_cancel_between_batches_and_secret(tmp_path):
    from mycode.errors import SecretRedactor

    loop, provider, history = agent(
        tmp_path, [call("write_file", '{"path":"x","content":"已完成"}')]
    )
    cancel = asyncio.Event()
    stream = loop.run("任务", history=history, mode="execute", cancel=cancel)
    from mycode.agent.events import ToolFinished

    async for event in stream:
        if isinstance(event, ToolFinished):
            cancel.set()
        if isinstance(event, RunFinished):
            assert event.reason == "cancelled"
    assert len(provider.requests) == 1 and len(history.snapshot()) == 3
    loop, _, history = agent(tmp_path, [ProviderError("connection", "secret-test")])
    loop.scheduler.executor.redactor = SecretRedactor(["secret-test"])
    assert "secret-test" not in (await run_agent(loop, history))[-1].error_message
