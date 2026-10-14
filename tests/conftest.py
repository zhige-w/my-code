"""可控的 SSE 字节流与协议测试客户端。"""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

import httpx
import pytest

from mycode.config import ProviderConfig
from mycode.models import Message, ProviderEvent, ResponseCompleted, TextDelta


def sse(data, event=None):
    prefix = f"event: {event}\n" if event else ""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return (prefix + f"data: {payload}\n\n").encode()


class SSEStream(httpx.AsyncByteStream):
    def __init__(self, parts, *, gate_after=None, error=None):
        self.parts = parts
        self.gate_after = gate_after
        self.error = error
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        for index, part in enumerate(self.parts):
            if index == self.gate_after:
                self.waiting.set()
                await self.release.wait()
            # 刻意拆开 UTF-8 字符和 SSE 行，覆盖真实网络分片。
            for start in range(0, len(part), 2):
                yield part[start : start + 2]
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def openai_parts(texts=("你好", "，世界"), finish="stop"):
    def chunk(delta, reason=None):
        return sse(
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
            }
        )

    parts = [chunk({"role": "assistant", "content": ""})]
    parts.extend(chunk({"content": text}) for text in texts)
    if finish is not None:
        parts.append(chunk({}, finish))
    parts.append(sse("[DONE]"))
    return parts


def anthropic_parts(*, thinking=False, reason="end_turn", initial="", stop=True):
    def event(kind, **fields):
        return sse({"type": kind, **fields}, kind)

    parts = [
        event(
            "message_start",
            message={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "test-model",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        )
    ]
    index = 0
    if thinking:
        parts.extend(
            [
                event(
                    "content_block_start",
                    index=0,
                    content_block={"type": "thinking", "thinking": "先", "signature": ""},
                ),
                event(
                    "content_block_delta",
                    index=0,
                    delta={"type": "thinking_delta", "thinking": "分析"},
                ),
                event(
                    "content_block_delta",
                    index=0,
                    delta={"type": "signature_delta", "signature": "signature-"},
                ),
                event(
                    "content_block_delta",
                    index=0,
                    delta={"type": "signature_delta", "signature": "complete"},
                ),
                event("content_block_stop", index=0),
                event(
                    "content_block_start",
                    index=1,
                    content_block={"type": "redacted_thinking", "data": "encrypted-hidden"},
                ),
                event("content_block_stop", index=1),
            ]
        )
        index = 2
    parts.extend(
        [
            event("ping"),
            event(
                "content_block_start", index=index, content_block={"type": "text", "text": initial}
            ),
            event("content_block_delta", index=index, delta={"type": "text_delta", "text": "你好"}),
            event(
                "content_block_delta", index=index, delta={"type": "text_delta", "text": "，世界"}
            ),
            event("content_block_stop", index=index),
            event(
                "message_delta",
                delta={"stop_reason": reason, "stop_sequence": None},
                usage={"output_tokens": 10},
            ),
        ]
    )
    if stop:
        parts.append(event("message_stop"))
    return parts


@pytest.fixture
def make_http():
    clients = []

    def make(responses):
        requests = []
        queue = iter(responses)

        async def handle(request):
            requests.append(request)
            response = next(queue)
            if isinstance(response, Exception):
                raise response
            if isinstance(response, SSEStream):
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=response
                )
            status, body = response
            return httpx.Response(status, json=body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        clients.append(client)
        return client, requests

    return make


@pytest.fixture
def config():
    return ProviderConfig(
        "test", "openai", "test-model", "https://example.test/v1", "unique-test-secret"
    )


class FakeProvider:
    def __init__(self, *, events=None, error=None, wait=False):
        self.events = events if events is not None else [TextDelta("你好")]
        self.error = error
        self.wait = wait
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.requests = []
        self.stream_closed = False
        self.closed = False

    async def stream(
        self, messages: Sequence[Message], *, tools=(), allow_tools=False, system_prompt=""
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append(tuple(messages))
        try:
            text = ""
            for event in self.events:
                if isinstance(event, TextDelta):
                    text += event.text
                yield event
            self.started.set()
            if self.wait:
                await self.release.wait()
            if self.error:
                raise self.error
            yield ResponseCompleted(Message("assistant", text))
        finally:
            self.stream_closed = True

    async def aclose(self):
        self.closed = True


def openai_tool_parts(calls, *, finish="tool_calls"):
    def chunk(delta, reason=None):
        return sse(
            {
                "id": "tools",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
            }
        )

    parts = [chunk({"content": "准备执行。"})]
    for index, (identifier, name, raw) in enumerate(calls):
        parts.append(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": identifier,
                            "type": "function",
                            "function": {"name": name[:3], "arguments": raw[:2]},
                        }
                    ]
                }
            )
        )
    for index, (identifier, name, raw) in reversed(list(enumerate(calls))):
        parts.append(
            chunk(
                {
                    "tool_calls": [
                        {"index": index, "function": {"name": name[3:], "arguments": raw[2:]}}
                    ]
                }
            )
        )
    if finish:
        parts.append(chunk({}, finish))
    parts.append(sse("[DONE]"))
    return parts


def anthropic_tool_parts(calls, *, reason="tool_use", stop=True, thinking=False):
    def event(kind, **fields):
        return sse({"type": kind, **fields}, kind)

    base = anthropic_parts(thinking=thinking)
    parts = base[:8] if thinking else base[:1]
    for index, (identifier, name, raw) in enumerate(calls, 2 if thinking else 0):
        parts.append(
            event(
                "content_block_start",
                index=index,
                content_block={"type": "tool_use", "id": identifier, "name": name, "input": {}},
            )
        )
        for fragment in (raw[:2], raw[2:]):
            parts.append(
                event(
                    "content_block_delta",
                    index=index,
                    delta={"type": "input_json_delta", "partial_json": fragment},
                )
            )
        parts.append(event("content_block_stop", index=index))
    parts.append(
        event(
            "message_delta",
            delta={"stop_reason": reason, "stop_sequence": None},
            usage={"output_tokens": 10},
        )
    )
    if stop:
        parts.append(event("message_stop"))
    return parts
