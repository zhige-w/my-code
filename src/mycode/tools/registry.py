"""工具注册与元信息校验。"""

from copy import deepcopy

from jsonschema import Draft202012Validator

from mycode.tools.base import Tool, ToolDefinition


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        definition = tool.definition
        if not definition.name.strip() or definition.name in self._tools:
            raise ValueError("工具名称为空或重复。")
        Draft202012Validator.check_schema(definition.parameters)
        self._tools[definition.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return deepcopy(tuple(tool.definition for tool in self._tools.values()))
