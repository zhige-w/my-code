"""参数校验、执行时限与统一结果封装。"""

import asyncio
import json

from jsonschema import Draft202012Validator, ValidationError

from mycode.errors import SecretRedactor
from mycode.models import ToolCall, ToolResult, ToolResultError
from mycode.tools.base import ToolContext, ToolError
from mycode.tools.registry import ToolRegistry

DEFAULT_TIMEOUT = 30


def strict_json(text):
    def reject(value):
        raise ValueError("参数包含非 JSON 数值。")

    value = json.loads(text, parse_constant=reject)
    json.dumps(value, allow_nan=False)
    return value


class ToolExecutor:
    def __init__(
        self, registry: ToolRegistry, context: ToolContext, redactor: SecretRedactor | None = None
    ):
        self.registry = registry
        self.context = context
        self.redactor = redactor or SecretRedactor()

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            try:
                tool = self.registry.get(call.name)
            except KeyError:
                raise ToolError("unknown_tool", f"未知工具：{call.name}") from None
            try:
                arguments = strict_json(call.arguments_json)
                Draft202012Validator(tool.definition.parameters).validate(arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("参数必须为对象。")
            except (ValueError, ValidationError) as exc:
                message = exc.message if isinstance(exc, ValidationError) else str(exc)
                raise ToolError(
                    "invalid_arguments",
                    "工具参数无效：" + message,
                    {"arguments_json": call.arguments_json},
                ) from None
            timeout = (
                arguments.get("timeout", DEFAULT_TIMEOUT)
                if (call.name == "execute_command")
                else DEFAULT_TIMEOUT
            )
            async with asyncio.timeout(timeout) as deadline:
                try:
                    output = await tool.execute(arguments, self.context)
                except asyncio.CancelledError as exc:
                    if deadline.expired():
                        frame = getattr(exc, "result_frame", None)
                        if frame:
                            error = frame.get("error")
                            if error:
                                raise ToolError(
                                    error["code"], error["message"], frame.get("data", {})
                                ) from None
                            return ToolResult(
                                call.id,
                                call.name,
                                "success",
                                frame["data"],
                                truncated=frame.get("truncated", False),
                            )
                        raise ToolError(
                            "timeout",
                            "工具执行超时，已停止执行；写入结果请核实。",
                            getattr(exc, "result_data", {}),
                            truncated=getattr(exc, "truncated", False),
                        ) from None
                    raise
            return ToolResult(
                call.id, call.name, "success", output.data, truncated=output.truncated
            )
        except TimeoutError:
            data = {"outcome_unknown": True} if call.name in {"write_file", "edit_file"} else {}
            return ToolResult(
                call.id,
                call.name,
                "error",
                data,
                ToolResultError("timeout", "工具执行超时；操作可能已生效，请核实。"),
            )
        except ToolError as exc:
            # 先递归处理字符串，避免 JSON 转义影响密钥匹配。
            def redact(value):
                if isinstance(value, str):
                    return self.redactor.redact(value)
                if isinstance(value, list):
                    return [redact(item) for item in value]
                if isinstance(value, dict):
                    return {self.redactor.redact(key): redact(item) for key, item in value.items()}
                return value

            data = redact(exc.data)
            return ToolResult(
                call.id,
                call.name,
                "error",
                data,
                ToolResultError(exc.code, self.redactor.redact(exc.message)),
                exc.truncated,
            )
        except FileNotFoundError:
            return ToolResult(
                call.id, call.name, "error", {}, ToolResultError("not_found", "目标路径不存在。")
            )
        except PermissionError:
            return ToolResult(
                call.id,
                call.name,
                "error",
                {},
                ToolResultError("permission_denied", "没有访问目标路径的权限。"),
            )
        except Exception:
            return ToolResult(
                call.id,
                call.name,
                "error",
                {},
                ToolResultError("execution_error", "工具执行失败，请检查参数与环境。"),
            )
