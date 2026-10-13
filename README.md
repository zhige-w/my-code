# MyCode

Python 全屏终端 AI 编程助手，支持 Anthropic Claude Messages 与 OpenAI Chat Completions SSE 流式回复、多轮对话、Claude 手动 extended thinking 及六个核心工具。

## 安装

需要 Python 3.11+，面向 macOS、Linux 交互终端。在项目目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
source .venv/bin/activate
```

安装后使用 `MyCode` 启动，也可使用 `.venv/bin/MyCode` 或 `.venv/bin/python -m mycode`。

## 配置与启动

将 `mycode.example.yaml` 复制为当前目录的 `mycode.yaml`，填写实际模型和 API key。真实配置已列入 .gitignore；占位密钥不能用于调用 API。

```yaml
- name: work
  protocol: anthropic
  model: claude-sonnet-4-6
  base_url: https://api.anthropic.com
  api_key: YOUR_ANTHROPIC_API_KEY
  thinking: true

- name: personal
  protocol: openai
  model: YOUR_OPENAI_CHAT_MODEL
  base_url: https://api.openai.com/v1
  api_key: YOUR_OPENAI_API_KEY
```

顶层是非空供应商列表。name、protocol、model、base_url、api_key 必填且为非空字符串；name 唯一。thinking 为可选布尔值，默认 false。未知字段、重复 YAML 键及非法配置在启动界面前报错。

```sh
MyCode
MyCode --provider personal
MyCode --config /path/to/config.yaml --provider work
MyCode --help
```

默认从当前目录读取 mycode.yaml，未指定供应商时使用列表第一项。切换供应商或模型需要退出后重新启动。

base_url 填 API 基础地址：Anthropic 追加 `/v1/messages`，OpenAI 追加 `/chat/completions`。Anthropic 末尾 `/v1` 会规范化去除，自定义网关路径前缀保留。完整端点及含用户名/密码、查询参数或 fragment 的地址被拒绝。

## 界面与按键

顶部显示供应商、模型及状态，中间是可滚动对话区，底部为固定输入区。正文以纯文本显示，每个 SSE 文本增量到达后即更新，不增加模拟打字延时。

| 按键 | 行为 |
| --- | --- |
| Enter | 发送非空问题；生成期间不重复发送 |
| Alt+Enter | 换行 |
| Tab / Shift+Tab | 在输入区、对话区和思考标题间移动焦点 |
| PageUp / PageDown、方向键 | 对话区聚焦后滚动 |
| 鼠标滚轮 | 滚动对话 |
| Enter / Space、点击思考标题 | 折叠或展开思考区 |
| Ctrl+C | 生成时取消当前请求；空闲时退出 |
| Ctrl+D | 清理当前请求后退出 |

生成期间可编辑下一条问题，当前请求结束后才能发送。在对话底部自动跟随输出；主动上滚后暂停，回到底部恢复。终端可调整大小。macOS 的 Option 为 Alt；部分终端需要将 Option 配置为发送 Meta/Esc。

## 会话、思考与错误

会话仅保留在内存，退出后不保存。普通对话只有完整成功的问答进入后续上下文；失败、截断或取消的部分内容保留在界面并标记状态。工具批次一旦进入执行，调用和每项实际或取消结果会保留到历史；即使后续总结失败或取消，下一轮仍能引用已执行结果。未完成的总结不保存。

Claude thinking=true 使用手动 extended thinking，预算 2048、总输出上限 8192。API 公开的思考文本独立流式显示，完成后可折叠；签名和隐藏内容不显示。模型须支持 `thinking.type=enabled`，例如示例中的 Claude Sonnet 4.6。仅支持 adaptive thinking 的模型会报错，本版不自动切换模式。thinking=false 不发送启用参数。

OpenAI 使用 Chat Completions 文本流，不发送额外推理参数；thinking=true 在启动前报错，本版不实现 Responses API。

认证、网络、超时、流内错误和不完整结束均会恢复输入能力，不自动重试。连接、写入和连接池超时 10 秒，读取空闲超时 120 秒。界面及应用错误隐藏配置密钥，请求与会话不写入应用日志。

## 工具使用

模型自行选择工具；首次响应可以包含多个调用，MyCode 按返回顺序串行执行，统一回传结果后再请求一次总结。总结阶段禁用工具，本轮不自动循环。可明确提问“请调用 read_file 读取 README.md，然后总结”。需要根据前一步结果决定新操作时，继续发送下一轮问题。

| 工具 | 参数与行为 |
| --- | --- |
| read_file | path；offset=1、limit=200，带行号读取 UTF-8，最多64 KiB |
| write_file | path、content，创建或覆盖并创建父目录，内容最多1 MiB |
| edit_file | path、old_text、new_text，非空原文必须恰好匹配一次，文件及新内容各最多1 MiB |
| execute_command | command；cwd 可选、timeout=30（大于0且最多120秒），使用 /bin/sh -c，stdin 关闭，stdout/stderr 各最多64 KiB |
| find_files | pattern；path="."，按 glob 找文件，支持 *、?、** |
| search_code | pattern 正则；path="."、glob="**/*"、case_sensitive=true，返回路径、行号、匹配行 |

相对路径以 MyCode 启动目录为基准，绝对路径也可用。搜索最多200条、64 KiB，匹配行保留2 KiB；默认跳过 .git、.venv、__pycache__、node_modules，显式指定其中目录为搜索根时可搜索。目录链接不递归，二进制/非法文本和不可读项记录跳过摘要。输出超限明确标记截断。

文件工具保留 UTF-8 换行，编辑使用字面非重叠唯一匹配；匹配失败文件不变。写入采用同目录临时文件原子替换，编辑提交前检查文件变化；不提供跨进程锁。工具以 MyCode 进程权限运行，本版没有逐次审批、命令白名单或路径沙箱。命令不支持密码输入、持续交互和后台任务管理。

工具默认超时30秒，工具执行与总结期间可 Ctrl+C 取消。命令独立进程组会被终止并回收，文件和搜索运行在可终止工作进程中；已完成副作用不回滚。若取消恰好发生在文件提交附近，结果会提示可能已生效，需重新读取确认。失败、超时、非法参数均以结构化结果回传模型，不自动重试。

本版尚无自动 Agent Loop、会话持久化、多模态、上下文压缩、Token/费用统计和 Windows 验收。

## 测试与构建

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m build
```

协议测试使用真实 SDK 加模拟 SSE 字节流，TUI 测试使用 Textual Pilot。端到端须另外在 tmux 中启动 MyCode、输入真实对话、观察流式和多轮行为，并对照 `specs/interactive-chat/checklist.md` 记录结果。两个真实后端及 Claude thinking 需要凭据与模型权限，缺少条件记为未验收。

完整需求、设计、任务及实际验收记录位于 `specs/interactive-chat/`。

工具系统文档与逐项验收位于 `specs/tool-system/`。真实终端验收入口：

```sh
.venv/bin/python tests/tmux_tools.py
# 可选：--protocol openai 或 --protocol anthropic
```

脚本使用现有 mycode.yaml，在专用临时目录与 tmux 会话中运行真实 MyCode 和真实 API，保存脱敏证据。需 tmux、真实凭据与支持工具调用的模型；模拟 SSE 测试不能代替真实 API 验收。只报告实际配置模型的结果。
