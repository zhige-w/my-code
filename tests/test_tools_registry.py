import json

from mycode.models import ToolResult, ToolResultError
from mycode.tools.base import ToolContext, ToolDefinition, ToolError, ToolOutput


def test_structures(tmp_path):
    data = {"nested": ["中文"]}
    result = ToolResult("id", "read_file", "error", data, ToolResultError("x", "说明"))
    data["nested"].append("改变")
    assert json.loads(result.to_json())["data"] == {"nested": ["中文"]}
    assert ToolContext(tmp_path).working_directory == tmp_path
    assert ToolDefinition("a", "说明", {}).name == "a"
    assert not ToolOutput({}).truncated
    assert ToolError("x", "失败", {"exit_code": 1}).data == {"exit_code": 1}


def test_registry():
    import pytest
    from jsonschema import SchemaError

    from mycode.tools import create_tool_registry
    from mycode.tools.base import ToolDefinition

    registry = create_tool_registry()
    assert len(registry.definitions()) == 6
    with pytest.raises(ValueError):
        registry.register(registry.get("read_file"))
    with pytest.raises(KeyError):
        registry.get("absent")

    class Invalid:
        definition = ToolDefinition("", "a", {})

    with pytest.raises(ValueError):
        registry.register(Invalid())
    Invalid.definition = ToolDefinition("new", "a", {"type": "invalid"})
    with pytest.raises(SchemaError):
        registry.register(Invalid())
    definitions = registry.definitions()
    definitions[0].parameters["properties"].clear()
    assert registry.definitions()[0].parameters["properties"]
