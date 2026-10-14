# Agent Loop Tasks

## 输入、状态与执行约束

依据已批准的 [spec.md](spec.md) 和 [plan.md](plan.md)。plan 最终批准依据：“确认批准完整 plan.md，继续进入 task.md 任务拆解。”本任务文档已获最终批准，依据用户回复：“确认批准完整 task.md，继续进入 checklist.md 验收设计。”四份文档全部获批后已执行 T1–T30；相关验证均通过。

- 必须在 spec、plan、task、checklist 四份文档全部明确获批后开始实现和任务验证。
- 按编号顺序执行，依赖列用于说明必要前提，不表示授权委派或并行 Agent 工作。
- 每个任务围绕一个聚焦工作单元拆分，以 2–5 分钟为粒度目标；异步清理、协议适配和真实模型验收可能更久，不以估时判定完成。
- 任务验证通过后才能标记完成；验证失败则修复并重跑受影响检查。涉及范围实质变化先修订受影响文档并重新审批。
- 以下命令均在项目根目录执行，使用现有 `.venv`。单任务运行相关测试；完整测试、静态检查与构建集中在 T28，后续只有新改动或失败才重跑受影响验证。
- 新测试中的 `-k` 名称是本任务要求创建的测试名片段，必须实际收集到测试；无测试匹配、跳过或缺少环境不算通过。
- 真实 tmux 验收使用受控临时目录与真实配置，不将凭据写入证据，不修改上一章验收结果。仅记录实际运行的协议、模型与能力。

## 文件清单

| 操作 | 文件 | 职责与任务 |
| --- | --- | --- |
| 新建 | `src/mycode/agent/__init__.py`、`options.py`、`events.py`、`mode.py` | 包入口、配置、事件和模式结构；T1、T19 |
| 新建 | `src/mycode/agent/history.py` | 历史快照与成组提交；T2 |
| 新建 | `src/mycode/agent/collector.py` | 双路流收集及取消；T8–T9 |
| 新建 | `src/mycode/agent/loop.py` | 循环、停止、用量、清理；T14–T17 |
| 新建 | `src/mycode/tools/scheduler.py` | 预检、串行屏障、并发和结果收齐；T10–T13 |
| 修改 | `src/mycode/models.py`、`providers/base.py` | 公共用量与 Provider 契约；T1、T5 |
| 修改 | `src/mycode/providers/openai.py`、`anthropic.py` | 系统提示、用量和完整流；T6–T7 |
| 修改 | `src/mycode/tools/base.py`、`registry.py`、`files.py`、`search.py` | 只读属性与定义过滤；T3 |
| 修改 | `src/mycode/tools/executor.py` | 复用参数校验；T4 |
| 修改 | `src/mycode/conversation.py` | 会话入口、模式、计划、取消与关闭；T18–T20 |
| 修改 | `src/mycode/tui/widgets.py`、`app.tcss` | 按迭代展示；T21 |
| 修改 | `src/mycode/tui/app.py` | 消费事件、命令、取消和退出；T22–T24 |
| 修改 | `src/mycode/cli.py` | 默认配置及组件装配；T25 |
| 新建 | `tests/test_agent_loop.py`、`test_agent_collector.py`、`test_agent_history.py` | 循环、流和历史的行为验证；T1–T2、T8–T9、T14–T17 |
| 新建 | `tests/test_tools_scheduler.py` | 预检、并发、屏障、清理；T10–T13 |
| 修改 | `tests/conftest.py`、`test_openai.py`、`test_anthropic.py` | 新 Provider 契约、可控流与双协议集成；T5–T7、T26 |
| 修改 | `tests/test_tools_registry.py`、`test_tools_executor.py` | 只读过滤、复用校验与执行回归；T3–T4 |
| 修改 | `tests/test_conversation.py`、`test_tui.py`、`test_cli.py` | 模式、会话、界面与启动集成；T18–T26 |
| 新建 | `tests/tmux_agent.py` | 真实终端验收入口；T27 |
| 修改 | `README.md` | 功能与验证说明；T30 |
| 修改 | `MANIFEST.in` | 源码包包含本轮文档并排除验收证据；T28 |
| 验收时生成 | `specs/agent-loop/evidence/` | 本轮脱敏终端画面、结果与命令摘要；T28–T30 |
| 更新 | `specs/agent-loop/spec.md`、`task.md`、`checklist.md` | 实际进度与逐项结果；实现与验收阶段 |

