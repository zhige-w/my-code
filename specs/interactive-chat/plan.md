# MyCode 第一版交互式对话 Plan

> 基于已批准的 spec.md。用户在完整文档最终审批请求后明确回复“批准”，完整 plan 已获批准；四份文档均已批准，现已完成实现，用户提供配置后已完成真实 API 验收，覆盖当前 DeepSeek Flash 两种兼容协议；未配置的原生 Claude 模型未实测。

## 架构概览

### 技术方案

采用 Python 3.11+、Textual 全屏 TUI、asyncio 异步执行、Anthropic 与 OpenAI 官方 Python SDK、PyYAML 配置解析。SDK 的自动重试显式关闭，使用 SDK 提供的异步 SSE 流式接口。

Textual 负责布局、滚动、多行输入、键盘绑定和可折叠思考区域。网络请求在异步 worker 中执行，避免阻塞终端交互。所有界面更新留在同一事件循环中。

采用官方 SDK 可复用协议解析和流资源管理能力，代价是引入两个 SDK 依赖。共同的 Provider 接口隔离 SDK 差异，TUI 和会话层不直接访问 SDK 对象。

### 组件职责

| 组件 | 职责 | 需求归属 |
| --- | --- | --- |
| CLI | 解析配置路径和供应商名；配置成功后启动 TUI；配置错误时输出脱敏提示并以非零状态退出 | F3 |
| Config | 读取 YAML，校验六个字段、唯一 name 和协议支持，选出当前供应商 | F2、F3、F6 |
| Provider | 统一异步流式接口；两种适配器分别构造请求、转换文本与思考事件、判断流是否成功结束、关闭连接并规范化错误 | F4、F6、F8 |
| Conversation | 管理当前会话历史和当前请求状态；仅成功结束时提交完整问答；失败或取消时丢弃待提交问答 | F5、F7、F8 |
| TUI | 固定输入区、滚动对话区、思考折叠区；处理发送、取消、退出与错误提示 | F1、F6、F7、F8 |

### 依赖与数据流

CLI 依赖 Config 和 TUI；CLI 使用 Provider 工厂创建选定适配器并传入 Conversation。TUI 通过 Conversation 使用 Provider 接口，Provider 不依赖 TUI。供应商新增时扩展适配器与配置/工厂注册，不改对话主流程。

正常路径：用户输入 → TUI 提交 → Conversation 创建历史快照和待提交问答 → Provider 发起 SSE 请求 → 统一增量事件 → TUI 展示 → 收到成功结束事件 → Conversation 提交问答。

取消或失败路径：取消当前 worker 或收到 Provider 错误 → 关闭响应流 → Conversation 丢弃待提交问答 → TUI 保留已显示内容并标记状态 → 恢复输入。应用退出时取消请求并关闭客户端，不保存历史。

### 展示与上下文分离

TUI 中的显示记录与发送给模型的成功历史分开管理。失败和取消的内容保留用于查看，但不会进入后续请求；思考内容也独立于正式回复展示。Claude 多轮请求需要保留的协议内容由适配器处理，不交给 TUI 拼装。

## 核心数据结构

以下类型属于应用自身，不暴露 SDK 类型。结构使用 dataclass；协议接口使用 typing.Protocol。

### ProviderConfig

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| name | str | 非空，在配置文件中唯一 |
| protocol | Literal["anthropic", "openai"] | 决定协议适配器 |
| model | str | 非空，保持用户指定的模型标识 |
| base_url | str | 非空 HTTP/HTTPS API 基础地址，具体路径约定在协议设计小节中明确 |
| api_key | str | 非空，不出现在 repr、日志或用户错误信息中 |
| thinking | bool | 可省略，默认 False；openai 不允许 True |

### Message 与协议内容

`Message(role: Literal["user", "assistant"], text: str, native_content: NativeContent | None = None)` 表示一条已成功历史消息，或本轮尚未提交的消息。

`NativeContent(protocol: str, blocks: list[dict[str, JsonValue]])` 保存适配器重建后续请求所需的 JSON 内容；JsonValue 为 JSON 标量、列表或字典的递归联合类型。创建时复制内容，提交后仅内部读取，不包含 SDK 对象。user 消息通常只需要 text；assistant 消息可以同时保存正式回复文本和协议内容。

