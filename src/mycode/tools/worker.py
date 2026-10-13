"""文件和搜索的工作进程；只输出内部 JSON 帧，不加载模型配置。"""

import json
import sys
from pathlib import Path

from mycode.tools.base import ToolContext, ToolError


def emit(frame):
    print(json.dumps(frame, ensure_ascii=False), flush=True)


def main():
    try:
        # 两段各 1 MiB 的编辑文本在 JSON 转义后仍须完整传入。
        request = json.loads(sys.stdin.buffer.readline(16 * 1024 * 1024))
        context = ToolContext(Path(request["working_directory"]))
        name = request["name"]
        if name in {"read_file", "write_file", "edit_file"}:
            from mycode.tools.files import operate
        elif name in {"find_files", "search_code"}:
            from mycode.tools.search import operate
        else:
            raise ToolError("unknown_tool", "工作进程不支持该工具。")
        output = operate(name, request["arguments"], context, emit)
        emit({"type": "result", "data": output.data, "truncated": output.truncated})
    except Exception as exc:
        if isinstance(exc, ToolError):
            code, message, data = exc.code, exc.message, exc.data
        elif isinstance(exc, FileNotFoundError):
            code, message, data = "not_found", "目标路径不存在。", {}
        elif isinstance(exc, PermissionError):
            code, message, data = "permission_denied", "没有访问目标路径的权限。", {}
        else:
            code, message, data = "execution_error", "文件或搜索操作失败。", {}
        emit({"type": "result", "error": {"code": code, "message": message}, "data": data})


if __name__ == "__main__":
    main()