checklist.md 在本任务文档获批后单独生成和审批，不提前标记任何功能通过。

## T1: 定义运行配置、模式和事件契约

**文件：** `src/mycode/agent/__init__.py`、`options.py`、`events.py`、`mode.py`，`src/mycode/models.py`，`tests/test_agent_loop.py`。
**依赖：** 无。
**关联：** F2、F3、F8；AC4、AC11。
**步骤：**
1. 建立轻量 agent 包，定义 AgentOptions、AgentMode、StopReason、TokenUsage、UsageSummary 和 plan 中的对外事件字段。
2. 使用不可变结构，分开 Provider 增量与包含 run_id/iteration 的 Agent 增量，移除对 initial/summary 的新代码依赖。
3. 保持默认 20/3/4，配置拒绝布尔值、非整数和非正数，用量保留 None 语义。
**验证：** `.venv/bin/python -m pytest -q tests/test_agent_loop.py -k contracts`；期望合法配置与事件可用，非法配置拒绝，缺失用量保持不可用。

## T2: 实现历史成组提交

**文件：** `src/mycode/agent/history.py`，`tests/test_agent_history.py`。
**依赖：** T1。
**关联：** F7；AC9、AC10。
**步骤：**
1. 实现 HistoryStore.snapshot、commit_reply、commit_batch，提交前检查调用/结果数量、ID 和顺序。
2. 同步成组追加用户、完整助手回复及结果，首次提交之后不重复用户请求。
3. 深拷贝嵌套原生内容与结果，拒绝非法提交时保持历史不变。
**验证：** `.venv/bin/python -m pytest -q tests/test_agent_history.py`；期望多批历史顺序正确、不重复用户、非法配对不修改历史、外部修改快照不影响存储。

## T3: 增加工具只读元信息与过滤

**文件：** `src/mycode/tools/base.py`、`registry.py`、`files.py`、`search.py`，`tests/test_tools_registry.py`。
**依赖：** T1。
**关联：** F5、F6；AC6、AC7。
**步骤：**
1. 增加 read_only 元信息，默认 False；仅 read_file、find_files、search_code 显式标记 True。
2. 注册中心支持只读定义筛选，保留重复注册和 Schema 校验。
3. 工具安全性由注册信息决定，不接受模型通过参数覆盖。
**验证：** `.venv/bin/python -m pytest -q tests/test_tools_registry.py`；期望全工具 6 个、只读工具恰好 3 个，未声明工具默认为副作用类。

## T4: 抽取共享参数校验

**文件：** `src/mycode/tools/executor.py`，`tests/test_tools_executor.py`。
**依赖：** T3。
**关联：** F4、F5；AC5、AC12。
**步骤：**
1. 提供 validate_arguments，复用 strict JSON、对象检查和 JSON Schema 校验，返回校验后的参数快照。
2. execute 调用同一校验逻辑，保留现有超时、错误封装、脱敏与取消信息。
3. 验证预检入口没有调用实际工具，非法 JSON、非对象和 Schema 不匹配均可识别。
**验证：** `.venv/bin/python -m pytest -q tests/test_tools_executor.py`；期望校验不产生副作用，执行器成功、失败、超时行为无回归。

## T5: 扩展 Provider 契约和可控流夹具

**文件：** `src/mycode/models.py`、`providers/base.py`，`tests/conftest.py`、`test_openai.py`、`test_anthropic.py`。
**依赖：** T1。
**关联：** F3、F4、F8；AC4、AC5、AC11。
**步骤：**
1. 定义公共 Provider 用量快照事件，Provider.stream 增加 system_prompt 默认参数。
2. 更新测试 Provider 接口，提供可捕获请求、分段响应、暂停点、关闭标记和用量片段的夹具。
3. 将协议完成标记与 Agent 运行结束事件分离，不依赖界面类型。
**验证：** `.venv/bin/python -m compileall -q src tests`；期望语法检查通过；新增模块可导入，现有测试流夹具可接收 system_prompt。双协议行为验证分别由 T6、T7 执行。

