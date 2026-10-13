"""UTF-8 文件工具及原子提交。"""

import hashlib
import os
import secrets
import stat
from pathlib import Path

from mycode.tools.base import PATH, ToolDefinition, ToolError, ToolOutput, schema
from mycode.tools.process import run_worker

FILE_LIMIT = 1024 * 1024
READ_LIMIT = 64 * 1024


def resolve(path, context):
    target = Path(path)
    if not target.is_absolute():
        target = context.working_directory / target
    return target.resolve()


def clip(text, limit):
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def bounded_lines(path):
    # 每行缓冲有界；超长行继续排空后提供截断标记。
    with path.open("r", encoding="utf-8", newline="") as stream:
        number = 0
        while first := stream.readline(READ_LIMIT + 1):
            number += 1
            if "\0" in first:
                raise ToolError("unsupported_file", "不支持含 NUL 的文本文件。")
            oversized = len(first) > READ_LIMIT
            tail = first
            while not tail.endswith(("\n", "\r")) and len(tail) == READ_LIMIT + 1:
                tail = stream.readline(READ_LIMIT + 1)
                if "\0" in tail:
                    raise ToolError("unsupported_file", "不支持含 NUL 的文本文件。")
                oversized = True
            yield number, first, oversized


def atomic_write(path, content, emit, before_commit=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    temporary = path.parent / (".mycode-" + secrets.token_hex(16))
    emit({"type": "temporary", "path": str(temporary)})
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
        if before_commit:
            before_commit()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
        emit({"type": "released", "path": str(temporary)})


def read_file(path, arguments):
    if not path.is_file():
        if not path.exists():
            raise FileNotFoundError(path)
        raise ToolError("unsupported_file", "目标不是普通文件。")
    offset, limit = arguments.get("offset", 1), arguments.get("limit", 200)
    pieces, size, truncated = [], 0, False
    try:
        for number, line, oversized in bounded_lines(path):
            if number < offset:
                continue
            if len(pieces) >= limit or size >= READ_LIMIT:
                truncated = True
                break
            prefix = f"{number}: "
            part = clip(prefix + line, READ_LIMIT - size)
            pieces.append(part)
            size += len(part.encode())
            if oversized or part != prefix + line:
                truncated = True
                break
    except UnicodeError:
        raise ToolError("unsupported_file", "文件不是有效 UTF-8 文本。") from None
    return ToolOutput({"path": str(path), "offset": offset, "content": "".join(pieces)}, truncated)


def operate(name, arguments, context, emit):
    path = resolve(arguments["path"], context)
    if name == "read_file":
        return read_file(path, arguments)
    if path.exists() and not path.is_file():
        raise ToolError("unsupported_file", "目标不是普通文件。")
    before_commit = None
    if name == "write_file":
        content = arguments["content"]
    else:
        if path.stat().st_size > FILE_LIMIT:
            raise ToolError("file_too_large", "编辑文件超过 1 MiB。")
        raw = path.read_bytes()
        if len(raw) > FILE_LIMIT:
            raise ToolError("file_too_large", "编辑文件超过 1 MiB。")
        try:
            original = raw.decode("utf-8")
        except UnicodeError:
            raise ToolError("unsupported_file", "文件不是有效 UTF-8 文本。") from None
        if "\0" in original:
            raise ToolError("unsupported_file", "不支持含 NUL 的文件。")
        old = arguments["old_text"]
        if not old:
            raise ToolError("invalid_arguments", "old_text 不得为空。")
        count = original.count(old)
        if count != 1:
            code = "multiple_matches" if count else "match_not_found"
            raise ToolError(
                code, f"原文匹配 {count} 次，必须恰好匹配一次。", {"match_count": count}
            )
        state, digest = path.stat(), hashlib.sha256(raw).digest()
        content = original.replace(old, arguments["new_text"], 1)

        def before_commit():
            try:
                now = path.stat()
                if (now.st_ino, now.st_size, now.st_mtime_ns, now.st_mode) != (
                    state.st_ino,
                    state.st_size,
                    state.st_mtime_ns,
                    state.st_mode,
                ) or hashlib.sha256(path.read_bytes()).digest() != digest:
                    raise ToolError("file_changed", "文件已被其他操作修改，请重新读取。")
            except FileNotFoundError:
                raise ToolError("file_changed", "文件已被删除，请重新读取。") from None

    if "\0" in content:
        raise ToolError("unsupported_file", "不支持写入含 NUL 的文本。")
    encoded = content.encode("utf-8")
    if len(encoded) > FILE_LIMIT:
        raise ToolError("file_too_large", "写入内容超过 1 MiB。")
    atomic_write(path, encoded, emit, before_commit)
    data = {"path": str(path), "bytes_written": len(encoded)}
    if name == "edit_file":
        data["replacement_count"] = 1
    return ToolOutput(data)


class ReadFileTool:
    definition = ToolDefinition(
        "read_file",
        "读取 UTF-8 文本，行号从1开始，默认200行，最多64KiB。",
        schema(
            {
                "path": PATH,
                "offset": {"type": "integer", "minimum": 1, "default": 1},
                "limit": {"type": "integer", "minimum": 1, "default": 200},
            },
            ["path"],
        ),
    )

    async def execute(self, arguments, context):
        return await run_worker(self.definition.name, arguments, context)


class WriteFileTool(ReadFileTool):
    definition = ToolDefinition(
        "write_file",
        "创建或覆盖 UTF-8 文件，自动创建父目录，最多1MiB。",
        schema({"path": PATH, "content": {"type": "string"}}, ["path", "content"]),
    )


class EditFileTool(ReadFileTool):
    definition = ToolDefinition(
        "edit_file",
        "原文必须非空且唯一匹配，字面替换一次，最多1MiB。",
        schema(
            {"path": PATH, "old_text": PATH, "new_text": {"type": "string"}},
            ["path", "old_text", "new_text"],
        ),
    )
