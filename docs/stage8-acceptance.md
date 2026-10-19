# Stage 8 方法与流程版本：实现与验收

日期：2026-10-03。范围为《当前产品-增量开发与技术选型》第 11 节及《产品设计-当前版本》第 5、8、9 节。保留已有成果和 RouteLLM BERT 实现；未实现 Stage 9 记忆、Stage 10 自动候选生成或自动晋级。

## 当前结论

版本资产、人工发布/回退、固定方法执行、受限研究模板、CLI/API 和高级管理页面已接通。方法、路由策略、研究流程分别存储版本及 active 指针。数据库、浏览器和跨进程证据见下文。最终全量回归 **336 passed in 87.29s**，无 skipped；前端构建与新增 CLI 入口通过。

**真实 DeepSeek 固定案例与显式恢复验收已完成。模板 4 步首次成功；手工路径经过固定输入重试及比较重算，最终 8 个节点均发布，独立业务核对通过。实际共 11 次运行（含 3 次恢复、2 次失败），不是一次全成功。** 见 [真实执行与独立核对](stage8-live-review.md)、[程序核对](stage8-live-checks.json) 与 [原始记录](stage8-live.json)。 Stage 7 真实双模型路由收益与历史语义签收的未决项继续保留。

独立验收库：`postgresql+psycopg://chenyouran@127.0.0.1:55446/stage8_release`；最终全量回归另用同集群 `stage8_final_regression`，不改写真实研究；数据库目录 `/private/tmp/stage8-pg-20261003`，可复用验收 blob 目录 `/private/tmp/stage8-pg-20261003/blobs`。前序阶段 55443/55444/55445 数据未改写；本阶段失败记录与旧版本也保留。

## 复用决策

| 来源 | 实际采用 | 边界 |
|---|---|---|
| BearCode 本地提交 `335b293b9f91c9a24b0018bf31a15df79b205f91`，`agents/skill_evolution.py:create_skill_file/evolve_skill_file` | 阅读版本、历史快照、actor/evidence 来源记录，采用独立历史记录的模式 | 原实现会覆盖当前 SKILL.md；本项目改为不可变版本与原子 active 指针。未复制用户购买项目源码，未引入其依赖或自动进化行为 |
| 当前 TaskSpec/TaskRun/TaskReceipt、RouteRecord、Stage 7 Feedback | 直接复用任务版本、方法摘要、模型与输入身份、交付检查和反馈归因 | 没有增加另一份结果正文，也没有改验收项来迎合新方法 |
| 当前 ResearchPlan/PlanStep、ResearchService、Dispatcher | 直接复用 DAG 校验、节点命令、作用域、结果依赖和运行预算 | 模板仅接受文本/资料参数及有向无环步骤，编译成原计划；没有 DSL、脚本执行、第二个调度循环 |
| SQLAlchemy 2.0.43、psycopg 3.2.10、Pydantic、现有 React/Playwright | JSONB 不可变资产、事务、条件更新与 RETURNING、严格模型验证、实际浏览器验收 | 没有新增运行时依赖；不做旧接口兼容或数据库迁移 |
| RouteLLM 0.2.0 与当前 BERT adapter | 保持原路由实现、候选预筛选、容量/预算检查、任务内保持；每次路由使用独立专家版本快照 | 模型策略是独立 routing 资产；不会修改在途任务或共享 Router 的专家集合 |

Co-STORM 本地仅有 README/元数据，没有可直接复用且满足当前限制的模板编译器；未把源码摘选当可运行依赖。本次优先复用了已经通过前序阶段验证的本项目计划与执行代码。

## 设计符合性矩阵

