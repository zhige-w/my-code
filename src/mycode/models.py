"""与厂商 SDK 无关的消息和流式事件。"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class NativeContent:
    protocol: str
    blocks: list[dict[str, JsonValue]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", deepcopy(self.blocks))


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    text: str
    native_content: NativeContent | None = None


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    text: str


@dataclass(frozen=True)
class TurnCompleted:
    message: Message


StreamEvent: TypeAlias = TextDelta | ThinkingDelta | TurnCompleted
