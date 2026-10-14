# Agent Loop Plan

## 输入与审批状态

依据已批准的 [spec.md](spec.md) 制定。用户最终批准依据：“确认批准完整 spec.md，继续进入 plan.md 技术设计。”

本技术设计已逐段确认：

1. 架构划分：用户回复“确认这一架构划分，继续定义核心数据结构与接口。”
2. 核心数据结构与接口：用户回复“确认”。
3. 模块交互、文件组织与技术决策：用户回复“确认”。

完整 plan 已获最终批准，依据用户回复：“确认批准完整 plan.md，继续进入 task.md 任务拆解。”当前进入任务拆解；checklist 和实现代码仍须遵循后续审批门槛。

## 架构概览

继续使用 Python 3.11+、现有异步 Provider、Textual TUI 和六个工具，不新增运行时依赖。

| 组件 | 职责 | Spec 归属 |
| --- | --- | --- |
| AgentLoop | 编排模型请求、工具批次、历史提交、迭代计数和停止条件，输出统一运行事件 | F1、F2、F3、F7、F8 |
| StreamCollector | 转发文本及思考增量，收集完整响应与请求用量 | F4、F8 |
| ToolScheduler | 整批预检、只读并发、副作用屏障、活动任务回收和结果收齐 | F5、F7 |
| HistoryStore | 提供历史快照，成组保存调用及配齐的结果 | F7 |
| Conversation | 持有历史、模式、最近计划及单次运行生命周期，委托 AgentLoop 工作 | F6、F7 |
| Provider | 双协议解析、模式提示传递、原生思考保留及用量归一化 | F4、F8 |
| TUI | 输入与命令处理，按迭代消费事件，展示状态、结果与用量，发出取消请求 | F3、F6、F8 |

调用关系：

```text
CLI 装配 → TUI → Conversation → AgentLoop
                                  ├→ StreamCollector → Provider
                                  ├→ ToolScheduler → ToolExecutor → 工具
                                  └→ HistoryStore
                 AgentEvent ←─────┘
TUI ← 持续消费 AgentEvent
```

HistoryStore 由 Conversation 持有并交给 AgentLoop 使用，提交时机由 AgentLoop 控制。Agent、Provider、工具均不导入界面模块。

## 核心数据结构

以下为计划接口，具体实现须等四份文档全部获批后开始。不可变结构使用 frozen dataclass；含嵌套可变数据的快照沿用深拷贝保护。

### 运行配置与状态

```python
AgentMode = Literal["plan", "execute"]
StopReason = Literal[
    "completed", "iteration_limit", "cancelled", "unknown_tool_limit",
    "stream_error", "invalid_response", "internal_error",
]

@dataclass(frozen=True)
class AgentOptions:
    max_iterations: int = 20
    unknown_tool_batch_limit: int = 3
    max_read_concurrency: int = 4
```

构造时拒绝布尔值、非整数和非正数；本轮不新增 YAML 字段或 CLI 配置项。测试可以注入较小上限。单次运行另持有 run_id、iteration、unknown_tool_batches、当前阶段、用量累计和历史更新标记；这些状态每次重置。run_id 使用新 UUID，不写入日志。

阶段为 `model`、`tools`、`cancelling`、`finished`。iteration 为已开始的模型请求次数，取消发生在首次请求之前时为 0。

### 消息、响应与用量

复用现有 Message、ToolCall、ToolResult、ToolResultError、NativeContent。工具拒绝结果使用现有 `status="error"`，错误码为 `mode_restricted`；未知工具使用 `unknown_tool`，取消使用 `cancelled`。

```python
@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None
    output_tokens: int | None

@dataclass(frozen=True)
class CollectedResponse:
    message: Message
    usage: TokenUsage

@dataclass(frozen=True)
class UsageSummary:
    known_input_tokens: int
    known_output_tokens: int
    input_complete: bool
    output_complete: bool
    requests_started: int
    requests_with_usage: int
```

TokenUsage 放在公共 models 模块，避免 Provider 依赖 Agent 编排。有效值为非负整数或 None；缺失值不转成 0。累计完整性分别按输入、输出计算；requests_with_usage 统计至少有一项有效用量的请求。

每次请求维护独立用量槽位，用最新归一化值替换，累计时每个槽位只计一次。失败、取消请求同样计入 requests_started；已收到的有效用量保留。尚无请求时界面不展示 Token 数值，没有有效用量时显示“不可用”，部分请求或字段缺失时显示已知累计并标注“不完整”。

