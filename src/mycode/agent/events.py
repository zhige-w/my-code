"""Agent 与界面之间的不可变事件。"""

from dataclasses import dataclass
from typing import Literal, TypeAlias

from mycode.models import Message, TokenUsage, ToolCall, ToolResult

StopReason = Literal[
    "completed",
    "iteration_limit",
    "cancelled",
    "unknown_tool_limit",
    "stream_error",
    "invalid_response",
    "internal_error",
]


@dataclass(frozen=True)
class UsageSummary:
    known_input_tokens: int = 0
    known_output_tokens: int = 0
    input_complete: bool = False
    output_complete: bool = False
    requests_started: int = 0
    requests_with_usage: int = 0


@dataclass(frozen=True)
class AgentFrame:
    run_id: str
    iteration: int


@dataclass(frozen=True)
class TextDelta(AgentFrame):
    text: str


@dataclass(frozen=True)
class ThinkingDelta(AgentFrame):
    text: str


@dataclass(frozen=True)
class ToolCallReceived(AgentFrame):
    call: ToolCall


@dataclass(frozen=True)
class ToolStarted(AgentFrame):
    call_id: str
    name: str


@dataclass(frozen=True)
class ToolFinished(AgentFrame):
    result: ToolResult


@dataclass(frozen=True)
class UsageUpdated(AgentFrame):
    request_usage: TokenUsage
    total: UsageSummary


@dataclass(frozen=True)
class ProgressUpdated(AgentFrame):
    phase: str
    max_iterations: int
    tools_total: int = 0
    tools_finished: int = 0


@dataclass(frozen=True)
class RunFinished(AgentFrame):
    reason: StopReason
    message: Message | None
    usage: UsageSummary
    error_message: str | None = None
    history_updated: bool = False


AgentEvent: TypeAlias = (
    TextDelta
    | ThinkingDelta
    | ToolCallReceived
    | ToolStarted
    | ToolFinished
    | UsageUpdated
    | ProgressUpdated
    | RunFinished
)