## T6: OpenAI 模式提示与用量解析

**文件：** `src/mycode/providers/openai.py`，`tests/test_openai.py`。
**依赖：** T5。
**关联：** F4、F6、F8；AC5、AC7、AC10、AC11。
**步骤：**
1. 将独立 system_prompt 转换为系统消息，保持工具调用及结果配对转换。
2. 检查已安装 SDK 支持的流用量字段与请求选项；解析尾部无候选用量片段，并在完成消息前发布用量。
3. 缺失用量标记 None；协议错误、截断不产生完整响应，不自动重试或为获取用量重发请求。
**验证：** `.venv/bin/python -m pytest -q tests/test_openai.py`；期望系统提示正确、JSON 碎片完整、尾部用量可用、缺失不记零、配对和错误行为正确。

## T7: Anthropic 模式提示与用量解析

**文件：** `src/mycode/providers/anthropic.py`，`tests/test_anthropic.py`。
**依赖：** T5。
**关联：** F4、F6、F8；AC5、AC7、AC10、AC11。
**步骤：**
1. 独立传入请求系统提示，保持同批 tool_result 格式和 ID 配对。
2. 检查 SDK 事件字段，将开始和后续用量归一化为请求快照，避免重复累加。
3. 保留 thinking、签名和隐藏原生块；仅公开思考文本，完整流结束后发布完成消息。
**验证：** `.venv/bin/python -m pytest -q tests/test_anthropic.py`；期望用量快照正确、缺失可识别、原生思考及多工具结果配对无回归。

## T8: 实现双路流式收集

**文件：** `src/mycode/agent/collector.py`，`tests/test_agent_collector.py`。
**依赖：** T5。
**关联：** F4；AC5。
**步骤：**
1. 实现 StreamCollector.collect、CollectedResponse，实时转发文本、思考和用量，收集完整消息。
2. 验证恰好一次 ResponseCompleted、角色和累计文本一致；完整消费并关闭底层流后才产生 CollectedResponse。
3. 截断、重复完成、完成后追加事件均拒绝；保留完整工具参数和 NativeContent。
**验证：** `.venv/bin/python -m pytest -q tests/test_agent_collector.py -k dual_path`；期望首段到达即可消费，完整消息不丢碎片，异常流无完整响应。

## T9: 收集器取消及流关闭

**文件：** `src/mycode/agent/collector.py`，`tests/test_agent_collector.py`。
**依赖：** T8。
**关联：** F4、F7；AC5、AC9。
**步骤：**
1. 同时监听取消信号与下一事件读取，取消时中止读取并关闭底层迭代器。
2. 为正常结束、异常、消费者提前关闭提供 finally 清理，不向已关闭生成器 yield。
3. 确认取消前已推送用量与文本可保留，且没有悬挂读取任务。
**验证：** `.venv/bin/python -m pytest -q tests/test_agent_collector.py -k cleanup`；期望暂停流可取消、关闭标记可见、活动读取任务回收，取消不产生 CollectedResponse。

## T10: 工具批次预检与分类

**文件：** `src/mycode/tools/scheduler.py`，`tests/test_tools_scheduler.py`。
**依赖：** T3、T4。
**关联：** F4、F5、F6；AC5、AC7。
**步骤：**
1. 定义 PreparedCall、PreparedBatch、BatchCompleted、SchedulerEvent，实现 validate_batch。
2. 先校验全部 ID，再按只读、副作用、未知、模式拒绝分类，保留模型原序。
3. 未知/模式拒绝预制错误；任何可执行项参数无效时整批拒绝，不启动前面的有效工具。
**验证：** `.venv/bin/python -m pytest -q tests/test_tools_scheduler.py -k preflight`；期望缺失/重复 ID 和后项非法参数均不产生副作用，未知与拒绝结果可区分。

