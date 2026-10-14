"""模型、工具和观察结果之间的自主循环。"""

import asyncio
from contextlib import aclosing
from uuid import uuid4

from mycode.agent import events as ae
from mycode.agent.collector import CollectedResponse, StreamCollector
from mycode.agent.mode import build_mode_prompt
from mycode.errors import ProviderError
from mycode.models import (
    Message,
    ProviderUsage,
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    ToolFinished,
    ToolStarted,
)
from mycode.tools.scheduler import BatchCompleted


class AgentLoop:
    def __init__(self, provider, scheduler, options):
        self.provider = provider
        self.scheduler = scheduler
        self.options = options
        self.last_finished = None

    async def run(self, user_text, *, history, mode, cancel):
        run_id = str(uuid4())
        iteration = unknown = 0
        usage_slots = []
        user = Message("user", user_text)
        response = None
        entered = updated = False
        reason, error, final = "cancelled", None, None
        self.last_finished = None

        def summary():
            return ae.UsageSummary(
                sum(u.input_tokens or 0 for u in usage_slots),
                sum(u.output_tokens or 0 for u in usage_slots),
                bool(usage_slots) and all(u.input_tokens is not None for u in usage_slots),
                bool(usage_slots) and all(u.output_tokens is not None for u in usage_slots),
                len(usage_slots),
                sum(u.input_tokens is not None or u.output_tokens is not None for u in usage_slots),
            )

        def commit_batch():
            nonlocal entered, user, updated
            if entered:
                history.commit_batch(user, response, self.scheduler.results)
                user = None
                entered = False
                updated = True

        try:
            while True:
                if cancel.is_set():
                    raise asyncio.CancelledError()
                iteration += 1
                usage_slots.append(TokenUsage())
                yield ae.ProgressUpdated(run_id, iteration, "model", self.options.max_iterations)
                definitions = (
                    self.scheduler.executor.registry.definitions(read_only=mode == "plan")
                    if self.scheduler
                    else ()
                )
                response = None
                stream = self.provider.stream(
                    (*history.snapshot(), *((user,) if user else ())),
                    tools=definitions,
                    allow_tools=bool(definitions),
                    system_prompt=build_mode_prompt(mode),
                )
                async with aclosing(StreamCollector().collect(stream, cancel=cancel)) as collected:
                    async for event in collected:
                        if isinstance(event, TextDelta):
                            yield ae.TextDelta(run_id, iteration, event.text)
                        elif isinstance(event, ThinkingDelta):
                            yield ae.ThinkingDelta(run_id, iteration, event.text)
                        elif isinstance(event, ProviderUsage):
                            usage_slots[-1] = event.usage
                            yield ae.UsageUpdated(run_id, iteration, event.usage, summary())
                        elif isinstance(event, CollectedResponse):
                            response = event.message
                            usage_slots[-1] = event.usage
                if cancel.is_set():
                    raise asyncio.CancelledError()
                if not response.tool_calls:
                    history.commit_reply(user, response)
                    updated = True
                    reason, final = "completed", response
                    break
                for call in response.tool_calls:
                    yield ae.ToolCallReceived(run_id, iteration, call)
                if cancel.is_set():
                    raise asyncio.CancelledError()
                if iteration >= self.options.max_iterations:
                    reason, error = "iteration_limit", "已达到迭代上限，本次工具调用未执行。"
                    break
                if self.scheduler is None:
                    raise ProviderError("invalid_response", "当前会话没有工具执行器。")
                batch = self.scheduler.validate_batch(response.tool_calls, mode=mode)
                await asyncio.sleep(0)
                if cancel.is_set():
                    raise asyncio.CancelledError()
                yield ae.ProgressUpdated(
                    run_id, iteration, "tools", self.options.max_iterations, len(batch.items), 0
                )
                count = 0
                self.scheduler.results = ()
                entered = True
                async with aclosing(
                    self.scheduler.execute_batch(batch, cancel=cancel)
                ) as scheduled:
                    async for event in scheduled:
                        if isinstance(event, ToolStarted):
                            yield ae.ToolStarted(run_id, iteration, event.call_id, event.name)
                        elif isinstance(event, ToolFinished):
                            count += 1
                            yield ae.ToolFinished(run_id, iteration, event.result)
                            yield ae.ProgressUpdated(
                                run_id,
                                iteration,
                                "tools",
                                self.options.max_iterations,
                                len(batch.items),
                                count,
                            )
                        elif isinstance(event, BatchCompleted):
                            commit_batch()
                if self.scheduler.failure:
                    raise RuntimeError(self.scheduler.failure)
                if cancel.is_set():
                    raise asyncio.CancelledError()
                unknown = unknown + 1 if batch.contains_unknown else 0
                if unknown >= self.options.unknown_tool_batch_limit:
                    reason, error = "unknown_tool_limit", "连续工具批次包含未知工具，已停止。"
                    break
        except asyncio.CancelledError:
            if not cancel.is_set():
                raise
            reason = "cancelled"
        except ProviderError as exc:
            reason = "invalid_response" if exc.kind == "invalid_response" else "stream_error"
            error = exc.user_message
        except Exception:
            reason, error = "internal_error", "Agent 运行异常，已停止并清理。"
        finally:
            if entered and self.scheduler.results:
                commit_batch()
            if self.scheduler:
                error = self.scheduler.executor.redactor.redact(error) if error else None
            self.last_finished = ae.RunFinished(
                run_id, iteration, reason, final, summary(), error, updated
            )
        yield self.last_finished
