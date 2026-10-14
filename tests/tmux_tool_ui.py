"""工具卡片的真实 tmux/模型验收，使用专用临时文件。"""

import json
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from mycode.config import load_config
from mycode.errors import SecretRedactor

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "specs/tool-call-tui/evidence"


def run():
    configs = load_config(ROOT / "mycode.yaml")
    config = next(c for c in configs if c.protocol == "openai")
    redactor = SecretRedactor(c.api_key for c in configs)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    report = {
        "provider": config.name,
        "protocol": config.protocol,
        "model": config.model,
        "results": {},
    }
    with tempfile.TemporaryDirectory(prefix="mycode-tool-ui-") as directory:
        work = Path(directory)
        (work / "sample.txt").write_text("DETAIL_ONLY_7fe\nsecond line\n")
        socket = str(work / "tmux.sock")

        def tmux(*args, check=True):
            return subprocess.run(
                ["tmux", "-S", socket, *args], capture_output=True, text=True, check=check
            ).stdout

        def screen():
            return redactor.redact(tmux("capture-pane", "-p", "-t", "tool-ui"))

        def save(label):
            value = screen()
            (EVIDENCE / f"{label}.txt").write_text(value)
            return value

        def keys(*args):
            tmux("send-keys", "-t", "tool-ui", *args)

        def wait(predicate, timeout=180):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                value = screen()
                if predicate(value):
                    return value
                time.sleep(0.03)
            save("failure")
            raise AssertionError("等待终端状态超时")

        try:
            tmux(
                "new-session",
                "-d",
                "-s",
                "tool-ui",
                "-x",
                "120",
                "-y",
                "40",
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
            tmux("send-keys", "-t", "tool-ui", "-l", command)
            keys("Enter")
            wait(lambda s: "MyCode ·" in s and "空闲" in s.splitlines()[0], 15)
            prompt = (
                "只用 read_file 读取 sample.txt，limit=2。完成后仅回复“已读取”，不要引用文件正文。"
            )
            tmux("send-keys", "-t", "tool-ui", "-l", prompt)
            keys("Enter")
            wait(lambda s: "生成中" in s.splitlines()[0], 15)
            frames = []
            end = time.monotonic() + 180
            while time.monotonic() < end:
                value = screen()
                if not frames or frames[-1] != value:
                    frames.append(value)
                    if len(frames) > 12:
                        del frames[5]
                if "空闲" in value.splitlines()[0]:
                    break
                time.sleep(0.03)
            else:
                raise AssertionError("真实模型请求超时")
            (EVIDENCE / "stream-frames.json").write_text(json.dumps(frames, ensure_ascii=False))
            before = save("collapsed")
            assert "工具 read_file · 成功" in before
            assert "path: sample.txt" in before and "limit: 2" in before
            assert "详细结果" in before and "DETAIL_ONLY_7fe" not in before
            assert "call_id" not in before and "truncated" not in before
            assert "已读取" in before
            report["results"]["名称参数状态及默认折叠"] = "通过"
            # 从输入区遍历焦点；工具结果标题使用原生 Collapsible Enter 行为。
            for _ in range(8):
                keys("Tab", "Enter")
                time.sleep(0.1)
                if "DETAIL_ONLY_7fe" in screen():
                    break
            else:
                raise AssertionError("无法通过键盘展开详细结果")
            expanded = save("expanded")
            assert "second line" in expanded
            report["results"]["Enter展开真实文件结果"] = "通过"
            keys("Space")
            wait(lambda s: "DETAIL_ONLY_7fe" not in s, 5)
            save("recollapsed")
            report["results"]["Space收起结果"] = "通过"
            keys("C-d")
            wait(lambda s: "MyCode ·" not in s, 10)
            report["results"]["退出恢复终端"] = "通过"
        finally:
            tmux("kill-server", check=False)
            (EVIDENCE / "tmux.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    run()
