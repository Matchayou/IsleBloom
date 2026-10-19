# 轻量 Agent 基座重新选型：Pi、Hermes、OpenAI Agents SDK

日期：2026-09-15。用户明确排除以 LangGraph 为架构。本报告替代上一轮主基座推荐。

## 结论

- **架构简洁优先：Pi SDK。** 复用 pi-coding-agent 的会话/扩展能力，或在确需彻底定制时用 pi-agent-core；补业务任务树、受控研究工具、证据和反馈存储。
- **现成功能复用优先：Hermes Agent。** 已有嵌套委派、并行、记忆和技能，可直接二开；它是功能丰富的个人 Agent 产品，代码和产品范围比一个精简 SDK 大。
- **轻量多 Agent 编程与官方示例优先：OpenAI Agents SDK。** Python/TypeScript 均可选；官方金融研究示例很贴合首个场景。

这三条路线选择一个，不建议为适配三个内核先造统一抽象，也不建议先把 DeerFlow 的 LangGraph 内核替换掉。

## 1. 核查概况

GitHub API 快照：Pi 当前仓库 earendil-works/pi，旧 badlogic/pi-mono 链接会跳转；105,339 星。Hermes Agent 245,662 星；OpenAI Agents Python 29,448 星，JS 3,818 星。上述仓库均 MIT，查询时当天都有推送。

本轮读取官方文档和 README，并抽查 Hermes delegate_tool.py、Pi 官方 subagent 扩展与 SDK 文档、OpenAI 官方研究示例。未部署、未跑测试，没有对社区扩展做完整源码审计。

## 2. Pi：最容易按你的方式塑造内核

