# ReAct 智能体 Plan 模式设计

日期：2026-10-12。状态：已按本设计实现；具体协议调整及验证范围见 [实现与验收](../plan-mode-acceptance.md)。

## 产品行为

用户在聊天输入框选择“计划”后，主助手分析目标、读取已授权资料并生成可审核的计划。系统保存提案后结束本次规划运行，不保持模型循环等待用户。用户可以修改要求、拒绝，或点击“确认并执行”。只有明确确认具体版本后，系统才执行计划步骤。

规划也是模型运行，会消耗模型与资料读取预算。“确认前不执行”指不启动计划步骤、不派遣专家、不修改业务资料、不采用或发布结果；规划运行的审计、草稿保存属于必要系统写入。

第一版覆盖项目已有研究节点可完成的资料检索、分析、比较和综合。每一步执行时继续使用现有 ReAct 工具循环。规划本身不引入新的智能体循环。编码、发布和跨计划自动重规划不属于本模式首版能力；若目标要求这些能力，提案明确标记不支持，不能假装已安排执行。

## 现有能力与需要修正的边界

- `research/models.py` 的 `ResearchPlan` / `PlanStep` 已包含节点身份、DAG 依赖、步骤预算及整体 `DispatchPolicy`，应继续作为唯一可执行计划结构。
- `research/plans.py` 已处理就绪步骤、固定上游结果绑定、Dispatcher 并发及预算，应修改这个执行器，不另建调度系统。
- `run_plan(plan)`、`POST /api/plan`、CLI `plan FILE` 当前直接执行，必须移除直接执行协议，统一进入提案与确认服务。不保留隐藏绕过入口或兼容开关。
- `Research.plans` 目前是已接纳计划的记录，可保留为执行归档；提案生命周期由独立仓库保存，避免把整份历史反复写入 research JSON。
- `admit_plan` 当前通过读取并重写 JSON 数组接纳计划，缺少足够的并发保护。确认、接纳及归档改为事务中的行锁/唯一约束，避免双重执行及并发丢失更新。
- 当前计划执行在开始时加载节点，准备步骤时又读取活动数据，尚不能保证用户确认的节点内容就是最终执行内容。执行入口必须接收冻结提案，步骤准备只能使用批准的定义和输入。
- 当前执行器允许上游缺失时携带 missing 继续执行。Plan 模式采用必需依赖：上游没有完整且获准使用的固定产物时，下游 blocked，不把部分结果当作完成。
- `OpenAIEngine` 对检查点 interruption 调用 `history.approve()`，是内部受控工具恢复流程，不是用户确认。业务确认独立于 SDK interruption。
- `durable.py` 当前仅支持专家序列，不可直接宣称支持整份 ResearchPlan 的持久恢复；需在现有 DBOS 实例中增加计划工作流和队列。

## 数据设计

保留 `ResearchPlan` 和 `PlanStep` 的基本结构，不把审批字段加到每一步，也不建立第二种可执行 DAG。新增一个 `PlanProposal`，包含三组信息：

1. 提案身份与状态：`id`、`research_id`、`origin_node_id`、`revision`、`status`、`request_id`、时间戳，以及确认人的服务端身份、确认时间和批准版本。
2. 审核内容：目标解释、交付要求、假设、缺口、需要用户回答的问题、计划步骤说明、精确资源引用、工具权限、步骤及总预算；可执行内容为 `plan: ResearchPlan`。
3. 冻结输入：按 `PlanStep.node_id` 映射的节点定义（目标、指令、工具、资料 ResourceRef、固定已有结果 ID），原始用户要求/相关对话的固定快照引用，原节点/聊天版本及授权范围版本；规划运行 ID、固定模型与指令版本。

每个步骤的交付要求写进冻结节点指令，并同时展示在审核卡片中。首版不引入额外的通用验收 DSL；已有结构校验及证据校验继续使用，语义审核继续保留人工状态。

规划模型输出一个严格 Pydantic 结构：目标分析、待澄清项、步骤草案、依赖步骤键及交付要求。它不生成数据库身份、确认记录或可信权限。服务端分配 proposal/plan/node 身份，将步骤键转换为现有 `PlanStep.node_id` 与 `depends_on`，构造 `ResearchPlan` 并执行其 DAG 校验。

步骤节点在确认前仅保存在提案中，确认事务中才创建真正的 `ResearchNode` 和 scope。采用新的步骤节点，不覆盖用户现有对话节点。节点挂在原始研究节点下用于导航；执行依赖只由 ResearchPlan 决定。

提案存放在新增 `research_plan_proposals` 表：独立主键、research_id 索引、revision、status、JSONB payload。生成请求的 request_id 在所属研究内唯一。提案行上的批准版本与请求身份足以实现确认幂等；不要再建通用审批平台。

修改待确认提案会增加 revision 并替换待确认内容；确认后内容不可变。后续改计划生成新的提案 ID，与旧提案关联；不修改已批准内容或沿用旧确认。

## 状态与确认协议

生成请求保存 `generating`，成功后进入 `awaiting_confirmation`；生成失败为 `generation_failed`。生成中取消使提案进入 `cancelled`，即使模型稍后完成，也不能发布可确认内容。