Claude 的完整 content blocks（包括 thinking、signature 和可能出现的 redacted_thinking）由 Anthropic 适配器保存并按协议原样回传；TUI 不读取或修改签名、隐藏内容。思考区仅显示 API 实际公开的思考文本，不尝试还原隐藏思考。OpenAI 使用普通文本历史，不保存额外协议块。

### StreamEvent

统一事件为以下 dataclass 的联合类型：

| 事件 | 字段 | 含义 |
| --- | --- | --- |
| TextDelta | text: str | 追加正式回复增量 |
| ThinkingDelta | text: str | 追加 API 公开的思考文本增量 |
| TurnCompleted | message: Message | 已验证完整成功结束的 assistant 消息；只发出一次且必须是最后一个事件 |

异常不伪装成完成事件。Provider 使用 `ProviderError(kind, user_message)` 抛出脱敏错误，kind 区分 authentication、rate_limit、timeout、connection、protocol、response；截断或不完整流不能产生 TurnCompleted。取消使用 asyncio.CancelledError，清理后继续向上传播。

### 展示记录与状态

`TurnView(user_text: str, answer_text: str, thinking_text: str, status: Literal["streaming", "completed", "cancelled", "failed"], error_message: str | None)` 仅用于当前 TUI 显示，不用于构造请求。思考折叠状态由对应 TUI 组件维护。

Conversation 对外提供只读 history 快照和 busy 状态。历史只包含完整成功的 user/assistant 对；本轮输入与累积回复先放在局部待提交数据中。应用仅运行一个生成 worker。

## 核心接口

以下签名为设计约定，不在本阶段编写实现。

```python
class Provider(Protocol):
    def stream(self, messages: Sequence[Message]) -> AsyncIterator[StreamEvent]: ...
    async def aclose(self) -> None: ...

def load_config(path: Path) -> tuple[ProviderConfig, ...]: ...
def select_provider(
    configs: Sequence[ProviderConfig], name: str | None
) -> ProviderConfig: ...
def create_provider(config: ProviderConfig) -> Provider: ...

class Conversation:
    def __init__(self, provider: Provider) -> None: ...
    @property
    def history(self) -> tuple[Message, ...]: ...
    @property
    def busy(self) -> bool: ...
    def stream_turn(self, user_text: str) -> AsyncIterator[StreamEvent]: ...
    async def aclose(self) -> None: ...
```

- load_config 校验完整文件，返回非空有序列表；失败抛出 ConfigError，只携带安全提示。select_provider 默认选择第一项，指定 name 时精确匹配。
- create_provider 是协议注册入口，按配置创建具体适配器；两个 SDK 均由适配器持有和关闭。
- Provider.stream 接收本次请求完整快照，包含成功历史与当前 user 消息；不修改传入消息，不管理历史。正式文本增量拼接结果必须等于 TurnCompleted.message.text。
- Provider 只有确认协议正常结束并关闭本次响应流后才发出 TurnCompleted；网络提前结束、协议错误或响应截断时抛出 ProviderError。成功流中忽略不影响内容的元数据事件。
- Conversation.stream_turn 开始时检查空白输入和 busy，拒绝非法调用；等待时立即进入 busy，构造快照后消费 Provider 流。收到 TurnCompleted 时以一个不含 await 的步骤提交 user/assistant 对，然后转发完成事件；所有出口均恢复 busy。
- 在收到完成事件前取消，待提交问答丢弃；完成提交后发生的取消不回滚已成功历史。此边界避免最后一段回复已成功完成却被随后的按键误删。
- TUI 持有当前 worker，Ctrl+C 生成时取消 worker 并等待清理，清理完成前不接受下一次提交；空闲时退出。Ctrl+D 发起退出，先取消并等待当前 worker，再调用 Conversation.aclose 关闭客户端。
- 无论成功、失败或取消，流迭代都使用明确的资源清理范围，关闭异步生成器和响应流，避免遗留请求或 busy 状态。TUI 通过统一异常和事件更新 TurnView，不导入 SDK 类型。

## 协议参数与错误处理

### base_url 约定

base_url 是 API 基础地址，不是完整请求端点。规范化时保留用户路径前缀、去掉末尾斜杠；禁止 userinfo、查询参数和 fragment，错误提示不回显原始地址。

| protocol | 官方地址示例 | 最终请求路径 |
| --- | --- | --- |
| anthropic | `https://api.anthropic.com` | 基础地址后追加 `/v1/messages` |
| openai | `https://api.openai.com/v1` | 基础地址后追加 `/chat/completions` |

