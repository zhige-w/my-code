# MyCode 第一版交互式对话 Tasks

> 基于已批准的 spec.md 与 plan.md。用户在完整文档审批请求后明确回复“批准”，本文已获批准；checklist.md 也已获用户明确批准，已授权按任务顺序开始实现。所有任务初始状态均为未执行，验证命令仅为计划，不代表已通过。

## 文件清单

所有路径相对于项目根目录，验证命令也在项目根目录执行。

| 操作 | 文件 | 职责与任务 |
| --- | --- | --- |
| 新建 | `pyproject.toml`、`.gitignore` | 构建、依赖、命令与工具配置、本地配置忽略；T1、T17、T19 |
| 新建 | `src/mycode/__init__.py`、`src/mycode/providers/__init__.py`、`src/mycode/tui/__init__.py` | 包定义、Provider 工厂；T1、T10 |
| 新建 | `src/mycode/models.py`、`src/mycode/errors.py`、`src/mycode/providers/base.py` | 消息、事件、统一接口、错误与超时；T2、T10、T18 |
| 新建 | `src/mycode/config.py` | 六字段配置校验与选择；T3 |
| 新建 | `src/mycode/providers/openai.py` | OpenAI 异步流适配；T5、T6 |
| 新建 | `src/mycode/providers/anthropic.py` | Claude 异步流及 thinking；T7、T8、T9 |
| 新建 | `src/mycode/conversation.py` | 多轮成功历史、取消与恢复；T11、T12 |
| 新建 | `src/mycode/tui/widgets.py` | 输入组件、轮次展示、思考折叠；T13 |
| 新建 | `src/mycode/tui/app.py`、`src/mycode/tui/app.tcss` | 全屏布局、worker、快捷键、滚动；T14、T15、T16 |
| 新建 | `src/mycode/cli.py`、`src/mycode/__main__.py` | 参数解析和启动；T17 |
| 新建 | `README.md`、`mycode.example.yaml` | 安装、配置、使用及模型边界；T17 |
| 新建 | `tests/conftest.py` | SDK HTTP 模拟、可控 SSE、假 Provider；T4 |
| 新建 | `tests/test_config.py` | YAML 正常、异常和选择；T3 |
| 新建 | `tests/test_openai.py` | 请求、SSE 和结束校验；T5、T6 |
| 新建 | `tests/test_anthropic.py` | 请求、SSE、thinking 和结束校验；T7、T8、T9 |
| 新建 | `tests/test_conversation.py` | 历史、并发拦截、取消与关闭；T11、T12 |
| 新建 | `tests/test_tui.py` | 组件、布局、流式交互、滚动与按键；T13–T16 |
| 新建 | `tests/test_cli.py`、`tests/test_errors.py` | 启动和密钥保护；T10、T17、T18 |
| 更新 | `specs/interactive-chat/task.md` | 实现时记录任务状态、实际验证和阻塞 |
| 更新 | `specs/interactive-chat/checklist.md` | T20 按已批准条目记录实际验收结果和证据 |

## 执行约定

- 仅四份文档全部获批后执行下列任务。按依赖顺序串行推进；无子代理或并行代理安排。
- 每项验证通过后才标记该任务完成；记录实际命令与结果，失败先修复再重跑受影响检查。
- 使用 Python 3.11+ 创建项目内 `.venv`，下文以 `.venv/bin/python` 和 `.venv/bin/MyCode` 指代相同环境中的命令。不修改系统 Python。
- pyproject.toml 的运行依赖为 textual、anthropic、openai、PyYAML、httpx，Rich 使用 Textual 兼容的依赖版本；dev 依赖为 pytest、pytest-asyncio、ruff、build。实现时根据实际安装兼容性声明版本范围并记录使用版本，不以未验证版本声称兼容。
- 测试命名使用对应任务约定的关键字，确保 `pytest -k` 选中实际测试；退出码 5（未收集测试）不能算通过。
- 项目当前没有 Git 仓库，不自行初始化、提交或推送。编写 .gitignore 是交付文件的一部分，不代表执行 Git 操作。