## T11: 调度器顺序执行与结果收齐

**文件：** `src/mycode/tools/scheduler.py`，`tests/test_tools_scheduler.py`。
**依赖：** T10。
**关联：** F5、F7；AC6、AC10。
**步骤：**
1. 实现 execute_batch 顺序骨架、结果槽位、内部工具开始和结束事件。
2. 副作用项独占执行；未知/拒绝项在其原序位置生成结果且不产生实际 ToolStarted。
3. 单个工具失败继续同批其他项，整批唯一 BatchCompleted 按原序返回全部结果。
**验证：** `.venv/bin/python -m pytest -q tests/test_tools_scheduler.py -k serial_results`；期望顺序正确、失败不跳过其他调用、每项唯一结果。

## T12: 连续只读段并发与背压

**文件：** `src/mycode/tools/scheduler.py`，`tests/test_tools_scheduler.py`。
**依赖：** T11。
**关联：** F5、N3；AC6。
**步骤：**
1. 将连续只读项组成并发段，活动任务上限为 max_read_concurrency，完成后再启动排队项。
2. 使用容量 max(8, 2 * max_read_concurrency) 的内部事件队列，消费者变慢时形成背压。
3. 副作用项等待前段结束，后段等待副作用完成；实时结果可以乱序，最终结果保持原序。
**验证：** `.venv/bin/python -m pytest -q tests/test_tools_scheduler.py -k concurrency`；通过同步点验证重叠、默认最多 4 个、“读 A/B → 写 C → 读 C”屏障及原序回写。

## T13: 调度器取消与异常回收

**文件：** `src/mycode/tools/scheduler.py`，`tests/test_tools_scheduler.py`。
**依赖：** T12。
**关联：** F7、N5；AC9、AC10。
**步骤：**
1. 取消后停止排队项启动，取消并等待活动任务；已完成槽位不重复覆盖。
2. 从 result_frame/result_data 保留实际结果或 outcome_unknown，未启动项补取消结果。
3. 队列满、重复取消、消费者提前关闭和内部异常都回收任务；保留结果供循环在已进入执行阶段时补齐历史。
**验证：** `.venv/bin/python -m pytest -q tests/test_tools_scheduler.py -k cleanup`；期望活动任务归零、排队项未执行、每项恰好一个结果，背压不阻止清理。

## T14: 接入 Agent 基本循环

**文件：** `src/mycode/agent/loop.py`，`tests/test_agent_loop.py`。
**依赖：** T2、T8、T11。
**关联：** F1、F3、F7；AC1、AC4、AC10。
**步骤：**
1. 实现 AgentLoop.run，创建 run_id 和单运行状态，调用收集器、调度器和 HistoryStore。
2. 无工具回复提交并结束；有工具则整批执行、回写，再将结果交给下一次模型请求。
3. 为内部事件附加 run_id/iteration，发布调用、进度与结果；正常结束恰好一个 RunFinished。
**验证：** `.venv/bin/python -m pytest -q tests/test_agent_loop.py -k react`；期望两批依赖结果的工具连续完成，普通文本只请求一次，用户历史只提交一次。

## T15: 完整停止条件与优先顺序

**文件：** `src/mycode/agent/loop.py`，`tests/test_agent_loop.py`。
**依赖：** T14。
**关联：** F2；AC2、AC3、AC5。
**步骤：**
1. 按模型请求计迭代，默认第 20 次有工具时停止且不执行、不保存该响应；无工具仍正常完成。
2. 每个含未知工具的模型批次计一次；第三批结果提交后停止，不含未知工具批次重置。
3. 映射 stream_error、invalid_response、internal_error，停止后不再请求模型或执行工具，普通工具错误继续回传。
**验证：** `.venv/bin/python -m pytest -q tests/test_agent_loop.py -k stopping`；期望 20 次上限、3 批未知及重置、无工具边界、整批校验失败与各结束原因符合 spec。