Anthropic SDK 的 base_url 采用服务根地址；若用户填入末尾 `/v1`，适配器统一移除该末尾片段后传给 SDK，避免重复 `/v1`。OpenAI SDK 的 base_url 采用版本前缀地址，明确要求用户填写服务提供的 API 基础路径，不猜测自定义网关的版本。完整 `/messages` 或 `/chat/completions` 端点配置在启动时拒绝，并提示填写基础地址。

### Anthropic 适配器

- 使用 AsyncAnthropic 和 Messages 异步流接口；SDK 处理 SSE/UTF-8 分片，适配器消费事件并累计完整内容。
- 每次请求传入原样 model、历史 messages 和固定 `max_tokens=8192`；SDK 设置认证头与 API 版本头，不传 tools、temperature 等本轮无关参数。
- `thinking: true` 映射为 `thinking={"type": "enabled", "budget_tokens": 2048}`，预算小于总输出上限。第一版实现用户要求的手动 extended thinking，不按模型名字猜测或自动切换 adaptive thinking；不支持此模式的模型由 API 返回错误，界面提示模型不支持当前 thinking 模式，不自动重试。
- `thinking: false` 不发送 thinking 启用参数；不声称能禁止模型服务端默认的所有内部推理。
- text_delta 映射 TextDelta；thinking_delta 映射 ThinkingDelta；签名增量和隐藏思考块只用于重建 native_content，不展示。对 content_block_start 中已包含的初始文本也按一次增量处理，避免丢失或重复。
- 只有收到 message_stop、全部内容块完整且 stop_reason 为 end_turn 或 stop_sequence，才允许成功完成。max_tokens 作为输出截断错误；tool_use、pause_turn 或未知结束原因作为当前纯对话流程无法完成的响应错误。流内 error 事件立即失败；ping 和无关元数据忽略。
- 保存完整 assistant content blocks，用于后续成功历史的协议转换；保持块顺序和签名，不以界面的思考文本重建协议块。

### OpenAI 适配器

- 使用 AsyncOpenAI 的 Chat Completions 接口，选择它以覆盖本轮文本对话及同协议网关；本轮不同时实现 Responses API。
- 请求传入原样 model、成功历史与当前 user 消息，使用 `stream=True`、单候选；不指定 reasoning、tools、temperature 或存储开关，不创建服务端会话。应用退出时不持久化本地会话。
- 不硬编码输出上限参数，让模型服务使用其默认值，避免不同模型对 max_tokens/max_completion_tokens 的兼容差异；服务端截断仍按失败处理。
- 读取 index=0 的 delta.content，映射 TextDelta；角色声明、空增量及无关元数据不产生文字。非文本的工具调用响应报错，拒绝执行工具。
- 只在 finish_reason 为 stop 且 SDK 流正常结束后发出 TurnCompleted；流结束但未收到 finish_reason 为不完整响应。length 为截断错误，content_filter、tool_calls、function_call 或未知结束原因按响应错误处理；保留已显示内容，不提交历史。
- SDK 对结束标记的解析属于传输层；适配器不能仅以迭代器结束认定业务成功，必须验证 finish_reason。

### 配置与异常边界

- YAML 使用安全加载，顶层必须为非空列表，条目只能出现六个约定字段；必填字符串不能是空白，thinking 必须为布尔值，拒绝重复映射键和重复 name。读取或解析失败时仅显示文件位置、条目序号、字段名称等安全信息，不回显配置行或解析器原始异常。
- 两个客户端均设置 `max_retries=0`；连接超时 10 秒、读取空闲超时 120 秒、写入及连接池等待超时 10 秒。读取超时按两次网络数据之间的等待计算，不设置整轮生成的固定时长。
- 将 SDK 的认证/权限错误、限流、超时、连接异常、服务端错误转换为固定中文用户提示；可带 HTTP 状态码，不展示响应体、请求头、原始异常字符串或堆栈。thinking 参数不受支持的错误仅在识别出明确错误原因时追加对应提示。
- 配置对象的 api_key 设置 repr=False，客户端不打印；应用不启用 SDK HTTP 调试日志，对 SDK 日志设置至少 WARNING 并过滤可能包含密钥的记录，错误出口统一脱敏。应用不保存请求或会话日志。
- 每个请求使用明确的流关闭范围。取消原样传播 CancelledError，经 finally 清理后恢复状态；一般 Exception 转换为可恢复错误；不捕获并吞掉进程退出信号。