### 对外 Agent 事件

所有事件为不可变结构，公共字段为 `run_id: str`、`iteration: int`。以下字段为各事件的额外字段：

| 事件 | 字段与语义 |
| --- | --- |
| TextDelta | text：当前迭代的文本增量 |
| ThinkingDelta | text：公开思考增量，不含签名和隐藏内容 |
| ToolCallReceived | call: ToolCall：完整响应中的调用，仅代表收到 |
| ToolStarted | call_id、name：实际开始执行 |
| ToolFinished | result: ToolResult：成功、失败、拒绝或取消结果 |
| UsageUpdated | request_usage: TokenUsage、total: UsageSummary |
| ProgressUpdated | phase、max_iterations、tools_total、tools_finished |
| RunFinished | reason: StopReason、message: Message 或 None、usage: UsageSummary、error_message: str 或 None、history_updated: bool |

AgentEvent 为上述事件的联合类型。ToolCallReceived 按模型顺序发布，工具结果按实际完成顺序发布，历史按原调用顺序保存。工具展示键使用 `(run_id, iteration, call_id)`，不假定 ID 跨迭代唯一。

RunFinished.message 只在正常完成时携带最终无工具回复；错误、到限、取消等路径为 None，已展示的部分内容仍留在界面。错误说明经过现有 SecretRedactor 处理。

### 内部流事件

ProviderEvent 使用公共模型层的文本、思考、用量和 ResponseCompleted 事件；这些事件不含 run_id，由 AgentLoop 加上运行元信息后转换为对外事件。Provider 内部用量事件为请求截至当前的归一化快照，不直接当作跨请求增量累加。

CollectorEvent 为 Provider 的文本、思考、用量事件与 CollectedResponse 的联合。CollectedResponse 只在底层流完整消费并关闭后产生一次；ResponseCompleted 属于内部协议完成标记，不透传到界面。

### 工具批次

```python
PreparedKind = Literal["read_only", "side_effect", "unknown", "rejected"]

@dataclass(frozen=True)
class PreparedCall:
    index: int
    call: ToolCall
    kind: PreparedKind
    arguments: dict[str, JsonValue] | None
    immediate_result: ToolResult | None

@dataclass(frozen=True)
class PreparedBatch:
    items: tuple[PreparedCall, ...]
    contains_unknown: bool

@dataclass(frozen=True)
class BatchCompleted:
    results: tuple[ToolResult, ...]
```

SchedulerEvent 为内部工具开始、工具结束和 BatchCompleted 事件的联合，不含运行元信息。BatchCompleted 只供循环消费，包含整批按原顺序排列的唯一结果。PreparedCall.arguments 是校验后的快照；unknown 和 rejected 使用预制错误结果，不启动工具。

### 最近计划

```python
@dataclass(frozen=True)
class PlanSnapshot:
    request: Message
    reply: Message
```

仅 plan 模式正常完成且最终回复有非空文本时更新。取消、失败、到限不覆盖已有计划。计划只保存在当前 Conversation 内存中；/do 显式附带最近计划文本，不解析自然语言步骤，不自动消费或删除计划。

## 核心接口与模块设计

### AgentLoop

```python
AgentLoop(provider: Provider, scheduler: ToolScheduler, options: AgentOptions)

def run(
    self, user_text: str, *, history: HistoryStore,
    mode: AgentMode, cancel: asyncio.Event,
) -> AsyncIterator[AgentEvent]: ...
```

职责：固定模式工具集和提示，逐次请求模型、转发事件、判断响应、提交历史、统计未知工具批次和用量。单次运行的可变状态局限于 run。正常运行流结束前必须发出一个 RunFinished，之后不再发事件；已关闭消费者的清理规则见取消章节。

### StreamCollector

```python
def collect(
    self, events: AsyncIterator[ProviderEvent], *, cancel: asyncio.Event,
) -> AsyncIterator[CollectorEvent]: ...
```

即时转发文本、思考和用量，同时累积文本并保留完成消息。验证恰好一个 ResponseCompleted、assistant 角色、累计文本与完成消息一致，以及完成后的事件顺序。完成标记之后出现其他内容视为协议错误；Provider 须在标记前发出用量。没有完成标记、流异常或取消时不产生 CollectedResponse。