正常执行路径：

`generating → awaiting_confirmation → queued → running → succeeded / failed / blocked / interrupted / cancelled`

`awaiting_confirmation → rejected` 表示用户拒绝。

确认就是一个事务：校验所有权、expected_revision、待确认状态、完整 DAG、工具/资源授权、当前源节点和聊天版本、无未解决的必需澄清问题、预算可执行性；创建冻结步骤节点/scope，保存 approved_revision、approved_by、approved_at 和唯一工作流身份，状态变为 queued。queued 即“确认已保存，等待执行”，不再增加一段容易悬空的 approved 状态。

版本冲突返回 409 和具体差异，保留旧提案待查看，要求重新生成并确认；权限撤回返回 403。不自动放宽工具、更新资料快照或以最新结果替代冻结结果。确认重复提交返回已有提案/工作流状态，不创建第二次执行。

确认人由当前受信服务身份决定，不接受客户端或模型传入的 approved_by。确认按钮和显式 CLI confirm 是确认入口；模型没有 confirm 工具，普通聊天中的“好的”不触发批准。

## 工具与执行隔离

规划能力采用服务端固定工具白名单，首版只开放当前授权资料的目录、检索、正文与既有结果读取。`search_web` / `fetch_web` 留给确认后的步骤；规划时依据已有资料提出取证方案，不提前完成正式联网研究。系统可以保存规划摘要/草稿，但不给规划模型业务写入工具。

规划模型不可见也不可调用 delegate、delegate_task、send_agent_message、revise_task、adopt_result、节点执行、代码写入或任意 shell。限制同时由工具绑定和服务端 admission 检查实现，不靠提示词或隐藏按钮。

规划接入现有 ControlledEngine、输入投影、用量账本和 SDK Runner；增加明确的 planning admission 类型及校验回调，不直接创建裸 Runner 绕过受控运行。只扩展规划所需的输出 schema 和调用入口。

确认后的步骤继续复用 build_execution/CoreRunner；能力来自冻结步骤定义与当前授权的交集。服务端将计划步骤绑定到 proposal_id/approved_revision，明确构造研究工具集合，不能因为节点后来成为主线而自动加载总管派发/管理工具。

所有对计划专属节点的 run、continue、retry、adopt、chat_reply 等执行入口检查所属提案及批准状态。用户确认可授权批准范围内的 ReAct 工具选择，不能授权额外节点、扩预算或动态委派。普通 Execute 模式仍存在，但不得借它启动未批准或已取消的计划步骤。

每次准备和工具调用仍检查授权撤回。计划节点冻结字段不可通过通用 mount/goal/instructions 操作修改；改执行内容需要新提案。展示位置可独立修改。

步骤结果只用于已批准的下游处理，不自动采用为项目最终结论，也不触发发布。确认计划与审核最终结果是两个操作。

## 执行、预算和恢复

从提案读取已批准的 `ResearchPlan`，仍按 DAG 分批选择 ready 步骤，并让 Dispatcher 控制并发。绑定结果时只查该计划的固定 command/run 产物，拒绝 latest 指针；保留明确的 source 版本和依赖记录。

整体 policy 必须满足计划可完成的条件，例如 max_runs 至少覆盖步骤数量；每步预算不得超过总预算。保留总时限、模型/工具调用上限及用量未知时停止的现有规则。默认 fail_fast，取消未开始步骤并停止运行中的同批步骤；必要依赖失败使下游 blocked。完整成功必须是所有必需步骤完整交付，不是“调度已结束”。

规划预算和执行预算分别展示和记账；确认页展示确切上限，模型给出的费用估计只能是估计。确认后的原预算在恢复时不得重置。

确认事务中的 queued 记录充当持久化派发意图。现有服务启动和后台工作器扫描 queued，使用固定 `plan:{proposal_id}:{approved_revision}` 工作流 ID 幂等投递 DBOS；确认请求也可立即尝试投递。这样确认后、投递前进程退出不会丢失批准任务。状态存储在业务仓库，DBOS 管恢复，不能由前端重新提交计划恢复。

扩展现有 DBOS 注册点，增加计划工作流；逐步 prepare/execute/publish 继续走受控账本。现有 admit_plan 返回 [] 的去重行为需改为读取执行记录并对账，恢复不是重新接纳或重新生成 plan ID。

工作流重启查询已保存 command、run、固定结果及步骤状态；已完成步骤不再调用模型。预算基于持久运行账本累计。未知工具副作用、未确认发布或不能证明安全恢复的情况进入 interrupted/blocked，展示原因；用户选择恢复也必须走账本校验，不能重跑整份计划。

取消先持久化取消意图，再停止 DBOS/活动执行器；开始每个新步骤前检查取消状态。拒绝适用于待确认提案，取消适用于生成或已排队/运行计划。保留已完成结果和审计。

## API 与前端

新增统一接口，参数和响应都用严格 schema：