## 模块设计与文件组织

采用 src 布局、setuptools 构建、pyproject.toml 声明依赖及 `MyCode = "mycode.cli:main"` 命令入口；另提供 `python -m mycode` 调用同一入口。

```text
my-code/
├── pyproject.toml                 # 构建、依赖、命令入口、pytest 和 Ruff 配置
├── README.md                      # 安装、配置、键盘操作、模型兼容与运行说明
├── .gitignore                     # 本地真实配置、虚拟环境、缓存、构建产物
├── mycode.example.yaml            # 两个供应商的示例，密钥只使用占位值
├── src/mycode/
│   ├── __init__.py                # 包定义
│   ├── __main__.py                # python -m mycode 入口
│   ├── cli.py                     # 参数解析、配置选择、组装与启动
│   ├── config.py                  # ProviderConfig、YAML 校验与选择
│   ├── models.py                  # JsonValue、Message、NativeContent、StreamEvent
│   ├── errors.py                  # ConfigError、ProviderError、错误映射与脱敏
│   ├── conversation.py            # 会话状态、成功历史、取消时的回滚
│   ├── providers/
│   │   ├── __init__.py            # create_provider 和协议注册
│   │   ├── base.py                # Provider 接口与共享超时策略
│   │   ├── anthropic.py           # Anthropic 请求、事件转换与原生内容保留
│   │   └── openai.py              # OpenAI 请求、事件转换与完成校验
│   └── tui/
│       ├── __init__.py            # TUI 包定义
│       ├── app.py                 # MyCodeApp、worker 生命周期与全局快捷键
│       ├── widgets.py             # ChatInput、TurnWidget、TurnView
│       └── app.tcss               # 布局、尺寸、纯文本样式
└── tests/
    ├── conftest.py                # 可注入的异步 HTTP 客户端、SSE 字节流与假 Provider
    ├── test_config.py             # 配置正常与异常路径
    ├── test_cli.py                # 命令参数、选择与安全退出
    ├── test_anthropic.py          # SDK 流式集成、thinking、签名与异常结束
    ├── test_openai.py             # SDK 流式集成、finish_reason 与异常结束
    ├── test_conversation.py       # 多轮历史、原子提交、取消与错误恢复
    ├── test_tui.py                # 输入、增量展示、折叠、滚动、尺寸和快捷键
    └── test_errors.py             # 密钥脱敏、日志和原始错误隔离
```

### 对外接口与依赖边界

| 模块 | 对外入口 | 依赖 |
| --- | --- | --- |
| cli | `main() -> None` | argparse、config、Provider 工厂、Conversation、MyCodeApp |
| config | ProviderConfig、load_config、select_provider | PyYAML、标准库、ConfigError |
| models | 消息、内容块和事件类型 | 标准库 typing、dataclasses |
| errors | ConfigError、ProviderError、安全错误映射辅助函数 | 标准库；SDK 异常识别由对应适配器调用 |
| providers.base | Provider | models、typing |
| providers.anthropic | AnthropicProvider(config, *, http_client=None) | anthropic SDK、config、models、errors |
| providers.openai | OpenAIProvider(config, *, http_client=None) | openai SDK、config、models、errors |
| providers 工厂 | create_provider | config、两个适配器 |
| conversation | Conversation | models、Provider 接口、标准库 |
| tui.app | MyCodeApp(conversation, provider_name, model) | Textual、Conversation、widgets |
| tui.widgets | ChatInput、TurnWidget、TurnView | Textual、Rich Text、标准库 |

适配器构造器中的可选 http_client 是 httpx.AsyncClient，生产环境由 SDK 默认创建，测试中传入 MockTransport 客户端。传入后由适配器统一负责关闭，调用方不共享该客户端。工厂只接收六字段配置，测试注入不成为产品配置。

models 不导入 config、SDK 或 TUI；Conversation 不导入具体适配器；errors 不导入应用上层。业务模块之间不形成循环依赖。app.tcss 作为包数据打入分发包，确保安装后 TUI 能正常加载样式。

## TUI 交互与模块协作

### 布局与快捷键