## T1：建立项目包与开发环境

**状态：** 已完成并验证。  
**文件：** pyproject.toml、.gitignore、三个包的 __init__.py。  
**依赖：** 四份文档全部批准。  
**对应：** N5；plan 的构建与包组织。

**步骤：**
1. 检查 Python 版本并创建 .venv；声明 setuptools 构建、src 包发现、MyCode 入口和 dev 可选依赖。
2. 声明 TUI 的 app.tcss 包数据，配置 pytest 异步模式及 Ruff 的 Python 3.11 目标。
3. 忽略 mycode.yaml、.venv、测试缓存、构建目录与 egg-info，不忽略示例和规格文档。
4. 创建最小包定义并安装本地开发依赖，保留后续模块的实现边界。

**验证：** `python3 --version` 确认 3.11+；`python3 -m venv .venv` 成功；`.venv/bin/python -m pip install -e '.[dev]'` 成功；`.venv/bin/python -c 'import mycode, textual, anthropic, openai, yaml, httpx'` 无错误。入口此时尚未实现，不验证启动。

## T2：定义消息、事件、Provider 与统一错误

**状态：** 已完成并验证。  
**文件：** models.py、errors.py、providers/base.py。  
**依赖：** T1。  
**对应：** F4、F5、F6、F8；plan 的核心接口。

**步骤：**
1. 定义 JsonValue、NativeContent、Message、TextDelta、ThinkingDelta、TurnCompleted 与 StreamEvent。
2. 按 plan 定义 Provider.stream 和 aclose 接口；定义共享超时策略。
3. 定义 ConfigError、ProviderError 及约定 kind，不默认保存或打印原始异常和敏感数据。
4. 在复制和读取消息时保护 native_content，确保历史不因外部修改而改变；具体行为测试放入会话任务。

**验证：** `.venv/bin/python -m compileall -q src/mycode` 成功；`.venv/bin/python -c 'from mycode.models import Message, NativeContent, TextDelta, ThinkingDelta, TurnCompleted; from mycode.providers.base import Provider; from mycode.errors import ConfigError, ProviderError'` 成功。

## T3：实现 YAML 配置与供应商选择

**状态：** 已完成并验证。  
**文件：** config.py、tests/test_config.py。  
**依赖：** T2。  
**对应：** F2、F3、F6；AC2、AC6。

**步骤：**
1. 实现 ProviderConfig、load_config、select_provider；api_key 不进入 repr。
2. 安全加载非空列表，校验必填字符串、未知字段、thinking 布尔类型、重复 YAML 键、唯一 name 和合法协议。
3. 校验并按 plan 规范化 base_url，拒绝完整端点、userinfo、query 和 fragment；保留自定义路径前缀。
4. 实现默认第一项和按 name 精确选择；OpenAI thinking 为 true 时明确报错。
5. 测试合法双供应商、缺字段、空值、类型错误、重复项、格式错误、文件缺失及安全错误输出。

**验证：** `.venv/bin/python -m pytest tests/test_config.py -q` 全部通过，默认和显式选择正确，非法配置不会回显假密钥或原始 YAML 行。

## T4：建立可控传输与假 Provider 测试设施

**状态：** 已完成并验证。  
**文件：** tests/conftest.py、tests/test_openai.py 中的传输设施测试。  
**依赖：** T2。  
**对应：** N6；AC3、AC5、AC8。

**步骤：**
1. 提供 httpx.MockTransport 与 AsyncByteStream，记录方法、URL、请求头、请求 JSON 和请求次数。
2. 使用 asyncio.Event 控制首个片段和后续片段的释放，允许在 SSE 及 UTF-8 中文字节内部切分。
3. 支持传输异常、流内错误、缺失结束事件和关闭状态观测。
4. 提供实现统一接口的假 Provider，可控制文本、思考、完成、失败和取消，不将测试能力暴露为产品配置。
5. 编写 transport_fixture 测试，直接通过 httpx 客户端验证片段门控、请求记录和关闭标记，不依赖尚未实现的 OpenAIProvider。

