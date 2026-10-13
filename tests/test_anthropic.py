import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from conftest import SSEStream, anthropic_parts, sse

from mycode.errors import ProviderError
from mycode.models import Message, TextDelta, ThinkingDelta, TurnCompleted
from mycode.providers.anthropic import AnthropicProvider


def claude_config(config, **changes):
    return replace(config, protocol="anthropic", base_url="https://example.test/gateway", **changes)


async def test_streaming_real_sdk(make_http, config):
    stream = SSEStream(anthropic_parts(initial="开头"), gate_after=3)
    client, requests = make_http([stream])
    provider = AnthropicProvider(claude_config(config), http_client=client)
    events = provider.stream([Message("user", "你好")])
    assert await anext(events) == TextDelta("开头")
    pending = asyncio.create_task(anext(events))
    await asyncio.wait_for(stream.waiting.wait(), 2)
    assert not pending.done()
    stream.release.set()
    assert await pending == TextDelta("你好")
    rest = [event async for event in events]
    assert rest[0] == TextDelta("，世界")
    assert rest[-1].message.text == "开头你好，世界"
    assert stream.closed
    request = requests[0]
    assert str(request.url) == "https://example.test/gateway/v1/messages"
    assert request.headers["x-api-key"] == "unique-test-secret"
    assert "anthropic-version" in request.headers
    body = json.loads(request.content)
    assert body == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 8192,
        "stream": True,
    }
    await provider.aclose()
    assert client.is_closed


async def test_thinking_streaming_and_native_history(make_http, config):
    stream = SSEStream(anthropic_parts(thinking=True), gate_after=2)
    client, requests = make_http([stream, SSEStream(anthropic_parts(thinking=True))])
    provider = AnthropicProvider(claude_config(config, thinking=True), http_client=client)
    events = provider.stream([Message("user", "问题")])
    assert await anext(events) == ThinkingDelta("先")
    pending = asyncio.create_task(anext(events))
    await asyncio.wait_for(stream.waiting.wait(), 2)
    assert not pending.done()
    stream.release.set()
    assert await pending == ThinkingDelta("分析")
    rest = [event async for event in events]
    assert [event for event in rest if isinstance(event, TextDelta)] == [
        TextDelta("你好"),
        TextDelta("，世界"),
    ]
    message = rest[-1].message
    blocks = message.native_content.blocks
    assert blocks[0] == {
        "type": "thinking",
        "thinking": "先分析",
        "signature": "signature-complete",
    }
    assert blocks[1] == {"type": "redacted_thinking", "data": "encrypted-hidden"}
    assert message.text == "你好，世界"
    assert "signature-complete" not in repr(rest[:-1])
    _ = [
        e
        async for e in provider.stream([Message("user", "问题"), message, Message("user", "追问")])
    ]
    body = json.loads(requests[1].content)
    assert body["messages"][1]["content"] == blocks
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert body["max_tokens"] == 8192
    await provider.aclose()


@pytest.mark.parametrize("reason", [None, "max_tokens", "tool_use", "pause_turn", "unknown"])
async def test_abnormal_reason(make_http, config, reason):
    stream = SSEStream(anthropic_parts(reason=reason))
    client, requests = make_http([stream])
    provider = AnthropicProvider(claude_config(config), http_client=client)
    events = []
    with pytest.raises(ProviderError):
        async for event in provider.stream([Message("user", "hello")]):
            events.append(event)
    assert not any(isinstance(e, TurnCompleted) for e in events)
    assert stream.closed and len(requests) == 1
    await provider.aclose()


@pytest.mark.parametrize("mode", ["no_stop", "unclosed", "stream_error", "tool", "network"])
async def test_broken_stream(make_http, config, mode):
    parts = anthropic_parts()
    error = None
    if mode == "no_stop":
        parts = parts[:-1]
    elif mode == "unclosed":
        parts = [p for p in parts if b'"type": "content_block_stop"' not in p]
    elif mode == "stream_error":
        parts = parts[:4] + [
            sse(
                {
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": "unique-test-secret"},
                },
                "error",
            )
        ]
    elif mode == "tool":
        parts = parts[:1] + [
            sse(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "tool_use", "id": "t", "name": "x", "input": {}},
                },
                "content_block_start",
            )
        ]
    else:
        parts = parts[:4]
        error = httpx.ReadError("unique-test-secret")
    stream = SSEStream(parts, error=error)
    client, _ = make_http([stream])
    provider = AnthropicProvider(claude_config(config), http_client=client)
    with pytest.raises(ProviderError) as caught:
        _ = [e async for e in provider.stream([Message("user", "hello")])]
    assert "unique-test-secret" not in str(caught.value)
    assert stream.closed
    await provider.aclose()


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
async def test_http_error_and_recovery(make_http, config, status):
    client, requests = make_http(
        [
            (
                status,
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "thinking.type.enabled is not supported unique-test-secret",
                    },
                },
            ),
            SSEStream(anthropic_parts()),
        ]
    )
    provider = AnthropicProvider(claude_config(config, thinking=True), http_client=client)
    with pytest.raises(ProviderError) as caught:
        _ = [e async for e in provider.stream([Message("user", "hello")])]
    assert "unique-test-secret" not in str(caught.value)
    if status == 400:
        assert "thinking" in str(caught.value)
    assert len(requests) == 1
    events = [e async for e in provider.stream([Message("user", "again")])]
    assert isinstance(events[-1], TurnCompleted)
    await provider.aclose()


@pytest.mark.parametrize("error", [httpx.ConnectError("secret"), httpx.ReadTimeout("secret")])
async def test_network_error(make_http, config, error):
    client, requests = make_http([error])
    provider = AnthropicProvider(claude_config(config), http_client=client)
    with pytest.raises(ProviderError) as caught:
        _ = [e async for e in provider.stream([Message("user", "hello")])]
    assert caught.value.kind in {"connection", "timeout"}
    assert len(requests) == 1
    await provider.aclose()


async def test_cancel_and_close(make_http, config):
    stream = SSEStream(anthropic_parts(), gate_after=4)
    client, _ = make_http([stream])
    provider = AnthropicProvider(claude_config(config), http_client=client)
    events = provider.stream([Message("user", "hello")])
    assert await anext(events) == TextDelta("你好")
    pending = asyncio.create_task(anext(events))
    await asyncio.wait_for(stream.waiting.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert stream.closed
    await events.aclose()
    await provider.aclose()