完整工具参数与原生内容以 Provider 已拼接的 Message 为准，不丢弃 NativeContent，也不以界面文本反向构造历史。收集器监听取消，停止活动的下一事件读取并关闭底层迭代器。已转发的有效用量由 Agent 保留。

### ToolScheduler 与 ToolExecutor

```python
ToolScheduler(executor: ToolExecutor, max_read_concurrency: int = 4)

def validate_batch(
    self, calls: tuple[ToolCall, ...], *, mode: AgentMode,
) -> PreparedBatch: ...

def execute_batch(
    self, batch: PreparedBatch, *, cancel: asyncio.Event,
) -> AsyncIterator[SchedulerEvent]: ...

# 执行器新增可复用校验入口，原有执行入口保留。
def validate_arguments(self, call: ToolCall) -> dict[str, JsonValue]: ...
async def execute(self, call: ToolCall) -> ToolResult: ...
```

预检先验证所有调用 ID 非空且批内唯一，再分类：未知工具生成 unknown_tool；plan 模式副作用调用生成 mode_restricted；其余已知可执行调用使用同一 strict JSON 与 JSON Schema 校验规则。未知或已拒绝调用不会执行，不要求其符合不存在或不可用工具的参数 Schema。可执行项中任意参数无效时，整批预检失败，尚未产生任何工具副作用。

执行器自身仍保留防御性校验。工具注册元信息增加 `read_only: bool = False`，只有 read_file、find_files、search_code 显式标记为 True；注册中心支持按该属性筛选定义。元信息由应用维护，不接受模型输入修改。

连续只读项组成并发段，最多启动 4 个，完成一个后再补入一个。每个副作用项独占执行。unknown/rejected 在其顺序位置返回预制结果，可保守地作为分段边界，不跨越副作用项。单工具失败不取消同批其他工具。

并发任务经有界内部队列汇报事件，容量采用 `max(8, 2 * max_read_concurrency)`，形成背压。结果槽位按 PreparedCall.index 写入，任务完成和取消只允许填充一次；所有槽位完整且活动任务已回收后发布 BatchCompleted。调度器异常退出时同样清理并补齐已进入执行阶段的批次结果，再由循环以 internal_error 停止。

### HistoryStore

```python
def snapshot(self) -> tuple[Message, ...]: ...
def commit_reply(self, pending_user: Message | None, reply: Message) -> None: ...
def commit_batch(
    self, pending_user: Message | None, reply: Message,
    results: tuple[ToolResult, ...],
) -> None: ...
```

提交接口同步执行，先校验后一次性追加深拷贝。commit_batch 要求结果数量与调用数量相等，调用 ID 对应且顺序一致；保存 assistant 调用消息及单条内部 tool 结果消息。Provider 继续转换为各协议要求的配对格式。

首次成功提交时附带 pending_user，之后传 None，避免每次迭代重复添加用户请求。完整普通回复通过 commit_reply 保存。已开始执行的整批即使取消也保存全部实际或取消结果；未进入执行阶段的响应不保存。单次运行首个响应失败且无已执行批次时，不新增用户历史。

### Conversation 与模式

```python
Conversation(provider: Provider, executor=None, *, options: AgentOptions | None = None)

def set_mode(self, mode: AgentMode) -> None: ...
def stream_turn(self, user_text: str) -> AsyncIterator[AgentEvent]: ...
def execute_plan(self) -> AsyncIterator[AgentEvent]: ...
def cancel(self) -> None: ...
async def aclose(self) -> None: ...
```

保留 history、busy 只读属性，增加 mode、latest_plan。Conversation 装配 AgentLoop、调度器、历史存储，负责运行锁和取消信号；TUI 不再轮询工具结果或执行阶段来恢复界面状态。

set_mode 在忙碌或关闭时拒绝；stream_turn 拒绝空输入、忙碌和已关闭状态。上述属于入口错误，不算已启动运行，不产生运行结束事件。无 executor 的既有普通聊天用法仍可运行，但不开放工具。

空闲时精确命令 /plan 切换 plan 模式，不调用模型；/do 切回 execute 模式并调用 execute_plan。有计划时构造明确的执行请求并附带最近计划；无计划时显示本地提示，不请求模型。忙碌时两个命令均拒绝，不改变当前模式；本轮不扩展带参数命令语法。

mode 模块提供 `build_mode_prompt(mode: AgentMode) -> str`。规划提示要求只读调查并输出可执行计划，不自动执行；执行提示要求根据工具结果继续推进，完成后回复。模式提示放在独立系统提示参数中，不混入用户历史。