**验证：** `.venv/bin/python -m pytest tests/test_openai.py -k transport_fixture -q` 通过，后续片段未释放时迭代保持等待、请求记录准确、退出后关闭标记正确。

## T5：实现 OpenAI 正常请求与流式文本

**状态：** 已完成并验证。  
**文件：** providers/openai.py、tests/test_openai.py。  
**依赖：** T3、T4。  
**对应：** F4；AC3。

**步骤：**
1. 实现 OpenAIProvider、可注入 http_client、AsyncOpenAI 客户端、超时与 max_retries=0。
2. 构造 Chat Completions 请求，将成功历史转换为文本 messages，原样传入 model 并设置 stream=True。
3. 转换 index=0 的 delta.content；忽略角色声明和空增量；累计正式文本。
4. 正常 stop 且流正常结束后关闭响应，生成唯一 TurnCompleted；实现客户端 aclose。
5. 使用真实 SDK 消费模拟 SSE，测试准确路径和请求体、中文分片、增量先于后续片段到达、最终文本一致及流关闭。

**验证：** `.venv/bin/python -m pytest tests/test_openai.py -k streaming -q` 通过，首个增量在释放后续片段前已收到，真实 SDK 使用 T4 设施正常消费 SSE。

## T6：补全 OpenAI 异常结束与错误恢复

**状态：** 已完成并验证。  
**文件：** providers/openai.py、tests/test_openai.py。  
**依赖：** T5。  
**对应：** F8；AC5、AC8。

**步骤：**
1. 验证 finish_reason；拒绝缺失完成原因、length、content_filter、tool_calls、function_call 和未知原因。
2. 将认证、限流、服务端、连接和超时异常映射为安全 ProviderError，保留 CancelledError 的传播。
3. 在提前断流及消费者取消时清理响应，不发出 TurnCompleted。
4. 参数化测试异常响应、流错误、缺失结束事件和请求次数；确认同一 Provider 后续仍可请求。

**验证：** `.venv/bin/python -m pytest tests/test_openai.py -q` 全部通过；异常路径没有成功事件、没有自动重试，并可关闭资源。

## T7：实现 Claude 正常请求与流式文本

**状态：** 已完成并验证。  
**文件：** providers/anthropic.py、tests/test_anthropic.py。  
**依赖：** T3、T4。  
**对应：** F4；AC3。

**步骤：**
1. 实现 AnthropicProvider、可注入 http_client、AsyncAnthropic、共享超时与 max_retries=0。
2. 构造 Messages 请求、max_tokens=8192 和完整历史，关闭 thinking 时不发送启用参数。
3. 消费文本块的初始内容及增量，按 index 重建块，处理 ping 和元数据，避免重字或漏字。
4. 检查块闭合、message_stop 和正常 stop_reason，清理后产生唯一 TurnCompleted。
5. 用真实 SDK 验证 URL、请求体、中文跨字节分片和实时增量，并测试客户端关闭。

**验证：** `.venv/bin/python -m pytest tests/test_anthropic.py -k streaming -q` 通过，完整正式文本等于全部文本增量的拼接。

## T8：实现 Claude extended thinking 与原生历史

**状态：** 已完成并验证。  
**文件：** providers/anthropic.py、tests/test_anthropic.py。  
**依赖：** T7。  
**对应：** F6；AC6。

**步骤：**
1. thinking 为 true 时发送 enabled 模式与 budget_tokens=2048；保持 max_tokens=8192。
2. 转换 ThinkingDelta，完整重建 thinking/signature/redacted_thinking 内容，隐藏内容不转换为可见文本。
3. 将 SDK 内容转换为应用的 NativeContent JSON，按原顺序回传历史，不修改签名。
4. 验证开关请求差异、思考与正文分流、初始块内容、多块顺序、签名分片及第二轮原样回传。

**验证：** `.venv/bin/python -m pytest tests/test_anthropic.py -k thinking -q` 通过；公开思考增量实时到达，隐藏内容和签名不会进入可见事件。

