# Stage 7 反馈、评测与路由证据验收记录

日期：2026-10-02。范围严格按 `architecture-analysis/当前产品-增量开发与技术选型.md` 第 10 节和 `architecture-analysis/产品设计-当前版本.md` 第 8、10 节；未进入 Stage 8 方法资产与发布。

路由方案已在本记录之后改为 [RouteLLM](routing-routellm.md)。下列 302 项回归和原始证据是改造前的历史基线；改造后的结果另见文末“RouteLLM 变更”。

## 当前结论

Stage 7 的本地产品链路和离线评测约束已实现：页面标记具体结果 → 绑定任务单/运行/方法/模型/路由及资料版本 → 独立核验 → 冻结开发或留出案例 → 固定任务拆分和候选池的成对比较。程序与浏览器控制验收通过。**真实五策略路由收益、真实总成本门槛和用户语义签收仍待外部条件，不能把控制例称为真实质量验收通过。** 线上路由没有读取历史评测记录。

独立验收库为 `127.0.0.1:55445/stage7_release`，集群和 blob 在 `/private/tmp/stage7-pg-20261002`。未修改 Stage 5 的 55443 和 Stage 6 的 55444 数据；开工时两库未响应，未尝试在其上写入。Stage 7 数据与 blob 应成对保留。

## 复用与设计取舍

| 来源 | 检查与采用 | 边界 |
|---|---|---|
| 现有 R1/R2、Stage 3/5/6 | 复用固定 `TaskSpec`、`TaskRun`、`TaskReceipt`、`RouteRecord`、`ContextSnapshot`、`ResourceRef`、用量、权限与 PostgreSQL 事务；不复制另一套任务执行器 | 评测记录独立于业务提交；反馈不会自动改答案或路由 |
| Langfuse `af8f4f192355a53374ee9d9db84770d08e23098f` 的 Dataset/Item/Run/Score 数据形态 | 借鉴固定案例、运行和评分分离的组织方式；只读本地摘选 `architecture-analysis/sources/langfuse__langfuse/code/packages/shared/prisma/schema.prisma` | 本阶段不需要协作标注或可视化追踪；未引入 Langfuse 依赖或复制代码。上游仓库元信息许可证为 `NOASSERTION`，不把摘选作为可运行组件 |
| 已锁定的 SQLAlchemy 2.0.43、Pydantic、Starlette、React/Playwright | 用已有事务、JSONB、严格模型验证、同源 API 和浏览器测试 | 没有新增运行时包；不建导出失败影响研究提交的追踪通道 |

`pyproject.toml` 新增 `agent-evals` 命令，`uv.lock` 已离线更新并用 `uv run --frozen agent-evals --help` 验证。没有旧协议兼容层、迁移或在线历史学习路径。

## 设计符合性矩阵

| ID / 设计要求 | 实现与接口 | 证据 | 状态 |
|---|---|---|---|
| S7-01 具体单位错误绑定正确结果、任务/运行/方法/模型/路由版本 | `evaluation/service.py` 的 `submit`，`Feedback`，`POST /api/feedback`，页面“标记纠错或偏好” | `tests/test_evaluation.py`，`stage7-browser-feedback.json`，刷新后再查 | 控制通过 |
| S7-02 幂等且同 ID 改内容拒绝，错误答案不自动当标准答案 | `submit` 比对固定身份；`adjudicate` 必须有核验结论/审查者；冻结只接受 verified 事实评估 | 测试重复提交、改结果冲突、pending 冻结冲突、矛盾标签冲突 | 通过 |
| S7-03 用户偏好与事实错误分离，正例须明确核验 | `Feedback.kind` 为 `fact_error`、`verified_example`、`preference`；偏好不能冻结成事实案例 | `test_preference_unverified_and_permission`、正负标签测试 | 通过 |
| S7-04 归因到来源/解析/任务/交接/方法/模型/路由/检查器/最终交付 | `adjudicate` 固定 attribution、finding、acceptance_number；检查器归因要求程序检查与核验标签相反 | 控制例记录第 4 项单位“存在”误判；`stage7-control-case.json` | 控制通过；真实语义需人工核对 |
| S7-05 冻结案例、资料权限、开发/留出隔离 | `FrozenCase` 固定输入/输出 SHA-256、运行身份和标签；读取及比较重验当前授权；同任务、同来源版本或相同 blob 内容不能跨 split | 撤权、重复上传同文件、同任务跨 split、跨进程读取测试 | 通过 |
| S7-06 无标签数据不能冒充已验证；检查器误判不能固化为真值 | 仅 verified 事实错误或 verified 正例可冻结，试验评分需审查者和证据；相同结果不允许相反冻结标签 | pending、preference、矛盾标签与 checker 控制测试 | 通过 |
| S7-07 分别测试专家选择、模型选择、交接和最终交付 | `Experiment.dimension`、同任务目标/输入/验收项配对；测试分别构造专家、模型、交接和最终交付案例 | `test_expert_selection_keeps_model_fixed`、`test_five_arm_route_evidence_is_paired_and_history_is_separate`、`test_handoff_and_final_delivery_have_distinct_cases` | 脚本控制通过；真实收益待验 |
| S7-08 五策略、固定候选池/任务拆分、历史证据仅来自开发集 | `Experiment` 冻结五 arm、候选池、任务拆分摘要及门槛；`record_trial` 重验路由候选池、另一选择阶段不变、Jev 决策来源；历史组必须覆盖候选且把聚合摘要放入路由输入，纯历史组按核验正确率选 | 两项五 arm 测试，历史泄漏/无上下文/候选变更冲突 | 离线控制通过；真实 Jev/多模型实验待验 |
| S7-09 改进前后质量、总成本和耗时，缺成本不能宣称通过 | `compare` 仅对完整配对计算质量/费用/耗时；费用 unknown、estimated、measured 分开；measured 必须给账单依据 | `stage7-control-case.json` 的 0→1 质量变化，费用标 estimated，结论 `provisional_estimated_cost`；成本缺失测试 | 计算与门槛通过；真实总账单待验 |
| S7-10 历史证据无额外收益不接入在线路由 | `compare` 返回两项 history gain，`online_history_enabled=false`；业务 `Router` 未读取评测库 | 五 arm 测试；代码边界核对 | 通过 |
| S7-11 页面可提交纠错且刷新后仍可见 | `FeedbackForm`，`GET /api/feedbacks` 按现有权限过滤 | Chromium 实际提交、重开、截图与 JSON | 通过 |
| S7-12 前序阶段连通回归 | 研究、任务、路由、证据、DBOS 恢复及新评测全量测试 | `stage7-regression.txt`：302 passed；`stage7-frontend-build.txt`：构建成功 | 通过 |

