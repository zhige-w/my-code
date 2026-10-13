import asyncio
import json
import os
import shlex
import sys

import pytest

from mycode.models import ToolCall
from mycode.tools import create_tool_registry
from mycode.tools.base import ToolContext, ToolError
from mycode.tools.executor import ToolExecutor
from mycode.tools.process import run_command, run_worker


def py(code):
    return shlex.quote(sys.executable) + " -c " + shlex.quote(code)


async def test_command_output(tmp_path):
    result = await run_command(py("import sys; print(input())"), tmp_path)
    assert result.data["exit_code"] != 0 and "EOFError" in result.data["stderr"]
    result = await run_command(
        py('import sys; print("中"*50000); print("e"*70000,file=sys.stderr)'), tmp_path
    )
    assert result.truncated
    assert len(result.data["stdout"].encode()) <= 65536
    assert len(result.data["stderr"].encode()) <= 65536
    assert (await run_command("pwd", tmp_path)).data["stdout"].strip() == str(tmp_path.resolve())
    result = await run_command(py('import os; os.write(1,b"\\xff")'), tmp_path)
    assert result.data["stdout"] == "�"
    result = await run_command(py('import os; os.write(1, b"\\xff"*65535)'), tmp_path)
    assert result.truncated and len(result.data["stdout"].encode()) <= 65536


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def test_process_cleanup_timeout_and_cancel(tmp_path):
    runner = ToolExecutor(create_tool_registry(), ToolContext(tmp_path))
    command = py(
        'import os,time,signal; open("pid","w").write(str(os.getpid()));'
        'signal.signal(signal.SIGTERM,signal.SIG_IGN); print("started",flush=True);'
        'time.sleep(20); open("late","w").write("bad")'
    )
    r = await runner.execute(
        ToolCall("id", "execute_command", json.dumps({"command": command, "timeout": 0.3}))
    )
    assert r.error.code == "timeout" and "started" in r.data["stdout"]
    pid = int((tmp_path / "pid").read_text())
    assert not alive(pid) and not (tmp_path / "late").exists()
    task = asyncio.create_task(run_command(command, tmp_path))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not alive(int((tmp_path / "pid").read_text()))
    assert (await run_command("printf recovered", tmp_path)).data["stdout"] == "recovered"


async def test_process_cleanup_shell_exits(tmp_path):
    command = py("import time; time.sleep(20)") + " & echo $! > child; exit 0"
    task = asyncio.create_task(run_command(command, tmp_path))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # macOS 正常回收；只探测本测试创建的进程。
    await asyncio.sleep(0.05)
    child = int((tmp_path / "child").read_text())
    with pytest.raises(ProcessLookupError):
        os.getpgid(child)


async def test_worker_protocol(tmp_path):
    result = await run_worker("write_file", {"path": "a", "content": "x"}, ToolContext(tmp_path))
    assert result.data["bytes_written"] == 1
    with pytest.raises(ToolError):
        await run_worker("unknown", {}, ToolContext(tmp_path))
    assert not list(tmp_path.glob(".mycode-*"))


@pytest.mark.parametrize(
    "payload",
    ["not-json\n", '{"type":"result"}\n', "oversize"],
    ids=["malformed", "missing-data", "oversize"],
)
async def test_worker_protocol_invalid(tmp_path, monkeypatch, payload):
    from mycode.tools import process

    original = asyncio.create_subprocess_exec

    async def fake(*args, **kwargs):
        # 真实子进程制造不完整或超限协议；不绕过进程回收逻辑。
        return await original(
            sys.executable,
            "-c",
            "import sys; sys.stdin.readline(); sys.stdout.write("
            + ('"x"*(2*1024*1024+1)' if payload == "oversize" else repr(payload))
            + ")",
            **kwargs,
        )

    monkeypatch.setattr(process.asyncio, "create_subprocess_exec", fake)
    with pytest.raises(ToolError):
        await run_worker("read_file", {"path": "a"}, ToolContext(tmp_path))


async def test_atomic_write_worker_cancel_cleanup(tmp_path, monkeypatch):
    from mycode.tools import process

    original = asyncio.create_subprocess_exec
    temp = tmp_path / ".mycode-controlled"
    target = tmp_path / "target"
    code = (
        "import json,time,pathlib,sys; sys.stdin.readline(); "
        f"p=pathlib.Path({str(temp)!r}); "
        'print(json.dumps({"type":"temporary","path":str(p)}),flush=True); '
        'p.write_text("pending"); '
        f'pathlib.Path({str(target)!r}).write_text("committed"); '
        "time.sleep(20)"
    )

    async def fake(*args, **kwargs):
        return await original(sys.executable, "-c", code, **kwargs)

    monkeypatch.setattr(process.asyncio, "create_subprocess_exec", fake)
    runner = ToolExecutor(create_tool_registry(), ToolContext(tmp_path))
    task = asyncio.create_task(
        runner.execute(ToolCall("a", "write_file", '{"path":"target","content":"x"}'))
    )
    for _ in range(100):
        if target.exists():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.result_data["outcome_unknown"]
    assert not temp.exists() and target.read_text() == "committed"


async def test_process_cleanup_spawn_cancel(tmp_path, monkeypatch):
    from mycode.tools import process

    original = asyncio.create_subprocess_exec
    started = asyncio.Event()
    processes = []

    async def delayed(*args, **kwargs):
        p = await original(*args, **kwargs)
        processes.append(p)
        started.set()
        await asyncio.sleep(0.03)
        return p

    monkeypatch.setattr(process.asyncio, "create_subprocess_exec", delayed)
    task = asyncio.create_task(run_command("sleep 20", tmp_path))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None