## T9：补全 Claude 流内错误与截断处理

**状态：** 已完成并验证。  
**文件：** providers/anthropic.py、tests/test_anthropic.py。  
**依赖：** T8。  
**对应：** F8；AC5、AC8。

**步骤：**
1. 处理 HTTP 认证、限流、服务端和网络异常，处理 SSE error 事件。
2. 拒绝 max_tokens 截断、未闭合块、缺失 message_stop、tool_use、pause_turn 及未知结束原因。
3. 对明确的手动 thinking 不受支持错误给出安全提示；不切换模式、不重试。
4. 验证取消、异常清理和后续请求恢复，确认失败不产生 TurnCompleted。

**验证：** `.venv/bin/python -m pytest tests/test_anthropic.py -q` 全部通过；所有失败测试请求次数符合零重试约定，资源关闭可观测。

## T10：注册 Provider 并统一安全错误出口

**状态：** 已完成并验证。  
**文件：** providers/__init__.py、errors.py、tests/test_errors.py。  
**依赖：** T6、T9。  
**对应：** N1、N4；AC8、AC9。

**步骤：**
1. 实现 create_provider，两个 protocol 均返回统一 Provider，不在 TUI 或会话层按厂商分支。
2. 统一固定中文错误提示、状态码与密钥脱敏，避免原始响应体、异常和堆栈进入用户出口。
3. 配置 SDK 日志级别和过滤，避免 HTTP 调试信息及密钥输出；不保存请求或会话日志。
4. 测试工厂归属、假密钥在错误字符串和日志中的过滤，测试 ProviderConfig repr。

**验证：** `.venv/bin/python -m pytest tests/test_errors.py -q` 通过；`.venv/bin/python -m ruff check src/mycode/providers src/mycode/errors.py` 通过。

## T11：实现成功会话历史与原子提交

**状态：** 已完成并验证。  
**文件：** conversation.py、tests/test_conversation.py。  
**依赖：** T2、T4。  
**对应：** F5；AC4、AC9。

**步骤：**
1. 实现 Conversation、只读 history 快照、busy 和 stream_turn。
2. 空白输入及忙时拒绝新请求；将成功历史与当前 user 组成快照传给 Provider。
3. 转发增量，在 TurnCompleted 上无 await 地提交一对完整消息，保留 native_content。
4. 测试两轮快照、新会话空历史、完成前无提交、成功后原子提交及外部修改不会污染历史。

**验证：** `.venv/bin/python -m pytest tests/test_conversation.py -k history -q` 通过；同一个会话测试可替换两个 Provider 而不改会话流程。

## T12：实现会话取消、失败回滚与关闭

**状态：** 已完成并验证。  
**文件：** conversation.py、tests/test_conversation.py。  
**依赖：** T11。  
**对应：** F5、F7、F8；AC5、AC7、AC8。

**步骤：**
1. 在所有出口恢复 busy；取消和失败时丢弃本轮待提交问答，保留此前成功历史。
2. 显式关闭异步迭代器和流，aclose 关闭 Provider；不吞 CancelledError。
3. 测试首段后取消、异常后再提问、消费者提前退出及忙时重复提交。
4. 测试完成事件前取消不提交、完成提交后取消不回滚，关闭无遗留请求。

**验证：** `.venv/bin/python -m pytest tests/test_conversation.py -q` 全部通过，下一请求的快照准确且所有结束状态 busy=False。

## T13：实现输入和轮次展示组件

**状态：** 已完成并验证。  
**文件：** tui/widgets.py、tests/test_tui.py。  
**依赖：** T2、T4。  
**对应：** F1、F6；AC1、AC6。

**步骤：**
1. 实现 TurnView、ChatInput 与 TurnWidget；区分用户、思考、正文和结束状态。
2. Enter 发送、Alt+Enter 换行，空白输入不发送，避免默认键绑定导致重复事件。
3. 按事件追加纯文本，首次思考时显示展开面板，完成后仍可折叠；签名和隐藏思考不进入组件。
4. 安全显示控制字符，不解释模型内容的 markup；保留正常换行和制表符。
5. 使用最小测试 App 与 Pilot 验证组件，不依赖尚未完成的 MyCodeApp。

