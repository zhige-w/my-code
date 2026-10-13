import asyncio

import pytest
from conftest import FakeProvider
from textual import events
from textual._xterm_parser import XTermParser
from textual.app import App, ComposeResult
from textual.widgets import Static

from mycode.conversation import Conversation
from mycode.errors import ProviderError, SecretRedactor
from mycode.models import Message, TextDelta, ThinkingDelta, TurnCompleted
from mycode.tui.app import ConversationScroll, MyCodeApp
from mycode.tui.widgets import ChatInput, TurnView, TurnWidget


class WidgetsApp(App):
    CSS = "ChatInput {height: 5;} TurnWidget {height: auto;}"

    def __init__(self, turn=None):
        super().__init__()
        self.turn = turn
        self.submitted = []

    def compose(self) -> ComposeResult:
        yield ChatInput()
        if self.turn:
            yield self.turn

    def on_chat_input_submitted(self, event: ChatInput.Submitted):
        self.submitted.append(event.text)


async def submit(app, pilot, text):
    app.query_one(ChatInput).load_text(text)
    app.query_one(ChatInput).focus()
    await pilot.press("enter")
    await pilot.pause()


async def test_widgets_keyboard():
    app = WidgetsApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one(ChatInput)
        input_widget.focus()
        await pilot.press("enter")
        assert app.submitted == []
        await pilot.press("h", "i", "alt+enter", "x")
        assert input_widget.text == "hi\nx"
        assert app.submitted == []
        await pilot.press("enter")
        assert app.submitted == ["hi\nx"]
        assert input_widget.text == "hi\nx"


@pytest.mark.parametrize("sequence", ["\x1b\r", "\x1b\n"])
async def test_widgets_legacy_alt_enter_bytes(sequence):
    app = WidgetsApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one(ChatInput)
        input_widget.load_text("first")
        input_widget.move_cursor((0, 5))
        parsed = list(XTermParser().feed(sequence))
        assert [event.key for event in parsed] == ["alt+enter"]
        for event in parsed:
            input_widget.post_message(event)
        await pilot.pause()
        assert input_widget.text == "first\n" and app.submitted == []
        await pilot.press("enter")
        assert app.submitted == ["first\n"]


async def test_widgets_thinking_plain_text_and_redaction():
    view = TurnView("你好 [bold] **问题**", answer_text="答复\x1b[2J\n\t尾部")
    turn = TurnWidget(view, SecretRedactor(["unique-test-secret"]))
    app = WidgetsApp(turn)
    async with app.run_test() as pilot:
        assert not turn.thinking.display
        assert turn.user.content.plain == "你好 [bold] **问题**"
        assert turn.answer.content.plain == "答复�[2J\n\t尾部"
        view.thinking_text = "先分析"
        view.answer_text = "答复 unique-test-"
        turn.refresh_content()
        await pilot.pause()
        assert turn.thinking.display and not turn.thinking.collapsed
        assert turn.answer.content.plain == "答复 "
        view.answer_text += "secret 后文"
        view.status = "completed"
        turn.refresh_content()
        assert turn.answer.content.plain == "答复 [密钥已隐藏] 后文"
        title = turn.thinking.query_one("CollapsibleTitle")
        title.focus()
        await pilot.press("enter")
        assert turn.thinking.collapsed
        await pilot.press("space")
        assert not turn.thinking.collapsed


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (60, 18)])
async def test_layout_fixed_input_and_resize(size):
    provider = FakeProvider(wait=True)
    app = MyCodeApp(Conversation(provider), "供应商", "模型")
    async with app.run_test(size=size) as pilot:
        input_widget = app.query_one(ChatInput)
        scroll = app.query_one(ConversationScroll)
        assert input_widget.region.height == 5
        assert input_widget.region.bottom < size[1]
        await submit(app, pilot, "hello")
        await asyncio.wait_for(provider.started.wait(), 2)
        await pilot.resize_terminal(70, 20)
        assert input_widget.region.height == 5
        assert input_widget.region.bottom < 20
        assert scroll.region.height > 0 and app.generating
        await pilot.press("tab")
        assert app.focused is not input_widget
        await pilot.press("shift+tab")
        assert app.focused is input_widget
        await pilot.press("ctrl+c")


