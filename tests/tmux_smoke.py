"""tmux 本地模拟 SSE 冒烟检查；不能替代真实 API 验收。"""

import json
import shlex
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml
from conftest import anthropic_parts, openai_parts

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "specs/interactive-chat/evidence"


def run() -> None:
    records = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            records.append({"path": self.path, "body": body})
            prompt = body["messages"][-1]["content"]
            if "长回复" in prompt:
                texts = tuple(f"第{i:02d}行 中文增量\n" for i in range(1, 81))
            elif "口令" in prompt:
                previous = json.dumps(body["messages"][:-1], ensure_ascii=False)
                texts = (
                    ("已经记住口令。",)
                    if "记住" in prompt
                    else ("口令是青竹742。" if "青竹742" in previous else "本会话没有口令。",)
                )
            else:
                texts = (
                    "第一段中文，",
                    "第二段增量，",
                    "第三段内容，",
                    "第四段说明，",
                    "第五段结束。",
                )
            if self.path.endswith("chat/completions"):
                parts = openai_parts(texts)
            else:
                parts = anthropic_parts(thinking=bool(body.get("thinking")))
                # 替换正文增量，保留完整的 Claude 事件及签名结构。
                start = next(i for i, p in enumerate(parts) if b'"type": "text_delta"' in p)
                index = 2 if body.get("thinking") else 0
                deltas = []
                for text in texts:
                    event = {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": text},
                    }
                    encoded = json.dumps(event, ensure_ascii=False)
                    deltas.append(f"event: content_block_delta\ndata: {encoded}\n\n".encode())
                parts = parts[:start] + deltas + parts[start + 2 :]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for part in parts:
                    self.wfile.write(part)
                    self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    EVIDENCE.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mycode-tmux-") as temp:
        directory = Path(temp)
        socket = directory / "tmux.sock"
        port = server.server_address[1]
        config = directory / "mock.yaml"
        config.write_text(
            yaml.safe_dump(
                [
                    {
                        "name": "mock-openai",
                        "protocol": "openai",
                        "model": "mock-model",
                        "api_key": "fake-secret",
                        "base_url": f"http://127.0.0.1:{port}/v1",
                    },
                    {
                        "name": "mock-claude",
                        "protocol": "anthropic",
                        "model": "mock-model",
                        "api_key": "fake-secret",
                        "base_url": f"http://127.0.0.1:{port}",
                        "thinking": True,
                    },
                ]
            )
        )
        config.chmod(0o600)

        def tmux(*args, check=True):
            return subprocess.run(
                ["tmux", "-S", str(socket), *args], check=check, capture_output=True, text=True
            ).stdout

        def keys(*values):
            if values == ("Escape", "Enter"):
                tmux("send-keys", "-t", "mycode-e2e", *values)
            else:
                for value in values:
                    tmux("send-keys", "-t", "mycode-e2e", value)
                    # 焦点切换是异步事件，等待上一按键分发后再发送下一键。
                    time.sleep(0.08)

        def literal(value):
            tmux("send-keys", "-t", "mycode-e2e", "-l", value)

        def capture():
            return tmux("capture-pane", "-p", "-t", "mycode-e2e")

        def wait_for(predicate, description, timeout=10):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                screen = capture()
                if predicate(screen):
                    return screen
                time.sleep(0.05)
            (EVIDENCE / "tmux-failure.txt").write_text(capture())
            raise AssertionError(description)

        def start(name):
            command = " ".join(
                shlex.quote(v)
                for v in (
                    str(ROOT / ".venv/bin/MyCode"),
                    "--config",
                    str(config),
                    "--provider",
                    name,
                )
            )
            literal(command)
            keys("Enter")
            wait_for(lambda s: f"MyCode · {name}" in s and "空闲" in s, "全屏启动失败")

        def question(text):
            literal(text)
            keys("Enter")
            wait_for(lambda s: "生成中" in s, "请求未开始")

        def finish():
            return wait_for(lambda s: "已完成" in s and "空闲" in s, "回复未完成")

        def shell():
            wait_for(lambda s: "MyCode · mock-" not in s, "未退出全屏界面")
            literal("printf 'E2E_SHELL_READY\\n'")
            keys("Enter")
            wait_for(lambda s: s.count("E2E_SHELL_READY") >= 2, "退出后 shell 未恢复")

        checks = []
        try:
            tmux(
                "new-session",
                "-d",
                "-s",
                "mycode-e2e",
                "-x",
                "100",
                "-y",
                "30",
                "-c",
                str(ROOT),
                "/bin/zsh -f",
            )
            tmux("set-environment", "-g", "NO_PROXY", "127.0.0.1,localhost")
            start("mock-openai")
            keys("Enter")
            time.sleep(0.15)
            assert not records
            literal("第一行")
            keys("Escape", "Enter")
            time.sleep(0.15)
            assert not records, "Alt+Enter 不应发送请求"
            literal("第二行")
            keys("Enter")
            early = wait_for(
                lambda s: "第一段中文" in s and "第五段结束" not in s, "未观察到结束前的增量"
            )
            (EVIDENCE / "tmux-openai-streaming.txt").write_text(early)
            finish()
            sent_text = records[-1]["body"]["messages"][-1]["content"]
            assert sent_text == "第一行\n第二行", repr(sent_text)
            checks.extend(["全屏启动", "空白不发送", "Alt+Enter 换行", "OpenAI 模拟 SSE 增量"])
            question("请记住临时口令：青竹742。")
            finish()
            question("刚才的临时口令是什么？")
            wait_for(lambda s: "口令是青竹742" in s, "多轮显示错误")
            finish()
            assert len(records[-1]["body"]["messages"]) == 5
            keys("C-c")
            shell()
            start("mock-openai")
            question("刚才的临时口令是什么？")
            wait_for(lambda s: "本会话没有口令" in s, "重启后历史未清空")
            finish()
            assert len(records[-1]["body"]["messages"]) == 1
            checks.extend(["多轮上下文", "重启空历史", "空闲 Ctrl+C 退出"])
            keys("C-c")
            shell()
            start("mock-claude")
            question("请思考后逐段回答")
            thought = wait_for(lambda s: "先分析" in s and "第五段结束" not in s, "未观察到思考流")
            (EVIDENCE / "tmux-claude-thinking.txt").write_text(thought)
            finish()
            keys("Tab", "Tab", "Enter")
            wait_for(lambda s: "先分析" not in s and "思考过程" in s, "思考折叠失败")
            keys("Space")
            wait_for(lambda s: "先分析" in s, "思考展开失败")
            keys("Tab")
            question("请继续回答")
            finish()
            blocks = records[-1]["body"]["messages"][1]["content"]
            assert blocks[0]["signature"] == "signature-complete"
            checks.extend(["Claude 模拟 SSE 思考", "思考折叠与展开", "思考签名多轮回传"])
            keys("C-c")
            shell()
            start("mock-openai")
            question("请给出长回复")
            wait_for(lambda s: "第35行" in s, "长回复不足以验证滚动")
            keys("Tab", "PPage")
            time.sleep(0.2)
            tmux("resize-window", "-t", "mycode-e2e", "-x", "80", "-y", "24")
            time.sleep(0.2)
            (EVIDENCE / "tmux-resized.txt").write_text(capture())
            keys("C-c")
            wait_for(lambda s: "空闲" in s, "取消后状态未恢复")
            question("取消后继续")
            wait_for(
                lambda s: "空闲" in s and len(records[-1]["body"]["messages"]) == 1,
                "取消内容进入历史",
            )
            checks.extend(["生成中滚动与尺寸调整", "Ctrl+C 取消", "取消后继续且历史隔离"])
            question("再次给出长回复")
            wait_for(lambda s: "生成中" in s, "退出场景未开始生成")
            keys("C-d")
            shell()
            checks.append("生成中 Ctrl+D 退出并恢复 shell")
            summary = {
                "kind": "本地模拟 SSE；非真实 API 验收",
                "checks": checks,
                "http_requests": len(records),
                "tmux": tmux("-V").strip(),
            }
            (EVIDENCE / "tmux-smoke.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2)
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        finally:
            (EVIDENCE / "tmux-mock-requests.json").write_text(
                json.dumps(records, ensure_ascii=False, indent=2)
            )
            tmux("kill-server", check=False)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    run()
