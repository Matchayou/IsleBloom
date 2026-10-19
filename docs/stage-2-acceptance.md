# Stage 2 实现、设计符合性与验收记录

日期：2026-09-19。依据 `architecture-analysis/增量开发架构与阶段验收.md` Stage 2、源码阅读索引和 `docs/development-acceptance-process.md`。本阶段只增加明确策略的路由、有界多执行分派和结果收集。

**实现、设计符合性核对、内存/真实 PostgreSQL、DeepSeek-V4.1-Flash 真实模型与 CLI 验收及 C0/C1/C2 回归全部通过：105 passed in 8.32s，无跳过。Stage 2 已完成；未推进 Stage 3。**

## 复用调查与接口决策

- 读取固定 SDK 提交 `fbf59a40e9da5adb88d370fefaeaae0478376d4a` 的 `examples/financial_research_agent/manager.py`，重点 `_perform_searches()` 和 `_search()`，以及 `agents/planner_agent.py`。采用明确输入项、结构化计划、独立执行与收集的模式；没有复制完整研究流程、联网检索、SDK item 读取、无限 task 列表或 `except Exception: return None`。
- 本阶段路由是用户显式选择的 `compare` 策略：固定成本/速度/限制三专家；普通 `direct` 返回无计划，不调用模型规划。当前无需把确定规则交给模型，未增加 planner Agent 或规划请求。结构化 `DispatchPlan` 使用项目已有 Pydantic。
- 实际检查已安装 `openai-agents==0.22.2` 的公开 `Runner.run`/`Agent`、`pydantic==2.13.5` 的 `model_validate`、`sqlalchemy==2.0.43` 的 `Connection.begin` 和 Python `asyncio.wait` 签名。已运行 `uv sync --frozen --offline` 重建 CLI，未添加第三方包。Context7 技能已读，当前工具清单无对应能力，使用本地固定源码与已安装公开接口核对。
- 复用 C1 `ExecutionService`、`Ledger`、`EventSink` 和 runs/commit 的事务语义。增加公开 `ExecutionService.execute(RunRequest) -> RunResult`，满足 ExecutePort；只有保存确认后才返回标准结果，否则抛 `PublicationError`，不会把预览当成发布结果。`retry_save` 仍只保存、不重新调用模型。
- 独立 run 的 `conversation_id=None`、revision=0、messages=空；运行结果和事件依然进入原有 runs/run_events。会话类型、会话服务、CoreRunner、contracts、SDK adapter、已有工具未修改。数据库当前结构允许 nullable conversation_id；不对 Stage 1 数据迁移，在同一隔离实例新建 `stage2` 数据库，保留原 `postgres` 数据库及文件。
- `dispatch_groups` 是计划/状态记录，包含固定 items、输入、策略、子 run IDs、停止原因；不复制运行结果、不建立另一套运行表。通过 RunRepository 的 create/get/finish_dispatch 和 Ledger 事务门面操作。编排模块不访问数据库连接、表、SDK messages 或会话。

## 设计符合性矩阵