**验证：** `.venv/bin/python -m pytest tests/test_tui.py -k widgets -q` 通过；中文、方括号、Markdown 字符按纯文本显示，思考与正文分开且可折叠。

## T14：实现全屏布局、滚动与尺寸适配

**状态：** 已完成并验证。  
**文件：** tui/app.py、tui/app.tcss、tests/test_tui.py。  
**依赖：** T13。  
**对应：** F1、N2；AC1、AC9。

**步骤：**
1. 建立 MyCodeApp 的状态栏、可聚焦 VerticalScroll 和固定 5 行输入区。
2. 显示 provider name、model 和状态，不显示密钥或基础地址。
3. 实现 Tab 焦点移动、对话区按键/鼠标滚动和思考标题切换。
4. 底部自动跟随新内容，上滚后暂停跟随；调整尺寸时软换行且输入区保持可见。

**验证：** `.venv/bin/python -m pytest tests/test_tui.py -k layout -q` 通过；在 120×40、80×24、60×18 的测试终端中检查固定输入区与对话区，尺寸改变和上滚不打断交互。

## T15：连接异步 worker、会话与增量显示

**状态：** 已完成并验证。  
**文件：** tui/app.py、tests/test_tui.py。  
**依赖：** T12、T14。  
**对应：** F1、F4、F5、F8；AC1、AC3、AC5、AC8。

**步骤：**
1. 提交时立即设置 TUI 忙状态、挂载轮次组件、清空已提交输入并启动唯一 worker。
2. 消费 Conversation.stream_turn，分别更新正文、思考和成功状态，不等待流全部完成。
3. 显示安全失败提示并保留部分内容，在 finally 恢复状态与输入焦点。
4. 生成期间允许编辑下一条输入但拒绝发送，验证 worker 启动前后的连续 Enter 都不会发起重复请求。
5. 用可控假 Provider 验证实时显示、思考分区、失败后恢复及历史隔离。

**验证：** `.venv/bin/python -m pytest tests/test_tui.py -k worker -q` 通过；后续片段被阻塞时界面已显示首段，只有一个请求，异常后能继续提问。

## T16：实现全局取消、退出及资源清理

**状态：** 已完成并验证。  
**文件：** tui/app.py、tests/test_tui.py。  
**依赖：** T15。  
**对应：** F7；AC5、AC7、AC9。

**步骤：**
1. 使用优先绑定实现 Ctrl+C 与 Ctrl+D，覆盖输入组件默认行为。
2. 生成时 Ctrl+C 取消并等待 worker，标记部分回复取消；清理期间重复取消忽略且禁止发送。
3. 空闲 Ctrl+C 退出；Ctrl+D 在空闲或生成时都执行清理后退出。
4. 确保关闭一次、无孤立任务或客户端；测试取消结束后继续输入和请求。

**验证：** `.venv/bin/python -m pytest tests/test_tui.py -q` 全部通过，Pilot 可观测取消、退出与资源关闭，已成功历史不会因后续取消丢失。

## T17：完成 CLI、配置示例与使用文档

**状态：** 已完成并验证。  
**文件：** cli.py、__main__.py、README.md、mycode.example.yaml、pyproject.toml、tests/test_cli.py。  
**依赖：** T3、T10、T16。  
**对应：** F2、F3；AC2、AC10。

**步骤：**
1. 实现 main、--config、--provider 和 --help；默认当前目录 mycode.yaml 与列表第一项。
2. 组装配置、Provider、Conversation、MyCodeApp；配置失败时全屏启动前安全退出并返回非零状态。
3. 让 python -m mycode 与安装后的 MyCode 使用同一入口；验证参数传递，不在单元测试中发起真实 API 请求。
4. 提供双供应商六字段示例；README 写明安装、基础地址、快捷键、纯文本显示、当前会话记忆、thinking 模型边界和 tmux 验收要求。

