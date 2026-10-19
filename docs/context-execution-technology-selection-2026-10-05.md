# 多专家协作：上下文、阅读进度与执行额度选型

调研日期：2026-10-05（Asia/Shanghai）。状态：**完成代码与一手资料核查，供实施决策；尚未实现或通过新方案验收。**

**当前统一入口：** [多 Agent 与上下文工程完整选型](multi-agent-context-architecture-2026-10-05.md)已合并本篇与后续沙箱、课程、编码专家 worktree 决策。总体架构和实施顺序以统一文档为准；本篇保留详细取证与候选分析。

**课程调研补充：** 已核对 ShareAI 的 Claude Code 教学仓库及 Anthropic 官方文档，补充独立任务上下文、事件驱动协作、分层压缩、完整工具回合保护与摘要来源管理，详见 [ShareAI / Claude Code 补充](shareai-claude-code-context-supplement-2026-10-05.md)。继续保留现有 SDK 和 DBOS，不导入教学运行时；该补充不是已实现声明。

**后续需求更新：** 用户已明确选择每个专家独立沙箱并拥有独立存储。执行部署与实施顺序以[独立沙箱决策](expert-sandbox-storage-decision-2026-10-05.md)为准：增加容器运行时和专家私有工作库，保留 SDK、DBOS 与中央权威仓储。本篇“不新增框架依赖”的结论不代表不需要新增隔离运行边界；下述上下文、证据、授权与收尾契约继续有效。

## 1. 推荐什么

**保留 OpenAI Agents SDK 0.22.2、DBOS 3.0.0 和现有 PostgreSQL/SQLAlchemy；本轮方案不新增框架依赖。** 在现有业务层增加三个清晰的模块：

1. **上下文投影**：最新指令、仍有效的约束、带出处的历史摘要、最近完整回合、资料/报告目录。原始聊天和报告仍是不可变依据，正文按需读取。
2. **阅读账本**：记录实际读到了哪个版本、哪些片段、是否被截断、对应哪个问题；把“已返回给模型”“已被引用”“已独立复核”分开。
3. **执行控制**：预留最终回答的模型轮次和时间；停止调查后关闭工具，生成有依据且如实说明缺口的报告；若模型或保存仍失败，持久化明确的未交付状态与恢复点。

借鉴 Deep Agents 的“摘要加可回查原文”、LangMem 的“增量摘要游标”、LangGraph 的“状态和检查点分离”、LangChain 的“工具限额由执行器控制”。不为这些机制替换已有协作系统。检索规模或召回率确有瓶颈时，再单独评估 LlamaIndex 检索组件；不采用 Mem0 作为研究事实或任务进度的主存储。

代价主要是业务契约与测试：需要可靠保存摘要出处、阅读结果和预算状态，并处理撤权、恢复与部分交付。引入大框架也不能省掉这些工作。摘要还可能增加模型调用与延迟，不能许诺一定节省总费用；用户已取消费用金额上限，本方案不重新加入美元限额。

## 2. 当前产品实际需要什么

### 2.1 核查范围与证据等级

根目录未发现实体 `AGENTS.md`，遵守本任务提供的 AGENTS 指令。已读 README、pyproject、核心执行/上下文/报告/信息交接/证据/恢复代码，以及指定验收文档与四份 Pro 试验 JSON。README 同时包含多个历史阶段，以下判断以当前代码和相应原始记录为准。

本次只读工作区和公开官方资料；没有读取 `.env`、安装依赖、调用付费模型、运行服务、访问或修改历史数据库/blob/构建目录、提交或推送。新增文件均为本次研究文档与取证索引。

- [本地取证索引](context-execution-selection-local-evidence-2026-10-05.json)：代码 SHA-256、四个运行的限额/终态/工具完成计数。
- [GitHub 元数据快照](context-execution-selection-github-2026-10-05.json)：公开 REST API 返回的星数、提交、许可证、release。
- [外部来源索引](context-execution-selection-sources-2026-10-05.json)：PyPI Python 版本、依赖、官方实现 URL 和所读内容 SHA-256；保留首次查询失败，不伪装为完整成功。

### 2.2 从使用流程到真实需求

