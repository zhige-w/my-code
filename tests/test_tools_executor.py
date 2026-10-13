import asyncio
import json

import pytest

from mycode.errors import SecretRedactor
from mycode.models import ToolCall
from mycode.tools import create_tool_registry
from mycode.tools.base import ToolContext, ToolDefinition, ToolError, ToolOutput, schema
from mycode.tools.executor import ToolExecutor
from mycode.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    "arguments", ["{", "[]", "NaN", "{}", '{"path":2}', '{"path":"x","extra":1}']
)
async def test_invalid_arguments(tmp_path, arguments):
    runner = ToolExecutor(create_tool_registry(), ToolContext(tmp_path))
    result = await runner.execute(ToolCall("id", "read_file", arguments))
    assert result.status == "error" and result.error.code == "invalid_arguments"
    assert result.call_id == "id"


async def test_unknown_and_failure(tmp_path, monkeypatch):
    registry = ToolRegistry()

    class Dummy:
        definition = ToolDefinition("dummy", "工具", schema({}, []))

        async def execute(self, arguments, context):
            raise ToolError("custom", "secret", {"stderr": "secret"})

    registry.register(Dummy())
    runner = ToolExecutor(registry, ToolContext(tmp_path), SecretRedactor(["secret"]))
    result = await runner.execute(ToolCall("a", "missing", "{}"))
    assert result.error.code == "unknown_tool"
    result = await runner.execute(ToolCall("b", "dummy", "{}"))
    assert "secret" not in result.to_json()

    async def crash(*args):
        raise RuntimeError("secret")

    monkeypatch.setattr(registry.get("dummy"), "execute", crash)
    assert (await runner.execute(ToolCall("c", "dummy", "{}"))).error.code == "execution_error"


async def test_default_timeout_and_cancel(tmp_path, monkeypatch):
    from mycode.tools import executor

    monkeypatch.setattr(executor, "DEFAULT_TIMEOUT", 0.03)
    registry = ToolRegistry()

    class Dummy:
        definition = ToolDefinition("dummy", "工具", schema({}, []))

        async def execute(self, arguments, context):
            await asyncio.sleep(10)
            return ToolOutput({})

    registry.register(Dummy())
    runner = ToolExecutor(registry, ToolContext(tmp_path))
    assert (await runner.execute(ToolCall("a", "dummy", "{}"))).error.code == "timeout"
    task = asyncio.create_task(runner.execute(ToolCall("b", "dummy", "{}")))
    await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("timeout", [0, -1, 121, True, "1"])
async def test_command_invalid_timeout(tmp_path, timeout):
    runner = ToolExecutor(create_tool_registry(), ToolContext(tmp_path))
    result = await runner.execute(
        ToolCall(
            "id",
            "execute_command",
            json.dumps({"command": "touch should-not-exist", "timeout": timeout}),
        )
    )
    assert result.error.code == "invalid_arguments"
    assert not (tmp_path / "should-not-exist").exists()


async def test_command_defaults_and_cwd(tmp_path, monkeypatch):
    from mycode.tools import executor

    monkeypatch.setattr(executor, "DEFAULT_TIMEOUT", 0.05)
    runner = ToolExecutor(create_tool_registry(), ToolContext(tmp_path))
    r = await runner.execute(ToolCall("a", "execute_command", '{"command":"sleep 20"}'))
    assert r.error.code == "timeout"
    (tmp_path / "child").mkdir()
    r = await runner.execute(
        ToolCall("b", "execute_command", '{"command":"pwd","cwd":"child","timeout":120}')
    )
    assert r.status == "success" and r.data["stdout"].strip() == str((tmp_path / "child").resolve())
    assert runner.context.working_directory == tmp_path
    r = await runner.execute(ToolCall("c", "execute_command", '{"command":"pwd","cwd":"missing"}'))
    assert r.error.code == "not_found"
