"""在专用 tmux 中运行真实 MyCode；证据仅包含受控内容和脱敏画面。"""

import argparse
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from mycode.config import load_config
from mycode.errors import SecretRedactor

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "specs/tool-system/evidence"


def run(only=None, selected=None):
    configs = load_config(ROOT / "mycode.yaml")
    redactor = SecretRedactor(c.api_key for c in configs)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    output = {
        "kind": "tmux 真实 MyCode 与真实 API",
        "results": {},
        "providers": [
            {"name": c.name, "protocol": c.protocol, "model": c.model, "thinking": c.thinking}
            for c in configs
        ],
    }
    report_path = EVIDENCE / "tmux-tools.json"
    if (only or selected) and report_path.exists():
        output["results"] = json.loads(report_path.read_text())["results"]

    def save():
        report_path.write_text(redactor.redact(json.dumps(output, ensure_ascii=False, indent=2)))

    def record(key, status, detail):
        output["results"][key] = {"status": status, "detail": detail}
        save()
        print(redactor.redact(f"{key}: {status}；{detail}"), flush=True)

    with tempfile.TemporaryDirectory(prefix="mycode-tools-") as temp:
        socket = str(Path(temp) / "tmux.sock")

        def tmux(*args, check=True):
            return subprocess.run(
                ["tmux", "-S", socket, *args], check=check, capture_output=True, text=True
            ).stdout

        def screen():
            return redactor.redact(tmux("capture-pane", "-p", "-t", "tools"))

        def keys(*values):
            tmux("send-keys", "-t", "tools", *values)

        def literal(value):
            tmux("send-keys", "-t", "tools", "-l", value)

        def wait(predicate, description, timeout=180):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                value = screen()
                if predicate(value):
                    return value
                time.sleep(0.03)
            raise AssertionError(description)

        def snapshot(label, value):
            (EVIDENCE / f"tmux-{label}.txt").write_text(value)

        def current(value):
            lines = value.splitlines()
            starts = [i for i, line in enumerate(lines) if line.strip() == "你"]
            return "\n".join(lines[starts[-1] :]) if starts else value

        def question(prompt, label, interrupt=None):
            literal(prompt)
            keys("Enter")
            wait(lambda s: "生成中" in s.splitlines()[0], "请求未开始", 10)
            frames = []
            seen = set()
            tools = {}
            cancelled = False
            active = False
            until = time.monotonic() + 240
            while time.monotonic() < until:
                value = screen()
                header = value.splitlines()[0]
                compact = re.sub(r"\s+", "", current(value))
                active |= re.sub(r"\s+", "", prompt[:20]) in compact
                for name, status in re.findall(r"工具 (\w+) · (成功|失败|取消)", current(value)):
                    if active and not cancelled:
                        tools[name] = status
                if value not in seen:
                    seen.add(value)
                    frames.append(value)
                    if len(frames) > 12:
                        del frames[5]
                if interrupt and not cancelled and interrupt(value):
                    keys("Tab", "PPage")
                    tmux("resize-window", "-t", "tools", "-x", "100", "-y", "40")
                    keys("C-c")
                    cancelled = True
                if "空闲" in header:
                    snapshot(label, value)
                    (EVIDENCE / f"tmux-{label}-frames.json").write_text(
                        json.dumps(frames, ensure_ascii=False)
                    )
                    if "失败，本轮" in value or "失败，工具记录" in value:
                        raise AssertionError("请求失败，见脱敏终端画面")
                    return current(value), tools, cancelled
                time.sleep(0.03)
            snapshot(label + "-timeout", screen())
            raise AssertionError("请求超过240秒")

        try:
            for protocol in ("openai", "anthropic"):
                if only and only != protocol:
                    continue
                config = next((c for c in configs if c.protocol == protocol), None)
                if config is None:
                    record(protocol, "未验收", "缺少该协议配置")
                    continue
                work = Path(temp) / protocol
                work.mkdir()
                (work / "demo.txt").write_text("port=8000\n")
                (work / "src").mkdir()
                (work / "src/sample.py").write_text("def hello():\n    pass\n")
                command = shlex.join(
                    [
                        str(ROOT / ".venv/bin/MyCode"),
                        "--config",
                        str(ROOT / "mycode.yaml"),
                        "--provider",
                        config.name,
                    ]
                )
                tmux(
                    "new-session",
                    "-d",
                    "-s",
                    "tools",
                    "-x",
                    "140",
                    "-y",
                    "55",
                    "-c",
                    str(work),
                    "/bin/zsh -f",
                )
                literal(command)
                keys("Enter")
                wait(lambda s: "MyCode ·" in s and "空闲" in s.splitlines()[0], "启动失败", 15)

                def check(case, operation):
                    if selected and case.split("-")[0] not in selected:
                        return
                    try:
                        detail = operation()
                        record(protocol + "-" + case, "通过", detail)
                    except Exception as exc:
                        snapshot(protocol + "-" + case + "-error", screen())
                        record(protocol + "-" + case, "失败", str(exc))

                def read_recall():
                    value, tools, _ = question(
                        "请调用 read_file 读取 demo.txt，按结果说明端口值，最终回复不超过30字。",
                        protocol + "-read",
                    )
                    assert tools.get("read_file") == "成功", "未观察到读取成功"
                    assert "8000" in value, "未显示端口"
                    recall, tools, _ = question(
                        "上一轮读取到的端口是多少？禁止再次调用工具，回复不超过20字。",
                        protocol + "-recall",
                    )
                    assert not tools, "引用时再次执行了工具"
                    assert "8000" in recall, "未正确引用端口"
                    return "读取成功、总结及后续历史引用；画面已保存"

                check("C47" if protocol == "openai" else "C48", read_recall)
                operations = [
                    (
                        "write_file",
                        "请调用 write_file 创建 new.txt，内容为 first。总结不超过20字。",
                        lambda: (work / "new.txt").read_text() == "first",
                    ),
                    (
                        "overwrite",
                        "请调用 write_file 覆盖 new.txt，内容为 second。总结不超过20字。",
                        lambda: (work / "new.txt").read_text() == "second",
                    ),
                    (
                        "edit_file",
                        "请调用 edit_file 将 demo.txt 中唯一的 port=8000 替换为 port=8080。"
                        "总结不超过20字。",
                        lambda: (work / "demo.txt").read_text() == "port=8080\n",
                    ),
                    (
                        "execute_command",
                        "请调用 execute_command 执行 printf COMMAND_OK。总结不超过20字。",
                        lambda: True,
                    ),
                    (
                        "find_files",
                        "请调用 find_files 在当前目录按 **/*.py 查找文件。总结不超过30字。",
                        lambda: True,
                    ),
                    (
                        "search_code",
                        "请调用 search_code 在当前目录搜索 def hello。总结不超过30字。",
                        lambda: True,
                    ),
                ]
                for name, prompt, disk in operations:

                    def op(name=name, prompt=prompt, disk=disk):
                        value, tools, _ = question(prompt, protocol + "-" + name)
                        expected = "write_file" if name == "overwrite" else name
                        assert tools.get(expected) == "成功", "未观察到目标工具成功"
                        assert disk(), "磁盘内容不一致"
                        marker = {
                            "execute_command": "COMMAND_OK",
                            "find_files": "sample.py",
                            "search_code": "hello",
                        }.get(name)
                        assert not marker or marker in value, "工具结果不符合预期"
                        return "真实工具调用、结果与总结通过；磁盘/输出核对成功"

                    check("C49-" + name, op)

                def batch():
                    value, tools, _ = question(
                        "必须在首次响应同一批给出两个工具调用：先 write_file 写 batch.txt 内"
                        "容为青竹742，再 read_file 读 batch.txt。之后总结不超过30字。",
                        protocol + "-batch",
                    )
                    assert tools.get("write_file") == "成功" and tools.get("read_file") == "成功"
                    assert (work / "batch.txt").read_text() == "青竹742"
                    assert value.find("工具 write_file") < value.find("工具 read_file"), (
                        "顺序不正确或未同屏观察到两项"
                    )
                    return "同一响应写后读，顺序与内容正确，生成总结"

                check("C50", batch)

                def failure():
                    _, tools, _ = question(
                        "请调用 read_file 读取 missing-intentionally.txt，"
                        "然后根据失败结果说明原因，不要重试，最终回复30字内。",
                        protocol + "-failure",
                    )
                    assert tools.get("read_file") == "失败"
                    _, tools, _ = question(
                        "请调用 read_file 读取 demo.txt，回复30字内。", protocol + "-recover"
                    )
                    assert tools.get("read_file") == "成功"
                    return "文件不存在产生失败结果和总结，下一次读取成功"

                check("C51", failure)

                def cancel_command():
                    prompt = (
                        '请只调用 execute_command，timeout=60，command="echo'
                        " $$ > cancel.pid; sleep 20; touch late-marker"
                        '"，之后总结。'
                    )
                    _, _, cancelled = question(
                        prompt,
                        protocol + "-cancel-command",
                        lambda s: (
                            "工具执行中" in s.splitlines()[0] and (work / "cancel.pid").exists()
                        ),
                    )
                    assert cancelled, "未在工具阶段取消"
                    assert not (work / "late-marker").exists()
                    pid = int((work / "cancel.pid").read_text())
                    try:
                        os.getpgid(pid)
                    except ProcessLookupError:
                        pass
                    else:
                        raise AssertionError("命令进程仍存在")
                    question("请只回复取消后继续成功，不要调用工具。", protocol + "-after-cancel")
                    return "执行时滚动、调整尺寸、Ctrl+C，进程停止，后续提问成功"

                check("C52", cancel_command)

                def cancel_summary():
                    prompt = (
                        "请调用 write_file 写 summary.txt 内容为 SUMMARY_SAVED，"
                        "然后在最终回复详细列出100条Python建议，每条30字以上。"
                    )
                    _, _, cancelled = question(
                        prompt,
                        protocol + "-cancel-summary",
                        lambda s: "总结中" in s.splitlines()[0],
                    )
                    assert cancelled and (work / "summary.txt").read_text() == "SUMMARY_SAVED"
                    recall, tools, _ = question(
                        "刚才写入 summary.txt 的内容是什么？禁止调用工具，回复20字内。",
                        protocol + "-summary-recall",
                    )
                    assert "SUMMARY_SAVED" in recall and not tools, "未正确引用已保存工具结果"
                    keys("C-d")
                    wait(lambda s: "MyCode ·" not in s, "终端未退出", 10)
                    return "总结阶段取消，文件保留、后续引用成功，Ctrl+D 恢复终端"

                # 最后退出前先验证超时。
                def timeout():
                    _, tools, _ = question(
                        '请只调用 execute_command，command="sleep 5"，timeout=1。'
                        "根据结果总结是否超时，不要重试，回复30字内。",
                        protocol + "-timeout",
                    )
                    assert tools.get("execute_command") == "失败", "未观察到超时失败结果"
                    question("请只回复超时后继续成功，不调用工具。", protocol + "-after-timeout")
                    return "真实命令超时返回失败结果、模型总结并继续对话"

                check("C54", timeout)
                check("C53", cancel_summary)
                tmux("kill-session", "-t", "tools", check=False)
        finally:
            tmux("kill-server", check=False)
            save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="工具系统真实 API tmux 验收")
    parser.add_argument("--protocol", choices=["openai", "anthropic"])
    parser.add_argument("--case", action="append", help="仅重跑指定检查编号，可重复指定")
    args = parser.parse_args()
    run(args.protocol, args.case)