| 产品流程 | 必需能力 | 当前已有 | 当前缺口及定位 |
| --- | --- | --- | --- |
| 主对话持续讨论、分支继承、后续汇总 | 保留最新要求、早期仍有效约束、纠错、分支截止点；输入可控 | 聊天 revision、固定分支继承、每次运行输入快照 | `research/service.py:196–299` 将全部继承/当前消息 JSON 塞进 goal，再被外层 JSON 序列化；没有面向长聊天的摘要/选择层 |
| 比较专家初报和补查报告 | 发现目录、固定版本、按需读正文，区分报告观点与原始证据 | `collaboration/history.py:23–141`；`ReportGrant` 固定 result/task/spec/run 身份 | 目录与正文读取已有，不能再次判成“系统缺少历史报告”；单份超长报告不能靠“少选几个 ID”解决，需要片段分页 |
| 专家缺资料向主助手索要，随后继续原任务 | 保存问题、进度、固定授权和待处理状态，恢复时重验 | `collaboration/information.py:59–132, 224–257` 保存 progress 和授权；同一 run 只一轮信息请求 | progress 是模型文字，不是机器确认的阅读覆盖；不能代替已读片段账本 |
| 主助手核对来源 | 查哪一份、读到哪里、未读什么、正文是否完整 | `context/tool_guard.py:14–83` 有按 location 读取和固定单份资料检索；`collaboration/service.py:123–127` 有 run→ref 读取记录 | source read 是整份 ref 粒度；搜索仅返回部分命中也会记 ref。缺查询参数、片段范围、截断状态与问题关联 |
| 调查必须可结束并交付 | 工具/轮次/时间分别计量；停止调查后有机会整理已得内容 | `core.py:77–158` 限次、调用 ID 去重、取消/超时；结果反馈 remaining_tool_calls | 硬超限使整个运行失败；“请停止调用”的 prompt 没有强制关闭工具或保留收尾资源 |
| 后台任务中断、超时、信息等待 | 固定身份、幂等提交、恢复已完成工作、取消优先 | `durable.py:20–60`、`tasks/service.py:504–688`、协作 intent/notice/information 的持久状态 | DBOS 步骤粒度目前是整次专家执行，不是每一轮模型/工具。不能把已有任务级恢复说成模型回合级无损恢复 |
| 用户撤权、排除节点、改变任务版本 | 禁止旧结果/摘要/缓存泄漏进新输入；旧报告保留 | `context/execution.py:12–60`；`tasks/service.py:534–552`；`history.py:23–37` | 新增摘要、索引、缓存也必须携带依赖并重验；框架的 session ID 或 namespace 不能替代这些检查 |
| 纠正主助手先前结论 | 错误不因重复或摘要变成事实；保留纠错出处 | 证据引用检查与独立语义复核分开，Stage 9 记忆须确认/选择 | 目前完整历史仍包含错误断言，缺少明确的“旧说法—纠正—证据—待核验”投影 |

代码入口：[research/service.py](../src/agent_core/research/service.py)、[context/builder.py](../src/agent_core/context/builder.py)、[history.py](../src/agent_core/collaboration/history.py)、[information.py](../src/agent_core/collaboration/information.py)、[core.py](../src/agent_core/core.py)、[durable.py](../src/agent_core/durable.py)。

### 2.3 已证实的问题与不能推断的事情

**输入问题属于应用层。** `context/scopes.py:22` 的默认 `max_input_chars=30000`；`context/builder.py:40` 在调用模型前检查序列化字符数。旧合成案例重建的 30,751 字符来自[试验记录](collaboration-c4-manager-pro-trial.md)；原始 JSON 的 `blocked_chat_attempt` 证明 `run_id=null`、`ScopeDenied`。本次未重新连原数据库重建 30,751，因此数字沿用该记录，不声称独立复测。字符数不是 token 数，更不是已核实的模型窗口上限。

**工具问题属于执行控制。** 普通聊天限额见 `app/api.py:476`：180 秒、12 轮、12 次工具；后台主助手续接 `collaboration/service.py:1082` 则为 180 秒、12 轮、8 次工具，不能把所有入口都说成 12 次。

| 已保存试验 | 时限 | 实际完成工具 | 终态 |
| --- | --- | --- | --- |
| [官方第 1 次](collaboration-c4-manager-pro-official.json) | 180 秒 | 5 read_context + 5 search_source + 1 read_source | timeout，无报告；usage 不完整 |
| [官方第 2 次](collaboration-c4-manager-pro-official-2.json) | 360 秒 | 7 read_context + 4 search_source + 1 read_source | 下一次 read_context 被 call_limit 拒绝，无报告 |
| [合成第 1 次](collaboration-c4-manager-pro-synthetic.json) | 180 秒 | 5 read_context + 1 find_experts | timeout，无报告 |
| [合成第 2 次](collaboration-c4-manager-pro-synthetic-2.json) | 360 秒 | 3 read_context + 1 find_experts | succeeded，原报告保留 |

四份 JSON 的位置为 `manager_run.events[].event`，终态为 `manager_run.run.result`。事件只有工具名、状态、调用 ID 等，没有参数和响应摘要；**7 次同名调用不证明重复读了同一材料**。阅读去重是需要建立的能力，不能把尚未取证的行为当作本次失败根因。

官方第二次记录 9 个请求、560,850 输入 tokens、19,021 输出 tokens，但 `complete=false`；合成第二次 4 个请求、83,017 输入 tokens、11,154 输出 tokens，`complete=true`。这是多次请求累计值，不是单次上下文长度。两案例输入组织不同，不能做模型质量或 token 节省的严格对照。

**质量问题独立存在。** [质量复验](collaboration-c4-quality-retest-2026-10-05.md)记录了“唯一缺陷”过度断言、跨年口径推断不足、脚注限定丢失、“不能相减”以及把历史追问说成本轮行动。增大窗口、加工具数、加向量库均不能自动修复这些错误。

### 2.4 必需与暂不需要

必需：可回查的短上下文；固定版本的片段阅读；可审计进度；有上界的执行与收尾；持久恢复点；权限/取消/版本重验；语义质量回归。

暂不需要：全用户自动长期记忆、知识图谱、向量数据库、第二套任务队列、全框架重写、无上限自主循环、美元费用封顶。现有例子只有少量已知报告和明确来源，尚无证据说明语义向量检索优于目录+固定版本定位。先测真实召回不足，再选择检索组件。

## 3. GitHub 候选筛选与版本核查

星数只用于筛选关注度，不代表适配、正确性或服务保障。以下为 **2026-10-05 22:15–22:18 上海时间**公开 API 查询快照，非缓存的 9 月项目清单。所有候选查询时均未 archived。`pushed_at` 仅说明仓库有活动，不等于已发布稳定版或已解决问题。许可证为对应仓库声明，具体依赖仍应在实际引入时核查。本次可靠性判断依据维护活动、发布记录、文档与实现一致性及本地适配证据；没有安装运行所有候选或进行漏洞审计，不将其表述为生产可靠性保证。

