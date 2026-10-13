# MyCode

Python 全屏终端 AI 对话助手，支持 Anthropic Claude Messages 与 OpenAI Chat Completions SSE 流式回复、多轮对话和 Claude 手动 extended thinking。

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

会话仅保留在内存，退出后不保存。只有完整成功的问答进入后续上下文；失败、截断或取消的部分内容保留在界面并标记状态，但不进入下一请求。

Claude thinking=true 使用手动 extended thinking，预算 2048、总输出上限 8192。API 公开的思考文本独立流式显示，完成后可折叠；签名和隐藏内容不显示。模型须支持 `thinking.type=enabled`，例如示例中的 Claude Sonnet 4.6。仅支持 adaptive thinking 的模型会报错，本版不自动切换模式。thinking=false 不发送启用参数。

OpenAI 使用 Chat Completions 文本流，不发送额外推理参数；thinking=true 在启动前报错，本版不实现 Responses API。

认证、网络、超时、流内错误和不完整结束均会恢复输入能力，不自动重试。连接、写入和连接池超时 10 秒，读取空闲超时 120 秒。界面及应用错误隐藏配置密钥，请求与会话不写入应用日志。

本版仅做纯对话，尚无工具调用、文件编辑、命令执行、多模态、上下文压缩、Token/费用统计和 Windows 验收。

## 测试与构建

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m build
```

协议测试使用真实 SDK 加模拟 SSE 字节流，TUI 测试使用 Textual Pilot。端到端须另外在 tmux 中启动 MyCode、输入真实对话、观察流式和多轮行为，并对照 `specs/interactive-chat/checklist.md` 记录结果。两个真实后端及 Claude thinking 需要凭据与模型权限，缺少条件记为未验收。

完整需求、设计、任务及实际验收记录位于 `specs/interactive-chat/`。
