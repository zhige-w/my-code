import json
import os

import pytest

from mycode.models import ToolCall
from mycode.tools import create_tool_registry
from mycode.tools.base import ToolContext, ToolError
from mycode.tools.executor import ToolExecutor
from mycode.tools.files import atomic_write, operate


async def invoke(root, name, **arguments):
    return await ToolExecutor(create_tool_registry(), ToolContext(root)).execute(
        ToolCall("file-id", name, json.dumps(arguments))
    )


async def test_read_file(tmp_path):
    (tmp_path / "a").write_bytes("一\r\n二\r\n三".encode())
    r = await invoke(tmp_path, "read_file", path="a", offset=2, limit=1)
    assert r.data["content"] == "2: 二\r\n" and r.truncated
    assert (await invoke(tmp_path, "read_file", path="a", offset=5)).data["content"] == ""
    (tmp_path / "empty").touch()
    assert (await invoke(tmp_path, "read_file", path="empty")).data["content"] == ""
    (tmp_path / "long").write_text("中" * 70000)
    r = await invoke(tmp_path, "read_file", path="long")
    assert r.truncated and len(r.data["content"].encode()) <= 65536
    (tmp_path / "lines").write_text("行\n" * 201)
    r = await invoke(tmp_path, "read_file", path="lines")
    assert r.truncated and len(r.data["content"].splitlines()) == 200


@pytest.mark.parametrize("kind", ["missing", "binary", "invalid", "directory"])
async def test_read_file_errors(tmp_path, kind):
    path = tmp_path / kind
    if kind == "binary":
        path.write_bytes(b"a\0b")
    elif kind == "invalid":
        path.write_bytes(b"\xff")
    elif kind == "directory":
        path.mkdir()
    result = await invoke(tmp_path, "read_file", path=str(path))
    assert result.status == "error"
    (tmp_path / "good").write_text("ok")
    assert (await invoke(tmp_path, "read_file", path="good")).status == "success"


async def test_write_file(tmp_path):
    r = await invoke(tmp_path, "write_file", path="nested/a", content="中文\r\n")
    target = tmp_path / "nested/a"
    assert r.status == "success" and target.read_bytes() == "中文\r\n".encode()
    assert r.data["bytes_written"] == len(target.read_bytes())
    target.chmod(0o640)
    link = tmp_path / "link"
    link.symlink_to(target)
    r = await invoke(tmp_path, "write_file", path="link", content="")
    assert r.status == "success" and link.is_symlink() and target.read_bytes() == b""
    assert target.stat().st_mode & 0o777 == 0o640
    for content in ["x" * (1024 * 1024 + 1), "a\0b"]:
        r = await invoke(tmp_path, "write_file", path="link", content=content)
        assert r.status == "error" and target.read_bytes() == b""


async def test_edit_file(tmp_path):
    path = tmp_path / "a"
    path.write_bytes("一\r\nport=8000\r\n".encode())
    r = await invoke(tmp_path, "edit_file", path="a", old_text="8000", new_text="8080")
    assert r.status == "success" and r.data["replacement_count"] == 1
    assert path.read_bytes() == "一\r\nport=8080\r\n".encode()
    for old, code in [
        ("", "invalid_arguments"),
        ("missing", "match_not_found"),
        ("0", "multiple_matches"),
    ]:
        before = path.read_bytes()
        r = await invoke(tmp_path, "edit_file", path="a", old_text=old, new_text="")
        assert r.error.code == code and path.read_bytes() == before
    assert (
        await invoke(tmp_path, "edit_file", path="a", old_text="port=8080", new_text="")
    ).status == "success"
    path.write_text("x" * (1024 * 1024 + 1))
    assert (
        await invoke(tmp_path, "edit_file", path="a", old_text="x", new_text="")
    ).error.code == "file_too_large"
    path.write_text("old")
    assert (
        await invoke(
            tmp_path, "edit_file", path="a", old_text="old", new_text="x" * (1024 * 1024 + 1)
        )
    ).error.code == "file_too_large"


def test_atomic_write_umask_and_failure(tmp_path):
    target = tmp_path / "a"
    frames = []
    previous = os.umask(0o027)
    try:
        atomic_write(target, b"old", frames.append)
    finally:
        os.umask(previous)
    assert target.stat().st_mode & 0o777 == 0o640

    def fail():
        raise ToolError("file_changed", "已变化")

    with pytest.raises(ToolError):
        atomic_write(target, b"new", frames.append, fail)
    assert target.read_bytes() == b"old"
    assert not list(tmp_path.glob(".mycode-*"))


def test_edit_file_changed_and_permission(tmp_path, monkeypatch):
    from mycode.tools import files

    target = tmp_path / "a"
    target.write_text("old")
    original = files.atomic_write

    def change(path, content, emit, before_commit):
        path.write_text("external")
        return original(path, content, emit, before_commit)

    monkeypatch.setattr(files, "atomic_write", change)
    with pytest.raises(ToolError, match="其他操作修改"):
        operate(
            "edit_file",
            {"path": "a", "old_text": "old", "new_text": "new"},
            ToolContext(tmp_path),
            lambda f: None,
        )
    assert target.read_text() == "external"


async def test_read_file_permission(tmp_path):
    path = tmp_path / "private"
    path.write_text("private")
    path.chmod(0)
    try:
        result = await invoke(tmp_path, "read_file", path="private")
        assert result.error.code == "permission_denied"
    finally:
        path.chmod(0o600)


async def test_edit_file_link_and_invalid(tmp_path):
    path = tmp_path / "original"
    path.write_text("old")
    link = tmp_path / "link"
    link.symlink_to(path)
    assert (
        await invoke(tmp_path, "edit_file", path="link", old_text="old", new_text="new")
    ).status == "success"
    assert link.is_symlink() and path.read_text() == "new"
    for raw in [b"\xff", b"a\0b"]:
        path.write_bytes(raw)
        assert (
            await invoke(tmp_path, "edit_file", path="link", old_text="a", new_text="new")
        ).error.code == "unsupported_file"
        assert path.read_bytes() == raw