- 顶部状态栏显示 MyCode、当前 provider name、model 和空闲/生成/取消处理中状态；不显示 base_url 或 api_key。
- 中间使用可聚焦的 VerticalScroll 承载 TurnWidget；每轮显示用户纯文本、独立思考 Collapsible、正式回复纯文本和完成/取消/失败标记。没有公开思考内容时不显示空思考面板；存在思考时默认展开，结束后仍可展开或折叠。
- 底部 ChatInput 基于 TextArea，固定占用 5 行区域，输入内容超出后内部滚动，不挤出对话区。Enter 提交，Alt+Enter 插入换行；重写默认 Enter 绑定并防止事件重复处理。
- 生成和取消清理期间禁止提交；输入区仍保留焦点或可返回编辑，允许准备下一条内容。TUI 在启动 worker 前设置自身生成状态，避免 Conversation 尚未开始迭代时的双重提交竞争。
- Ctrl+C/Ctrl+D 使用应用级优先绑定，覆盖 TextArea 默认绑定；Ctrl+C 生成时取消、清理中重复按键忽略、空闲时退出；Ctrl+D 退出并等待资源清理。
- Tab/Shift+Tab 在输入区、对话区和可折叠标题之间移动焦点；对话区聚焦时使用 PageUp/PageDown、方向键滚动，鼠标滚轮也可滚动，思考标题使用 Enter/Space 或点击切换。
- 用户位于底部时，新内容自动跟随到底部；用户主动上滚后停止跟随，回到底部后恢复。终端变更尺寸时由布局系统重新排版，正文软换行。
- 所有文本使用 Rich Text 或关闭 markup 的纯文本控件；模型返回的方括号、转义序列和 Markdown 不解释为界面标记。对终端控制字符进行安全显示，保留正常换行与制表符，避免模型内容直接发出终端控制命令。

### 一次请求的时序

1. ChatInput 检查非空且 TUI 空闲，生成状态立即置为忙，创建 TurnView 和 TurnWidget，清空已提交输入并启动单个异步 worker。
2. worker 消费 Conversation.stream_turn；会话层创建成功历史快照，加上本轮 user 消息，传给 Provider.stream。
3. Provider 转换 SDK SSE 增量；TUI 每收到一个事件就追加到本轮对应文本组件，不等待完整回复，也不增加模拟打字延时。
4. Provider 校验正常结束并完成流清理，发出 TurnCompleted；Conversation 原子提交完整消息对；TUI 标记成功。
5. 失败时 TUI 显示安全错误并标记失败；取消时标记取消。worker 的 finally 恢复 TUI 空闲状态和输入焦点。
6. 退出时停止新提交、取消并等待当前 worker、关闭 Conversation 持有的 Provider，然后恢复终端；启动时配置错误不进入全屏界面。

## 技术决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 全屏终端框架 | Textual | 覆盖滚动、多行输入、折叠组件、异步 worker 和测试工具 |
| 并发方式 | asyncio 单事件循环、单生成 worker | 保持交互响应，减少跨线程和多请求状态竞争 |
| API 接入 | 两家官方异步 SDK | 复用 SSE/UTF-8 解析与客户端资源管理，通过适配器隔离差异 |
| OpenAI 协议 | Chat Completions | 满足本轮文本对话，也便于使用同协议的自定义 base_url |
| Claude thinking | 手动模式、固定 2048 预算与 8192 输出上限 | 保持六字段配置，明确模型兼容边界，不暗中请求降级或重试 |
| 历史保存 | 内存成功消息对，与展示记录分开 | 保留可查看的失败内容，确保模型上下文只包含成功问答 |
| 配置 | PyYAML 安全加载、严格字段和重复键校验 | 与用户约定一致，尽早发现拼写、类型和选择错误 |
| 命令与打包 | argparse、setuptools、src 布局 | 保持启动依赖较少，提供安装后可执行的 MyCode 命令 |
| 测试 | pytest、pytest-asyncio、httpx MockTransport、Textual Pilot | 分别验证协议传输、会话状态与界面行为，无需测试时依赖真实 API |
| 静态检查与构建 | Ruff、compileall、python -m build | 检查代码规范、语法和可分发包，不把安装源码成功等同于打包成功 |

## 验证设计

当前阶段只完成验证方案，不宣称以下检查已执行；具体操作与逐项结果分别留给 task.md 和 checklist.md。

