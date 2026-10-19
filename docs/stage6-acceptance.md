# Stage 6 持久执行与中断恢复验收记录（2026-10-02）

## 结论与边界

Stage 6 的后台控制和持久恢复已实现，并在独立 PostgreSQL 上完成真实 worker `SIGKILL`、重启、原运行查询和剩余步骤续跑。固定的 Tesla、Ford、GM 2025 年报摘录先用于 `scripted` 三专家控制例，随后用 DeepSeek-V4.1-Flash（API ID `deepseek-flash`）完成真实模型恢复验收。第一家成功提交后在第二家调用前杀死 worker，重启后第一家没有重跑，另两家完成；三份专家结果、回执与程序检查均通过。Stage 6 **技术及真实模型恢复验收通过**。三份语义检查仍为 `pending`，Stage 5 关键财务数字与口径也仍待真人语义签收；本阶段不代替该签收。

## 设计依据与选型

- 当前产品设计以[第 9 节 Stage 6](../architecture-analysis/当前产品-增量开发与技术选型.md)为准：固定任务、路由、方法、模型和输入；恢复前后核对权限；业务幂等提交保持唯一真源。旧[阶段规划](../architecture-analysis/增量开发架构与阶段验收.md)曾建议另建 `durable_jobs` 和 `CheckpointCodec`。当前最小链路以稳定 `research:{job_id}` DBOS workflow ID、DBOS input 和既有 `TaskRun`/`ContextSnapshot`/`TaskReceipt` 实现关联，不增第二个业务状态表，也不承诺 SDK token 级暂停，因此没有引入这两个预留抽象。
- 参考项目 `dbos-inc/dbos-transact-py` 的源码摘选固定在提交 `0558a5bc40981fc7d186c8607badab0ae1a52407`，许可 MIT，主要阅读 `dbos/_dbos.py`、`_queue.py`、`_recovery.py`。摘选只作设计依据，运行时使用完整 PyPI 包 `dbos==3.0.0`，由 [pyproject.toml](../pyproject.toml) 和 [uv.lock](../uv.lock) 锁定。采用公开的 `workflow`、`step`、`register_queue_async`、`enqueue_workflow_async`、`get_workflow_status_async`、`cancel_workflow_async`、`SetWorkflowID`；没有复制内部恢复代码，也没有在 Core、ScopeService、证据或研究领域对象里放 DBOS 装饰器。[DBOS Python 指南](https://docs.dbos.dev/python/programming-guide)、[工作流与步骤 API](https://docs.dbos.dev/python/reference/decorators)用于核对已安装 3.0.0 的实际类型和方法。
- DBOS 系统记录在同一 PostgreSQL 的 `dbos` schema；任务、回执、证据仍由现有业务仓库提交。`src/agent_core/durable.py` 只处理顺序、队列、恢复、取消和安全查询，`TaskService.run_durable` 复用已有任务上下文与业务提交。

## 可运行入口

在单独 PostgreSQL 库与持久 blob 目录中执行：

```sh
uv sync --frozen
export DATABASE_URL='postgresql+psycopg://USER@127.0.0.1:55444/stage6_release'
uv run agent-research --storage postgres --engine scripted \
  --blobs /tmp/stage6-pg-20261002/blobs serve --durable --port 8744
```

网页“派发专家任务”在 `--durable` 下进入后台队列；关闭并重新打开页面，任务活动从数据库恢复显示。无 `--durable` 时仍按现有直接执行模式运行，这是 Stage 6 要求的显式两种执行方式。真实模型使用 `--engine openai` 和已授权环境配置。API：`POST /api/durable_submit` 提交 `{ "job_id": "...", "task_ids": ["..."] }`；`GET /api/durable_status?job_id=...` 查询；`POST /api/durable_cancel` 取消。网页单任务的 workflow ID 固定为 `research:{task_id}-v{spec.version}`。worker 重启必须使用同一业务数据库和 blob 目录。

## 设计符合性矩阵

| 要求 | 实现和证据 | 状态 |
|---|---|---|
| 完成一项、杀 worker、重启、查询、只跑剩余项、检查汇总 | DBOS 每专家一组 `prepare_expert`/`execute_expert` 步骤，`summarize_experts` 读取已提交回执及程序检查；三公司固定摘录控制例在三个杀进程位置均为 `SUCCESS` | 控制链路通过 |
| 任务单、路由、方法、模型、输入固定 | workflow input 固定 `TaskSpec` 版本/方法摘要/模型/route；已保存 `TaskRun` 与 `ContextSnapshot` 固定运行 ID 和输入；恢复时逐项比较，不匹配拒绝 | 通过 |
| 现时权限与新增排除 | 恢复前验证分支修订、任务作用域和根资料库；工具前后及提交前沿用 `ScopeExecutionGate`、`TaskLedger`；查询已提交任务也再次检查权限 | 撤权与替换测试通过 |
| 幂等、重复投递与业务真源 | 相同 job ID+相同输入只读既有状态；不同输入拒绝。复用原 run ID 和 `TaskLedger.commit`，回执与证据原子提交；提交后崩溃无第二次调用 | 通过 |
| 三个外部调用边界真实崩溃 | `SIGKILL` 退出码 `-9`；[调用前](stage6-fault-before_call.json)、[调用后未提交](stage6-fault-after_call.json)、[提交后](stage6-fault-after_commit.json)保存重启前后原始状态 | 通过 |
| 调用留痕、预算和次数 | `durable_attempts` 在调用前写入 run ID、尝试号、时间、预留 SDK 回合、工具调用和路由美元估算；最多两次。默认 `8` 回合/`8` 工具调用分配为每次 `4`/`4`；真实模型任务明确设为 `16`/`16`，每次最多 `8`/`8`。OpenAI SDK 的 HTTP 内部重试设为 `0`。路由预算不足时拒绝下一次 | 测试通过；真实模型记录 6 次 SDK 请求、20,322 输入 token、6,938 输出 token；未启用路由报价，未核算实际账单 |
| 取消、超时、重试上限 | DBOS 可抢占 async step；只对 Core 标记 `retryable` 的失败重试。真实取消、两次崩溃上限、一次超时后成功与两次超时后停止均通过 | 通过 |
| 旧代码与任务版本拒绝 | `CODE_VERSION=stage6-v1` 与 DBOS application version 固定；查询旧版本检查点明确拒绝。任务单升版后真实重启拒绝旧执行且不发布回执 | 故障注入和重启测试通过 |
| 直接与持久执行等价 | 同一冻结 `TaskSpec`、`TaskRun`、`ContextSnapshot` 克隆执行，`RunResult` 与 `TaskReceipt` 完全相等；仅外层调度记录不同 | 通过 |
| 页面关闭不丢后台任务 | 真 Chromium 页面提交后关闭；worker 随后杀死并重启；新 Chromium 页面查询同一 job 与回执 | 通过 |
| 真实三公司模型恢复 | [原始运行与回执](stage6-real-model-recovery.json)：DeepSeek `/models` 返回 `deepseek-flash`；worker `SIGKILL` 退出 `-9`；杀前回执数 `1/0/0`，重启后 `1/1/1`；尝试数 `1/2/1`，前两家原 run ID 保持；三项 `RunResult=succeeded`、`delivery=pending_review`、程序检查全过 | **通过，语义待真人签收** |

## 实际 gap 与修复

1. 原 `TaskService.run` 每次新建运行、旧进程运行重启后标为 `interrupted`。现在 DBOS 先登记稳定 `TaskRun`，恢复复用其快照和 ID；`ExecutionService` 只在持久路径允许同一请求从 `running`/`interrupted` 继续。已提交回执先查询，避免提交后检查点丢失导致重算。
2. 已存的 Core 事件在重试时序号会冲突。`EventSink.register` 从已存事件数接续；调用后未提交的故障测试验证新尝试可追加事件并最终原子发布。
3. DBOS 3.0.0 的 `register_queue` 不能在运行的 event loop 中同步调用。启动改为 `register_queue_async`；真实库三专家测试通过。
4. 起初预算只计回合；现在同一任务的两个尝试还共同限制工具调用，并在每次模型调用前保守预留路由估算美元成本。奇数上限向下分配，避免恢复时更改已登记请求的 limits；故障前可能实际未调用，但预留仍计入预算。
5. 原网页的专家执行 Promise 绑定页面生命周期。持久模式由 `task_run` 立即返回已入队工作，`task_activity` 从 DBOS 状态读取并提供后台取消；关闭网页不取消 worker。
6. 首轮真实模型测试暴露验收脚本误把 DBOS 工作流 `SUCCESS` 当成专家成功：三项专家回执实际是 `rework`，错误包括 JSON 无效和工具调用超限。[首轮原始记录](stage6-real-model-initial-failure.json)保留此负例。现在验收脚本同时要求每个 `RunResult=succeeded`、`delivery=pending_review` 和所有程序检查通过；单任务创建可明确传入 `RunLimits`，本次真实任务设为 16 回合/16 工具调用、120 秒。
7. 加大预算后模型生成了不精确的形式化引用，业务证据验证拒绝了三项结果。普通搜索任务此前也被提示生成 `claims`；现按 `evidence_required` 分流提示：普通任务在报告中注明来源位置并将 `claims` 留空，证据任务继续使用完整 ref、逐字 quote 和位置校验。本次三份普通任务报告的关键数字由 Codex 对固定摘录独立核对，但不是正式语义签收。

## 实测记录

- 独立 PostgreSQL：`127.0.0.1:55444/stage6_release`，集群 `/private/tmp/stage6-pg-20261002`，blob 使用各测试专用目录；未覆盖 Stage 5 的 `55443/stage5_release` 与其 blob。
- 累计测试：`TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55444/stage6_release .venv/bin/python -m pytest -q`，退出码 `0`、`289 passed`，原始输出见 [stage6-tests.txt](stage6-tests.txt)。并发同 ID 不同输入的最终校验后，再运行 `tests/test_durable.py tests/test_durable_process.py -q`，退出码 `0`、`13 passed`，见 [stage6-final-targeted-tests.txt](stage6-final-targeted-tests.txt)。覆盖此前阶段的内存/真实 PostgreSQL 契约、Stage 6 真实进程和 Chromium 测试。
- 前端：`npm run build --prefix frontend`，退出码 `0`，原始输出见 [stage6-frontend-build.txt](stage6-frontend-build.txt)。既有 Stage 5 Playwright 案例绑定原 Stage 5 库的固定研究 ID，未在 Stage 6 新库重跑；Stage 6 的新 Chromium 关闭/重开测试在本阶段累计 pytest 中执行。
- 三份故障原始 JSON 均显示第一家和第三家各 `1` 次调用尝试、`1` 份回执；第二家在调用前与调用后未提交时为 `2` 次尝试，提交后为 `1` 次。每个案例均保留了杀进程前后 job 状态、原 run ID、时间和预算预留。[Chromium 原始记录](stage6-browser-reopen.json)保存浏览器提交响应、worker `-9` 退出和新浏览器查询结果。
- 真实模型验收由 [verify_stage6_real_model.py](../scripts/verify_stage6_real_model.py) 在非回显终端提示输入 API key；密钥只进入该进程和子 worker 的内存环境，未写入源码、日志或验收 JSON。DeepSeek `/models` 返回 `deepseek-flash`、`deepseek-v4-pro`；按[官方价格/型号页](https://api-docs.deepseek.com/quick_start/pricing/)使用前者。真实模型[原始记录](stage6-real-model-recovery.json)显示上述 `SIGKILL`、恢复、回执、尝试和用量。Tesla、Ford、GM 报告分别给出 2025 合并经营活动现金流 `14,747`、`21,282`、`26,867`，单位均为百万美元，均与固定年报摘录对应；Ford 明确取 `Consolidated` 列。三份语义检查仍待独立人工签收。
- 最终改动后完整回归再次运行：`TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55444/stage6_release .venv/bin/python -m pytest -q`，退出码 `0`、`289 passed in 81.39s`，输出见 [stage6-final-regression.txt](stage6-final-regression.txt)。

恢复承诺止于步骤级。已发送但未提交的模型/API 请求可能再次发送，并已保守计入尝试预算；外部计费和副作用不承诺恰好一次。业务库、DBOS schema 和 blob 目录必须一起保留。未启动 `--durable` 的直接模式不会暗中接管未完成的 DBOS 工作。
