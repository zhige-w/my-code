"""使用用户配置执行真实 API 的 tmux 验收，不输出认证信息。"""

import argparse
import json
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from mycode.config import load_config
from mycode.errors import SecretRedactor

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "specs/interactive-chat/evidence"


def run(*, thinking_only: bool = False) -> None:
    configs = load_config(ROOT / "mycode.yaml")
    redactor = SecretRedactor(c.api_key for c in configs)
    summary = {
        "kind": "真实配置与真实 API，tmux 终端验收",
        "providers": [
            {"name": c.name, "protocol": c.protocol, "model": c.model, "thinking": c.thinking}
            for c in configs
        ],
        "results": {},
        "note": "只覆盖配置中指定的模型，不据此宣称未配置的原生 Claude 模型通过。",
    }
    EVIDENCE.mkdir(exist_ok=True)
    previous = EVIDENCE / "tmux-real.json"
    if thinking_only and previous.exists():
        old = json.loads(previous.read_text())
        summary["results"] = old.get("results", {})
        summary["thinking_initial_attempt"] = old.get(
            "thinking_initial_attempt", old.get("results", {}).get("C38")
        )

    def save_summary():
        text = redactor.redact(json.dumps(summary, ensure_ascii=False, indent=2))
        (EVIDENCE / "tmux-real.json").write_text(text)

    def result(key, status, detail):
        summary["results"][key] = {"status": status, "detail": detail}
        save_summary()
        print(redactor.redact(f"{key}: {status}；{detail}"), flush=True)

    with tempfile.TemporaryDirectory(prefix="mycode-real-") as temp:
        socket = Path(temp) / "tmux.sock"

        def tmux(*args, check=True):
            return subprocess.run(
                ["tmux", "-S", str(socket), *args],
                check=check,
                capture_output=True,
                text=True,
            ).stdout

        def keys(*values):
            if values == ("Escape", "Enter"):
                tmux("send-keys", "-t", "mycode-real", *values)
            else:
                for value in values:
                    tmux("send-keys", "-t", "mycode-real", value)
                    time.sleep(0.08)

        def literal(value):
            tmux("send-keys", "-t", "mycode-real", "-l", value)

        def screen():
            return redactor.redact(tmux("capture-pane", "-p", "-t", "mycode-real"))

        def write_screen(label, content):
            (EVIDENCE / f"real-{label}.txt").write_text(redactor.redact(content))

        def wait(predicate, description, timeout=30):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                value = screen()
                if predicate(value):
                    return value
                time.sleep(0.04)
            write_screen("failure", screen())
            raise AssertionError(description)

        def start(config):
            tmux("resize-window", "-t", "mycode-real", "-x", "120", "-y", "45")
            command = " ".join(
                shlex.quote(v)
                for v in (
                    str(ROOT / ".venv/bin/MyCode"),
                    "--config",
                    str(ROOT / "mycode.yaml"),
                    "--provider",
                    config.name,
                )
            )
            literal(command)
            keys("Enter")
            wait(lambda s: "MyCode ·" in s and "空闲" in s, "全屏启动失败")

        def exit_to_shell(key="C-c"):
            keys(key)
            wait(lambda s: "MyCode ·" not in s, "退出后未恢复终端")
            literal("printf 'REAL_SHELL_READY\\n'")
            keys("Enter")
            wait(lambda s: s.count("REAL_SHELL_READY") >= 2, "shell 未恢复")

        def last_answer(value):
            lines = value.splitlines()
            positions = [i for i, line in enumerate(lines) if line.strip() == "MyCode"]
            if not positions:
                return ""
            answer = []
            for line in lines[positions[-1] + 1 :]:
                stripped = line.strip()
                if stripped.startswith(("已完成", "失败，本轮", "生成中…", "已取消", "╭")):
                    break
                answer.append(line)
            return "\n".join(answer).strip()

        def observe(label, timeout=240):
            until = time.monotonic() + timeout
            seen_busy = False
            snapshots = []
            bodies = set()
            while time.monotonic() < until:
                value = screen()
                header = value.splitlines()[0] if value else ""
                if "生成中" in header:
                    seen_busy = True
                    body = last_answer(value)
                    if body and body not in bodies:
                        bodies.add(body)
                        if len(snapshots) >= 12:
                            # 保留开始及结束前的画面，同时覆盖思考与正式正文。
                            del snapshots[6]
                        snapshots.append(
                            {
                                "elapsed": round(timeout - (until - time.monotonic()), 2),
                                "screen": value,
                            }
                        )
                elif seen_busy and "空闲" in header:
                    write_screen(label, value)
                    (EVIDENCE / f"real-{label}-stream.json").write_text(
                        redactor.redact(json.dumps(snapshots, ensure_ascii=False, indent=2))
                    )
                    if "失败，本轮" in value:
                        raise AssertionError("真实请求失败，见脱敏终端证据")
                    return value, len(bodies), snapshots
                time.sleep(0.02)
            write_screen(label + "-timeout", screen())
            raise AssertionError("真实回复未在验收时限内完成")

        def question(prompt, label):
            literal(prompt)
            keys("Enter")
            return observe(label)

        try:
            tmux(
                "new-session",
                "-d",
                "-s",
                "mycode-real",
                "-x",
                "120",
                "-y",
                "45",
                "-c",
                str(ROOT),
                "/bin/zsh -f",
            )
            print("已启动专用 tmux 验收会话。", flush=True)
            available = {c.protocol: c for c in configs} if thinking_only else {}
            protocol_checks = () if thinking_only else (("openai", "C36"), ("anthropic", "C37"))
            for protocol, check in protocol_checks:
                config = next((c for c in configs if c.protocol == protocol), None)
                if config is None:
                    result(check, "未验收", f"配置中没有 {protocol} 供应商")
                    continue
                try:
                    start(config)
                    final, changes, _ = question(
                        "请用中文分五点说明 Python 异步编程的用途，每点至少两句话。", protocol
                    )
                    if changes < 2:
                        raise AssertionError("未观察到足够的结束前增量画面")
                    question("请记住临时口令：青竹742。只回复已记住。", protocol + "-remember")
                    recall, _, _ = question("刚才的临时口令是什么？", protocol + "-recall")
                    if "青竹742" not in last_answer(recall).replace(" ", "").replace("\n", ""):
                        raise AssertionError("多轮回复未正确复述临时口令")
                    exit_to_shell()
                    start(config)
                    question("刚才的临时口令是什么？", protocol + "-restart")
                    exit_to_shell()
                    available[protocol] = config
                    result(
                        check,
                        "通过",
                        f"{config.name}/{config.model}：真实流式、多轮口令与重启；"
                        f"首轮观察到 {changes} 次生成期间的内容变化",
                    )
                except Exception as exc:
                    write_screen(protocol + "-error", screen())
                    result(check, "失败", str(exc))
                    if "MyCode ·" in screen():
                        exit_to_shell("C-d")

            thinking_config = next(
                (c for c in configs if c.protocol == "anthropic" and c.thinking), None
            )
            if thinking_config is not None and "anthropic" in available:
                try:
                    start(thinking_config)
                    prompt = (
                        "袋子里有3个红球、2个蓝球，不放回地抽取2个球。"
                        "请计算两个球同色的概率，正式回答不超过100字。"
                        if thinking_only
                        else "有12个外观相同的球，其中1个重量异常，可能偏重也可能偏轻。"
                        "请分析如何用天平称3次定位异常球并判断轻重。"
                    )
                    final, changes, snapshots = question(
                        prompt,
                        "thinking-retest" if thinking_only else "thinking",
                    )
                    if not any("思考过程" in item["screen"] for item in snapshots):
                        raise AssertionError("服务没有返回可观察的公开思考内容")
                    if thinking_only:
                        formal_frames = set()
                        for item in snapshots:
                            lines = item["screen"].splitlines()
                            positions = [
                                i for i, line in enumerate(lines) if line.strip() == "MyCode"
                            ]
                            formal = []
                            if not positions:
                                continue
                            for line in lines[positions[-1] + 1 :]:
                                if line.strip().startswith("╭"):
                                    break
                                if (
                                    line.startswith(" ")
                                    and len(line) > 1
                                    and line[1] != " "
                                    and line.strip()
                                    and not line.strip().startswith(("生成中", "▔", "已完成"))
                                ):
                                    formal.append(line.strip())
                            if formal:
                                formal_frames.add("\n".join(formal))
                        if len(formal_frames) < 2:
                            raise AssertionError("未保存到多次正式正文增量画面")
                        summary["thinking_formal_streaming_frames"] = len(formal_frames)
                    keys("Tab", "Tab", "Enter")
                    folded = wait(lambda s: "▶ 思考过程" in s, "思考区未折叠")
                    write_screen("thinking-folded", folded)
                    keys("Space")
                    expanded = wait(lambda s: "▼ 思考过程" in s, "思考区未展开")
                    write_screen("thinking-expanded", expanded)
                    keys("Tab")
                    question("请用一句话总结刚才的方案。", "thinking-followup")
                    exit_to_shell()
                    result(
                        "C38",
                        "通过",
                        f"{thinking_config.model} 返回公开思考，独立流式显示、"
                        f"折叠/展开及追问成功；{changes} 次生成期间内容变化。",
                    )
                except Exception as exc:
                    result("C38", "失败", str(exc))
                    if "MyCode ·" in screen():
                        exit_to_shell("C-d")
            else:
                result("C38", "未验收", "没有可用的 thinking=true Anthropic 协议配置")

            if thinking_only:
                pass
            elif "openai" in available:
                try:
                    config = available["openai"]
                    start(config)
                    keys("Enter")
                    time.sleep(0.15)
                    assert "空闲" in screen() and "\n 你\n" not in screen()
                    literal("请只回复收到第一行。")
                    keys("Escape", "Enter")
                    assert "空闲" in screen(), "Alt+Enter 错误提交"
                    literal("这是第二行。")
                    keys("Enter")
                    wait(lambda s: "生成中" in s, "多行问题未开始")
                    literal("下一条草稿")
                    keys("Enter", "Enter")
                    observe("multiline-and-duplicate")
                    assert "下一条草稿" in screen(), "生成时错误提交了下一条草稿"
                    keys("C-u")
                    literal(
                        "请输出80条Python编程建议，每条至少30个汉字，每条单独一行，"
                        "以第01条、第02条的格式编号。请完整输出，不要使用表格。"
                    )
                    keys("Enter")
                    wait(
                        lambda s: "生成中" in s and "第30条" in s,
                        "长回复未达到滚动场景",
                        timeout=180,
                    )
                    keys("Tab", "PPage")
                    scrolled = screen()
                    write_screen("scrolled-during-generation", scrolled)
                    tmux("resize-window", "-t", "mycode-real", "-x", "80", "-y", "24")
                    resized = wait(
                        lambda s: len(s.splitlines()) == 24 and "╭" in s, "尺寸改变后输入区不可见"
                    )
                    assert "生成中" in resized, "尺寸改变前请求已结束"
                    write_screen("resized-during-generation", resized)
                    result(
                        "C39",
                        "通过",
                        "真实 API 期间验证换行、空白和重复提交拦截、滚动、"
                        "120×45 → 80×24 尺寸变化；思考折叠见 C38。",
                    )
                    keys("C-c")
                    wait(lambda s: "空闲" in s, "取消后未恢复")
                    keys("Tab", "End", "Tab")
                    wait(lambda s: "已取消" in s, "取消后未保留部分回复和标记")
                    write_screen("cancelled", screen())
                    question("请只回复：取消后继续成功。", "after-cancel")
                    literal("请完整输出100条Python建议，每条30字以上。")
                    keys("Enter")
                    wait(lambda s: "生成中" in s, "退出测试未开始生成")
                    exit_to_shell("C-d")
                    result(
                        "C40",
                        "通过",
                        "真实请求 Ctrl+C 取消、保留部分内容后继续成功，"
                        "空闲 Ctrl+C 和生成时 Ctrl+D 退出且 shell 恢复。",
                    )
                except Exception as exc:
                    result("C39_C40", "失败", str(exc))
                    if "MyCode ·" in screen():
                        exit_to_shell("C-d")
            else:
                result("C39_C40", "未验收", "OpenAI 协议真实请求未成功，未继续依赖场景")
        finally:
            tmux("kill-server", check=False)
            save_summary()
            print("专用 tmux 会话已清理；脱敏结果已保存。", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="真实 API tmux 验收")
    parser.add_argument("--thinking-only", action="store_true", help="仅用较短题目重测思考区")
    args = parser.parse_args()
    run(thinking_only=args.thinking_only)
