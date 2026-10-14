"""全屏对话界面和请求生命周期。"""

import asyncio
from contextlib import aclosing, suppress

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static
from textual.worker import Worker, WorkerCancelled, WorkerFailed

from mycode.agent.events import (
    ProgressUpdated,
    RunFinished,
    TextDelta,
    ThinkingDelta,
    ToolCallReceived,
    ToolFinished,
    ToolStarted,
    UsageUpdated,
)
from mycode.conversation import Conversation
from mycode.errors import ProviderError, SecretRedactor
from mycode.tui.widgets import ChatInput, TurnView, TurnWidget, safe_text


class ConversationScroll(VerticalScroll, can_focus=True):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.follow = True

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        # 输出增长时布局可能暂时未到底；只有向上移动才暂停跟随。
        if new_value < old_value:
            self.follow = self.is_vertical_scroll_end
        elif self.is_vertical_scroll_end:
            self.follow = True

    def follow_output(self) -> None:
        if self.follow:
            self.call_after_refresh(self._scroll_if_following)

    def _scroll_if_following(self) -> None:
        if self.follow:
            self.scroll_end(animate=False, x_axis=False)


class MyCodeApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "MyCode"
    BINDINGS = [
        Binding("ctrl+c", "cancel_or_exit", "取消 / 退出", priority=True),
        Binding("ctrl+d", "quit", "退出", priority=True),
    ]

    def __init__(
        self,
        conversation: Conversation,
        provider_name: str,
        model: str,
        *,
        redactor: SecretRedactor | None = None,
    ) -> None:
        super().__init__()
        self.conversation = conversation
        self.provider_name = provider_name
        self.model = model
        self.redactor = redactor or SecretRedactor()
        self.generating = False
        self.cancelling = False
        self.exiting = False
        self._generation: Worker | None = None
        self.turns: list[TurnWidget] = []
        self._progress = None
        self._usage = None

    def compose(self) -> ComposeResult:
        yield Static(id="status", markup=False)
        yield ConversationScroll(id="conversation")
        yield ChatInput(id="input", placeholder="输入问题…", soft_wrap=True)
        yield Static(
            "Enter 发送 · Alt+Enter 换行 · Tab 切换焦点 · Ctrl+C 取消/退出 · Ctrl+D 退出",
            id="hint",
            markup=False,
        )

    def on_mount(self) -> None:
        self._update_status()
        self.query_one(ChatInput).focus()

    def _update_status(self) -> None:
        state = "取消处理中" if self.cancelling else "生成中" if self.generating else "空闲"
        if self.generating and not self.cancelling and self._progress:
            phase = {"model": "模型响应中", "tools": "工具执行中"}
            state += " · " + phase.get(self._progress.phase, self._progress.phase)
        mode = "规划" if self.conversation.mode == "plan" else "执行"
        progress = ""
        if self._progress:
            progress = f" · {self._progress.iteration}/{self._progress.max_iterations}"
        usage = ""
        if self._usage:
            u = self._usage
            if not u.requests_with_usage:
                usage = " · Token 不可用"
            else:
                incomplete = "（不完整）" if not (u.input_complete and u.output_complete) else ""
                usage = f" · Token {u.known_input_tokens}/{u.known_output_tokens}{incomplete}"
        text = f"MyCode · {self.provider_name} · {self.model} · {mode} · {state}{progress}{usage}"
        self.query_one("#status", Static).update(Text(safe_text(self.redactor.redact(text))))

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        command = event.text.strip()
        if self.generating or self.cancelling or self.exiting:
            if command in {"/plan", "/do"}:
                self.query_one("#hint", Static).update("当前任务未结束，不能切换模式。")
            return
        if not command:
            return
        if command == "/plan":
            self.conversation.set_mode("plan")
            self.query_one(ChatInput).clear()
            self.query_one("#hint", Static).update(
                "已进入只读规划模式，请输入任务；计划完成后 /do 执行。"
            )
            self._update_status()
            return
        if command == "/do":
            self.conversation.set_mode("execute")
            if self.conversation.latest_plan is None:
                self.query_one("#hint", Static).update(
                    "没有可执行计划，请先 /plan 规划或直接提交任务。"
                )
                self._update_status()
                return
        self._progress = self._usage = None
        # 在 worker 启动前锁定，防止连续按键创建两个请求。
        self.generating = True
        self._update_status()
        self.query_one(ChatInput).clear()
        # 用户主动发起新一轮时回到最新内容；本轮上滚仍暂停自动跟随。
        self.query_one(ConversationScroll).follow = True
        self._generation = self.run_worker(self._generate(event.text), exit_on_error=False)

    async def _generate(self, user_text: str) -> None:
        turn = TurnWidget(TurnView(user_text), self.redactor)
        scroll = self.query_one(ConversationScroll)
        try:
            self.turns.append(turn)
            await scroll.mount(turn)
            scroll.follow = True
            scroll.follow_output()
            stream = (
                self.conversation.execute_plan()
                if user_text.strip() == "/do"
                else self.conversation.stream_turn(user_text)
            )
            async with aclosing(stream) as events:
                async for event in events:
                    iteration = turn.view.iterations.setdefault(
                        event.iteration, {"text": "", "thinking": ""}
                    )
                    if isinstance(event, TextDelta):
                        iteration["text"] += event.text
                        turn.view.answer_text += event.text
                    elif isinstance(event, ThinkingDelta):
                        iteration["thinking"] += event.text
                        turn.view.thinking_text += event.text
                    elif isinstance(event, ToolCallReceived):
                        key = (event.run_id, event.iteration, event.call.id)
                        turn.view.tools[key] = (event.call.name, None)
                        turn.view.tool_arguments[key] = event.call.arguments_json
                    elif isinstance(event, ToolStarted):
                        key = (event.run_id, event.iteration, event.call_id)
                        turn.view.tools[key] = (event.name, None)
                        turn.view.started.add(key)
                    elif isinstance(event, ToolFinished):
                        key = (event.run_id, event.iteration, event.result.call_id)
                        turn.view.tools[key] = (event.result.name, event.result)
                    elif isinstance(event, ProgressUpdated):
                        self._progress = event
                    elif isinstance(event, UsageUpdated):
                        self._usage = event.total
                    elif isinstance(event, RunFinished):
                        self._usage = event.usage
                        turn.view.status = (
                            "completed"
                            if event.reason == "completed"
                            else "cancelled"
                            if event.reason == "cancelled"
                            else "failed"
                        )
                        turn.view.tool_history_saved = event.history_updated
                        turn.view.error_message = event.error_message
                    turn.refresh_content()
                    self._update_status()
                    scroll.follow_output()
        except asyncio.CancelledError:
            if turn.view.status == "streaming":
                turn.view.status = "cancelled"
            raise
        except Exception as exc:
            turn.view.status = "failed"
            turn.view.error_message = (
                exc.user_message if isinstance(exc, ProviderError) else "请求失败，请重新提问。"
            )
        finally:
            if turn.is_mounted:
                turn.refresh_content()
                scroll.follow_output()
            self.generating = False
            if not self.exiting:
                self._update_status()
                self.query_one(ChatInput).focus()

    async def _cancel_generation(self) -> None:
        if self._generation is not None and not self._generation.is_finished:
            self.conversation.cancel()
            with suppress(WorkerCancelled, WorkerFailed):
                await self._generation.wait()

    async def action_cancel_or_exit(self) -> None:
        if self.cancelling or self.exiting:
            return
        if self.generating:
            self.cancelling = True
            self._update_status()
            try:
                await self._cancel_generation()
            finally:
                self.generating = False
                self.cancelling = False
                self._update_status()
        else:
            await self.action_quit()

    async def action_quit(self) -> None:
        if self.exiting:
            return
        self.exiting = True
        await self._cancel_generation()
        await self.conversation.aclose()
        self.exit()

    async def on_unmount(self) -> None:
        await self.conversation.aclose()