## T16: 请求用量槽位与进度事件

**文件：** `src/mycode/agent/loop.py`，`tests/test_agent_loop.py`。
**依赖：** T14、T6、T7。
**关联：** F8；AC11。
**步骤：**
1. 为每次已启动请求建立用量槽位，用快照替换而非重复相加。
2. 计算 UsageSummary，分别维护输入/输出完整性及有用量请求数，保留失败和取消前收到的用量。
3. 在请求开始、用量更新、工具完成及结束时发布对应事件，最终用量与过程累计一致。
**验证：** `.venv/bin/python -m pytest -q tests/test_agent_loop.py -k usage_progress`；期望重复快照不重复累计、部分缺失不记零、失败请求有已知统计和不完整标记。

## T17: Agent 取消、提前关闭与历史一致性

**文件：** `src/mycode/agent/loop.py`，`tests/test_agent_loop.py`。
**依赖：** T9、T13、T15、T16。
**关联：** F2、F3、F7；AC2、AC4、AC9、AC10。
**步骤：**
1. 在模型请求前后、批次入口及工具结果回写后检查取消，固定停止优先顺序。
2. 已进入执行阶段则收齐结果并成组提交，未进入阶段的响应不保存；清理完成后才发送唯一 RunFinished。
3. 外部任务取消或消费者关闭时执行清理，保留内部最终状态，不在关闭生成器时发送事件。
**验证：** `.venv/bin/python -m pytest -q tests/test_agent_loop.py -k lifecycle`；期望取消无后续模型请求、每调用结果完整、先前历史保留、提前关闭无孤儿任务或生成器关闭错误。

## T18: Conversation 委托循环与运行锁

**文件：** `src/mycode/conversation.py`，`tests/test_conversation.py`。
**依赖：** T17。
**关联：** F1、F7；AC1、AC10、AC12。
**步骤：**
1. 用 AgentLoop 替换单批工具与禁用工具总结编排，持有 HistoryStore、options 与取消信号。
2. 保留 history/busy，入口拒绝空输入、忙碌、关闭状态；无 executor 时继续普通聊天。
3. 锁定一次运行直至清理结束，下一次请求引用完整已提交历史，向调用者返回 AgentEvent。
**验证：** `.venv/bin/python -m pytest -q tests/test_conversation.py -k agent_entry`；期望普通聊天和多批循环可用，并发提交拒绝，失败后的下一轮可引用已有结果。

## T19: 模式、最近计划与执行入口

**文件：** `src/mycode/agent/mode.py`、`conversation.py`，`tests/test_conversation.py`。
**依赖：** T18、T3。
**关联：** F6；AC7、AC8。
**步骤：**
1. 实现 build_mode_prompt、PlanSnapshot、set_mode，默认 execute，忙碌和关闭时拒绝切换。
2. 仅正常完成且非空的 plan 回复更新 latest_plan；失败、取消、到限不覆盖已有计划。
3. execute_plan 切回全工具，明确附入最近计划立即运行；无计划本地提示、不请求模型、不解析自然语言步骤。
**验证：** `.venv/bin/python -m pytest -q tests/test_conversation.py -k plan_mode`；期望只读定义与提示正确、越界拒绝、正常计划保存、/do 使用最近计划、无计划无请求。

## T20: 会话取消和幂等关闭

**文件：** `src/mycode/conversation.py`，`tests/test_conversation.py`。
**依赖：** T19。
**关联：** F7、N5；AC9、AC10。
**步骤：**
1. cancel 只设置显式信号，保持幂等；运行结束后再次取消无副作用。
2. aclose 等待活动运行或提前关闭流的清理，再关闭 Provider、清空内存历史和计划。
3. 清理期间维持忙碌/关闭状态，不放行新请求，重复关闭不重复释放资源。
**验证：** `.venv/bin/python -m pytest -q tests/test_conversation.py -k cleanup`；期望关闭顺序正确、无泄漏和死锁、Provider 仅关闭一次。

## T21: 按迭代组织对话视图