cancel 幂等设置当前运行的 asyncio.Event，不直接取消 TUI 消费 worker。aclose 幂等：先取消并等待活动运行清理，再关闭 Provider、清空内存历史及计划。正常运行消费至 RunFinished 后恢复空闲；无法继续消费时通过迭代器关闭路径清理。

### Provider 适配

```python
def stream(
    self, messages: Sequence[Message], *,
    tools: Sequence[ToolDefinition] = (), allow_tools: bool = False,
    system_prompt: str = "",
) -> AsyncIterator[ProviderEvent]: ...
async def aclose(self) -> None: ...
```

OpenAI 将独立提示转换为请求系统消息，Anthropic 使用其请求系统提示字段。plan 模式仅发送只读工具定义，execute 模式发送全部工具定义；循环各次请求都允许所提供工具继续被调用，不再请求禁用工具的总结。

Provider 在现有协议解析基础上收集供应商提供的用量，按请求归一化后输出快照。OpenAI 适配须允许没有候选内容的用量片段；Anthropic 适配合并开始及后续片段中的用量字段，区分累计值与增量。完成消息在底层流消费完成后产生，确保尾部用量不被提前丢弃。具体 SDK 字段在实现时以已安装 SDK 与协议测试验证，不支持的网关明确缺失，不自动重发请求。

保留现有工具调用碎片解析、结束原因检查、原生思考签名和隐藏块，仅公开思考文本供界面展示。沿用 SDK 禁用自动重试与既有网络超时。异常继续映射为脱敏 ProviderError。

### TUI 与装配

CLI 保留现有启动参数与配置读取方式，传入 AgentOptions 默认值。TUI 消费 Conversation 返回的 AgentEvent，不依赖 Provider 或调度器内部类型。

TurnView 改为按 iteration 管理文本、思考和工具；替换 initial/summary 两段结构，保留思考折叠、纯文本安全展示、滚动跟随及部分输出。状态栏显示当前模式、迭代/上限、阶段和本次运行用量。工具状态包含收到但未执行、执行中、成功、失败和取消；到限或校验失败的收到调用不能显示为已执行。

RunFinished 驱动最终状态和停止说明；取消或错误时按 history_updated 明确已有记录被保留，避免固定显示“本轮未计入上下文”。生成期间允许编辑下一条输入，但不启动第二次运行；模式命令给出拒绝提示。Ctrl+C 请求取消后继续消费清理事件；退出先等待运行结束，再关闭会话。

## 模块交互与停止顺序

1. Conversation 检查入口，固定 mode、创建取消信号并取得运行锁。
2. AgentLoop 检查取消，开始模型请求并增加 iteration；发布 ProgressUpdated。输入上下文为历史快照及尚未提交的本次用户请求。
3. StreamCollector 实时传递内容及用量，流完整结束后返回 CollectedResponse；取消或流异常不返回完整响应。
4. 收到完整响应后再次检查取消。无工具调用时提交回复并正常结束，即使这是第 20 次请求。
5. 有调用时发布 ToolCallReceived。若 iteration 达到 max_iterations，以 iteration_limit 结束；该响应不执行、不保存。否则整批预检；失败以 invalid_response 结束。
6. 进入执行阶段前提供取消检查点。进入后必须补齐整批结果，按只读并发段及副作用屏障执行，并实时转发工具事件。
7. 收齐结果后提交历史，首次提交同时保存用户请求。取消已发生时优先结束为 cancelled，不再请求模型。
8. contains_unknown 为 True 时连续计数加一，否则重置为 0；达到上限则以 unknown_tool_limit 结束，不额外请求总结；未达到则继续下一次模型请求。
9. 完成清理并汇总最终用量后发布唯一 RunFinished；不再有后续事件。Conversation 更新正常规划结果并结束运行生命周期，释放忙碌状态。

明确停止优先关系：活动操作观察到取消后走取消清理；已确定正常完成并发布结束事件后，再次取消无效。完整响应先判断有无工具，因此到限的无工具回复仍正常完成；到限工具响应不进入校验或执行。流和预检错误均不执行当前响应工具；普通工具执行错误是结果，不直接停止循环。

## 取消与资源清理