| 要求 | 实现证据 | 测试/交互证据 | 判定 |
|---|---|---|---|
| 明确路由策略、RouteDecision | orchestration/router.py；direct/compare | three_experts、普通 C1 回归的模型调用计数 | 通过 |
| DispatchPlan/Item/Policy，固定输入绑定 | orchestration/models.py；唯一 key/run ID，固定元组 | 重复 ID/非法策略拒绝；保存并读回计划 | 通过 |
| 最小三专家端到端 | composition.build_orchestration、agent-dispatch compare | stage-2-memory-demo.txt、stage-2-cli-restart.txt | 通过；真实模型见 stage-2-real-eval.json |
| 并发与有界 task 数 | dispatcher 仅为可用名额创建 task | barrier_concurrency_cap：屏障证明 0/1 同时活动，2 等待 | 通过 |
| 乱序收集、原维度稳定对应 | collect 遍历原计划，按 run ID 查记录 | 屏障控制 1/2 先完成，输出仍 0/1/2/3 | 通过 |
| 独立输入、父子 ID | 子请求 depth=1、parent_run_id=group ID | 内存/SQL 均核对请求、事件、输入；SDK 三个独立输入 | 通过 |
| 局部失败不得被掩盖 | 每项保留状态、标准错误、missing_reason；无模型总结覆盖 | partial_failure；真实 CLI 注入 speed 故障，另两项完成 | 通过；真实模型混合故障场景已验证 |
| 完成策略 | all_settled 默认；fail_fast 取消兄弟并停止排队项 | stop_drains_children：失败、取消、超时 | 通过 |
| 取消传播和清理 | 取消活动 task，await drain，保存子 cancelled 后结束组 | 清理事件/持久终态断言，无活动遗留 | 通过 |
| 递归限制 | 组 depth 只能 0；专家 depth=1 | 委派 depth=2 返回 depth_limit；组递归值拒绝 | 通过 |
| 预算停止 | max_runs、累计观测 token 阈值、期限 | zero_budget、budget_stops_admission、已启动子任务完成 | 通过；token 不是硬费用上限 |
| C1 保存成功语义 | 独立 execute + 原 commit；collector 只读保存结果 | COMMIT 前失败不可发布；COMMIT 后响应丢失可确认；只重试保存 | 通过 |
| 不能把裸 Core 成功当成已保存 | dispatcher 核对 repository result/commit_id | plain_core_success_cannot_be_published | 通过 |
| 幂等、不恢复计算 | 相同 plan/group 只查询；改 plan 冲突；run 同请求只查询/重试保存 | 重放计数不增加；独立 execute 身份检查 | 通过 |
| 计划与记录查询 | agent-dispatch show 输出 group、runs、events、ResultSet | 新进程关闭/重开正常与局部失败结果精确相等 | 通过 |
| 生命周期 | 单进程锁不变；重开旧 running group/runs 为 interrupted | stage2_crash_worker SIGKILL 后 3 个子与组全为 interrupted | 通过 |
| 单项与直接 execute 等价 | 同一个公开 execute 入口 | single_item_equivalent（除 run ID 外结果相同） | 通过 |
| C0/C1→C2 连接与完整回归 | composition；SDK 公共 adapter→Core→C1→Dispatcher | 原 64 项全部通过，新增 41 项通过 | 通过 |
| 关闭编排后串行 delegate 正常 | 原 agent-core / agent-records 入口 | stage-2-c0-demo.txt；C1 计算→委派→追问回归 | 通过 |
| 模块边界 | orchestration 仅使用公共入口与值对象 | AST import/属性访问检查，原 Core/adapter 测试 | 通过 |
| 真实模型三专家、失败混合、关闭后查询 | evals/stage2_orchestration/run.py | stage-2-real-eval.json：passed，退出 0；两次新进程查询一致 | 通过 |
| 不推进 Stage 3+ | 无资料、研究树、证据、记忆、进化或自动恢复模块 | 本阶段新增文件范围与边界核对 | 通过 |

## Gap 修复记录

1. C1 原入口只接受会话文本，会自动拼历史并提交消息，不能拿来给并行专家共用会话。增加独立 ExecutePort 入口和 nullable conversation_id，独立运行仍经过同一提交实现；未造临时会话或第二套 ledger。C0/C1 原 64 项回归仍通过。
2. 审查中发现“达到运行次数预算”不应取消已获准的任务。调度器改为停止新增，等待已启动任务完成；达到 token 阈值、失败快速停止、主动取消及期限到达会取消活动任务。新增两项预算测试确认未启动项明确缺失。
3. 保存失败后的预览不能进入 ResultSet。以真实事务故障注入验证并使用 repository 回查；COMMIT 前失败显示 confirming/缺失，当前进程 retry_save 后才能 completed。修复重试成功后仍残留 missing_reason 的查询问题。
4. 分派查询初版只有结果，没有足够定位信息。show 补全固定计划、策略、输入、run record 和事件，实际 CLI 关闭后查询验证。
5. 在每轮新增前检查组 deadline，避免期限已到后继续获得任务名额。运行中超时仍依赖协作取消，测试通过清理完成事件验证。
6. 明确跨进程不恢复计算：启动时将未完成 dispatch_groups 标 interrupted，复用 C1 对 runs 的中断标记；真实 SIGKILL 后新进程查询验证，没有模型重试或后台恢复。

## 实际执行与证据

