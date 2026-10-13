import asyncio
import json

import httpx
import pytest
from conftest import SSEStream, openai_parts, sse

from mycode.errors import ProviderError
from mycode.models import Message, ResponseCompleted, TextDelta
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
    assert completed == ResponseCompleted(Message("assistant", "你好，世界"))
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
    assert not any(isinstance(event, ResponseCompleted) for event in events)
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
    assert isinstance(events[-1], ResponseCompleted)
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


async def test_tool_messages_and_fragments(make_http, config):
    from conftest import openai_tool_parts

    from mycode.models import ToolResult
    from mycode.tools import create_tool_registry

    calls = [("a", "read_file", '{"path":"中文"}'), ("b", "find_files", '{"pattern":"**/*"}')]
    client, requests = make_http([SSEStream(openai_tool_parts(calls)), SSEStream(openai_parts())])
    provider = OpenAIProvider(config, http_client=client)
    tools = create_tool_registry().definitions()
    events = [
        e async for e in provider.stream([Message("user", "工具")], tools=tools, allow_tools=True)
    ]
    response = events[-1].message
    assert [(c.id, c.name, c.arguments_json) for c in response.tool_calls] == calls
    results = tuple(ToolResult(c.id, c.name, "success", {}) for c in response.tool_calls)
    _ = [
        e
        async for e in provider.stream(
            [response, Message("tool", "", tool_results=results)], tools=tools
        )
    ]
    first, second = [json.loads(r.content) for r in requests]
    assert len(first["tools"]) == 6 and first["tool_choice"] == "auto"
    assert second["tool_choice"] == "none"
    assert [m["tool_call_id"] for m in second["messages"][1:]] == ["a", "b"]
    await provider.aclose()


@pytest.mark.parametrize(
    "finish,allow", [("length", True), (None, True), ("stop", True), ("tool_calls", False)]
)
async def test_tool_invalid_stream(make_http, config, finish, allow):
    from conftest import openai_tool_parts

    client, _ = make_http([SSEStream(openai_tool_parts([("a", "read_file", "{}")], finish=finish))])
    provider = OpenAIProvider(config, http_client=client)
    with pytest.raises(ProviderError):
        _ = [e async for e in provider.stream([], allow_tools=allow)]
    await provider.aclose()


@pytest.mark.parametrize("mode", ["missing_index", "extra_after_finish", "duplicate_finish"])
async def test_tool_protocol_order(make_http, config, mode):
    from conftest import openai_tool_parts

    parts = openai_tool_parts([("a", "read_file", "{}")])
    if mode == "missing_index":
        value = json.loads(parts[1].decode().split("data: ")[1])
        value["choices"][0]["delta"]["tool_calls"][0]["index"] = 2
        parts[1] = sse(value)
    elif mode == "extra_after_finish":
        parts.insert(-1, openai_parts(["late"])[1])
    else:
        parts.insert(-1, parts[-2])
    client, _ = make_http([SSEStream(parts)])
    p = OpenAIProvider(config, http_client=client)
    with pytest.raises(ProviderError):
        _ = [e async for e in p.stream([], allow_tools=True)]
    await p.aclose()
