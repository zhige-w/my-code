"""与厂商 SDK 无关的消息和流式事件。"""

import json
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Literal, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class NativeContent:
    protocol: str
    blocks: list[dict[str, JsonValue]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", deepcopy(self.blocks))


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ToolResultError:
    code: str
    message: str


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    status: Literal["success", "error", "cancelled"]
    data: dict[str, JsonValue]
    error: ToolResultError | None = None
    truncated: bool = False

    def __post_init__(self):
        object.__setattr__(self, "data", deepcopy(self.data))

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str
    native_content: NativeContent | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()


@dataclass(frozen=True)
class TextDelta:
    text: str
    phase: Literal["initial", "summary"] = "initial"


@dataclass(frozen=True)
class ThinkingDelta:
    text: str
    phase: Literal["initial", "summary"] = "initial"


@dataclass(frozen=True)
class ResponseCompleted:
    message: Message


@dataclass(frozen=True)
class ToolStarted:
    call_id: str
    name: str


@dataclass(frozen=True)
class ToolFinished:
    result: ToolResult


@dataclass(frozen=True)
class TurnCompleted:
    message: Message


ProviderEvent: TypeAlias = TextDelta | ThinkingDelta | ResponseCompleted
StreamEvent: TypeAlias = TextDelta | ThinkingDelta | ToolStarted | ToolFinished | TurnCompleted
