"""有界输出、工作进程协议及可取消的进程组清理。"""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from mycode.tools.base import ToolError, ToolOutput

OUTPUT_LIMIT = 64 * 1024
PROTOCOL_LIMIT = 2 * 1024 * 1024


async def _terminate(process):
    def send(sig):
        try:
            os.killpg(process.pid, sig)
            return True
        except ProcessLookupError:
            return False

    if not send(signal.SIGTERM):
        await process.wait()
        return
    # shell 已退出时，仍需处理持有管道的子进程；给内核一次回收机会。
    gone = False
    try:
        async with asyncio.timeout(1):
            await process.wait()
            while True:
                await asyncio.sleep(0.02)
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    gone = True
                    break
                except PermissionError:
                    # macOS 的已退出孤儿组可短暂返回 EPERM；仅在管道均 EOF 后接受。
                    if process.stdout.at_eof() and process.stderr.at_eof():
                        gone = True
                        break
                    raise
    except TimeoutError:
        pass
    if not gone:
        send(signal.SIGKILL)
    await process.wait()


async def _protected_cleanup(process):
    task = asyncio.create_task(_terminate(process))
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()


async def _spawn(*args, **kwargs):
    # 创建期间也可能取消，先拿到进程句柄再清理，避免遗留无主子进程。
    task = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        if not task.cancelled() and task.exception() is None:
            process = task.result()

            async def discard(stream):
                while await stream.read(8192):
                    pass

            drains = [asyncio.create_task(discard(s)) for s in (process.stdout, process.stderr)]
            await _protected_cleanup(process)
            await asyncio.gather(*drains)
        raise


async def run_command(command, cwd):
    process = await _spawn(
        "/bin/sh",
        "-c",
        command,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    buffers = [bytearray(), bytearray()]
    cut = [False, False]

    async def drain(stream, index):
        while chunk := await stream.read(8192):
            remaining = OUTPUT_LIMIT - len(buffers[index])
            buffers[index].extend(chunk[:remaining])
            cut[index] |= len(chunk) > remaining

    tasks = [
        asyncio.create_task(drain(process.stdout, 0)),
        asyncio.create_task(drain(process.stderr, 1)),
    ]

    def decoded(index):
        text = bytes(buffers[index]).decode("utf-8", errors="replace")
        encoded = text.encode("utf-8")
        if len(encoded) > OUTPUT_LIMIT:
            cut[index] = True
        return encoded[:OUTPUT_LIMIT].decode("utf-8", errors="ignore")

    def data():
        return {
            "cwd": str(cwd),
            "exit_code": process.returncode,
            "stdout": decoded(0),
            "stderr": decoded(1),
        }

    try:
        await asyncio.gather(*tasks)
        await process.wait()
    except BaseException as exc:
        await _protected_cleanup(process)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if isinstance(exc, asyncio.CancelledError):
            exc.result_data = data()
            exc.truncated = any(cut)
        raise
    return ToolOutput(data(), any(cut))


async def run_worker(name, arguments, context):
    # 包通过安装环境导入；不把文件目标目录设为 Python 模块搜索目录。
    process = await _spawn(
        sys.executable,
        "-m",
        "mycode.tools.worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    registered = set()
    result = None
    total = 0

    async def frames():
        nonlocal result, total
        # 手工切帧，避免 StreamReader 默认 64 KiB 行长限制。
        pending = bytearray()
        while chunk := await process.stdout.read(8192):
            total += len(chunk)
            if total > PROTOCOL_LIMIT:
                raise ToolError("execution_error", "工作进程输出超过协议上限。")
            pending.extend(chunk)
            while b"\n" in pending:
                raw, _, tail = pending.partition(b"\n")
                pending[:] = tail
                frame = json.loads(raw)
                kind = frame.get("type")
                if kind == "temporary":
                    path = Path(frame["path"])
                    if not path.name.startswith(".mycode-") or not path.is_absolute():
                        raise ValueError("非法临时文件帧")
                    registered.add(path)
                elif kind == "released":
                    registered.discard(Path(frame["path"]))
                elif kind == "result" and result is None:
                    result = frame
                else:
                    raise ValueError("非法结果帧")
        if pending:
            raise ValueError("未完成的工作进程帧")

    async def stderr():
        # 持续排空，诊断不直接进入界面或模型历史。
        while await process.stderr.read(8192):
            pass

    tasks = [asyncio.create_task(frames()), asyncio.create_task(stderr())]
    try:
        request = {
            "name": name,
            "arguments": arguments,
            "working_directory": str(context.working_directory),
        }
        process.stdin.write(json.dumps(request, ensure_ascii=False).encode() + b"\n")
        await process.stdin.drain()
        process.stdin.close()
        await asyncio.gather(*tasks)
        await process.wait()
        if process.returncode or result is None:
            raise ToolError("execution_error", "工作进程未完整返回结果。")
        if result.get("error"):
            error = result["error"]
            raise ToolError(
                error["code"],
                error["message"],
                result.get("data"),
                truncated=result.get("truncated", False),
            )
        return ToolOutput(result["data"], result.get("truncated", False))
    except BaseException as exc:
        await _protected_cleanup(process)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if isinstance(exc, asyncio.CancelledError):
            if result is not None:
                exc.result_frame = result
            elif name in {"write_file", "edit_file"}:
                exc.result_data = {"outcome_unknown": True}
        if isinstance(exc, (ValueError, KeyError, TypeError)):
            raise ToolError("execution_error", "工作进程返回格式错误。") from None
        raise
    finally:
        for path in registered:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