| 验收标准 | 计划验证方式 |
| --- | --- |
| AC1 | Textual Pilot 验证固定输入区、换行、空白提交和并发提交拦截；tmux 验证真实终端按键与滚动 |
| AC2 | 临时 YAML 文件测试全部字段、重复键、唯一 name、默认选择、指定路径及错误退出；检查错误输出不含敏感值 |
| AC3 | MockTransport 返回逐段异步 SSE 字节，刻意切分 UTF-8 中文字符；在释放后续片段前断言前一增量已到达；tmux 观察真实 API 增量展示 |
| AC4 | 检查第二轮 HTTP 请求包含首轮完整问答；新建 Conversation 后历史为空；tmux 进行临时口令对话并重启应用 |
| AC5 | 在取消、超时和中途流错误后检查下一次请求，仅带此前成功历史和新输入；验证 busy 恢复与客户端仍可用 |
| AC6 | 检查 thinking 开/关请求体、初始内容、thinking_delta、签名、隐藏思考块与多轮回传；Pilot 验证独立显示和折叠；tmux 对支持手动模式的真实 Claude 模型验证 |
| AC7 | Pilot 与 tmux 验证生成中 Ctrl+C、空闲 Ctrl+C、Ctrl+D；用可检测关闭状态的传输验证没有遗留请求 |
| AC8 | 模拟 401/403、429、500、连接异常、超时、流内错误、缺失结束事件及截断；记录请求次数确认无重试；在配置、响应体和异常中植入假密钥，验证输出及日志不泄露 |
| AC9 | 两个真实适配器通过同一个 Conversation 接口测试；Pilot 与 tmux 在生成时滚动、取消、调整尺寸，确认界面可操作 |
| AC10 | 在 tmux 启动已安装的 MyCode，分别使用两个真实 API 配置进行流式和多轮对话，并额外执行 Claude thinking 场景；记录运行环境和实际证据 |

- 额外验证构建后的 wheel 包含 TUI 样式，在隔离虚拟环境安装后能运行 `MyCode --help`；执行 Ruff、compileall 和自动化测试。
- 协议测试使用真实 SDK 加模拟 HTTP 字节流，不仅测试绕过 SDK 的假事件；会话和 TUI 测试使用统一假 Provider 控制完成、失败和取消时机。
- tmux 测试使用真实 API，模拟协议测试结果不能代替真实后端验收。缺少 tmux、运行环境、模型权限或凭据时将受影响条目标记为未验收并说明阻塞；macOS/Linux 兼容性只报告实际执行的平台。
- 测试期间不将真实密钥写入示例、版本控制文件或证据输出；真实配置使用被忽略的本地 mycode.yaml 或用户指定文件。
- 按 AGENTS.md 要求逐项对照后续已批准的 checklist.md。工具调用项因本轮纯对话范围不适用，不伪造工具执行结果。

## 设计自检

- F1–F8 已分配到 CLI、Config、Provider、Conversation 或 TUI；AC1–AC10 均有可观测的验证方式。
- 所有 plan 中的组件均有文件归属和对外接口；依赖无环，SDK 类型停留在适配器内部。
- 六字段配置、纯文本展示、当前会话记忆、手动 extended thinking、不自动重试等已批准约束保持一致。
- 完整文档已获批准；实现与验证结果以 task.md、checklist.md 的实际记录为准。

## 实现补充记录

- 使用已实测兼容的 Textual 8.2、Anthropic 0.125、OpenAI 2.54 及 httpx 0.28 系列；Anthropic 1.x 已改用 httpx2，故依赖约束在 0.x，保持已批准的传输接口。
- tmux 发现 Textual 8 的旧式 Meta+Enter 解析丢失修饰符，在 widgets.py 中仅补充未注册的 Escape+CR/LF 映射；扩展键协议保持框架处理，原始字节回归和 tmux 重测通过。
- 增加 MANIFEST.in 和 tests/tmux_smoke.py，分别保证源码包包含配置示例/测试设施并复现既定 tmux 模拟检查；不增加产品功能。

## 参考

- [Textual Workers](https://textual.textualize.io/guide/workers/)：异步 worker 支持界面交互期间执行网络任务。
- [Textual TextArea](https://textual.textualize.io/widgets/text_area/)：多行输入和键盘绑定基础。
- [Claude extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)：手动模式、预算约束与模型兼容边界。
- [Claude 流式事件](https://platform.claude.com/docs/en/build-with-claude/streaming)：消息、内容块、思考增量与流内错误。
- [OpenAI Chat Completions 流事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)：delta 与 finish_reason。
- [OpenAI Chat Completions 请求](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)：流式请求参数。
