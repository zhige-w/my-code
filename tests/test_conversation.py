import asyncio
from dataclasses import replace

import pytest
from conftest import FakeProvider, SSEStream, anthropic_parts, openai_parts

from mycode.conversation import Conversation
from mycode.errors import ProviderError
from mycode.models import Message, NativeContent, TextDelta, TurnCompleted
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
            yield TurnCompleted(Message("assistant", "answer", native))

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