- 模型流：同时监听取消信号与下一事件读取。取消后中止活动读取，关闭流及响应连接，保留已展示文本和有效用量，不提交未完成回复。
- 工具阶段：停止启动排队任务，取消活动任务并等待它们清理；保留已完成结果。尚未启动调用补 cancelled 结果；活动工具依据现有 result_frame/result_data 保存实际完成信息或取消信息。
- 文件提交附近取消：沿用 outcome_unknown 表示无法确认的写入结果，不声称操作回滚。命令及搜索、文件工作进程继续沿用可终止进程管理。
- 队列背压：取消控制路径不依赖队列能立即写入；进入清理时由调度器回收任务、读取结果槽位，再有序输出缺失结果，避免生产者因队列满而阻止退出。
- 所有流与任务由其创建组件持有，通过 try/finally 和显式关闭回收。清理期间重复取消不跳过历史补齐或进程回收。
- 正常取消通过显式信号，消费者持续消费，因而能观察到唯一 RunFinished。消费者提前 aclose、外部任务取消或界面意外卸载时，关闭路径同样执行资源清理与历史补齐；已关闭的迭代器无法交付事件，不在 GeneratorExit/finally 中继续 yield，由 Conversation 保留最终运行状态供关闭处理使用。

## 文件组织

| 操作 | 文件 | 内容 |
| --- | --- | --- |
| 新建 | `src/mycode/agent/__init__.py` | 保持轻量，不在包导入时反向导入编排模块 |
| 新建 | `src/mycode/agent/events.py` | Agent 事件、StopReason、UsageSummary |
| 新建 | `src/mycode/agent/options.py` | AgentOptions 及校验 |
| 新建 | `src/mycode/agent/collector.py` | StreamCollector、CollectedResponse |
| 新建 | `src/mycode/agent/history.py` | HistoryStore |
| 新建 | `src/mycode/agent/mode.py` | AgentMode、PlanSnapshot、模式提示 |
| 新建 | `src/mycode/agent/loop.py` | AgentLoop 与单次运行状态 |
| 新建 | `src/mycode/tools/scheduler.py` | PreparedCall、PreparedBatch、BatchCompleted、ToolScheduler |
| 修改 | `src/mycode/models.py` | 公共 TokenUsage、Provider 用量事件，拆分 Provider 与 Agent 事件职责 |
| 修改 | `src/mycode/providers/base.py` | system_prompt 与事件契约 |
| 修改 | `src/mycode/providers/openai.py`、`anthropic.py` | 用量解析、模式提示和完整流检查 |
| 修改 | `src/mycode/tools/base.py`、`registry.py` | 只读元信息及定义筛选 |
| 修改 | `src/mycode/tools/files.py`、`search.py` | 显式声明三个读类工具 |
| 修改 | `src/mycode/tools/executor.py` | 抽取可复用参数校验，保留执行防御 |
| 修改 | `src/mycode/conversation.py` | 会话入口、运行锁、模式、计划、取消生命周期 |
| 修改 | `src/mycode/cli.py` | 默认配置与生命周期装配 |
| 修改 | `src/mycode/tui/app.py`、`widgets.py`、`app.tcss` | 命令、迭代视图、事件消费、状态及退出清理 |
| 新建 | `tests/test_agent_loop.py`、`test_agent_collector.py`、`test_agent_history.py` | 循环停止、事件、双路收集和历史一致性 |
| 新建 | `tests/test_tools_scheduler.py` | 并发上限、屏障、预检和取消 |
| 修改 | 现有 Provider、Conversation、工具、CLI、TUI 测试及 `tests/conftest.py` | 新契约适配与回归 |
| 新建 | `tests/tmux_agent.py` | 真实 API 与真实终端多批调用、规划执行验收 |
| 修改 | `README.md` | 运行行为、模式命令、停止条件、用量及测试说明 |
| 修改 | `MANIFEST.in` | 延续现有文档打包方式，包含本轮文档并排除证据 |

依赖方向：公共 models、options、mode、events、history 为底层；Provider 依赖公共模型及工具定义；scheduler 依赖 executor 与底层类型；collector 依赖公共模型；loop 依赖以上组件；Conversation 依赖 loop；TUI 依赖 Conversation 和事件。scheduler 不依赖 loop、Conversation 或 TUI，保持无环。