| 要求 | 实现 | 验收证据 |
|---|---|---|
| 指令、通用示例、职责、工具范围成为版本资产 | `methods/models.py`、`methods/defaults.py`、`methods/service.py`；保留原 5 个方法作为初始资产 | `test_immutable_versions_and_compare_swap`；同版本异内容拒绝、未发布版本不得执行 |
| 专家方法、路由策略、业务流程分开版本化 | 三种类型分别校验，按 `(category, asset_id, version)` 保存；路由策略复用 CatalogFile | `test_routing_policy_version_is_separate_and_frozen`；发布路由策略不改方法版本，续接保持原策略 |
| 保存不自动启用，人工发布/回退可追溯 | `save` 与 `publish` 分离，active 条件更新与发布记录在同一事务，记录操作者、依据和前版本 | PostgreSQL/内存测试；浏览器发布、刷新和回退 |
| 在途及排队任务固定旧方法 | 创建 TaskSpec 时绑定已发布版本；执行/DBOS 恢复按准确历史版本解析，不读 active | `test_publish_while_inflight_and_restart_binding`；`stage8-process.json` 新进程实际执行旧/新方法并核对 prompt |
| 回退后新任务采用旧方法 | 切换 active；原版本与任务不删除 | `test_publish_rollback_and_fixed_task_execution`；跨进程及浏览器记录 |
| 缺参、类型错误、字段访问、未知依赖或环不运行 | 精确参数集合，`Formatter` 只允许简单文本占位符；复用 ResearchPlan DAG 校验 | `test_missing_parameters_cycles_and_no_partial_compile`：失败前未创建节点，未调用模型 |
| 模板不带入原公司事实或资料身份 | 模板不含结果、TaskSpec、ResourceRef 字段；调用方显式绑定新资源；不自动复制当前聊天/报告 | `test_compile_idempotent_new_inputs_and_manual_equivalence`：新公司、唯一新材料标记，旧库材料未进入模型输入 |
| 模板/方法权限只取交集，当前撤权仍有效 | 方法工具 ∩ 模板工具 ∩ 当前 scope；执行与提交重验；`search_source` 延续现有“原文读取权限”的授权含义 | `test_workflow_and_method_permission_intersection`、`test_compiled_plan_rechecks_current_tool_permission`；资料撤权阻止实例读取和执行 |
| 模板输出原有任务/依赖计划，不另建运行器 | `instantiate` 输出原 ResearchPlan，实例保存准确模板摘要、方法绑定、参数、节点和依赖 | 浏览器 v8 三公司模板 4 个步骤均产出结果；实际执行仍是 ResearchService → Dispatcher → Core/SDK |
| 预算覆盖完整计划 | 模板显式 max_runs/max_concurrency/timeout；保存和编译前检查次数预算不少于步骤数 | `test_template_rejects_insufficient_budget`；保留首次比较步骤被预算阻止的记录 |
| 同版本实例化稳定、同 ID 改参数拒绝 | 固定参数指纹、不可变实例、幂等节点命令；编译失败可用原请求继续；已执行计划重复请求保持查询语义 | 同 ID 重放/改参、重复执行不增加调用、节点输入被改后拒绝执行 |
| 人工流程与等价模板业务一致 | 两条路径使用相同目标、来源、方法和依赖；控制引擎比较真实业务输入、源文本及上游输出，不比较随机 ID | 内存/PostgreSQL 等价性测试；真实模型最终两路业务结论一致，含显式恢复；首次失败及额外用量保留 |
| 管理视图显示职责、版本、内容对照、评测依据、回退 | 主工作区折叠的 `MethodsPanel.jsx`，版本 JSON 编辑、模板参数表单、发布历史和当前版本对照 | `stage8-browser.json`、`stage8-browser.png`；刷新后发布仍有效，回退成功，JS 错误为 0 |
| 与 Stage 7 反馈及 RouteLLM 连通 | 结果反馈继续读取准确 method_version/method_digest；路由捕获历史候选，发布不改变续接专家 | `test_stage7_feedback_binds_published_method`、`test_routellm_method_publication_keeps_continuation` |
| 前序阶段集成 | 保留 Core、SDK adapter、RouteLLM adapter 和 DBOS 工作原语 | `stage8-regression.txt`：336 passed，涵盖 C0–C7、R1–R3、RouteLLM 与 Stage 8 |

模板的 `sanitized: true` 是操作者对通用内容的明确确认，不是自动语义脱敏证明。编辑器提示不得粘贴原公司事实、历史报告或凭据。程序保证实例化只读取本次参数与获准来源；不会声称能自动识别任意自然语言中全部敏感信息。

发布依据目前由操作者人工录入，可以引用 Stage 7 评测。它不构成自动质量晋级门禁；后者属于 Stage 10。没有自动生成候选、训练模型或扩展工具权限。

## Gap 与修复

1. 最初按所有已发布专家构建路由集合，破坏了原配置的单候选语义。修正为配置允许的专家 ID 内解析已发布方法，RouteLLM 唯一候选免调用行为恢复。
2. 首次 PostgreSQL 测试暴露：插入结果不能依赖当前驱动的 rowcount；frozenset 序列化顺序还会造成版本内容误判。改用条件更新/插入的 RETURNING，集合字段统一排序；初始资产与 active 初始化合并为同一事务。
3. 模板依赖执行正常扩展下游来源，重复运行校验却把它误判为用户修改。已采用原 ResearchPlan 的幂等查询语义；未开始的计划仍检查固定来源/方法与人工修改。
4. 前序 Stage 6 克隆执行测试直接构造 TaskService，遗漏新方法仓库。将依赖设为必需参数并修正测试装配，没有添加旧接口 fallback。
5. 浏览器参数选择框的隐式标签包含 option 文本。添加明确的 aria-label，实际选择新资料通过。
6. 浏览器完整三公司流程暴露默认运行预算为 3，最终第 4 步比较被阻止。保存失败证据；模板 v2 显式预算为 4，新模板不足预算在执行前拒绝，旧 v1 作为历史失败资产保留。
7. 视觉检查发现管理面板挤在窄侧栏，已移入主工作区的折叠进阶视图。
8. 最终审查补上编译后工具权限缩小的重验，防止旧实例或旧任务按历史工具范围发布。
9. 真实调用纠正验收脚本误选 Chat Completions，沿用产品已有 Responses API；未修改 SDK adapter 或增加协议回退。
10. 发布通用原文引用方法，补强元数据、文件名、重复读取与跨行引用的约束；固定案例经显式恢复后通过，不把它当作模型引用可靠性的普遍证明。所有旧方法及失败产物保留。
11. 切换研究时重建方法面板，清除上一研究的模板参数与实例，避免从新研究页面误执行旧实例。浏览器回归检查这一隔离。
12. 内存仓库发布历史返回深拷贝，避免读方改写原审计记录；不可变版本回归同时验证历史隔离。
13. 模板仅能配置计划时限，单步固定使用默认 60 秒。复用原 RunLimits，贯通 TemplateStep → PlanStep → NodeCommand → Dispatcher；新模板每步 180 秒、计划 600 秒。低时限测试仍正确阻断超时，旧版本的默认预算和内容摘要不变。