| 项目 | 星数 | 许可证 | 当前发布 / 本地版本 | 最近 push（UTC） | 决定 |
| --- | ---: | --- | --- | --- | --- |
| [OpenAI Agents Python](https://github.com/openai/openai-agents-python) | 29,841 | [MIT](https://github.com/openai/openai-agents-python/blob/main/LICENSE) | v0.23.1（10-02）；本地 0.22.2 | 10-05 | 继续直接依赖，不因有新版就升级 |
| [DBOS Python](https://github.com/dbos-inc/dbos-transact-py) | 1,606 | [MIT](https://github.com/dbos-inc/dbos-transact-py/blob/main/LICENSE) | 3.2.0（09-29）；本地 3.0.0 | 10-01 | 继续直接依赖；虽非万星，但已有适配和恢复证据 |
| [LangGraph](https://github.com/langchain-ai/langgraph) / [LangChain](https://github.com/langchain-ai/langchain) | 42,735 / 147,465 | MIT / MIT（各仓库 LICENSE） | Python 1.2.12（09-21）/ 1.4.3（09-28） | 10-05 / 10-05 | 借鉴状态与限额控制；完整框架备选 |
| [Deep Agents](https://github.com/langchain-ai/deepagents) | 29,950 | [MIT](https://github.com/langchain-ai/deepagents/blob/main/LICENSE) | deepagents 0.7.22（10-05） | 10-05 | 借鉴上下文组织，不直接接入 |
| [LangMem](https://github.com/langchain-ai/langmem) | 1,692 | [MIT](https://github.com/langchain-ai/langmem/blob/main/LICENSE) | PyPI 0.0.30（2025-10-27）；无 latest release 记录 | 10-02 | 小组件候选，当前只借鉴 |
| [LlamaIndex](https://github.com/run-llama/llama_index) | 52,410 | [MIT](https://github.com/run-llama/llama_index/blob/main/LICENSE) | 仓库 v0.14.25（09-21）；具体检索子包需独立定版 | 10-01 | 数据检索显著扩展时的组件备选 |
| [Mem0](https://github.com/mem0ai/mem0) | 66,592 | [Apache-2.0](https://github.com/mem0ai/mem0/blob/main/LICENSE) | Python mem0ai 2.2.1（09-25） | 10-05 | 不选作研究上下文/阅读进度主存储 |

版本依据：GitHub [Agents release](https://github.com/openai/openai-agents-python/releases/tag/v0.23.1)、[DBOS release](https://github.com/dbos-inc/dbos-transact-py/releases/tag/3.2.0)、[Deep Agents release](https://github.com/langchain-ai/deepagents/releases/tag/deepagents%3D%3D0.7.22)、[LlamaIndex release](https://github.com/run-llama/llama_index/releases/tag/v0.14.25)；PyPI 官方元数据 [LangGraph](https://pypi.org/pypi/langgraph/json)、[LangChain](https://pypi.org/pypi/langchain/json)、[LangMem](https://pypi.org/pypi/langmem/json)、[Mem0](https://pypi.org/pypi/mem0ai/json)。GitHub `releases/latest` 对 LangGraph 返回 CLI 开发标签、对 Mem0 返回 TS 标签，故不能把它们当 Python 版本；LangMem 首次 404 是 release 入口，不是仓库不存在。

### 3.1 现有 SDK：优先用已有能力，但明确边界

核查了 `.venv/lib/python3.12/site-packages/openai_agents-0.22.2.dist-info/METADATA` 及已安装类型/源码，而非只看最新版宣传：

- `RunConfig.call_model_input_filter` 在**每次**模型调用前接收 instructions/input，可投影发送给模型的内容；`session_input_callback` 处理 session 历史合并，两者用途不同。项目目前只设置 `tracing_disabled=True`，未使用这两个入口。[SDK 运行文档](https://openai.github.io/openai-agents-python/running_agents/)、[0.22.2 RunConfig](https://github.com/openai/openai-agents-python/blob/v0.22.2/src/agents/run_config.py)。
- `FunctionTool.is_enabled` 可动态决定工具是否可用，`ModelSettings.tool_choice` 有 `none`，Agent 可 clone 去除 tools；本项目的格式修复已使用 `agent.clone(tools=[])`。这些是强制停止调查的现成接点，不需要重写模型循环。[工具实现](https://github.com/openai/openai-agents-python/blob/v0.22.2/src/agents/tool.py)、[模型设置](https://github.com/openai/openai-agents-python/blob/v0.22.2/src/agents/model_settings.py)。
- SDK 有 `SQLAlchemySession`、`RunState` 序列化及恢复，但项目已有聊天 revision、分支、运行快照和业务数据库。直接开启自动 Session 全量加载会重复存储，并不能解释本项目权限；本方案继续让现有仓储拥有业务历史。`RunState` 是可用的运行快照格式，**不是自动写入 DBOS 的每轮检查点**。[Sessions 文档](https://openai.github.io/openai-agents-python/sessions/)、[RunState 实现](https://github.com/openai/openai-agents-python/blob/v0.22.2/src/agents/run_state.py)。
- 内置 `OpenAIResponsesCompactionSession` 调用 `responses.compact`；0.22.2 构造器明确检查 OpenAI 模型命名，DeepSeek 不满足。不能用兼容 URL 或伪装模型名绕过，也不能在这轮擅自换模型。使用现有模型生成应用级摘要，出处和授权仍由业务层掌握。[固定版本压缩器源码](https://github.com/openai/openai-agents-python/blob/v0.22.2/src/agents/memory/openai_responses_compaction_session.py)。

一个关键限制：当前 30,000 字符拒绝发生在 Runner 之前，单独接 `call_model_input_filter` 来不及修复。必须先替换 `_prepare` 的全历史拼接，再用 SDK filter 控制运行中逐渐增长的工具结果。

### 3.2 DBOS：继续负责可靠执行，细化现有步骤

官方模型是 workflow 加持久 steps：已提交步骤结果可重放，未完成步骤可能再次执行；外部系统的副作用仍需幂等设计。队列/取消/恢复已经由当前项目接入，没必要再引入另一个编排运行时。[Workflow 文档](https://docs.dbos.dev/python/tutorials/workflow-tutorial)、[Step 文档](https://docs.dbos.dev/python/tutorials/step-tutorial)、[3.0.0 恢复实现](https://github.com/dbos-inc/dbos-transact-py/blob/3.0.0/dbos/_recovery.py)。

项目目前 `execute_expert` 整体包成一个可重试 step，最多两次；`tasks/service.py:670–684` 将可用轮次/工具次数分给尝试。这保证任务级恢复的预算约束，却不会保存 Core 的局部 `completed` 字典或 SDK 的每轮 history。后续应在业务可提交边界保存模型输入/响应、工具结果引用和预算；恢复仍走原业务权限和提交入口。

不要许诺外部模型 exactly-once：请求已到提供方但本地未保存响应时，可能需要重新调用；需记录 unknown/in-flight、实际尝试和不完整用量。DBOS 不替本项目判断报告版本、来源许可或结论正确性。

### 3.3 LangGraph / LangChain：完整能力强，当前替换代价高

LangGraph checkpointer 使用 thread 身份保存图执行状态、检查点和 pending writes，可支持中断后的继续。已读固定提交的 `CheckpointTuple/get_tuple/put/put_writes` 接口；这些解决图运行状态，不会自动记录“某报告哪一页已被某专家验证”。[持久化文档](https://docs.langchain.com/oss/python/langgraph/persistence)、[固定提交实现](https://github.com/langchain-ai/langgraph/blob/9a0394d88b2211f299dcd69df92db3480c69ee61/libs/checkpoint/langgraph/checkpoint/base/__init__.py)。

LangChain `ToolCallLimitMiddleware` 有 run/thread 两层计数以及 continue/error/end；已读 1.4.3 源码。continue 把超额调用变成错误消息，仍由模型决定结束；end 生成说明停止的 AI 消息，**不等同于本项目的 ResearchAnswer、逐条引用和缺口报告**。部分概览文档对并行调用的 end 行为仍较旧，本文以 1.4.3 实现为准。[API 文档](https://reference.langchain.com/python/langchain/agents/middleware/tool_call_limit/ToolCallLimitMiddleware)、[1.4.3 实现](https://github.com/langchain-ai/langchain/blob/langchain%3D%3D1.4.3/libs/langchain_v1/langchain/agents/middleware/tool_call_limit.py)。

采用它需要改消息类型、Agent 构造、运行入口、检查点和错误映射，并解决与 DBOS/现有仓储的双重状态所有权；固定报告、授权交接、撤权仍必须自建。当前只借鉴；若未来产品主要复杂度变成大量图分支/人工中断，再单独提出替换执行层的决策，不能同时堆叠两套总调度器。

### 3.4 Deep Agents：最贴近长任务上下文，但不是轻量插件

已读 0.7.22 对应提交的 graph、summarization 与 pyproject。它将文件操作、子代理、摘要等 middleware 装配到 LangChain agent；长历史可摘要，并把完整历史放到 backend 供回查。这支持本项目采用“原文保留、当前上下文缩小、需要时再读”的设计。[官方概览](https://docs.langchain.com/oss/python/deepagents/overview)、[上下文设计](https://www.langchain.com/blog/context-management-for-deepagents)、[摘要源码](https://github.com/langchain-ai/deepagents/blob/c431ee5caed20aa94718f81653948d7f2a665106/libs/deepagents/deepagents/middleware/summarization.py)。

但当前版本依赖 LangChain 以及多家模型适配包，内部文件/子代理机制也有自己的身份与权限规则。本项目应把回查对象放在已有不可变资料仓储，而不是再把专家报告复制成可变文件。完整引入需要重接专家会话、报告授权、工具名和模型消息，收益不足以覆盖改造面。[构造实现](https://github.com/langchain-ai/deepagents/blob/c431ee5caed20aa94718f81653948d7f2a665106/libs/deepagents/deepagents/graph.py)、[依赖声明](https://github.com/langchain-ai/deepagents/blob/c431ee5caed20aa94718f81653948d7f2a665106/libs/deepagents/pyproject.toml)。

### 3.5 LangMem：看似小的摘要组件，实际依赖不小

`summarize_messages`/`RunningSummary` 跟踪已摘要消息 ID 和最后游标，处理工具调用与对应返回的成组边界。该设计值得采用，尤其不能只按任意字符位置截断 SDK 消息。[短期记忆文档](https://langchain-ai.github.io/langmem/reference/short_term/)、[摘要实现](https://github.com/langchain-ai/langmem/blob/48e3c11f5bb527282c7d5339c6a87a0b35abccfc/src/langmem/short_term/summarization.py)。

它接收 LangChain 消息和模型；发布包 0.0.30 还依赖 LangGraph、LangSmith、多个模型适配与 trustcall。源码在摘要输入本身超限时会裁掉更早消息，不能直接承担“任何早期约束都不丢”的契约。它也不自带本项目固定报告授权和撤权传播。仓库近期有活动，但最近包发布约一年前；不能等同于每个新提交都已发布验证。[PyPI 依赖元数据](https://pypi.org/pypi/langmem/0.0.30/json)。

因此当前不为一个摘要函数引入整套消息/模型栈。若项目未来已经采用 LangChain，它才变成合理的小组件选择；这属于替代方案，不是现在增加兼容适配层的理由。

### 3.6 LlamaIndex：检索增长时可局部采用

其 Memory 提供短期聊天与 memory blocks 的 token 分配和 flush；BM25Retriever 可索引已有节点、保留 node 元数据并进行过滤，不要求把整个研究工作流换成 LlamaIndex。已读对应固定提交的 memory.py 和 BM25 base.py。[Memory 文档](https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/)、[Memory 实现](https://github.com/run-llama/llama_index/blob/962940ddc079cc21701d28d1237c84c82a7c5164/llama-index-core/llama_index/core/memory/memory.py)、[BM25 实现](https://github.com/run-llama/llama_index/blob/962940ddc079cc21701d28d1237c84c82a7c5164/llama-index-integrations/retrievers/llama-index-retrievers-bm25/llama_index/retrievers/bm25/base.py)。

当前 `search_source` 只是单份资料内的词项计数排序，中文无空格、同义表达和跨报告召回值得测试。但当前首要故障是全历史装入和没有收尾，不是已证明检索失败。若今后已授权资料增长到目录难以选择，可只接检索子包，把节点 ID 映射回 `(resource_id,snapshot_id,parse_id,location)`；向量/BM25分数只负责候选排序，服务端仍先限权、后重验、再回原始片段。不要启用默认重新解析切块而丢失原 location，也不要默认使用英语词干处理中文。

### 3.7 Mem0：持久个性记忆不等于研究证据

Mem0 支持按 user/agent/run 过滤的记忆写入和检索；默认 infer 会让模型抽取信息并与已有记忆整合，infer=false 也能存原始内容。已读 `Memory.add/search/update/delete` 实现，不把它误描述为只能存摘要。[官方工作机制](https://docs.mem0.ai/core-concepts/how-it-works)、[固定提交实现](https://github.com/mem0ai/mem0/blob/abb81c88e1f738a8117d8293530fbc31a5ef8fd9/mem0/memory/main.py)。

本项目更需要“不覆盖旧版本、知道谁说了什么、什么未经复核”，且 Stage 9 已有用户确认的偏好/经验记忆（`memories/service.py:17–76`）。再引入自动记忆整合会增加事实混淆与另一套更新/删除语义；元数据过滤也不自动等于主任务、报告授权及祖先取消检查。因此不选。不是认为它不可靠，而是其主要抽象与本次需求不匹配。

## 4. 三条实施路线的真实成本

| 路线 | 能复用什么 | 仍必须自建什么 | 本项目代价/风险 | 结论 |
| --- | --- | --- | --- | --- |
| 现有 SDK + DBOS + 业务模块 | SDK 输入过滤/工具开关/结构化输出；DBOS 队列/恢复；现有来源、结果、scope、SQL 事务 | 摘要出处、阅读覆盖、预算阶段、检查点提交和权限重验 | 无新框架；主要改上下文和执行边界，容易保留既有契约 | **推荐** |
| 加 LangMem 或 LlamaIndex 组件 | 通用摘要游标或成熟检索 | 业务身份、来源授权、取消、证据/复核仍完整保留 | LangMem 多消息/模型依赖；LlamaIndex 带节点/索引映射和召回验证 | 当前不加；有独立收益证据再局部接检索 |
| 换 LangGraph/Deep Agents | 图状态、中断、统一 middleware、长任务上下文 | 全部研究业务契约及双系统一致性问题 | 改 SDK 适配、执行器、会话、工具、DBOS 边界，回归面最大 | 当前不选；不是性能/质量结论 |

这里新增的是研究产品自身的数据与状态规则，不是自己再造 Session、任务队列、通用 Agent runtime 或向量检索库。框架不拥有业务权限，业务层不重复实现框架已有模型循环。

## 5. 推荐方案的具体契约

### 5.1 上下文投影：原文留存、摘要有出处

每次主助手运行输入按以下顺序构建：

1. 当前目标与最新用户指令；有效的禁止联网/指定时期版本/不得重跑等约束及其原消息 ID。结构化权限仍取自服务端 scope，摘要没有授权能力。
2. 历史摘要：已确认决定、待核验说法、分歧、纠正记录和未完成事项，每项指向原消息/固定报告；标注作者、历史轮次和复核状态。
3. 最近若干**完整回合**，保留工具调用与结果配对；按总输入预算装配，而非固定“最后 N 条就安全”。
4. 可见资料/报告的小目录、已读进度、必要引用片段；未选正文继续通过现有工具读取。

摘要派生记录至少有：branch/node、chat_revision、覆盖消息范围与内容 hash、原消息/报告/来源依赖、摘要生成版本和模型标识。该记录不能覆盖聊天、专家报告或证据；原文始终可按 ID 回查。摘要只从新增内容与上次有效摘要增量更新，纠错时重读被纠正的原文，不能无限“摘要的摘要”把出处和限定越压越少。

**约束保留必须有应用检查。** 显式权限和已结构化约束直接保留；从自然语言提取的约束保留原句及 message_id，不能仅存改写。投影检查约束 ID、报告版本、数字/单位/期间与纠错链接是否齐全；这能检测结构缺失，不能证明自然语言完全理解正确。摘要 schema 失败或缺必要项时不发布该摘要，记录整理失败并保留原文；不退回旧的全历史灌入路径。单条最新指令或必需约束本身超出预算，须返回具体输入超限原因，不能悄悄截断。

来源目录和 `read_context` 正文也要统一预算：目前普通源工具默认 20,000 字符，主助手 `read_context` 可到 100,000；控制初始 30,000 并不能控制后续窗口。增加固定正文的游标/片段读取：请求绑定 ref/result_id、固定 hash 和 cursor，返回实际范围、是否完整、下一 cursor。超大单片段要能继续细读，引用仍指向原始 location 与原文范围。

预算覆盖系统指令、tool schema、历史、工具返回、摘要和预留输出。现阶段保留明确字符防护并分别记录；token 估算必须标明估算算法/误差，没有核实当前模型 tokenizer 时不能把 tiktoken 数值标成 DeepSeek 精确值，也不能把 SDK filter 当作 provider 容量保证。

### 5.2 阅读账本：记录观察，不记录模型自称“读懂了”

建议在现有执行仓储增加类型化记录，独立于普通事件日志：

| 信息 | 最小字段/规则 |
| --- | --- |
| 身份 | research/node、task/spec、run、scope/version；报告完整 pin 或来源完整 ref |
| 一次请求 | tool_call_id、工具名、规范化参数 hash；query/location/cursor 等允许记录的非敏感参数 |
| 实际结果 | fragment IDs/范围、正文 hash、完整/摘录/截断/无匹配/拒绝/失败、可回查响应引用、时间 |
| 问题关联 | 本次正在核查的问题、仍缺材料；模型填写部分标为自述，不伪装执行事实 |
| 使用与复核 | 返回给模型、被某 claim 引用、独立语义复核分别保存；目录曝光不是正文已读 |

Core 已在**同一进程运行内**按 call_id 检查相同参数并复用结果（`core.py:81–95`），应保留。跨调用 ID 的内容重读用上述账本识别：固定身份+规范化参数相同、结果完整且权限仍有效时，可复用不可变响应；调用尝试仍计入执行额度，外部请求节省另计。不同页、不同关键词、新报告版本不能误判重复。搜索无匹配是“这个 query 没找到”，不能归纳成“这份资料没有答案”。

从上下文中移除长结果后，允许模型按固定响应引用再次获取片段；账本告诉它已有位置，而非全局禁止重读。资料没有进入本次模型输入时，“历史曾返回”不等于“当前看得见”。待引用的必要原句必须在收尾输入中可用。

每次命中缓存、分页、恢复和最终发布前均经过原 scope 和 report-grant 检查。撤权使派生摘要/缓存对后续模型输入不可用，不改变历史报告字节；审计记录的存在不能成为新的阅读授权。首版不把原文或密钥写入公共日志，详细内容进入已有受控仓储。

### 5.3 执行额度：先停止调查，再用保留资源交付

采用最小阶段：`researching → finalizing → delivered / incomplete`，另有 `cancelled` 和权限失效终态。阶段归执行器所有，模型只能建议停止。

- 工具次数、模型轮次、截止时间各自持久记账；工具参数错误仍消耗一次尝试；同一 call_id 的已提交重放不重复消耗工具次数。外部是否收费以提供方记录为准。摘要/格式修复的模型调用同样计入，不能成为隐形无限循环。
- 举例：保留总轮数 12 时，可给调查最多 9 次模型调用，留 1 次生成报告和至多 2 次格式修复；工具上限仍是独立的 12 次，不能保证全部用满。实际保留量待延迟测量，不把示例当新配置已生效。
- 时间也预留收尾窗口，在调查软截止前结束新外部调用；保留窗口仍受原总硬截止限制。180 秒是否足够必须测量，不能从 360 秒测试反推默认已改善。
- 在下一次模型调用前根据持久预算把所有调查工具设为 disabled；进入 finalizing 时显式构造 tools=[]，必要时使用 tool_choice=none，并保留原输出 schema。工具调度器再次检查预算，阻止模型或并行返回越过边界。
- 若已收到超额工具调用，应为相应调用记录“未执行、额度不足”，补齐协议返回并转 finalizing；不能留下未配对的 tool call，也不能无限返回额度错误让模型继续耗轮次。
- `call_model_input_filter` 负责输入投影；工具是否开启由工具/执行控制负责，两者不要混在一个隐式副作用函数里。

最终报告只使用已获得的正文与可追溯片段，列出已查范围、能成立的结论、未解决问题和停止原因。`ResearchAnswer`、证据校验和复核状态仍生效；待复核专家观点不能变成已核验事实。已有格式修复最多次数继续有界，不再开放调查工具。

**保证的是有终态、可见原因与恢复信息，不是任何网络故障下都能产出合格 AI 报告。** 若保留轮次里模型超时、拒绝或 schema/引用持续失败，输出应用生成的“本轮未完成交付”状态通知，列出已持久化的任务/材料和缺口；不把这条通知伪装成成功研究报告，不绕过 `evidence_required`，不自动采用结果。用户主动取消或撤权时直接停止，不能为了“收尾”继续使用被撤销内容。

### 5.4 恢复：保留同一工作身份，不重新开始调查

首个最小版本可只承诺已提交工具结果/阅读账本可恢复；下一层补充每轮模型边界的检查点。检查点绑定 task/spec/run、上下文投影 hash、固定报告/来源依赖、方法/模型、已用预算、阶段、待处理调用和 SDK 状态版本。

复用 SDK `RunState`/输出 item 表示和 DBOS 提交步骤，不自写一套对话协议解析器。需要先用受控模型验证 0.22.2 公共 API 在计划暂停点如何取得可恢复状态；不能假设 `Runner.run` 中任意 Python await 都能直接序列化。无法安全暂停的在途请求记为未确认，以最后一个已提交边界恢复。

恢复前重验取消、scope/version、任务当前版本、ReportGrant、方法/模型固定值；过期检查点拒绝继续，不猜测映射到新任务。已提交工具响应及最终回执只读重放；新调用用稳定的操作 ID。派发、发送专家消息等有副作用的工具必须经过原 command_id 幂等路径，不能仅靠工具参数 hash 判定等价。

模型/外部服务返回后、响应持久化前崩溃是不可消除的不确定窗口：外部服务支持幂等键才可避免重复，否则显式记尝试与未知用量，不声称零重复或零额外消耗。恢复不能重置耗尽预算；若需要新的时间/轮次，应作为明确的新继续命令记录新 allowance，并与原逻辑任务累计关联，不复用旧 retry 来偷偷放宽限额。

当前 `research/service.py:285–288` 的 retry 要求固定输入不变；新的上下文整理或追加授权不应伪装成该 retry。继续原专家任务走既有 information/resume 契约；只有用户修改任务目标才新建 spec version。模型调用恢复、信息等待恢复、后台主助手续接是三个边界，不能只给专家 DBOS 加检查点就宣称主聊天也已支持恢复。

### 5.5 永久保留的业务边界

- **引用**：仍由 `evidence/service.py:12–71` 核验固定 ref、原文和 location；来源事实、专家推断、未知分开。摘要、搜索摘要和目录不能冒充原始引文。
- **固定版本**：摘要、索引与阅读账本指向确切 result/task/spec/run 或 resource/snapshot/parse；“最新报告”只用于发现，读取后立即固定。
- **权限/撤权**：目录、正文、缓存、摘要、检查点、发布都重验；派生内容携带全部依赖，不能由模型声称“已经忘了受限来源”来解除依赖。
- **信息授权**：主助手实际读过才可授权；沿用原请求与 ReportGrant；专家自己的历史也需来源权限；继续原任务而不覆盖初报。
- **取消/版本竞争**：提交前检查取消和 revision；CAS 失败的旧运行不可覆盖新版本。取消优先于无工具收尾。
- **质量**：引用文字存在与支持结论是两项检查。错误历史结论保留审计但标记被纠正，模型自述“已验证”不能将 semantic pending 改为 passed。
- **已有报告**：只新增派生投影/运行记录；不重写、不重新解析替换、不自动采用、不迁移历史数据。不保留旧全历史输入开关或双运行时兼容层。

## 6. 最小可运行落地顺序

以下每层都先打通一个真实业务入口与持久查询，再扩大覆盖；不能先替换运行时再补权限。工期需要实施后估算，此处不给无依据的天数。

| 层 | 产物与改动位置 | 本层必须工作的端到端路径 | 进入下一层的条件 |
| --- | --- | --- | --- |
| 1：限额后交付 | `contracts.py`、`core.py`、`adapters/openai.py`；新增窄的 `execution/budget.py`；`app/research_execution.py` 接入 | 正常主聊天→工具达到软停止条件→无工具最终报告→原证据检查→保存；失败可见且不冒充报告 | 受控 SDK 模型证明不执行第 13 次工具，最后轮次/时间不被调查吃光；取消与撤权仍有效 |
| 2：阅读可见与可复用 | `execution` 的调用记录、`collaboration/service.py/history.py/information.py`、`context/tool_guard.py`、Postgres/内存仓储 | 查目录→读报告片段→读来源→看到已读范围→续页/合法复读→引用原文 | 参数/返回摘要可审计；分页无漏重；版本/授权/缓存命中正确 |
| 3：长对话投影 | 新增 `context/projection.py`；`research/service.py`、`context/builder.py`、SDK input filter；派生摘要仓储 | 长聊天→固定 revision 整理→小于输入预算的快照→读取历史正文→纠正旧结论→正常交付 | 移除旧全历史注入路径；历史约束与纠错测试通过；摘要失败有明确终态 |
| 4：细粒度恢复 | `durable.py`、`tasks/service.py`、SDK 状态适配、`execution` checkpoint；主助手执行入口也接持久步骤 | 在隔离 worker 中断→原 task/spec/run 从安全边界继续→结果只提交一次；主助手 finalizing 可恢复 | 数据库强杀测试通过；重复副作用、预算重置、撤权重放均被阻止 |
| 5：按测量改善检索 | 先测当前 `search_source`；必要时单独接 LlamaIndex 检索子包 | 中文/英文、同义词、长来源、不同版本的定位评测 | 有明确召回提升且权限/定位不退化才增加依赖；否则不实施 |

层 1–3 组成最小可用修复；层 4 才能宣称“模型调查进度可在进程崩溃后恢复”。已有任务级恢复在前几层仍保留原保证，不夸大。实现时使用独立空测试库和 blob 目录，已有记录仅只读；新派生记录使用当前明确 schema，旧不兼容在途检查点应明确拒绝，不加迁移/兼容猜测。生产部署是否等待在途任务结束属于实施安排，本轮不触碰服务。

## 7. 验收矩阵与失败边界

先用真实 SDK 的受控 Model 替身验证协议与控制，再用隔离 PostgreSQL/worker 验证持久化。现有测试基线包括 `tests/test_core.py`、`test_execution.py`、`test_report_information.py`、`test_information_requests.py`、`test_collaboration.py`，不能把已有跳过项算作通过。真实付费模型复验留待用户另行授权；本轮没有运行下表测试。

| 用例 | 输入与操作 | 必须观察到的结果 |
| --- | --- | --- |
| 历史超长 | 用保存消息构建超过 30,000 字符聊天，保留最近指令与早期约束；再做 10 倍长历史 | 原历史 hash 不变；模型拿到可控投影并能回查老消息；不是仅把上限调大；单条不可容纳输入明确报错 |
| 摘要漏约束 | 摘要替身故意漏“禁止联网”“首次发布版本”“不能覆盖初报”之一 | 缺项摘要不被发布；服务端无联网能力提升；缺失被明确呈现，不能默默继续 |
| 摘要错误历史 | 早期写“12% 与 21% 不能相减”，后来用户纠正 | 旧话被标为历史错误；输出能算 9 个百分点但不推出可比增长；不得把纠错摘要当外部证据 |
| 分支隔离 | 在相同祖先分叉，两边给相反要求或新报告 | 每个投影仅含固定继承范围和本分支；摘要/缓存不串支 |
| 同名非重复读取 | 相同 read_context 工具分别读 A v1、A v3、B v1、目录 | 四种请求可区分；不依据工具名自动抑制 |
| 重读与上下文淘汰 | 同版本同页重复调用；随后将正文移出窗口再次需要引用 | 可复用授权有效响应并保留调用审计；允许按原 ID 恢复必要片段；不能误称整篇已读 |
| 截断和无命中 | 单份报告超过 100,000 字符；资料查询没有命中；继续分页 | 单份可续读，范围连续且版本不变；无命中不变成“材料不存在”；目录/摘录/完整正文状态分离 |
| 工具额度耗尽 | 受控模型坚持调用第 13 次；含第 12 次参数错误、一个回复里多个工具调用 | 未执行超额 handler；工具协议完整；进入 tools=[] 收尾；实际计数与账本一致 |
| 轮次/时限先耗尽 | 工具未满但调查轮次或软时间到达 | 仍有预留收尾资源；无继续调查；总硬限不被重试延长 |
| 收尾失败 | 无工具模型再次给工具调用、坏 JSON、坏引用或超时 | 有界终止；不发布不合格报告；应用通知真实说明未交付并可查询进度 |
| 超时/崩溃恢复 | 在模型返回前、工具完成后未提交、检查点提交后、最终发布后分别强杀隔离 worker | 已提交结果不重执行；未确认窗口显式记尝试；同一 command 不重复派发/发消息；预算不清零；旧报告 hash 不变 |
| 信息等待恢复 | 专家已保存 progress 与请求，主助手已读并授权后中断 | 继续同 task/spec/run，ReportGrant pin 不漂移，不新建重复信息轮次；原初报仍存在 |
| 撤权与取消 | 在读后、摘要后、缓存命中前、恢复前、提交前撤权/取消 | 受影响内容不再送模型或发布；取消不触发额外收尾；缓存和摘要不能绕过原检查 |
| 历史行为误报 | 本轮只读取前次 send_agent_message 记录 | 报告必须说历史讨论；不声称本轮已追问；工具事实由账本核对 |
| 官方案例语义 | 固定 Census 历史资料、原专家报告、相同指令重跑；保留未见原笔记事实 | 不说“唯一缺陷”；完整保留调整限定；不从历史序列调整直接推出跨年断点；事实/推断/未知分列 |

对前后方案至少记录：输入字符/估计 token 与实际 usage、实际工具参数和返回范围、唯一已读片段数量、重复读类型、摘要次数、交付比例、失败原因、恢复重放/重试次数、首响应与总耗时。比较必须固定资料版本、任务指令、模型配置和限额；成功率应同时报告样本数及失败明细，不用一个合成成功替代稳定性结论。

**完成选型不等于 C4 语义通过。** 新控制用例可以确定性验收；摘要保真与结论正确仍需原始资料支持的独立语义检查。产品可以在证据不足时诚实交付“目前不能判定”，不应为了用尽额度或填满报告而强迫专家一致。

## 8. 决策记录

本项目最值得直接继续使用的是已接入的 **OpenAI Agents SDK + DBOS**，它们已有所需底层接口，且本地版本可查。最值得借鉴的是 **Deep Agents 的上下文分层、LangMem 的摘要游标、LangGraph/LangChain 的显式状态与限额处理**。当前不增加检索/记忆平台，是因为尚未证明新增依赖能比补齐现有业务缺口带来更大收益。

本篇原先的“额度收尾→阅读账本/分页→长历史投影”是上下文模块内部路线；纳入独立沙箱与编码 worktree 后，总体阶段以[统一选型第 7 节](multi-agent-context-architecture-2026-10-05.md#7-从现状到完整方案的实施顺序)为准。每一步都必须通过原有固定版本、权限撤销、取消、信息交接、证据校验和不覆盖报告的回归门槛。若未来测量显示跨资料检索成为主要瓶颈，才启用 LlamaIndex 组件备选；这一条件不要求今天预埋第二套运行时或可切换框架抽象。
