# Stage 4 — 实现与验收记录

范围：最新《增量开发架构与阶段验收》Stage 4 全节、两案例、模块关闭与发布门槛。2026-09-20。Stage 0–3 保留；未实现 Stage 5。

**状态：Stage 4 已通过真实模型、跨进程及逐项语义验收。** 用户提供当前凭据后实际调用 DeepSeek-V4.1-Flash（API 标识 deepseek-flash），18 次运行成功；最终报告人工复查通过。此前缺密钥的 not_run 已由本阶段实际证据取代。完整回归见 `stage-4-tests.txt`。

## 固定复用来源与边界

- 完整阅读阶段设计、源码索引、需求—功能—参考实现、development-acceptance-process、README 及 Stage 0–3 验收记录。仓库无 HEAD，继续现有目录；未创建空 worktree、reset、提交或删除前序资料。
- Co-STORM 固定 `fb951af7744dab086e34962e9bc6fe878e145f83` 的 `KnowledgeNode`、`insert_information`、`need_regenerate_synthesize_output`：借鉴父子组织和更新提示。没有复制其递归收集全部后代、可变汇总正文或 DSPy 运行流程。
- React Flow 固定参考 `0a1f9575b25679f2880175de8d3eae21aedde921` 的 nodes/edges 类型、useNodesState；核对[受控交互官方文档](https://reactflow.dev/learn/concepts/adding-interactivity)与 [ReactFlow 事件接口](https://reactflow.dev/api-reference/react-flow)。实际完整依赖 `@xyflow/react=12.11.1`、React/ReactDOM 19.2.0，未导入源码摘选。
- 沿用 SDK 0.22.2、SQLAlchemy 2.0.43、psycopg 3.2.10、Docling 2.129.0 等既有依赖。HTTP 采用已安装的 Starlette 1.6.0 / Uvicorn 0.53.0，升级为直接锁定依赖。没有加入第二套 Agent/runtime 或托管网站产品。
- 前端使用 modern-web-guidance 的 forms 指南：原生 label、checkbox、fieldset、按钮、状态提示与响应式布局。技能提示本地版本旧，不阻断本轮工作；未修改用户技能。Markdown 表格采用 react-markdown 10.1.0、remark-gfm 4.0.1，默认不渲染原始 HTML。Vite 固定到审计修复版本 7.3.6，npm audit 为 0 项。
- 完整安装锁见 `uv.lock` 和 `frontend/package-lock.json`。Playwright 1.55.1 测试脚本已交付，但独立浏览器安装遇磁盘空间不足；没有把该测试标通过。实际交互改用已有 Codex 内置浏览器，详情见 `stage-4-ui-acceptance.md`。

## 逐项符合性矩阵

| 设计要求 | 实现证据 | 必要测试 / 交互证据 | 判定与 gap |
|---|---|---|---|
| 通用节点，不强制公司属性 | research/models.py：Research、ResearchNode、NodeCommand | 两案例共用同一服务和执行端口 | 通过 |
| 展示树、执行依赖、实际输入分离 | display_parent_id / ResearchPlan / NodeResultDependency；projection.py | branch_versions；plan_cycle；页面移动后挂载不变 | 通过 |
| 展示树无环 | 新节点只指向已存在同研究父节点；无重写父关系路径 | branch 创建及跨研究验证 | 通过 |
| 计划无环、先等待再固定版本 | ResearchPlan validator；plans.execute_plan；计划固定存入 Research | plan_waves、partial_plan 不读旧成功结果、plan_identity_budget | 通过 |
| 调用原 C2，无新执行循环 | plans→Dispatcher；app/research_execution→C0/C1 | 原 Dispatcher SHA 不变；计划批次、预算、局部失败 | 通过 |
| 公共服务边界、表各归其主 | ResearchService 调 Scope/Context/资源与 Ledger；repository 六表 | AST 边界检查；SQL 与 memory 同契约 | 通过 |
| 稳定 command_id 幂等 | CommandRecord + 唯一键；同 ID 改内容 Conflict | branches_versions_compare_layout_idempotency | 通过 |
| 不可变结果版本，只复用原 output | NodeResultVersion.run_id；ResearchAnswer.report/missing 在原 runs | 旧版本回看、重复命令不增版本、SDK 实际输出契约 | 通过 |
| 结果、依赖、指针、原运行终态、命令原子发布 | Ledger.commit_in 公共事务边界 + app/commits.ResearchLedger | 提交中断回滚、COMMIT 响应丢失、三个真实 SIGKILL | 通过 |
| revision / expected run / 当前权限发布检查 | validate_binding + ScopeExecutionGate + 事务内 revision/run 检查 | late_result、scope_switch、save_retry、SIGKILL | 通过 |
| 同节点串行，独立分支可并行 | 节点 asyncio.Lock；计划与手工命令共用锁；原 Dispatcher | retry_fixed_input_and_queue；C2 屏障并发回归 | 通过；单进程部署 |
| 选择不执行、不扩权 | select 仅更新 selection/revision | 页面排除再选择仍 v1；挂载面板显式保存 | 通过 |
| 排除不删除历史，停止新派发、取消在途、阻断迟到 | _mutate、计划 Admission、validate_commit | swallowed_cancel；混合摘要拒绝；页面历史仍可看 | 通过 |
| 重试新 run，失败输入固定且重验权限 | _prepare retry 读原 NodeRun/ContextSnapshot | retry_fixed_input；撤权后 retry 拒绝 | 通过 |
| 续研新目标、新 run、新版本 | continue 在锁内追加目标，明确选输入 | queue；两案例 v2、v1 可回看 | 通过 |
| 分支只读父固定版本，无兄弟自动流入 | ResultMount / builder results；check_result | A/B 唯一标记输入与回答隔离；SDK read_source 无 B | 通过；真实模型分支报告人工复查通过 |
| 多版本比较必须明确输入 | command.result_ids；compare/adopt；不可变依赖表 | 输入 2/3 版本可查询，页面明确选择后比较 | 通过 |
| 完整/部分与缺失项保留 | ResearchAnswer 强类型；输入缺失 + 解析缺失 + 模型缺失合并 | partial_plan、model_missing_is_partial_and_propagates | 通过 |
| 上游变化只提示，不抹掉结果或自动重算 | projection 根据实际依赖查询；忽略同节点续研自身旧版本提示 | 父 v2 不改 A/B 输入；公司重汇总后提示清除；页面验证 | 通过 |
| 当前权限优先于旧结果与保存重放 | check_result 递归来源校验；ResearchExecutor 保存/重放门禁 | resource_revocation；saved replay exclusion；C3 原套件 | 通过 |
| 资料上传、解析状态、挂载选择、最低交互页面 | app/api.py；frontend；ScopeService/ResourceService | HTTP 产品测试；内置浏览器实际上传 ready→勾选→执行 | 通过 |
| 每次执行可查目标、输入、结果、停止原因 | NodeProjection；GET run；CLI show/result；saving 重试按钮 | HTTP、CLI 重开、过程故障测试 | 通过 |
| 公司同期间/口径校验、两个维度、比较表/报告 | aggregate.py 读取资料正文中的结构化财务记录；研究 Agent 契约；10 节点计划 | 三公司×两维度→公司汇总→比较；period mismatch 模型调用前拒绝 | 通过；真实比较表数值与口径人工复查通过 |
| 公司续研、重新汇总、排除后干净输入 | scenarios.companies | A v2、比较 v2、排除 C 后 mixed 拒绝；C_SECRET 不入受限输入 | 通过；真实版本与受限输入验证通过 |
| 关闭研究/Scope 不影响基础 Agent | composition 前置检查；独立原入口 | disabling_research；C0/C1/C2 完整回归 | 通过 |
| 全部前序回归 | 原 C0–C3 测试原样保留 | stage-4-tests.txt，真实 PG/OCR/HTTP/SDK/进程 | 通过 |
| 本阶段真实模型小样本 | evals/stage4_research/run.py，默认 openai | stage-4-real-eval.json：passed，退出 0；semantic_review passed | **通过** |
| 不进入 Stage 5 | 无 Evidence/Claim/CitationValidator 或点击结论验证承诺 | 源码目录与页面只展示来源/输入版本 | 通过 |

## 最小 gap 修复记录

1. 原 C1 commit 自行打开事务，无法拼接研究发布。抽出 `Ledger.commit_in(tx, commit)` 公共边界；应用 ResearchLedger 在一个事务调用原仓库和研究仓库。没有在模型 await 内持有事务。
2. 最初只用 current_result_id 绑定计划前置输入，会在前置新执行失败时误用旧成功结果。改为本计划 command.run_id 对应的不可变 result，失败显式 missing。
3. 跨批次预算最初可重置，且计划 ID 缺少整体内容记录。固定 ResearchPlan 到 research 记录；同 ID 修改拒绝、原计划重发只查询。共享 max_runs、token 观测阈值和 deadline。
4. 计划与手工续研最初可能分别抢同节点。统一节点锁，准备在真正开始时完成；未派发项在取消/预算停止后清除 active 指针并留停止原因。
5. 资料库撤权不仅影响库 scope，还影响派生节点。增加 ScopeService.track 公共生命周期入口供应用登记依赖；授权 check/select/grant/revoke 算法未改变。发布和读取继续通过当前权限递归校验。
6. 续研 v2 引用自身 v1 时，初版更新提示会误报自己更新，导致新比较仍提示过期。投影忽略同节点版本沿革的自身指针差异，保留真实上游变化提示；重汇总后清除提示的断言通过。
7. 仅有输入缺失不能表达模型在研究中发现的缺失。最终研究 Agent 使用 `ResearchAnswer(report, missing)`，由原 Core/SDK 输出契约校验；与解析/前置缺失合并发布，部分结果挂载继续携带 missing。
8. API 异步失败不能只停留在“saving”。PublicationError 的 save_failed/confirming 留在命令原因；UI 可只重试保存。保存结果重放仍检查研究排除与当前资源权限。
9. 初装 Vite 7.1.7 审计报告高风险问题，更新到固定修复版本 7.3.6。独立 Chromium 下载因 ENOSPC 失败，删除的仅为本任务未完成浏览器下载，改用已存在内置浏览器；不清理用户文件。
10. 开发中间产物契约与最终结构不同，保留迭代库 stage4，最终演示使用新库 stage4_demo；没有迁移或旧产物 fallback。
11. 补跑真实模型时，无资料的创意分支调用了原本仍暴露的 read_source，权限门禁正确拒绝，但使分支失败（run e1adbd80-f33f-4e14-8241-6d30ed79c5fb）。研究应用现在仅在 scope 有已选/继承资料时暴露该工具，并明确 results.output 已是可读正文、result_id 不能当资料 ref。新增空资料父节点与只挂结果分支测试；原有真实 SDK 资料读取契约测试继续通过。未更改 Core 或放宽权限。
12. 第二轮头脑风暴通过，公司计划有两次 SDK model_behavior（其中青松现金流 run a5184d5d-ca90-49a4-968a-7fb2e4316066）；失败未发布版本。研究指令补充既有 report/missing JSON 输出格式与简洁报告要求；没有解析修补、协议回退或放宽输出校验。
13. 第三轮自动断言 passed，但人工语义审查不通过：线上方向未主动创作，把目标误当缺失事实；公司报告将 66.7% 错称最高比例（另一行 91.7%）。原报告保留为 stage-4-real-attempt-3.json，其 passed 仅表示当时自动断言通过，不能当最终语义结论。修正指令：事实受来源限制，创意需主动生成且标为假设，分支标签不要求外部定义；绝对金额与比例分开，排序核对全部行。

## 证据与部署边界

- `stage-4-tests.txt`：最终完整测试原始输出。`test_research_process.py` 杀死测试自己的子进程：running/before 都无结果版本且重开 interrupted；after 保留一个版本与指针。
- `stage-4-scenarios.json`：两案例全部 run、context 关联、事件、版本、原始输出以及两个独立 CLI 查询。状态明确为 infrastructure_passed，脚本财务表不是模型分析。
- `stage-4-real-eval.json`：真实调用 passed，人工 semantic_review passed。18 次运行全部 succeeded、27 次 SDK 请求、65,830 输入 token / 33,321 输出 token（仅最终一轮）；6 个公司维度均调用 read_source；2 次独立 CLI 查询退出 0。非敏感配置保持 `https://api.deepseek.com`、`deepseek-flash`、SDK 默认 Responses。没有协议回退、历史密钥复制或登录令牌复用。
- 公司程序校验支持材料正文中的明确结构化财务记录，示例为 UTF-8 JSON 文本（record_type=company_financials）。不会从文件名猜公司或报告期。普通 PDF/扫描件使用 C3 解析，期间/口径语义由 Agent 核对并应披露缺失；未声称任意财报自动结构化或 Stage 5 证据验证已通过。
- 单主机、同 OS 用户、单执行进程。同步解析和短数据库操作；没有后台恢复、分布式调度或多租户认证。页面服务只监听 127.0.0.1，跨 Origin 写操作拒绝。
- 只读结果引用保存真实来源闭包。排除不删除历史，但被排除节点的结果不能作为新输入；来源撤权会阻断历史正文读取。普通父子布局边不传播权限。
- 本阶段真实模型门槛已通过。头脑风暴 research ac2c4384-02b6-43ed-983c-17f533eb9d1e；公司 research 3a66e409-a97a-4091-ba91-5b365b5c5e76。报告人工检查记录在 semantic_review，易读正文见 stage-4-real-brainstorm-report.md / stage-4-real-company-report.md。不推进 Stage 5。

收尾：最终累积 184 passed in 30.18s；生产构建、compileall、uv sync --frozen --offline 均退出 0。最终演示服务监听 127.0.0.1:8744，PostgreSQL 保持运行以便用户复查。测试库 stage4 与演示库 stage4_demo、前序库、资料均保留。停止演示服务后才能启动另一执行进程或运行验收。