原始失败证据：`stage8-initial-failures.txt`、`stage8-initial-regression.txt`、`stage8-browser-initial-failure.txt`。这些是调试记录，不是当前结论。

## 实际验证结果

- 全量：`TEST_DATABASE_URL=… .venv/bin/python -m pytest -q`，退出 0，**336 passed in 87.29s**；其中 Stage 8 共 30 个内存/PostgreSQL 用例，未跳过数据库或进程测试。
- `npm run build --prefix frontend`：退出 0；保留既有 Vite bundle 大小提示，没有升级依赖。
- `uv run --frozen agent-methods --help`：退出 0，命令已安装；`uv.lock` 已同步。
- `python -m compileall -q …`：退出 0。
- 两个独立进程：`stage8-process.json`，历史排队任务、新方法实际 prompt、回退未来任务全部核对通过。
- Chromium + PostgreSQL：`stage8-browser.json`，发布刷新持久化、回退、模板 v8 的 4 步执行、切换研究清空旧实例全部通过，0 个页面 JS 错误。浏览器 fixture 服务已停止；资料与历史记录保留。
- DeepSeek：恢复后 8 个节点均发布；绑定、来源及重试输入检查通过，独立业务核对见 `stage8-live-review.md`。共 11 次运行、2 次失败、1 次用量未知。此前十轮与恢复失败全部保留。

## 操作与复现

```sh
UV_CACHE_DIR=/private/tmp/uv-stage8-cache uv sync --frozen
npm run build --prefix frontend
export DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55446/stage8_release'
uv run --frozen agent-research --blobs /private/tmp/stage8-pg-20261003/blobs serve --durable --port 8744
```

页面：创建/打开研究 → 上传新资料 → 展开“方法与流程版本管理”。选择方法可查看版本、来源与内容；首次示例先分别填入、保存并发布原文引用综合方法和分析方法，再填入并发布三公司模板。高级编辑保存新版本后，另填发布依据启用。选择旧版本可回退。高级编辑的“三公司研究模板”只填入通用占位符；当前示例为模板 v8，固定两个 evidence-v7 方法，并明确每步 RunLimits。保存、发布后，为每次实例填行业、三个公司名及各自获准资料，生成固定计划再执行。

CLI：`agent-methods list`；`save VERSION.json`；`publish COMMAND.json`；`compile INSTANCE.json`；`instance ID`；`run ID`。实例命令字段为 `instance_id / asset_id / version / research_id / parameters`；发布命令字段为 `category / asset_id / version / expected / actor / reason`。`expected` 必须是读取时的 active 版本（尚未发布时为 null），过期页面不能覆盖新发布。API 对应 `assets / asset_save / asset_publish / workflow_compile / workflow_instance / workflow_run`。

模板计划使用原研究节点执行路径；专家任务继续使用 TaskService/RouteLLM/DBOS。两者的版本与业务职责分离，模板执行不会暗中增加另一套专家调度器。已有 ResearchPlan 的重复/中断计划是查询语义；要重新计算，明确创建新实例。

```sh
# 先关闭本机业务服务，测试与服务不能同时持有单进程执行锁。
TEST_DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55446/stage8_final_regression' .venv/bin/python -m pytest -q
DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55446/stage8_final_regression' .venv/bin/python scripts/verify_stage8_process.py
DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55446/stage8_final_regression' .venv/bin/python scripts/verify_stage8_browser_server.py
# 另一终端运行浏览器，完成后关闭 fixture 服务：
node frontend/scripts/stage8-browser.mjs
```

真实模型脚本：`scripts/verify_stage8_live.py`，在本机提供获准 `OPENAI_API_KEY` 后运行。使用已有固定年报摘录，分别执行三公司模板与等价手工计划，保存实际输出和全运行用量，供独立核对数字、单位、范围与限制。不会把脚本成功、模型自检或估算费用当成人工语义签收与质量提升证明。

最终 CLI 状态见 `stage8-cli-state.json`。恢复复现脚本 `verify_stage8_response.py`、`verify_stage8_live_recovery.py`、`verify_stage8_comparison_retry.py` 针对保留的第十轮实例执行现有 retry/adopt 命令；不是生产自动重试或第二套运行器。所有模型调用均使用本轮获准凭据，凭据只存在于调用进程，未写入仓库与报告。