**文件：** `src/mycode/tui/widgets.py`、`app.tcss`，`tests/test_tui.py`。
**依赖：** T1。
**关联：** F3、F8、N2；AC4、AC12。
**步骤：**
1. TurnView 按 iteration 保存文本、思考与工具，替代首次/总结两段结构。
2. 工具键包含 run_id/iteration/call_id，区分收到未执行、执行中、成功、失败、取消。
3. 保留纯文本、思考折叠、脱敏、滚动及部分输出，增加停止说明和用量展示位置。
**验证：** `.venv/bin/python -m pytest -q tests/test_tui.py -k iteration_view`；期望多轮内容不覆盖、重复跨轮 ID 不冲突、未执行调用不显示为执行成功。

## T22: TUI 消费 Agent 事件

**文件：** `src/mycode/tui/app.py`，`tests/test_tui.py`。
**依赖：** T18、T21。
**关联：** F3、F8；AC4、AC11、AC12。
**步骤：**
1. 用统一 AgentEvent 驱动逐轮文本、工具、进度、用量与最终状态，不轮询内部 phase/tool_results。
2. 状态栏显示模式、迭代/上限和阶段，Token 无数据/部分缺失按约定展示。
3. RunFinished 后展示具体原因与 history_updated，保留已完成工具记录和部分内容。
**验证：** `.venv/bin/python -m pytest -q tests/test_tui.py -k agent_events`；期望事件到达即展示、仅结束时解锁、停止原因与用量缺失正确可见。

## T23: TUI 模式命令入口

**文件：** `src/mycode/tui/app.py`，`tests/test_tui.py`。
**依赖：** T19、T22。
**关联：** F6；AC7、AC8。
**步骤：**
1. 空闲时精确匹配 /plan、/do；/plan 本地切换不请求模型，/do 有计划立即开始执行。
2. 无计划时显示本地提示；普通请求仍走 stream_turn，不扩展带参数命令。
3. 忙碌时模式命令明确拒绝，模式不变；普通生成期间仍可编辑下一条输入。
**验证：** `.venv/bin/python -m pytest -q tests/test_tui.py -k mode_commands`；期望命令不误入普通聊天、/do 立即运行、忙碌切换拒绝、无计划无模型请求。

## T24: TUI 取消与退出清理

**文件：** `src/mycode/tui/app.py`，`tests/test_tui.py`。
**依赖：** T20、T22。
**关联：** F7、N2、N5；AC9、AC12。
**步骤：**
1. Ctrl+C 设置 Conversation 取消信号，继续消费至 RunFinished，不直接取消正常消费 worker。
2. 清理期间显示取消处理中，防止重复发送；清理后恢复输入并保留已执行记录。
3. Ctrl+D、卸载和异常关闭先清理活动运行，再关闭会话；保留滚动与编辑能力。
**验证：** `.venv/bin/python -m pytest -q tests/test_tui.py -k cancel_exit`；期望取消事件完整显示、清理后可继续对话、退出无活动任务残留。

## T25: CLI 默认装配与启动回归

**文件：** `src/mycode/cli.py`，`tests/test_cli.py`。
**依赖：** T20、T23、T24。
**关联：** N1；AC12。
**步骤：**
1. 使用 AgentOptions 默认值装配现有 Provider、Executor、Conversation 和 TUI。
2. 保持 --config、--provider、配置格式与既有启动错误行为；不增加新配置字段或依赖。
3. 保留退出异常的幂等关闭，避免重复关闭和事件循环生命周期冲突。
**验证：** `.venv/bin/python -m pytest -q tests/test_cli.py`；期望启动、参数、异常退出和默认值装配正确；`.venv/bin/MyCode --help` 正常输出原有选项。

## T26: 双协议多轮集成与回归适配