async def test_layout_scroll_follow_and_pause():
    gate = asyncio.Event()
    started = asyncio.Event()

    class LineProvider(FakeProvider):
        async def stream(self, messages):
            text = "\n".join(f"line {i}" for i in range(90))
            yield TextDelta(text)
            started.set()
            await gate.wait()
            yield TextDelta("\nnew content")
            yield TurnCompleted(Message("assistant", text + "\nnew content"))

    app = MyCodeApp(Conversation(LineProvider()), "p", "m")
    async with app.run_test(size=(80, 24)) as pilot:
        await submit(app, pilot, "long")
        await asyncio.wait_for(started.wait(), 2)
        await pilot.pause()
        scroll = app.query_one(ConversationScroll)
        assert scroll.max_scroll_y > 0
        assert scroll.is_vertical_scroll_end
        scroll.focus()
        await pilot.press("pageup")
        await pilot.pause()
        assert not scroll.is_vertical_scroll_end
        before = scroll.scroll_y
        gate.set()
        await pilot.pause()
        assert scroll.scroll_y <= before
        scroll.scroll_end(animate=False)
        await pilot.pause()
        assert scroll.is_vertical_scroll_end and scroll.follow
        scroll.post_message(events.MouseScrollUp(scroll, 2, 2, 0, -1, 0, False, False, False))
        await pilot.pause(0.3)
        assert not scroll.is_vertical_scroll_end and not scroll.follow


async def test_worker_streaming_duplicate_and_recovery():
    provider = FakeProvider(events=[ThinkingDelta("思考"), TextDelta("首段")], wait=True)
    conversation = Conversation(provider)
    app = MyCodeApp(conversation, "p", "m")
    async with app.run_test() as pilot:
        await submit(app, pilot, "first")
        await asyncio.wait_for(provider.started.wait(), 2)
        assert app.generating
        assert app.turns[0].answer.content.plain == "首段"
        assert app.turns[0].thinking_text.content.plain == "思考"
        assert conversation.history == ()
        await submit(app, pilot, "next draft")
        await pilot.press("enter", "enter")
        assert len(provider.requests) == 1
        assert app.query_one(ChatInput).text == "next draft"
        provider.release.set()
        await pilot.pause()
        assert not app.generating and app.turns[0].view.status == "completed"
        assert len(conversation.history) == 2
        provider.wait = False
        provider.error = ProviderError("connection", "网络连接异常")
        await submit(app, pilot, "failed")
        assert app.turns[-1].view.status == "failed"
        assert app.turns[-1].view.answer_text == "首段"
        assert "网络连接异常" in app.turns[-1].status_text.content.plain
        assert not app.generating and len(conversation.history) == 2
        provider.error = None
        await submit(app, pilot, "recovered")
        assert app.turns[-1].view.status == "completed"
        assert [m.text for m in provider.requests[-1]] == ["first", "首段", "recovered"]
        assert app.focused is app.query_one(ChatInput)


async def test_worker_double_submit_before_start():
    provider = FakeProvider(wait=True)
    app = MyCodeApp(Conversation(provider), "p", "m")
    async with app.run_test() as pilot:
        app.on_chat_input_submitted(ChatInput.Submitted("first"))
        app.on_chat_input_submitted(ChatInput.Submitted("duplicate"))
        await pilot.pause()
        assert len(provider.requests) == 1
        await pilot.press("ctrl+c")