[仓库](https://github.com/earendil-works/pi) 中有三个不同层次：

- pi-ai：多模型调用层。
- pi-agent-core：工具循环、状态、事件、上下文转换。
- pi-coding-agent SDK：在其上提供会话、压缩、技能与扩展等能力。

先选能满足需求的现有 SDK 层，不必为追求代码小就只拿最底层、再重写会话功能。当前仓库使用 @earendil-works 包名；不少旧文章使用 @mariozechner，实施时应按选定版本文档操作。

[agent-core 文档](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md) 显示 transformContext/convertToLlm 可在模型调用前转换上下文，并提供事件流。它是实现 ScopeContext 的切入点，但消息过滤本身不能阻止工具从共享文件或数据库读入其他分支资料。

[SDK 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md) 有持久化 SessionManager、恢复、分支、树遍历接口。不过，会话分支不等于多 Agent 研究任务树；需要保留 node_id、parent_id 与 session_id 的映射。

[项目说明](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md) 明确将多 Agent 交给扩展。官方 [subagent 示例](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/subagent/index.ts) 有单任务、并行、链式调用；当前示例用 --no-session 启动子进程，所以不能直接拿它满足持久化续研。可以借鉴调用和事件模式，改用持久化 SDK 会话。

**需要补：** 搜索与抓取工具、业务范围校验、引用数据、任务生命周期、分层委派预算、用户选择事件与偏好版本。Pi 内核简洁，不代表你的业务调度责任消失。

## 3. Hermes：最接近“现成 Agent + 进化”

[仓库](https://github.com/NousResearch/hermes-agent)；[架构](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)。可复用工具、会话、CLI/网关等产品能力。其 [技能](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) 与 [记忆](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) 机制提供跨任务复用的基础，但不自动等价于用户节点选择驱动的工作流优化。

[委派文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation) 提供并行、独立子会话、嵌套深度控制。

抽查 [delegate_tool.py](https://github.com/NousResearch/hermes-agent/blob/main/tools/delegate_tool.py) 可见：

- child_depth 与 max_spawn_depth 控制是否还可委派；另有 orchestrator_enabled 总开关。
- 创建子 Agent 时使用单独 session_db、parent_session_id，传入 skip_memory=True。
- 子任务进度含 subagent_id、parent_id，适合映射研究节点。

**文档/主线存在差异：** 文档展示 role="orchestrator"，本次主线代码则说明 role 参数被忽略，依据深度与开关计算角色。因此真正实施必须固定版本；本轮不把文档样例直接当作最新可执行配置。

**需要补：** 行业/公司/维度研究节点、scope 数据边界、长期保存的证据记录、用户点击继续的状态恢复和偏好归因。日志与子会话存在，并不能单独证明任意中断都能无损恢复。

**适用：** 不想重写技能、记忆、工具、个人助手入口，愿意维护完整 Python Agent 产品。

## 4. OpenAI Agents SDK：控制流直接，官方金融例子可复用

这里选的是 Agents SDK，不只是底层 openai API 客户端。

[官方编排文档](https://developers.openai.com/api/docs/guides/agents/orchestration) 区分 handoff 与 agents-as-tools：你需要子任务结果回给父 Agent，由父 Agent 汇总，因此优先 agents-as-tools 或明确的 Runner 调用。handoff 更适合转交对话控制权。

[结果与状态](https://developers.openai.com/api/docs/guides/agents/results) 提供会话续接和可序列化的中断状态。它不替代研究节点和任务数据库，也不意味着所有外部工具都具备事务性的自动重放。

直接复用的官方例子：

- [research_bot](https://github.com/openai/openai-agents-python/tree/main/examples/research_bot)：规划→并行搜索→报告。
- [financial_research_agent](https://github.com/openai/openai-agents-python/tree/main/examples/financial_research_agent)：金融搜索、基本面/风险分析师作为工具、报告、校验。

每个公司/维度用独立会话与显式输入包；普通代码负责并发、等待和汇总。SDK 不会自动理解“排除了哪个行业”，业务 scope 仍须由工具和存储执行。

## 5. 额外找到的可复用代码

以下为 GitHub 元数据和 README 初筛，不代表运行验证通过。不要把上游 Pi/Hermes 的星数当作扩展自身的成熟度。

| 项目 | Stars | 最后推送 | 复用判断 |
|---|---:|---|---|
| [GalaxyXieyu/pi-agents-flow](https://github.com/GalaxyXieyu/pi-agents-flow) | 0 | 2026-08-27 | 有研究角色和多 Agent 流程，0 星；仅作为源码线索。 |
| [edxeth/pi-subagents](https://github.com/edxeth/pi-subagents) | 123 | 2026-09-14 | Pi 前后台、多 Agent、fresh/forked context 与父子消息扩展；适合进一步审查，非成熟度保证。 |
| [SnowWarri0r/hermes-researcher](https://github.com/SnowWarri0r/hermes-researcher) | 7 | 2026-05-28 | Hermes 研究 UI/多阶段流程；README 描述 SQLite、重试与可视化，但仅 7 星。 |
| [pafozz/hermes-deep-research](https://github.com/pafozz/hermes-deep-research) | 0 | 2026-06-11 | 大纲→并行研究→报告的 Skill；0 星，许可 API 未分类。 |
| [Muuuun/luxas](https://github.com/Muuuun/luxas) | 1,055 | 2026-09-06 | 基于 Pi 的科研 Agent，含子进程、文件记忆、实验与论文流程；偏科研，且 vendored Pi 包会增加升级维护责任。 |
| [qx-labs/agents-deep-research](https://github.com/qx-labs/agents-deep-research) | 789 | 2025-12-27 | OpenAI Agents SDK 上的迭代研究与并行章节生成；789 星，最后推送 2025-12-27，需检查当前 SDK 兼容性。 |

## 6. 推荐结构

研究节点 UI → 研究任务服务 → 选定的一个 Agent 内核 → 受范围约束的搜索/读取工具。

研究任务服务只负责：创建节点、构造允许输入、运行/继续/取消、收集结果、持久化状态与事件。模型负责提出研究分解和使用工具。界面按实际执行与证据显示节点，无需让用户先设计一张完整静态图。

最小长期数据：ResearchNode、Evidence、Feedback、SkillVersion。Agent 会话引用放在节点中，避免聊天历史成为唯一业务事实来源。SQLite 可用于单实例起步；不要先为假设中的多内核和分布式负载引入抽象。

进化路径：明确评价/选择 → 场景偏好 → 候选工作流或技能 → 保留任务集评估 → 版本升级。点击继续只能是兴趣信号之一。

下一步应先验证一个端到端场景：一行业、三公司、每公司两个维度、父级汇总、选择一个已完成节点续研。分别检查范围污染、分支失败、跨重启恢复、引用完整性。通过后再加偏好进化。