**文件：** `tests/conftest.py`、`test_openai.py`、`test_anthropic.py`、`test_conversation.py`、`test_tui.py`、`test_tools_process.py`。
**依赖：** T6、T7、T13、T17、T25。
**关联：** F1–F8；AC1、AC5、AC9、AC10、AC12。
**步骤：**
1. 使用真实 SDK 与模拟 SSE 串起两协议“调用 → 回传 → 再调用 → 最终回复”，检查实际请求消息和工具集。
2. 将旧单批总结断言改为自主循环契约，保留工具、错误、原生思考及界面有效回归检查，不削弱取消和超时验证。
3. 接入真实工具执行器验证活动命令/文件工作进程取消及下一轮历史引用，受控目录检查副作用结果。
**验证：** `.venv/bin/python -m pytest -q tests/test_openai.py tests/test_anthropic.py tests/test_conversation.py tests/test_tui.py tests/test_tools_process.py`；期望双协议消息配对正确、真实工具资源回收、普通能力无回归。此结果属于模拟协议/本地集成，不称为真实 API 验收。

## T27: 编写真实 tmux 验收入口

**文件：** `tests/tmux_agent.py`。
**依赖：** T25、T26。
**关联：** N7、N8；AC13。
**步骤：**
1. 复用上一章独立 tmux socket、临时目录、真实 MyCode 启动及脱敏证据方式，不改写上一章报告。
2. 多批场景先读取运行时随机生成的受控内容，再根据结果写入目标并验证，观察至少两批依赖工具操作与最终回复。
3. 规划场景输入 /plan 和真实任务，检查规划期间文件不变；输入 /do 后检查立即执行、目标文件正确和最终回复。
4. 增加真实取消后继续提问场景；支持按已配置协议筛选，缺少 tmux、配置或凭据明确输出未验收；脚本只终止本次专用会话。
**验证：** `.venv/bin/python -m compileall -q tests/tmux_agent.py`、`.venv/bin/python tests/tmux_agent.py --help`；期望脚本可加载、帮助不发起 API 请求，验收入口参数清晰。真实运行另由 T29 完成。

## T28: 完整自动化、静态检查与构建

**文件：** 本轮新增及修改的源码与测试，`MANIFEST.in`、`specs/agent-loop/evidence/`。
**依赖：** T26、T27。
**关联：** N1、N7、N8；AC12、AC13。
**步骤：**
1. 顺序运行 `.venv/bin/python -m pytest -q`、`.venv/bin/python -m ruff check .`、`.venv/bin/python -m compileall -q src tests`、`.venv/bin/python -m build`。
2. 检查本次 wheel 包含新 agent 模块、scheduler 和 TUI 样式，使用 Python zipfile 读取本次构建产物，不安装或发布外部软件包。
3. 记录实际命令、退出状态和测试统计；修复失败后重跑受影响检查，不能以预期结果代替执行结果。
**验证：** 上述命令均成功、实际构建产物包含新模块和样式，且没有跳过必要验收被误计通过；网络或构建依赖阻塞单独记录。

## T29: 真实模型 tmux 端到端与逐项验收

**文件：** `tests/tmux_agent.py`，`specs/agent-loop/checklist.md`、`evidence/`。
**依赖：** T28；四份文档已全部获批。
**关联：** AC1–AC13，重点 AC13。
**步骤：**
1. 检查 tmux、运行环境、真实配置及本轮 checklist；不输出密钥。运行 `.venv/bin/python tests/tmux_agent.py`，按现有配置逐协议执行真实场景。
2. 在 tmux 中启动 MyCode 并输入真实请求，观察自主多批调用、流式回复、/plan 只读和 /do 立即执行、取消后恢复输入，核对受控文件结果。
3. 对照 checklist 逐项填写实际结果及证据，区分自动化验证和真实模型验证，记录实际供应商/模型，脱敏保存终端画面与结果。
4. 失败则修复并重跑受影响检查及真实场景；未配置协议、原生 thinking 模型或其他环境缺失项列为未验收，不虚构通过。
**验证：** 实际运行的真实场景全部符合预期，checklist 有逐项结果和证据；存在阻塞则如实保留未完成项，本任务不得标记全部完成。

## T30: 更新使用说明与最终交付记录