## Gap 与修复记录

1. 第一次全量回归的 Stage 6 worker 无法启动。原因是服务入口把局部未定义的 `evaluation` 传入 `create_app`；改为已装配的 `task_service.evaluation`，真实杀进程恢复用例单独通过，最终全量回归 302 通过。[首次失败摘录](stage7-initial-regression-failure.md)与 [最终原始输出](stage7-regression.txt)均保留。
2. 初版五组试验只记录策略名，不能证明历史证据进入路由输入。现在聚合已核验开发案例、要求每个候选有标签、历史上下文与 `RouteRecord.summary` 一致，纯历史组按核验正确率作出实际选择；缺历史或留出集泄漏拒绝。
3. 初版成本字段可被直接标成 measured。现在必须提供独立 `cost_evidence`，未知费用不出通过结论，估算费用只得临时结论。
4. 初版 split 检查只比较 `ResourceRef`，重复上传相同文件可进入另一 split。现在额外比较 blob SHA-256，测试覆盖重复文件。

## 实际证据与限制

- [浏览器记录](stage7-browser-feedback.json) 和 [页面截图](stage7-browser-feedback.png)：实际 Chromium 在 Stage 7 PostgreSQL 上提交单位反馈并刷新查询。页面使用脚本专家生成的故意错误回答。
- [固定控制例](stage7-control-case.json)：原文 `thousand USD`、原输出 `million USD`、程序第 4 项仅检查非空而误通过；程序控制核验后冻结案例，后续脚本结果改成 `thousand USD`。这是检查器假阳性控制，不是用户真人签收，也不是经发布的新专家方法。
- [独立进程 CLI](stage7-cli-compare.json)：从新进程查询同一试验。其质量 0→1、费用为估算 0，结论 `provisional_estimated_cost`，不宣称真实模型节约费用。
- 五组测试的 Jev 来源是构造的路由记录，专家和模型产物由脚本控制。当前进程检查 `TYPESAFE_API_KEY`、`OPENAI_API_KEY` 均未设置，项目中也没有可用的冻结路由目录；现无获准 Jev 凭据、两执行模型及价格目录与真实账单，不能判断固定模型、规则、Jev、历史、Jev＋历史的真实质量/成本收益。真实五组验收需要同一任务拆分和候选池，收集全部调用、失败、重试、复核和端到端费用后再测。控制例不能替代该门槛。
- Stage 5/6 的公司财报语义仍待用户真人签收；Stage 7 未修改历史回执或把模型自检当真人核验。

## 复现

```sh
UV_CACHE_DIR=/private/tmp/uv-stage7-cache uv sync --frozen
npm run build --prefix frontend
pg_ctl -D /private/tmp/stage7-pg-20261002 -o '-h 127.0.0.1 -p 55445 -k /private/tmp' -l /private/tmp/stage7-pg-20261002.log start
export DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55445/stage7_release'
uv run --frozen agent-research --blobs /private/tmp/stage7-pg-20261002/blobs serve --durable --port 8744
```

网页在专家任务活动中提交纠错；高级核验与冻结用 `agent-evals adjudicate command.json`、`agent-evals freeze command.json`；`experiment` 和 `trial` 也接受单个 JSON 命令文件，`compare EXPERIMENT_ID` 输出完整度、各 arm 指标、历史增益及结论。CLI 与服务使用同一 `DATABASE_URL`/`--blobs`。关闭服务后运行 `TEST_DATABASE_URL="$DATABASE_URL" .venv/bin/python -m pytest -q`；本机执行锁不允许服务和测试同时运行。

冻结事实案例与原资料仍受当前权限约束。数据库及 blob 须一并备份；案例不能脱离原资料被无条件导出。正式路由实验应先在开发集冻结质量、成本、阻断率门槛，再运行留出集；本轮脚本门槛仅用于控制测试。

## RouteLLM 变更

Jev 运行时代码和 Choice 协议已删除。五组试验现在仅针对模型选择：`fixed`、`rule`、`routellm`、`history`、`routellm_history`；RouteLLM 组要求模型阶段真实使用 `routellm` 来源。专家选择使用任务类型默认方法或用户指定方法，仍可用前后配对案例单独评估，但不再把 RouteLLM 组用于专家选择。此前 MF 的官方 Python API 已用固定权重和离线嵌入夹具调用；现改用本地 BERT，实际模型选择与已授权双执行模型的总账单需重新验收。具体配置和限制见 [路由说明](routing-routellm.md)。

改造后在同一独立 PostgreSQL 测试库运行 `.venv/bin/python -m pytest -q`：**303 passed in 86.82s**。`python -m compileall -q src tests/test_routing.py tests/test_evaluation.py` 退出 0。这些结果验证代码回归，不改变上文真实质量与真人签收的限制。
