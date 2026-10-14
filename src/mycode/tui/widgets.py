"""输入、思考与回复展示组件。"""

import json
import unicodedata
from dataclasses import dataclass, field
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
from mycode.models import ToolResult


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
    summary_text: str = ""
    summary_thinking: str = ""
    tools: dict[str, tuple[str, ToolResult | None]] = field(default_factory=dict)
    tool_arguments: dict = field(default_factory=dict)
    tool_history_saved: bool = False
    iterations: dict[int, dict[str, str]] = field(default_factory=dict)
    started: set = field(default_factory=set)


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


class ToolCard(Vertical):
    """单次调用的摘要与持久折叠结果，刷新不重建折叠状态。"""

    def __init__(self, redactor):
        super().__init__(classes="tool-card")
        self.redactor = redactor
        self.heading = Static(markup=False, classes="tool-heading")
        self.parameters = Static(markup=False, classes="tool-parameters")
        self.result_text = Static(markup=False, classes="tool-detail")
        self.details = Collapsible(self.result_text, title="详细结果", collapsed=True)
        self._last_update = None
        self._rendered_result = None

    def compose(self):
        yield self.heading
        yield self.parameters
        yield self.details

    def update_call(self, name, raw, result, started):
        signature = (name, raw, id(result), started)
        if signature == self._last_update:
            return
        self._last_update = signature
        self._rendered_result = result
        label = (
            {"success": "成功", "error": "失败", "cancelled": "取消"}[result.status]
            if result
            else "执行中"
            if started
            else "收到，未执行"
        )
        suffix = " · 结果已截断" if result and result.truncated else ""
        heading = f"工具 {name} · {label}{suffix}"
        self.heading.update(Text(safe_text(self.redactor.redact(heading))))
        try:
            arguments = json.loads(raw) if raw is not None else None
            if not isinstance(arguments, dict):
                raise ValueError()
            lines = []
            for key, value in arguments.items():
                rendered = (
                    value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                )
                rendered = self.redactor.redact(rendered).replace("\r", "\\r").replace("\n", "\\n")
                if len(rendered) > 300:
                    rendered = rendered[:300] + "…（参数内容已省略）"
                lines.append(f"{key}: {rendered}")
            parameters = "参数：\n" + ("\n".join(lines) or "（无参数）")
        except (ValueError, TypeError):
            parameters = "参数：" + (
                "未提供"
                if raw is None
                else "无法解析\n"
                + self.redactor.redact(raw).replace("\r", "\\r").replace("\n", "\\n")[:1000]
            )
        self.parameters.update(Text(safe_text(parameters)))
        if result is None:
            detail = "等待执行结果。"
        else:
            lines = []
            if result.error:
                lines.append(f"错误 [{result.error.code}]：{result.error.message}")
            for key, value in result.data.items():
                rendered = (
                    value
                    if isinstance(value, str)
                    else json.dumps(value, ensure_ascii=False, indent=2)
                )
                lines.append(f"{key}:\n{rendered}")
            if result.truncated:
                lines.append("结果已截断，完整内容超过本次返回限制。")
            detail = "\n\n".join(lines) or "操作已完成，无返回内容。"
        self.result_text.update(Text(safe_text(self.redactor.redact(detail))))
        self.details.display = result is not None
        self.details.title = "详细结果" if result else "详细结果（等待中）"


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
        self.tools_text = Vertical(classes="tool-results")
        self.tool_cards = {}
        self.iteration_blocks = {}
        self.summary = Static(classes="answer-text", markup=False)
        self.summary_thought_text = Static(classes="thinking-text", markup=False)
        self.summary_thought = Collapsible(
            self.summary_thought_text, title="总结思考", collapsed=False
        )

    def compose(self) -> ComposeResult:
        yield Static("你", classes="role", markup=False)
        yield self.user
        yield Static("MyCode", classes="role", markup=False)
        yield self.thinking
        yield self.answer
        yield self.tools_text
        yield self.summary_thought
        yield self.summary
        yield self.status_text

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        partial = self.view.status == "streaming"
        self.user.update(Text(safe_text(self.redactor.redact(self.view.user_text))))
        body = self.view.answer_text
        if self.view.iterations:
            parts = []
            for number, iteration in self.view.iterations.items():
                lines = [f"第 {number} 轮", iteration["text"]]
                parts.append("\n".join(lines))
            body = "\n\n".join(parts)
            if len(self.view.iterations) == 1 and not self.view.tools:
                body = self.view.answer_text
        self.answer.update(
            Text(
                safe_text(
                    self.redactor.redact(
                        body,
                        partial=partial,
                    )
                )
            )
        )
        thought_body = self.view.thinking_text
        if len(self.view.iterations) > 1:
            thought_body = "\n\n".join(
                f"第 {number} 轮\n{iteration['thinking']}"
                for number, iteration in self.view.iterations.items()
                if iteration["thinking"]
            )
        thought = self.redactor.redact(thought_body, partial=partial)
        self.thinking_text.update(Text(safe_text(thought)))
        self.thinking.display = bool(thought)
        self.answer.display = not bool(self.view.tools and self.view.iterations)
        self.tools_text.display = bool(self.view.tools)
        self.call_after_refresh(self._sync_tools)
        summary = self.redactor.redact(self.view.summary_text, partial=partial)
        self.summary.update(Text(safe_text("总结\n" + summary if summary else "")))
        self.summary.display = bool(summary)
        summary_thought = self.redactor.redact(self.view.summary_thinking, partial=partial)
        self.summary_thought_text.update(Text(safe_text(summary_thought)))
        self.summary_thought.display = bool(summary_thought)
        labels = {
            "streaming": "生成中…",
            "completed": "已完成",
            "cancelled": "已取消，本轮未计入上下文",
            "failed": "失败，本轮未计入上下文",
        }
        status = labels[self.view.status]
        if self.view.tool_history_saved and self.view.status in {"failed", "cancelled"}:
            status = (
                "已取消，工具记录已保留"
                if self.view.status == "cancelled"
                else "失败，工具记录已保留"
            )
        if self.view.error_message:
            status += " · " + self.view.error_message
        self.status_text.update(Text(safe_text(self.redactor.redact(status))))

    def _sync_tools(self):
        if not self.tools_text.is_attached or not self.view.tools:
            return
        numbers = list(self.view.iterations) or [0]
        for number in numbers:
            if number not in self.iteration_blocks:
                text = Static(markup=False, classes="answer-text")
                block = Vertical(text, classes="tool-iteration")
                self.iteration_blocks[number] = (block, text)
                self.tools_text.mount(block)
            block, text = self.iteration_blocks[number]
            body = self.view.iterations.get(number, {}).get("text", "")
            title = f"第 {number} 轮\n" if number else ""
            text.update(
                Text(
                    safe_text(
                        self.redactor.redact(title + body, partial=self.view.status == "streaming")
                    )
                )
            )
            for key, (name, result) in self.view.tools.items():
                if (key[1] if isinstance(key, tuple) else 0) != number:
                    continue
                if key not in self.tool_cards:
                    card = ToolCard(self.redactor)
                    self.tool_cards[key] = card
                    block.mount(card)
                self.tool_cards[key].update_call(
                    name,
                    self.view.tool_arguments.get(key),
                    result,
                    key in self.view.started or not self.view.iterations,
                )

    def on_key(self, event: events.Key) -> None:
        if event.key == "space" and self.app.focused is not None:
            if isinstance(self.app.focused.parent, Collapsible):
                section = self.app.focused.parent
                section.collapsed = not section.collapsed
                event.stop()
                event.prevent_default()
