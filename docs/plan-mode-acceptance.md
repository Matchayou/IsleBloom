# Plan 模式实现与验收

日期：2026-10-12。

## 已实现的产品流程

聊天输入框的“计划”生成结构化提案；“执行”继续原有 ReAct 回复。生成提案结束后等待用户明确确认，不在模型循环中自动批准。确认前没有计划执行节点，没有专家派发、正式结果采用或发布。

用户可查看分析、假设、必需澄清、步骤、依赖、资料版本、权限与预算；可修改、拒绝或取消。修改原子地拒绝旧提案并创建新提案，旧确认失效。确认绑定待审核的具体 revision，确认身份来自服务端；重复确认和重复 DBOS 投递不会重复执行。

主线规划读取已授权研究资料库，分支规划保持分支资料范围。规划使用独立只读 scope，保留完整用户要求与历史，不改变原节点或原 scope。受控引擎同时限制 planning 与 plan_step 的工具集合，模型没有确认工具。网页搜索留给确认后的步骤。

确认事务保存批准记录、创建冻结执行节点并归档现有 ResearchPlan。ResearchPlan / PlanStep 继续表达 DAG 和预算，Dispatcher 继续控制就绪批次和并发；CoreRunner、ScopeExecutionGate、ControlledEngine、SDK、输入投影与用量账本继续执行每一步。节点业务修改和直接运行被阻止，布局移动及后续独立分支仍可使用。

步骤仅绑定本计划已保存的固定上游结果。必需上游失败或部分交付阻止下游。最终执行完成不代表结果已经完成语义审核、采用或发布。

## 持久化与入口

新增 research_plan_proposals 表，使用研究内唯一 request_id、revision CAS 和事务保存批准/拒绝/修改。queued 作为持久化派发意图。服务恢复扫描生成和排队记录，生产复用现有 DBOS 生命周期与队列，增加独立计划工作流；代码版本为 plan-v1。

确认后重启可执行 queued 计划，批次间恢复复用已提交结果并累计原有预算。模型或工具执行是否完成无法确认时，状态变为 interrupted，停止且保留检查线索，不分配新 run ID 盲目重试。

API：plan_generate、plan_proposals、plan_proposal、plan_revise、plan_confirm、plan_recover、plan_reject、plan_cancel。前端只取得审核内容、状态、用量和已交付报告，不传输完整私有规划输入/历史。

CLI：plan-generate、plan-show、plan-revise、plan-confirm、plan-recover、plan-reject、plan-cancel。旧 `/api/plan` 与 `plan FILE` 直接执行协议已移除；方法工作流改为 workflow_propose / agent-methods propose，统一进入同一确认机制。评测与测试中的手工计划也显式导入并确认。

## 验证结果

最终相关测试：**150 passed，1 skipped**。跳过项是内存适配器上的“数据库关闭重开”证明；相同测试的真实 PostgreSQL 版本通过。

覆盖：

- 生成不创建执行节点或正式运行；规划工具越权和伪造派发失败。
- 待澄清问题禁止确认，旧版本/旧提案确认失败，来源变动与撤权阻止执行。
- 重复确认/执行只保存一个计划与一组步骤；确认事务失败回滚。
- 正式执行节点的普通 run、retry、continue、mount、chat_reply 不能绕过计划守卫。
- 原用户要求、历史、资料和结果固定，后续聊天不会替换批准输入。
- 上游部分交付阻止下游；保存的上游结果恢复后不再次调用模型。
- 确认后恢复队列、真实数据库关闭重开、未知步骤停止不重发。
- 真实 PostgreSQL + 已安装 Agents SDK + ControlledEngine/Broker 验证规划与步骤的独立预算、工具列表和检查点身份。
- DBOS 生成后等待确认；确认后后台执行，重复投递只查询原工作流。
- 原有研究、方法、项目、聊天、受控协作和 SDK 检查点回归。

最终测试范围：

```sh
pytest tests/test_planning.py tests/test_planning_controlled.py \
  tests/test_research.py tests/test_methods.py tests/test_controlled.py \
  tests/test_projects.py tests/test_chat.py tests/test_controlled_collaboration.py \
  tests/test_checkpoint_sdk.py -q
```

测试使用本次独立创建的临时 PostgreSQL 实例，TEST_DATABASE_URL 和 CE2_TEST_DATABASE_URL 指向不同测试库；未使用用户生产数据库。

浏览器：**2 passed**。通过真实 HTTP 服务、真实 PostgreSQL 和受控 SDK 验证生成→修改→确认→执行→刷新→查看交付，以及前后端同时禁止确认未解决的必需问题。使用 scripts/verify_plan_browser_server.py 提供固定模拟模型，端口 8769；测试在 frontend/tests/plan.spec.js。

前端 `npm --prefix frontend run build` 通过；保留现有 React Flow 的 use client 忽略和 bundle 大小提示，没有新增依赖。

## 验证边界

模型响应是确定性验收夹具，证明控制机制、持久化和界面联通，不证明真实模型生成计划的语义质量。上述夹具验收未调用外部付费模型；后续真实模型验证见下节。未知执行中断采用停止检查策略；未宣称任意工具副作用可以自动恢复或自动重跑。

