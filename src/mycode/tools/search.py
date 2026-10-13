"""有界文件查找和逐行正则搜索。"""

import fnmatch
import os
import re

from mycode.tools.base import PATH, ToolDefinition, ToolError, ToolOutput, schema
from mycode.tools.files import bounded_lines, clip, resolve
from mycode.tools.process import run_worker

SKIP = {".git", ".venv", "__pycache__", "node_modules"}


def matches(path, pattern):
    parts, patterns = path.split("/"), pattern.split("/")

    def match(i, j):
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == "**":
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)

    return match(0, 0)


def operate(name, arguments, context, emit):
    root = resolve(arguments.get("path", "."), context)
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise ToolError("unsupported_file", "搜索根路径不是目录。")
    # 根目录失败必须直接返回错误，不当作子项跳过。
    with os.scandir(root):
        pass
    reasons = {}

    def skip(reason):
        reasons[reason] = reasons.get(reason, 0) + 1

    pattern = arguments["pattern"]
    expression = None
    if name == "search_code":
        try:
            expression = re.compile(pattern, 0 if arguments.get("case_sensitive", True) else re.I)
        except re.error:
            raise ToolError("invalid_pattern", "正则表达式无效。") from None
    results, size, truncated, full = [], 0, False, False

    def add(value):
        nonlocal size, truncated, full
        import json

        length = len(json.dumps(value, ensure_ascii=False).encode())
        if len(results) >= 200 or size + length > 64 * 1024:
            truncated = True
            full = True
            return False
        results.append(value)
        size += length
        return True

    def walk_error(exc):
        skip("unreadable")

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(
            d for d in dirs if d not in SKIP and not (root / directory / d).is_symlink()
        )
        for filename in sorted(files):
            from pathlib import Path

            path = Path(directory) / filename
            relative = path.relative_to(root).as_posix()
            if not matches(
                relative, pattern if expression is None else arguments.get("glob", "**/*")
            ):
                continue
            if not path.is_file():
                skip("not_regular_file")
                continue
            if expression is None:
                if not add(relative):
                    break
                continue
            try:
                for line_number, line, oversized in bounded_lines(path):
                    if oversized:
                        skip("oversized_line")
                        truncated = True
                        continue
                    if expression.search(line):
                        text = line.rstrip("\r\n")
                        clipped = clip(text, 2048)
                        truncated |= clipped != text
                        if not add({"path": relative, "line_number": line_number, "text": clipped}):
                            break
            except (UnicodeError, ToolError):
                # 文件含无效文本时，不保留此前的部分匹配。
                results[:] = [r for r in results if r["path"] != relative]
                import json

                size = sum(len(json.dumps(r, ensure_ascii=False).encode()) for r in results)
                skip("unsupported_file")
            except OSError:
                skip("unreadable")
            if full or len(results) >= 200 or size >= 64 * 1024:
                truncated = True
                break
        if full or len(results) >= 200 or size >= 64 * 1024:
            truncated = True
            break
        # add 可能因下一条太大停止，不能继续无限遍历。
        if truncated and expression is None:
            break
    if expression is None:
        results.sort()
    return ToolOutput(
        {"matches": results, "skipped_count": sum(reasons.values()), "skip_reasons": reasons},
        truncated,
    )


class FindFilesTool:
    definition = ToolDefinition(
        "find_files",
        "按 glob 查文件，支持 **；最多200条/64KiB；跳过常见缓存目录。",
        schema({"pattern": PATH, "path": PATH}, ["pattern"]),
    )

    async def execute(self, arguments, context):
        return await run_worker(self.definition.name, arguments, context)


class SearchCodeTool(FindFilesTool):
    definition = ToolDefinition(
        "search_code",
        "逐行正则搜索 UTF-8 文件，最多200条/64KiB，匹配行保留2KiB。",
        schema(
            {
                "pattern": {"type": "string"},
                "path": PATH,
                "glob": PATH,
                "case_sensitive": {"type": "boolean", "default": True},
            },
            ["pattern"],
        ),
    )
