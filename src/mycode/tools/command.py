"""非交互式 shell 命令工具。"""

from pathlib import Path

from mycode.tools.base import PATH, ToolDefinition, ToolError, schema
from mycode.tools.process import run_command


class ExecuteCommandTool:
    definition = ToolDefinition(
        "execute_command",
        "用 /bin/sh -c 执行非交互命令；默认30秒，最多120秒；输出各64KiB，取消不回滚。",
        schema(
            {
                "command": PATH,
                "cwd": PATH,
                "timeout": {"type": "number", "exclusiveMinimum": 0, "maximum": 120, "default": 30},
            },
            ["command"],
        ),
    )

    async def execute(self, arguments, context):
        cwd = Path(arguments.get("cwd", "."))
        if not cwd.is_absolute():
            cwd = context.working_directory / cwd
        output = await run_command(arguments["command"], cwd.resolve())
        if output.data["exit_code"]:
            raise ToolError(
                "command_failed", "命令以非零退出码结束。", output.data, truncated=output.truncated
            )
        return output
