import pytest

from mycode.agent.history import HistoryStore
from mycode.models import Message, NativeContent, ToolCall, ToolResult


def test_reply_and_batch_snapshots():
    store = HistoryStore()
    reply = Message(
        "assistant",
        "",
        NativeContent("anthropic", [{"type": "tool_use"}]),
        (ToolCall("a", "read_file", "{}"),),
    )
    result = ToolResult("a", "read_file", "success", {"content": "中文"})
    store.commit_batch(Message("user", "任务"), reply, (result,))
    store.commit_reply(None, Message("assistant", "完成"))
    snapshot = store.snapshot()
    snapshot[1].native_content.blocks[0].clear()
    snapshot[2].tool_results[0].data.clear()
    assert len(store.snapshot()) == 4
    assert store.snapshot()[1].native_content.blocks[0]
    assert store.snapshot()[2].tool_results[0].data


def test_invalid_batch_atomic():
    store = HistoryStore()
    reply = Message("assistant", "", tool_calls=(ToolCall("a", "read_file", "{}"),))
    with pytest.raises(ValueError):
        store.commit_batch(Message("user", "x"), reply, ())
    assert store.snapshot() == ()