## 技术决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 循环与界面连接 | AsyncIterator[AgentEvent] | 延续现有流式消费方式，便于独立测试 |
| 并发事件传递 | 有界内部队列与固定活动任务上限 | 提供背压，避免输出和任务无限积压 |
| 工具任务生命周期 | 调度器统一持有、取消并等待回收 | 并发段和副作用屏障可验证，避免孤儿任务 |
| 参数检查 | 整批预检复用执行器校验规则 | 满足 AC5，防止先执行有效项再发现后续参数错误 |
| 历史提交 | 同步、成组追加完整调用与全部结果 | 失败和取消后仍有合法配对上下文 |
| 用量累计 | Provider 归一化单请求快照，Agent 按槽位累计 | 防止累计快照重复相加，保留缺失语义 |
| 模式限制 | 工具定义过滤加调度入口检查 | 模型越界调用也不能启动副作用工具 |
| 最近计划 | 保存正常规划最终文本并显式附入执行请求 | 不依赖模型反推最近计划，不增加计划解析系统 |
| 取消 | 显式信号，继续消费到结束 | 清理结果和停止原因可通过事件流展示 |
| 界面展示 | 每次请求按 iteration 分段 | 完整表达多轮循环，避免 initial/summary 二阶段假设 |
| 模式和系统提示 | 独立请求参数 | 不污染会话用户历史，双协议各自适配 |
| 验证 | 可控流测试加真实 tmux 对话 | 精确验证边界，同时验证实际模型和终端集成 |

## 验证设计与范围

后续 task 定义具体执行顺序，checklist 定义逐项验收与证据。实现阶段执行相关 pytest、ruff、compileall 和包构建，不新增无关测试范围。

| 需求 | 实现组件 | 验收编号 |
| --- | --- | --- |
| F1 | AgentLoop、HistoryStore | AC1 |
| F2 | AgentLoop、StreamCollector、ToolScheduler | AC2、AC3 |
| F3 | Agent 事件、AgentLoop、TUI | AC4 |
| F4 | StreamCollector、Provider、整批预检 | AC5 |
| F5 | ToolScheduler、只读元信息 | AC6 |
| F6 | Conversation、模式提示、工具过滤及入口检查 | AC7、AC8 |
| F7 | HistoryStore、取消信号、任务回收 | AC9、AC10 |
| F8 | Provider 用量、请求槽位累计、进度及界面 | AC11 |
| N1、N2、N6 | 协议与界面回归、脱敏 | AC12 |
| N7、N8 | 独立组件测试及真实 tmux 场景 | AC13 |

- AC1–AC3：可控模型分别模拟多批调用、普通回复、第 20 次到限、未知工具计数及重置；核对请求数、执行数和停止原因。
- AC4–AC5：独立消费事件，验证双路收集、唯一结束、尾部用量、截断、非法参数及调用 ID；校验失败不能产生副作用。
- AC6：使用可控同步点验证真实重叠、上限 4、副作用独占、读取新写入内容及原序回写，不用脆弱时间差作为唯一证据。
- AC7–AC8：验证模式定义过滤、执行入口拒绝、最近计划更新、/do 无计划、忙碌时拒绝切换及系统提示双协议适配。
- AC9–AC10：模型流、并发工具、排队项和文件/命令取消；验证资源回收、每调用唯一结果、成组历史与下一轮可引用，保留 Anthropic 原生思考。
- AC11：验证完整、部分缺失、全部缺失、重复快照和失败请求用量；累计值与已知供应商数据一致。
- AC12：现有聊天、六工具和 TUI 回归，检查滚动、输入、脱敏及不新增敏感日志。
- AC13：tmux 启动真实 MyCode，真实请求驱动至少两批依赖结果的工具操作；另跑 /plan 只读调查、确认未产生副作用、/do 执行并观察最终文件。记录实际供应商、模型、工具与回复，仅声明实际执行配置的通过结果；环境、凭据或模型能力缺失明确记为未验收。

## 设计自检

- F1–F8 均在架构表中有实现归属，AC1–AC13 均有验证路径；无功能缺口。
- 核心结构、方法参数、输出事件、历史提交条件、运行结束及关闭契约完整；内部事件不会直接进入界面。
- 依赖单向，无 Provider 到 AgentLoop 或工具到 TUI 的反向依赖。
- 到限工具不执行、第三批未知工具先回写、只读并发不跨副作用屏障，均与已批准 spec 一致。
- 唯一结束事件针对有效且持续消费的运行；提前关闭时先保证资源与历史清理，不向关闭的异步生成器 yield。
- 默认 20/3/4 保持一致，未加入额外停止预算、权限审批、上下文压缩或其他排除能力。
- 当前仅完成技术设计及文档自检，未执行功能测试或 tmux 验收，不将设计预期报告为已通过。
