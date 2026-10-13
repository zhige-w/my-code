"""输入、思考与回复展示组件。"""

import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from rich.text import Text
from textual import events
from textual._ansi_sequences import ANSI_SEQUENCES_KEYS
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message as UIMessage
from textual.widgets import Collapsible, Static, TextArea

from mycode.errors import SecretRedactor


class _TerminalKey(Enum):
    ALT_ENTER = "alt+enter"


# Textual 8 的旧式 Meta 解析会丢失 Enter 的 Alt 修饰符。
# 只补充未注册的两种换行序列；现代终端的扩展键协议保持原有解析。
for _sequence in ("\x1b\r", "\x1b\n"):
    ANSI_SEQUENCES_KEYS.setdefault(_sequence, (_TerminalKey.ALT_ENTER,))


def safe_text(text: str) -> str:
    # 不把模型内容中的控制序列直接发送给终端。
    return "".join(
        char if char in "\n\t" or unicodedata.category(char) != "Cc" else "�" for char in text
    )


@dataclass
class TurnView:
    user_text: str
    answer_text: str = ""
    thinking_text: str = ""
    status: Literal["streaming", "completed", "cancelled", "failed"] = "streaming"
    error_message: str | None = None


class ChatInput(TextArea):
    class Submitted(UIMessage):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        if event.key in {"enter", "alt+enter"}:
            event.stop()
            event.prevent_default()
            if event.key == "alt+enter":
                self.insert("\n")
            elif self.text.strip():
                self.post_message(self.Submitted(self.text))
            return
        await super()._on_key(event)


class TurnWidget(Vertical):
    def __init__(self, view: TurnView, redactor: SecretRedactor | None = None) -> None:
        super().__init__(classes="turn")
        self.view = view
        self.redactor = redactor or SecretRedactor()
        self.user = Static(classes="user-text", markup=False)
        self.answer = Static(classes="answer-text", markup=False)
        self.thinking_text = Static(classes="thinking-text", markup=False)
        self.thinking = Collapsible(self.thinking_text, title="思考过程", collapsed=False)
        self.status_text = Static(classes="turn-status", markup=False)

    def compose(self) -> ComposeResult:
        yield Static("你", classes="role", markup=False)
        yield self.user
        yield Static("MyCode", classes="role", markup=False)
        yield self.thinking
        yield self.answer
        yield self.status_text

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        partial = self.view.status == "streaming"
        self.user.update(Text(safe_text(self.redactor.redact(self.view.user_text))))
        self.answer.update(
            Text(
                safe_text(
                    self.redactor.redact(
                        self.view.answer_text,
                        partial=partial,
                    )
                )
            )
        )
        thought = self.redactor.redact(self.view.thinking_text, partial=partial)
        self.thinking_text.update(Text(safe_text(thought)))
        self.thinking.display = bool(thought)
        labels = {
            "streaming": "生成中…",
            "completed": "已完成",
            "cancelled": "已取消，本轮未计入上下文",
            "failed": "失败，本轮未计入上下文",
        }
        status = labels[self.view.status]
        if self.view.error_message:
            status += " · " + self.view.error_message
        self.status_text.update(Text(safe_text(self.redactor.redact(status))))

    def on_key(self, event: events.Key) -> None:
        if event.key == "space" and self.app.focused is not None:
            if self.app.focused.parent is self.thinking:
                self.thinking.collapsed = not self.thinking.collapsed
                event.stop()
                event.prevent_default()