**文件：** `README.md`，`specs/agent-loop/spec.md`、`task.md`、`checklist.md`、`evidence/`。
**依赖：** T28、T29 的实际结果已取得，允许如实记录环境阻塞但不宣称验收完成。
**关联：** F1–F8、N8；AC13。
**步骤：**
1. 更新自主循环、20/3/4 默认值、模式命令、停止原因、用量缺失、取消及真实测试入口，替换已过时的单批总结说明。
2. 更新任务完成状态与本主题实际验收摘要，保持旧主题文档和批准依据可追溯。
3. 自检文档与最终代码/证据一致，汇报通过数、失败项、未验收项、真实端到端结果和环境限制；未完成任务仍标记未完成。
**验证：** `git diff --check` 通过，README 命令和行为与实现一致，报告数字可与 checklist 和证据逐项核对；仅文档改动不重复运行全部功能测试。

## 执行顺序与依赖

推荐按 T1 → T2 → … → T30 顺序执行。所有硬依赖指向此前任务，没有依赖环；T29 另有四份文档全部获批的全局前提。

| 阶段 | 任务 | 输出 |
| --- | --- | --- |
| 基础与协议 | T1–T7 | 类型、历史、工具元信息、校验与双 Provider 契约 |
| 收集与调度 | T8–T13 | 双路流、整批预检、安全并发和清理 |
| Agent 与会话 | T14–T20 | 自主循环、停止、用量、模式和运行生命周期 |
| 界面与装配 | T21–T25 | 迭代展示、命令、取消和 CLI |
| 集成与验收 | T26–T29 | 双协议回归、真实入口、构建和 tmux 证据 |
| 文档交付 | T30 | 使用说明与实际结果报告 |

## 追踪与自检

| 需求 | 任务 | 验收 |
| --- | --- | --- |
| F1 | T14、T18、T26、T29 | AC1 |
| F2 | T1、T15、T17 | AC2、AC3 |
| F3 | T1、T14、T17、T21、T22 | AC4 |
| F4 | T4–T10、T15、T26 | AC5 |
| F5 | T3、T10–T13 | AC6 |
| F6 | T3、T6、T7、T10、T19、T23、T29 | AC7、AC8 |
| F7 | T2、T9、T13、T17、T18、T20、T24、T26 | AC9、AC10 |
| F8 | T1、T5–T7、T16、T22 | AC11 |
| N1、N2、N6 | T21–T26、T28 | AC12 |
| N3、N4、N5 | T2、T12、T13、T17、T20、T24、T26 | AC6、AC9、AC10 |
| N7、N8 | 各任务相关验证、T27–T30 | AC13 |

- plan 中每个组件及文件均有任务归属；沿用接口名称与默认值，没有引入新能力或依赖。
- 30 个任务均列明文件、依赖、具体步骤及命令/观察预期；依赖顺序合法，无未决占位内容。
- 关键取消、并发和流协议测试验证行为与资源结果，不仅检查实现细节；并发验证使用同步点，不以易波动耗时为唯一证据。
- 真实 tmux 验收独立于模拟 SSE，通过范围限于实际配置与运行；缺少 checklist、tmux 或运行条件明确阻塞。
- 实现与验收已完成；任务验证及实际结果见下表和本主题 evidence，未执行模型/平台范围独立说明。

## 实际任务进度

| 任务 | 状态 | 实际验证证据 |
| --- | --- | --- |
| T1 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T2 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T3 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T4 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T5 | 已完成 | verification.json：compileall |
| T6 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T7 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T8 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T9 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T10 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T11 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T12 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T13 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T14 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T15 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T16 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T17 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T18 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T19 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T20 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T21 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T22 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T23 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T24 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T25 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T26 | 已完成 | pytest.txt（完整执行，相关用例均通过；共233项） |
| T27 | 已完成 | verification.json：脚本帮助、语法；tmux-agent.json |
| T28 | 已完成 | pytest.txt、ruff.txt、build.txt、packaging.json |
| T29 | 已完成 | tmux-agent.json：两协议12子场景；acceptance.json：64项 |
| T30 | 已完成 | README.md、checklist.md、acceptance.json；verification.json：diff检查 |
