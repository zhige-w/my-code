import json

from mycode.models import ToolCall
from mycode.tools import create_tool_registry
from mycode.tools.base import ToolContext
from mycode.tools.executor import ToolExecutor
from mycode.tools.search import matches


async def invoke(root, name, **arguments):
    return await ToolExecutor(create_tool_registry(), ToolContext(root)).execute(
        ToolCall("search-id", name, json.dumps(arguments))
    )


def fixture_tree(root):
    (root / "a.py").write_text('def Hello():\n    return "中文"\n')
    (root / "sub").mkdir()
    (root / "sub/b.py").write_text("def hello():\n    pass\n")
    (root / ".git").mkdir()
    (root / ".git/hidden.py").write_text("def hidden():\n")
    (root / "loop").symlink_to(root, target_is_directory=True)


def test_traversal():
    assert matches("a.py", "**/*.py")
    assert matches("sub/a.py", "**/*.py")
    assert not matches("sub/a.py", "*.py")
    assert matches("sub/a.py", "s?b/*.py")
    assert matches("sub/x/y.py", "sub/**/y.py")


async def test_find_files(tmp_path):
    fixture_tree(tmp_path)
    result = await invoke(tmp_path, "find_files", pattern="**/*.py")
    assert result.data["matches"] == ["a.py", "sub/b.py"]
    result = await invoke(tmp_path, "find_files", path=".git", pattern="**/*")
    assert result.data["matches"] == ["hidden.py"]
    assert (await invoke(tmp_path, "find_files", pattern="*.none")).data["matches"] == []
    for i in range(205):
        (tmp_path / f"{i}.txt").touch()
    result = await invoke(tmp_path, "find_files", pattern="*.txt")
    assert len(result.data["matches"]) == 200 and result.truncated
    assert (
        await invoke(tmp_path, "find_files", path="absent", pattern="*")
    ).error.code == "not_found"


async def test_search_code(tmp_path):
    fixture_tree(tmp_path)
    result = await invoke(tmp_path, "search_code", pattern="def hello", case_sensitive=False)
    assert [m["path"] for m in result.data["matches"]] == ["a.py", "sub/b.py"]
    assert all(m["line_number"] == 1 for m in result.data["matches"])
    assert len((await invoke(tmp_path, "search_code", pattern="def hello")).data["matches"]) == 1
    assert (await invoke(tmp_path, "search_code", pattern="[")).error.code == "invalid_pattern"
    assert (await invoke(tmp_path, "search_code", pattern="no match")).data["matches"] == []
    (tmp_path / "binary").write_bytes(b"\x00")
    (tmp_path / "invalid").write_bytes(b"\xff")
    (tmp_path / "long").write_text("x" * 70000 + "\n" + "中" * 2000 + "\n")
    result = await invoke(tmp_path, "search_code", pattern="中")
    assert result.truncated and result.data["skipped_count"] >= 3
    assert len(result.data["matches"][0]["text"].encode()) <= 2048
    (tmp_path / "many").write_text("match\n" * 205)
    result = await invoke(tmp_path, "search_code", pattern="match")
    assert len(result.data["matches"]) == 200 and result.truncated


async def test_search_code_regex_timeout(tmp_path, monkeypatch):
    from mycode.tools import executor

    monkeypatch.setattr(executor, "DEFAULT_TIMEOUT", 0.3)
    (tmp_path / "expensive").write_text("a" * 200 + "!")
    result = await invoke(tmp_path, "search_code", pattern="(a+)+$")
    assert result.error.code == "timeout"


async def test_traversal_unreadable(tmp_path):
    path = tmp_path / "private"
    path.mkdir()
    (path / "secret").write_text("test")
    path.chmod(0)
    try:
        result = await invoke(tmp_path, "find_files", pattern="**/*")
        assert result.data["skip_reasons"]["unreadable"] == 1
        result = await invoke(tmp_path, "find_files", path="private", pattern="*")
        assert result.error.code == "permission_denied"
    finally:
        path.chmod(0o700)


async def test_search_code_total_bytes(tmp_path):
    import json

    (tmp_path / "many").write_text(("中" * 600 + "\n") * 100)
    result = await invoke(tmp_path, "search_code", pattern="中")
    assert result.truncated and len(result.data["matches"]) < 100
    assert (
        sum(len(json.dumps(v, ensure_ascii=False).encode()) for v in result.data["matches"])
        <= 65536
    )


async def test_find_files_total_bytes(tmp_path):
    root = tmp_path
    for i in range(5):
        root = root / ("d" * 100)
        root.mkdir()
    for i in range(150):
        (root / (str(i) + "x" * 180)).touch()
    result = await invoke(tmp_path, "find_files", pattern="**/*")
    assert result.truncated and len(result.data["matches"]) < 150
    assert (
        sum(len(json.dumps(v, ensure_ascii=False).encode()) for v in result.data["matches"])
        <= 65536
    )
