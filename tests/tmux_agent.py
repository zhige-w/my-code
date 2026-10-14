"""专用 tmux 中的真实模型验收；只保存受控内容和脱敏画面。"""

import argparse
import json
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from mycode.config import load_config
from mycode.errors import SecretRedactor

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "specs/agent-loop/evidence"


def run(protocol=None):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    report = {"kind": "真实 MyCode、真实 API、真实 tmux", "results": {}}
    redactor = SecretRedactor()

    def save():
        (EVIDENCE / "tmux-agent.json").write_text(
            redactor.redact(json.dumps(report, ensure_ascii=False, indent=2))
        )

    if not shutil.which("tmux"):
        report["blocked"] = "缺少 tmux"
        save()
        return 2
    try:
        configs = load_config(ROOT / "mycode.yaml")
    except Exception:
        report["blocked"] = "缺少有效真实配置"
        save()
        return 2
    redactor = SecretRedactor(c.api_key for c in configs)
    report["providers"] = [
        {"name": c.name, "protocol": c.protocol, "model": c.model, "thinking": c.thinking}
        for c in configs
    ]
    save()
    with tempfile.TemporaryDirectory(prefix="mycode-agent-") as directory:
        base = Path(directory)
        socket = str(base / "tmux.sock")

        def tmux(*args, check=True):
            return subprocess.run(
                ["tmux", "-S", socket, *args], check=check, capture_output=True, text=True
            ).stdout

        def screen(history=False):
            args = ["capture-pane", "-p", "-t", "agent"]
            if history:
                args += ["-S", "-3000"]
            return redactor.redact(tmux(*args))

        def send(text):
            tmux("send-keys", "-t", "agent", "-l", text)
            tmux("send-keys", "-t", "agent", "Enter")

        def wait(predicate, timeout=240):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                value = screen()
                if predicate(value):
                    return value
                time.sleep(0.1)
            raise AssertionError("等待终端状态超时")

        def snapshot(label):
            target = EVIDENCE / f"tmux-{label}.txt"
            target.write_text(screen(True))
            return target.name

        def ask(prompt, label, cancel=False):
            send(prompt)
            wait(lambda s: "生成中" in s.splitlines()[0], 15)
            frames = []
            interrupted = False
            until = time.monotonic() + 240
            while time.monotonic() < until:
                value = screen()
                if not frames or frames[-1] != value:
                    frames.append(value)
                    if len(frames) > 20:
                        del frames[3]
                if cancel and not interrupted and (work / "pid").exists():
                    tmux("send-keys", "-t", "agent", "Tab", "PPage", "BTab")
                    tmux("send-keys", "-t", "agent", "-l", "ACTIVE_DRAFT")
                    wait(lambda s: "ACTIVE_DRAFT" in s, 5)
                    tmux("send-keys", "-t", "agent", "C-u")
                    snapshot(label + "-active-input")
                    tmux("send-keys", "-t", "agent", "C-c")
                    interrupted = True
                if "空闲" in value.splitlines()[0]:
                    if cancel:
                        tmux("send-keys", "-t", "agent", "Tab", "End", "BTab")
                        wait(lambda s: "已取消" in s, 5)
                    (EVIDENCE / f"tmux-{label}-frames.json").write_text(
                        json.dumps(frames, ensure_ascii=False)
                    )
                    artifact = snapshot(label)
                    if cancel:
                        assert interrupted and "已取消" in screen(True), "没有观察到取消"
                    else:
                        assert "已完成" in screen(True), "没有观察到最终回复"
                        assert "失败，" not in value and "失败，本轮" not in value, "请求失败"
                    return artifact, frames
                time.sleep(0.1)
            snapshot(label + "-timeout")
            raise AssertionError("真实请求超过 240 秒")

        for selected in ("openai", "anthropic"):
            if protocol and protocol != selected:
                continue
            config = next((c for c in configs if c.protocol == selected), None)
            if config is None or config.api_key.startswith("YOUR_"):
                report["results"][selected] = {"status": "未验收", "detail": "缺少真实协议配置"}
                save()
                continue
            work = base / selected
            work.mkdir()
            target = f"result-{uuid4().hex[:8]}.txt"
            value = "青竹-" + uuid4().hex[:12]
            (work / "task.txt").write_text(f"请创建 {target}，内容精确为 {value}，再读取核对。")
            (work / "demo.txt").write_text("port=8000\n")
            cases = {}
            report["results"][selected] = cases

            def record(case, operation):
                try:
                    detail = operation()
                    cases[case] = {"status": "通过", "detail": detail}
                except Exception as exc:
                    cases[case] = {"status": "失败", "detail": redactor.redact(str(exc))}
                    try:
                        snapshot(selected + "-" + case + "-error")
                    except Exception:
                        pass
                save()
                print(f"{selected}/{case}: {cases[case]['status']}", flush=True)

            try:
                tmux(
                    "new-session",
                    "-d",
                    "-s",
                    "agent",
                    "-x",
                    "180",
                    "-y",
                    "60",
                    "-c",
                    str(work),
                    "/bin/zsh -f",
                )
                command = shlex.join(
                    [
                        str(ROOT / ".venv/bin/MyCode"),
                        "--config",
                        str(ROOT / "mycode.yaml"),
                        "--provider",
                        config.name,
                    ]
                )
                send(command)
                wait(lambda s: "MyCode ·" in s and "空闲" in s.splitlines()[0], 20)

                def ordinary():
                    artifact, frames = ask("请只用中文回答：1加1等于几？", selected + "-ordinary")
                    assert any("2" in f or "二" in f for f in frames)
                    return {"evidence": artifact, "stream_frames": len(frames)}

                record("ordinary", ordinary)

                def multibatch():
                    artifact, frames = ask(
                        "读取 task.txt，根据其中要求创建目标文件，再读取核对并说明结果。"
                        "请实际调用工具，回复不超过60字。",
                        selected + "-multibatch",
                    )
                    assert (work / target).read_text().strip() == value
                    capture = screen(True)
                    rounds = set(re.findall(r"第\s+(\d+)\s+轮", capture))
                    assert len(rounds) >= 3, "未观察到至少两批工具及最终模型回复"
                    assert "read_file" in capture and "write_file" in capture
                    return {
                        "evidence": artifact,
                        "file_match": True,
                        "rounds": sorted(rounds),
                        "stream_frames": len(frames),
                    }

                record("multibatch", multibatch)

                def plan():
                    before = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
                    send("/plan")
                    wait(lambda s: "规划" in s.splitlines()[0], 5)
                    artifact, _ = ask(
                        "读取 demo.txt，规划将端口改为8123并读取验证，先只给计划。"
                        "计划要明确使用工具执行，最终计划不超过100字。",
                        selected + "-plan",
                    )
                    after = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
                    assert before == after, "规划产生文件副作用"
                    assert "规划" in screen().splitlines()[0]
                    return {"evidence": artifact, "files_unchanged": True}

                record("plan", plan)

                def execute():
                    artifact, _ = ask("/do", selected + "-do")
                    assert (work / "demo.txt").read_text().strip() == "port=8123"
                    assert "执行" in screen().splitlines()[0]
                    return {"evidence": artifact, "file_match": True}

                record("do", execute)

                def cancellation():
                    artifact, _ = ask(
                        "只调用 execute_command 执行这个命令：echo $$ > pid; sleep 30; touch late。"
                        "等待命令结束再回复。",
                        selected + "-cancel",
                        True,
                    )
                    assert not (work / "late").exists()
                    pid = int((work / "pid").read_text().strip())
                    status = subprocess.run(["kill", "-0", str(pid)], capture_output=True)
                    assert status.returncode != 0, "命令进程仍存活"
                    recovery, _ = ask("取消后继续：请只回答恢复成功。", selected + "-recovery")
                    return {"evidence": artifact, "recovery": recovery, "process_stopped": True}

                record("cancel_recovery", cancellation)

                def interaction():
                    tmux("send-keys", "-t", "agent", "Tab", "PPage")
                    tmux("resize-window", "-t", "agent", "-x", "120", "-y", "40")
                    tmux("send-keys", "-t", "agent", "Tab")
                    tmux("send-keys", "-t", "agent", "-l", "下一条草稿")
                    artifact = snapshot(selected + "-interaction")
                    tmux("send-keys", "-t", "agent", "C-d")
                    wait(lambda s: "MyCode ·" not in s.splitlines()[0], 10)
                    return {"evidence": artifact, "resize_scroll_input_exit": True}

                record("interaction_exit", interaction)
            except Exception as exc:
                cases["startup"] = {"status": "失败", "detail": redactor.redact(str(exc))}
                save()
            finally:
                tmux("kill-session", "-t", "agent", check=False)
        tmux("kill-server", check=False)
    report["limitations"] = ["仅声明实际配置模型；未验收原生 Claude 或 Windows。"]
    save()
    return (
        1
        if any(
            c.get("status") == "失败"
            for cases in report["results"].values()
            for c in cases.values()
            if isinstance(c, dict)
        )
        else 0
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="真实 Agent Loop tmux 验收")
    parser.add_argument("--protocol", choices=["openai", "anthropic"])
    args = parser.parse_args()
    raise SystemExit(run(args.protocol))
