"""工具公共接口，与模型协议及界面无关。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mycode.models import JsonValue


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, JsonValue]


@dataclass(frozen=True)
class ToolContext:
    working_directory: Path


@dataclass(frozen=True)
class ToolOutput:
    data: dict[str, JsonValue]
    truncated: bool = False


class ToolError(Exception):
    def __init__(self, code: str, message: str, data=None, *, truncated=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}
        self.truncated = truncated


class Tool(Protocol):
    definition: ToolDefinition

    async def execute(
        self, arguments: dict[str, JsonValue], context: ToolContext
    ) -> ToolOutput: ...


def schema(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


PATH = {"type": "string", "minLength": 1}