- `POST /api/plan_generate`：research_id、origin_node_id、expected_chat_revision、request_id、要求；返回 202 与 proposal_id，后台生成。
- `GET /api/plan_proposal?id=...`：返回当前版本、可展示审核内容、状态与步骤执行投影，沿用所有权/授权检查。
- `POST /api/plan_revise`：proposal_id、expected_revision、request_id、反馈；原子进入 generating，旧内容仅供参考，生成期间不可确认。
- `POST /api/plan_confirm`：proposal_id、expected_revision；返回 202 queued 或已有执行状态，不接收客户端再次提交的 ResearchPlan。
- `POST /api/plan_reject` 与 `POST /api/plan_cancel`：proposal_id、expected_revision。

规划消息以服务端命令身份标记 planning，使普通 chat_reply 无法执行同一生成请求。模式只作用于本次提交；切换输入框选项不能批准、取消或改写已保存提案。

聊天输入框加入“执行 / 计划”；计划卡片展示分析摘要、步骤、依赖、资料版本、联网权限、交付要求、预算和待澄清项，并提供“修改计划 / 拒绝 / 确认并执行”。未解决必需问题时禁用确认，后端也拒绝。无需展示隐藏思维链。

确认按钮绑定卡片 revision。生成中、确认中和已确认分别显示清楚状态；刷新从 proposal_id 查询，不依赖 localStorage 判断已批准。可以复用现有 SSE 展示规划摘要/进度，流式预览不可确认，只有已保存完整提案可确认。

执行中的步骤节点复用 React Flow 和现有结果面板。卡片同时显示“计划执行状态”和“结果审核状态”，避免执行成功被理解为事实已确认。

CLI 移除直接执行 `plan FILE`，改为 plan-generate、plan-show、plan-revise、plan-confirm、plan-reject、plan-cancel。若允许手工计划导入，也只能保存待确认提案，不能顺带执行。

## 代码职责

- research/models.py：PlanProposal、规划输出和冻结节点定义；保留 ResearchPlan 的 DAG 职责。
- research/planning.py（新增）：规划运行、草案规范化、业务校验、提案 revision。
- research/plan_service.py（新增）：确认/拒绝/取消、审批与排队事务。
- research/plans.py：复用并收敛为仅执行获批准提案；固定输入、必需依赖、状态与账本恢复。
- adapters/postgres/research.py 及仓库协议、内存测试适配器：提案 CRUD/CAS、唯一请求、事务行锁和计划归档。
- controlled/admission.py、app/research_execution.py：planning admission 与计划步骤工具限制、入口执行守卫。
- durable.py：现有 DBOS 生命周期中的计划工作流、固定身份投递与 queued 扫描。
- app/api.py、research_cli.py：统一调用计划服务，删除旧直接执行入口。
- frontend/src/workspace：PlanProposal 卡片、输入框模式、刷新/进度；现有 FlowCanvas 继续导航。

## 分层交付与验收

第一层完成生成、保存、查看、修改、拒绝的端到端流程，运行受控只读规划；执行确认按钮在后端执行链完成前不开放。第二层完成原子确认、固定 ResearchPlan 后台执行与结果展示，开放确认。第三层在同一执行链上完成崩溃恢复、取消和并发验证；正式发布整个模式须三层都通过。

必要验收：

1. 生成计划只能出现 planning 运行和资料读取，无正式步骤 run、专家派发、业务结果采用。
2. 强制模型尝试派发或伪造确认，不会获得工具或执行许可；旧 API/CLI 直接执行协议不可用。
3. 提案步骤确实转为 ResearchPlan，未知依赖、环、超权资源、无效预算和不支持任务均不能确认。
4. 两次确认、两个浏览器同时确认、响应丢失重试，只出现一个批准版本和一个工作流。
5. 修改与确认并发只允许一方 CAS 成功；用户看到的版本与最终执行定义一致。
6. 源对话、输入版本或权限改变时旧确认失败；批准后权限撤回阻止后续读取/执行。
7. 上游失败/部分交付阻止必需下游；不使用更新的 current_result_id 偷换上游。
8. 确认事务后投递前退出、运行中退出、发布边界退出均有明确恢复结果；已完成步骤无新增模型调用、预算不重置。
9. 点击取消后不再启动新步骤；通用节点命令无法绕过计划锁定及取消。
10. 浏览器覆盖生成→修改→确认→后台执行→查看结果，刷新仍能找回待确认/排队状态。
11. 原有普通 ReAct 执行通过必要回归；最终结果审核/采用仍由原有权限及流程控制。

## 参考依据

本设计已检查仓库保存的 OpenAI Agents SDK 官方源码示例与说明：

- architecture-analysis/sources/openai__openai-agents-python/code/examples/financial_research_agent/agents/planner_agent.py：规划通过结构化 output_type 产出，可独立于后续工具执行。
- architecture-analysis/sources/openai__openai-agents-python/code/docs/human_in_the_loop.md：确认绑定明确待批准动作并持久化恢复状态。

采用结构化规划与持久化确认的既有模式；业务计划批准放在应用层，SDK checkpoint 保持原职责。参考文件为本地保存版本，本文不对外部最新 SDK API 作断言。