设计依据保留在 [设计稿](designs/react-plan-mode.md)。前端异步加载采用取消过期读取的现有项目模式，核对了 [React 官方 Effect 指南](https://react.dev/learn/synchronizing-with-effects)。modern-web-guidance 的 npm 获取在本机不可用，未因此增加依赖或阻塞实现。

## 真实模型确认后执行（2026-10-12）

真实 deepseek-v4-pro 生成三个步骤，人工确认原提案版本 1 后进入执行。第一步调用真实模型并保存了 partial 结果：执行上下文仍携带“当前只生成计划、等待确认”限制，模型再次产出计划，没有搜索或读取资料。fail_fast 正确阻止了后两步，完整研究尚未完成。结果保存和服务重启后的前端读取已验证。

修复：执行系统指令和结构化 handoff 明确当前阶段为 approved_plan_execution，保留批准版本/身份和历史研究约束，明确历史规划阶段限制已被确认解除。前端明确显示“不完整交付，已阻止后续步骤”。新增恢复入口只创建待确认提案，不重放原请求、不覆盖旧结果；同一恢复请求幂等，未知用量或结果禁止恢复。

修复后的相关 PostgreSQL 回归：86 passed，1 skipped；前端构建通过。新增测试覆盖历史规划限制解除、失败记录保留、恢复方案确认前禁止执行、明确确认后执行。真实页面手工验证恢复入口、待确认状态、原结果重启后可读及短窗口中确认按钮可见。短窗口自动化测试已添加，尚未执行。

恢复提案 7ebde8db-a948-4758-9551-c885958c927c 版本 1 待确认，未启动新模型调用。三步内容、模型、权限、依赖与每步预算沿用原计划；新执行预算为 3 次运行 / 600 秒 / 30000 tokens，上轮 9503 tokens 另存。因此本轮仅完成真实执行尝试、错误修复和恢复草案，不能宣称三步完整成功。

记录：[原执行快照](plan-mode-evidence/live-plan-execution.json)、[恢复方案](plan-mode-evidence/live-recovery-review.md)、[恢复提案快照](plan-mode-evidence/live-recovery-proposal.json)。

## 恢复方案已确认并实际运行

2026-10-12：用户确认恢复版本 1，前端提交批准后执行器完成 search_web 和 fetch_web，第一步在 180 秒时限到期后保存 timeout。已确认用量 24024 tokens，另有 2 个模型 operation 用量未确认。后两步未运行，没有最终研究报告。

服务重启后，原结果逐项相同，本次错误和来源记录仍保存；前端展示超时和未完全确认的用量。新增失败展示回归测试通过，前端构建通过。完整三步真实模型验收仍未通过，不能宣称成功。详见 [恢复执行验证](plan-mode-evidence/live-recovery-validation.md)。

## 超时审计与第3份待确认计划

已核对第二轮的5条模型operation，发现未确认两条分别是上下文摘要超时及输出修正取消，并非两次已完成研究响应。模型计量仍保持unknown；已确认24024 tokens，旧账记录不改写。上下文摘要约45秒失败，最终交付非JSON触发修正，修正仅余约6秒，180秒硬截止生效。详细证据见 [超时诊断](plan-mode-evidence/live-timeout-diagnosis.md)。

新提案 c7a9db03-a614-40da-83f1-0205e099de78 版本1，awaiting_confirmation，单步600秒、计划1800秒、120000 tokens、三步串行、每步8轮模型和6次工具调用。使用本地已有PlanningOutput→ResearchPlan编译及校验路径创建，无外部规划调用，未创建执行节点。前两份提案不变。

联网后生成新计划发现discovery摘要被错误挂载为正文，已在规划prepare筛除discovery，保留正文和发现记录；新增回归测试。计划测试（内存与隔离PostgreSQL）35 passed，1 skipped。新版提案及预算已在前端确认，截图live-v3-awaiting.jpg。

## 运行层根因修复（2026-10-12）

继续使用现有SDK/ResearchPlan/受控链：研究和规划严格JSON；DeepSeek调查low、摘要和无工具交付none；DSML协议异常不执行，进入原预算内纠正或明确失败。摘要改按窗口压力触发；收尾预算按时限和已测任务模型时延预留。批准步骤不再混入原规划请求，原历史保存在提案。请求关联标识和时延保存到审计记录。DBOS代码版本更新plan-v2。

193 passed、1 skipped；另durable单测1 passed、2 skipped。真实模型无工具JSON和一次原生读取工具后关闭工具/JSON交付均成功；后者出现一次DSML，识别并纠正成功，未执行额外工具。不能宣称提供商永无协议错误。

真实新提案cda35191-f054-471f-a836-44b7ba2ce55d版本1待确认，三步600秒/总1800秒/120000 tokens，规划一次2610输入+2598输出。前端确认入口已核对，execution_steps为空；原两轮记录保留，旧本地草案已关闭。详细见 [修复验收](research-runtime-fix-2026-10-12.md)。

## 修复后批准计划执行的实际结果

版本1按600/1800秒和120000 tokens批准并运行。第一步56.708秒结束，4次模型/6次工具、0摘要，45495 tokens计量完整；一次DSML纠正成功。结果保存3份来源和4条证据关联声明，但因论文全文缺口missing非空被标partial，fail_fast阻止后两步。

前两轮结果逐项保持，前端刷新展示当前部分交付和后两步等待。新发现完成判定没有区分阻断缺项与允许局限；三步整体验收未通过，不能标记成功。详见 [本轮执行验证](plan-mode-evidence/live-fixed-execution-validation.md)。
