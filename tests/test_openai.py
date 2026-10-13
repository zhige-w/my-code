import asyncio
import json

import httpx
import pytest
from conftest import SSEStream, openai_parts, sse

from mycode.errors import ProviderError
from mycode.models import Message, TextDelta, TurnCompleted
from mycode.providers.openai import OpenAIProvider


async def test_transport_fixture(make_http):
    stream = SSEStream([b"first", b"second"], gate_after=1)
    client, requests = make_http([stream])
    response = await client.send(client.build_request("GET", "https://test.invalid"), stream=True)
    reader = response.aiter_bytes()
    assert await anext(reader) == b"fi"
    assert await anext(reader) == b"rs"
    assert await anext(reader) == b"t"
    pending = asyncio.create_task(anext(reader))
    await asyncio.wait_for(stream.waiting.wait(), 2)
    assert not pending.done()
    stream.release.set()
    assert await pending == b"se"
    await response.aclose()
    await client.aclose()
    assert stream.closed and len(requests) == 1


async def test_streaming_real_sdk(make_http, config):
    stream = SSEStream(openai_parts(), gate_after=2)
    client, requests = make_http([stream])
    provider = OpenAIProvider(config, http_client=client)
    events = provider.stream([Message("user", "你好")])
    assert await anext(events) == TextDelta("你好")
    pending = asyncio.create_task(anext(events))
    await asyncio.wait_for(stream.waiting.wait(), 2)
    assert not pending.done()
    stream.release.set()
    assert await pending == TextDelta("，世界")
    completed = await anext(events)
    assert completed == TurnCompleted(Message("assistant", "你好，世界"))
    assert stream.closed
    assert [event async for event in events] == []
    request = requests[0]
    assert str(request.url) == "https://example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer unique-test-secret"
    body = json.loads(request.content)
    assert body == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "你好"}],
        "stream": True,
    }
    await provider.aclose()
    assert client.is_closed


@pytest.mark.parametrize(
    "reason", [None, "length", "content_filter", "tool_calls", "function_call", "unknown"]
)
async def test_abnormal_finish(make_http, config, reason):
    stream = SSEStream(openai_parts(finish=reason))
    client, requests = make_http([stream])
    provider = OpenAIProvider(config, http_client=client)
    events = []
    with pytest.raises(ProviderError):
        async for event in provider.stream([Message("user", "hello")]):
            events.append(event)
    assert not any(isinstance(event, TurnCompleted) for event in events)
    assert stream.closed and len(requests) == 1
    await provider.aclose()


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
async def test_http_error_no_retry_and_recovery(make_http, config, status):
    client, requests = make_http(
        [
            (status, {"error": {"message": "unique-test-secret", "type": "invalid_request_error"}}),
            SSEStream(openai_parts()),
        ]
    )
    provider = OpenAIProvider(config, http_client=client)
    with pytest.raises(ProviderError) as error:
        _ = [event async for event in provider.stream([Message("user", "hello")])]
    assert "unique-test-secret" not in str(error.value)
    assert len(requests) == 1
    events = [event async for event in provider.stream([Message("user", "again")])]
    assert isinstance(events[-1], TurnCompleted)
    await provider.aclose()


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("unique-test-secret"), httpx.ReadTimeout("unique-test-secret")]
)
async def test_connection_failure(make_http, config, error):
    client, requests = make_http([error])
    provider = OpenAIProvider(config, http_client=client)
    with pytest.raises(ProviderError) as caught:
        _ = [event async for event in provider.stream([Message("user", "hello")])]
    assert caught.value.kind in {"connection", "timeout"}
    assert "unique-test-secret" not in str(caught.value)
    assert len(requests) == 1
    await provider.aclose()


async def test_cancel_and_close(make_http, config):
    stream = SSEStream(openai_parts(), gate_after=2)
    client, _ = make_http([stream])
    provider = OpenAIProvider(config, http_client=client)
    events = provider.stream([Message("user", "hello")])
    assert isinstance(await anext(events), TextDelta)
    pending = asyncio.create_task(anext(events))
    await asyncio.wait_for(stream.waiting.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert stream.closed
    await events.aclose()
    await provider.aclose()


async def test_stream_error_and_early_consumer_exit(make_http, config):
    stream = SSEStream([sse({"error": {"message": "unique-test-secret"}})])
    other = SSEStream(openai_parts())
    client, _ = make_http([stream, other])
    provider = OpenAIProvider(config, http_client=client)
    with pytest.raises(ProviderError):
        _ = [event async for event in provider.stream([Message("user", "hello")])]
    assert stream.closed
    events = provider.stream([Message("user", "again")])
    await anext(events)
    await events.aclose()
    assert other.closed
    await provider.aclose()
