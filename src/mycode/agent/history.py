"""完整回复与工具结果的成组提交。"""

from copy import deepcopy

from mycode.models import Message


class HistoryStore:
    def __init__(self):
        self._messages = []

    def snapshot(self):
        return deepcopy(tuple(self._messages))

    def commit_reply(self, pending_user, reply):
        if reply.role != "assistant" or reply.tool_calls:
            raise ValueError("普通回复必须完整且没有工具调用。")
        self._commit(pending_user, (reply,))

    def commit_batch(self, pending_user, reply, results):
        ids = [c.id for c in reply.tool_calls]
        if (
            reply.role != "assistant"
            or not ids
            or any(not i.strip() for i in ids)
            or len(set(ids)) != len(ids)
            or ids != [r.call_id for r in results]
            or [c.name for c in reply.tool_calls] != [r.name for r in results]
        ):
            raise ValueError("工具调用与结果必须完整且按序配对。")
        self._commit(pending_user, (reply, Message("tool", "", tool_results=tuple(results))))

    def _commit(self, user, messages):
        if user is not None and user.role != "user":
            raise ValueError("新增请求必须为用户消息。")
        self._messages.extend(deepcopy(((user,) if user is not None else ()) + messages))

    def clear(self):
        self._messages.clear()