async def test_worker_safe_error_and_split_secret():
    provider = FakeProvider(events=[TextDelta("answer unique-test-")], wait=True)
    app = MyCodeApp(
        Conversation(provider), "p", "m", redactor=SecretRedactor(["unique-test-secret"])
    )
    async with app.run_test() as pilot:
        await submit(app, pilot, "unique-test-secret")
        assert app.turns[0].user.content.plain == "[密钥已隐藏]"
        assert app.turns[0].answer.content.plain == "answer "
        await pilot.press("ctrl+c")
        provider.wait = False
        provider.events = [TextDelta("unique-test-secret")]
        provider.error = RuntimeError("unique-test-secret raw response")
        await submit(app, pilot, "again")
        assert "unique-test-secret" not in app.turns[-1].answer.content.plain
        assert "raw response" not in app.turns[-1].status_text.content.plain


async def test_cancel_then_continue_and_idle_exit():
    provider = FakeProvider(wait=True)
    conversation = Conversation(provider)
    app = MyCodeApp(conversation, "p", "m")
    async with app.run_test() as pilot:
        await submit(app, pilot, "cancel")
        await pilot.press("ctrl+c")
        assert not app.generating and not app.cancelling
        assert provider.stream_closed and not conversation.busy
        assert app.turns[-1].view.status == "cancelled"
        assert app.turns[-1].view.answer_text == "你好"
        assert conversation.history == ()
        provider.wait = False
        await submit(app, pilot, "recover")
        assert app.turns[-1].view.status == "completed"
        await pilot.press("ctrl+c")
        assert app.exiting and provider.closed


@pytest.mark.parametrize("generating", [False, True])
async def test_ctrl_d_exit(generating):
    provider = FakeProvider(wait=True)
    app = MyCodeApp(Conversation(provider), "p", "m")
    async with app.run_test() as pilot:
        if generating:
            await submit(app, pilot, "hello")
        await pilot.press("ctrl+d")
        assert app.exiting and provider.closed
        if generating:
            assert provider.stream_closed


async def test_cancel_cleanup_rejects_submit_and_repeated_cancel():
    cleaning = asyncio.Event()
    cleanup_gate = asyncio.Event()

    class CleanupProvider(FakeProvider):
        async def stream(self, messages):
            self.requests.append(messages)
            try:
                yield TextDelta("partial")
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await cleanup_gate.wait()
                self.stream_closed = True

    provider = CleanupProvider()
    app = MyCodeApp(Conversation(provider), "p", "m")
    async with app.run_test() as pilot:
        await submit(app, pilot, "hello")
        cancel = asyncio.create_task(app.action_cancel_or_exit())
        await asyncio.wait_for(cleaning.wait(), 2)
        assert app.cancelling
        app.on_chat_input_submitted(ChatInput.Submitted("duplicate"))
        await app.action_cancel_or_exit()
        assert len(provider.requests) == 1 and not app.exiting
        cleanup_gate.set()
        await asyncio.wait_for(cancel, 2)
        assert provider.stream_closed and not app.generating


async def test_status_redacts_provider_identity():
    app = MyCodeApp(
        Conversation(FakeProvider()),
        "unique-test-secret",
        "m",
        redactor=SecretRedactor(["unique-test-secret"]),
    )
    async with app.run_test():
        assert "unique-test-secret" not in app.query_one("#status", Static).content.plain


async def test_worker_exit_does_not_persist_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    provider = FakeProvider()
    app = MyCodeApp(Conversation(provider), "p", "m")
    async with app.run_test() as pilot:
        await submit(app, pilot, "临时口令：青竹742")
        assert len(app.conversation.history) == 2
        await pilot.press("ctrl+d")
    assert set(tmp_path.iterdir()) == before
    next_provider = FakeProvider()
    next_app = MyCodeApp(Conversation(next_provider), "p", "m")
    async with next_app.run_test() as pilot:
        await submit(next_app, pilot, "口令是什么")
        assert next_provider.requests == [(Message("user", "口令是什么"),)]