- 先跑内存端到端与原回归：45 passed / 19 skipped（数据库尚未接入，未算作 SQL 通过）。新增内存场景后 16 passed / 16 skipped。
- 启动已有 PostgreSQL 17.11 测试实例；首次沙箱共享内存权限失败，经执行审批启动成功。创建独立数据库 `stage2`，未删除或迁移 Stage 1 数据。
- 最终命令：`TEST_DATABASE_URL=postgresql+psycopg://stage1@127.0.0.1:55439/stage2 .venv/bin/pytest -v`。退出 0，**105 passed in 8.32s，无跳过**，原始输出 `stage-2-tests.txt`。
- `uv sync --frozen --offline` 退出 0，固定依赖不变，新增 `agent-dispatch` 入口可执行。
- `agent-dispatch --storage memory --engine scripted compare '请三个专家分别比较方案的成本、速度与限制'` 退出 0，见 `stage-2-memory-demo.txt`。
- PostgreSQL CLI：compare→进程退出→show，退出分别 0/0；speed 故障注入→进程退出→show，退出分别 1/1（预期局部失败）。全文含命令、退出码、run ID 和记录见 `stage-2-cli-restart.txt`。
- 原 C0 CLI 演示退出 0，串行 multiply→delegate→追问仍可用，见 `stage-2-c0-demo.txt`。
- 首轮因缺少 OPENAI_API_KEY，真实评测退出 2、明确 not_run。用户随后授权本轮凭据，现已补齐：使用同一评测脚本（通过 runpy 调用），实际退出 0，`stage-2-real-eval.json` 为 passed。没有复用 Stage 0/1 历史结果。
- 数据库验收后停止实例，保留 `/tmp/agent-stage1-pg` 和两个数据库。

## 策略与当前限制

- `max_runs` 是每组严格的专家启动次数上限；取消/失败也占用次数。每个 run 的 max_turns/max_calls/timeout 继续由 C0 保证。
- `max_total_tokens` 是完成结果报告的累计使用量停止阈值；观测到达到阈值后停止新增并取消在途任务。用量未知也停止新增。并发在途请求或单次请求可以超过阈值，**不承诺硬 token/费用封顶**；最终用量不完整会标 complete=false。SDK 当前接口没有请求期间的逐 token 预算拦截，本阶段不改模型循环。
- `ResultSet.complete` 表示每个原任务都有已提交成功结果；组 `settled` 只表示调度结束，局部失败/保存不确定必须继续看每项，CLI 返回 1。报告不做隐藏失败的额外总结模型调用。
- 同一事件循环/单主机/单 OS 用户/单执行进程。事务短且同步，不跨模型 await。协作取消不能强制中断阻塞函数或撤销外部副作用。
- 计划最多 100 项；同时创建的 task 不超过 max_concurrency（1–32）。专家递归委派不在本阶段允许范围。
- group ID 是分派父 ID，子 run request.parent_run_id 引用它；group 本身不是虚构的模型 run。没有运行结果副本。
- 新进程只能查询已保存结果和 interrupted 状态；不自动恢复，不在新进程重试丢失的预览。重发新任务需要新 plan/group ID；`show GROUP_ID` 是 CLI 查询方式。应用内持有原计划时重复 dispatch 只查询。

## 真实模型验收补齐结果

用户在本任务提供配置及授权凭据后，实际以 `OPENAI_BASE_URL=https://api.deepseek.com`、`OPENAI_MODEL=deepseek-flash` 执行。沿用已验证的 DeepSeek-V4.1-Flash API 标识与 SDK 默认 Responses 接口，没有使用 Anthropic 接口、协议回退或修改产品代码。

- 正常组 `f4d53db9-16f6-48cf-a307-036744cb37dd`：成本/速度/限制三个独立运行全部 completed。人工检查回答：A 为100元/月、B为200元/月，B月费为A的2倍；A每项10秒、B每项5秒；A可离线、B必须联网。各专家保留原维度，没有编造题目未给出的成本或限制。SDK 报告3次请求、输入390 / 输出1729 tokens，usage.complete=true。
- 故障组 `b776dc85-a1aa-428f-8a58-054e4d71194b`：显式注入 speed 引擎失败（不是让模型假装失败），成本/限制两个专家真实调用并保存，速度 failed，标准错误为 engine_failed，CLI 明确显示“速度…缺失”。没有额外总结覆盖失败。SDK 报告2次请求、输入260 / 输出1287 tokens；故障项令组 usage.complete=false，未误标完整用量。
- 两组各自 compare CLI 完全退出后启动独立 show 进程，保存的 ResultSet 与原结果精确一致；正常 compare/show 退出0/0，故障 compare/show 退出1/1（预期）。整个评测脚本退出0，结果 passed。
- `stage-2-real-eval.json` 保存结构化结果及完整 CLI 输出；`stage-2-real-cli.txt` 单独整理同一次运行的原始 compare/show 交互。人工复核三维内容与缺失展示通过。这是固定假设的小样本验收，不代表广泛模型质量评测。
- 密钥通过无回显终端输入进入已退出的测试进程环境，仅传给评测子进程；未写入脚本、报告、仓库或持久配置，未使用 Codex 登录令牌。
- 补验后全量 C0/C1/C2 再次通过，原始输出见 `stage-2-tests.txt`。没有暴露需修改实现的新 gap；数据库已停止，数据保留。

本阶段所有验收项已通过，无未解决外部验收项。范围止于 Stage 2，不自动进入 Stage 3。