**验证：** `.venv/bin/python -m pytest tests/test_cli.py -q` 通过；`.venv/bin/MyCode --help` 与 `.venv/bin/python -m mycode --help` 正常；示例可被配置加载器解析，但不使用占位密钥发起请求。

## T18：补全协议与界面集成的密钥保护验证

**状态：** 已完成并验证。  
**文件：** errors.py、tests/test_errors.py、test_config.py、test_cli.py、test_openai.py、test_anthropic.py、test_tui.py。  
**依赖：** T17。  
**对应：** N4；AC8。

**步骤：**
1. 在非法配置、模拟响应体、SDK 异常和日志中植入独有假密钥。
2. 经过 CLI、Provider 和 TUI 用户出口验证密钥、请求头、原始响应和堆栈均不显示。
3. 在 API 返回的可见文本包含配置密钥的场景中验证展示脱敏；脱敏仅影响展示，不修改会话的协议签名和原生内容。
4. 修复任何泄露出口并重跑受影响测试；不记录或打印真实密钥。

**验证：** `.venv/bin/python -m pytest tests/test_errors.py tests/test_config.py tests/test_cli.py tests/test_openai.py tests/test_anthropic.py tests/test_tui.py -q` 通过，捕获输出和日志中不存在独有假密钥。

## T19：执行整体检查与安装包验证

**状态：** 已完成并验证。  
**文件：** 全部实现和测试文件、pyproject.toml；构建产物在忽略目录。  
**依赖：** T18。  
**对应：** N5、N6；构建与集成验收。

**步骤：**
1. 执行全量测试、Ruff 和语法编译，修复失败后只重跑相关检查，最后确认全量结果。
2. 构建 wheel 与 sdist，检查 wheel 包含 app.tcss 与命令入口。
3. 在临时隔离虚拟环境安装本次 wheel，执行 MyCode --help 和配置错误启动检查，确认不依赖源码目录。
4. 记录 Python、操作系统与依赖版本；平台兼容只报告实际验证范围。

**验证：** `.venv/bin/python -m pytest -q`、`.venv/bin/python -m ruff check .`、`.venv/bin/python -m compileall -q src tests`、`.venv/bin/python -m build` 均成功；用标准库 zipfile 检查新生成 wheel 含 mycode/tui/app.tcss；隔离环境安装该 wheel 后在项目外运行 MyCode --help 成功，配置错误返回非零且无堆栈。

## T20：按 checklist 执行 tmux 端到端验收

**状态：** 已完成并验证；真实 API 覆盖当前 DeepSeek Flash 的两种兼容协议配置，原生 Claude 未配置。  
**文件：** 已批准的 checklist.md、task.md；本地被忽略的真实配置与脱敏证据。  
**依赖：** T19，checklist.md 已批准。  
**对应：** AC1–AC10，特别是 AC10；AGENTS.md。

**步骤：**
1. 检查 tmux、已安装 MyCode、真实供应商配置及模型权限；只检查凭据是否可用，不输出密钥。缺少条件时继续执行不依赖它的检查，并记录受影响未验收项。
2. 使用独立 tmux 会话，分别启动 `.venv/bin/MyCode --config mycode.yaml --provider <实际 name>`；会话只由本任务创建与清理，不操作用户已有 tmux 会话。
3. 输入真实请求：“请用中文分五点说明 Python 异步编程的用途，每点至少两句话。”在生成结束前多次观察并截取脱敏终端画面，确认内容逐步增加且输入区固定。
4. 分两轮发送“请记住临时口令：青竹742。”及“刚才的临时口令是什么？”，观察回答；退出重启后询问口令，结合自动化请求检查确认新会话无旧历史。
5. 对支持手动 extended thinking 的 Claude 配置启用 thinking，输入需要推理的真实问题，验证独立思考区流式显示及完成后折叠。
6. 在较长回复生成时测试滚动、尺寸调整、Ctrl+C 取消并继续提问；分别测试空闲 Ctrl+C 和生成中 Ctrl+D 退出。Enter、Alt+Enter 和空白输入也在真实终端验证。
7. 对照 checklist 每项记录通过、失败、未验收或不适用及实际证据；工具调用明确记为本轮不适用。失败修复后重新测试受影响项。
8. 清理本任务创建的 tmux 会话，汇报通过数、失败/未验收项、真实后端结果和环境限制。

**验证：** `tmux -V` 可用；实际 tmux 会话中观察到两个后端的真实增量回复、多轮记忆、Claude thinking 与取消/退出行为；checklist.md 每项都有实际结果。没有真实凭据时仅能标记相关项未验收，不能以模拟 SSE 或预期结果替代通过。

## 执行顺序

串行推荐顺序为 T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20。

每个任务的必要依赖以上述任务小节为准，推荐串行顺序满足全部依赖。T4 独立验证传输设施，T5 起再通过真实 SDK 验证协议适配，不存在循环依赖。

## 覆盖与自检

| plan 组件/约束 | 任务 |
| --- | --- |
| 打包、依赖、安装后命令与样式 | T1、T17、T19 |
| 核心消息与统一 Provider 接口 | T2、T10 |
| 配置及供应商选择 | T3、T17 |
| OpenAI SSE 与失败结束 | T4–T6 |
| Claude SSE、thinking 与签名 | T4、T7–T9 |
| 成功历史、失败回滚与关闭 | T11、T12 |
| 全屏输入、折叠、纯文本和布局 | T13、T14 |
| worker、实时更新、取消与退出 | T15、T16 |
| 错误脱敏与密钥保护 | T3、T10、T18 |
| 全量测试、构建、真实 tmux 验收 | T19、T20 |

- 文件清单覆盖 plan 的所有实现及测试文件，任务接口沿用 plan，不引入新产品能力。
- T1–T20 均有文件、依赖、具体步骤与可观测验证；AC1–AC10 均有对应任务。
- 任务按聚焦工作单元拆分，实际耗时以执行情况为准，不以估时或编写代码代替验证通过。
- 四份文档均已批准；实现与验证结果见下方进度和 checklist.md。

## 实现进度记录

- T1–T3：环境导入、语法检查通过；配置测试 28 项通过。
- T4–T9：真实 SDK + 可控 HTTP SSE 测试 37 项通过，包括中文字节分片、思考签名、异常结束及取消清理。
- T10–T12：工厂、安全日志及会话测试 10 项通过；Provider 与错误模块 Ruff 检查通过。
- 当前只完成上述自动化验证，尚未执行 TUI、构建和 tmux 真实 API 验收。

- T13–T18：TUI、CLI、安全展示与退出测试通过；补充终端 Meta+Enter 原始字节回归、鼠标滚动与退出无持久化检查。
- T19：最终自动化 102 项通过，Ruff/compileall、wheel/sdist 构建及项目外隔离安装验证通过。
- 首次 T20：tmux 本地模拟 SSE 的14个场景通过；当时用户暂无配置，真实检查未验收。随后用户提供配置，补充验收结果见下方记录。
- 构建补充 MANIFEST.in，确保源码包含示例与 conftest；补充 tests/tmux_smoke.py 用于复现模拟终端验收。这些文件用于既定构建和验收，不扩展产品范围。

## 真实 API 补充验收

- 用户提供 mycode.yaml 后，使用专用 tmux 会话分别启动两个配置，当前模型均为 deepseek-flash。
- C36–C40 现已在当前配置范围通过，checklist 40/40；T20 标记完成。原生 Claude 模型未配置，未声称实测通过。
- 真实流式、多轮口令、重启、公开思考、折叠/展开、追问、滚动、尺寸调整、取消恢复及退出清理均有脱敏证据，见 evidence/tmux-real.json。
- 初始12球题触发服务输出上限，应用正确报错并恢复输入；使用较短概率题复测思考和正文后通过，保留原始截断记录。
- 新增 tests/tmux_real.py 支持复现真实配置验收及 --thinking-only 单独复测，不修改用户配置。最终全量自动化仍为102项通过（6.89s），Ruff与compileall通过。
