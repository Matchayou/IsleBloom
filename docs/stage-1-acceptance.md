# Stage 1 设计符合性与验收记录

日期：2026-09-19。依据最新 `architecture-analysis/增量开发架构与阶段验收.md` 的 Stage 1 与提交边界，遵循 `docs/development-acceptance-process.md`。

**实际实现、设计符合性核对、64 项确定性/真实 PostgreSQL 测试、CLI/进程故障验证及本阶段 DeepSeek-V4.1-Flash 真实连通验收均通过，Stage 1 完成。没有推进 Stage 2。** 本阶段真实结果独立记录，不以 Stage 0 历史结果代替。

## 固定复用来源与决策

阅读源码索引及固定 SDK 提交 `fbf59a40e9da5adb88d370fefaeaae0478376d4a` 下的 `memory/session.py`、`extensions/memory/sqlalchemy_session.py`。继续使用完整 `openai-agents==0.22.2`，参考摘选不加入导入路径。

SDK Session 的 get/add 分工、独立消息表及事务内追加模式可复用；其 Session 每次追加自行提交、保存 SDK item，没有本产品的 revision、run/commit 幂等及运行终态原子性。故不直接套用 Session，也不复制其私有 `_await_mutation`、SQLite 分支、兼容分支或自动重试。SDK adapter 和模型循环完全保留。

采用同一成熟库 SQLAlchemy 的公开 Core API：MetaData/Table、Connection.begin/execute、PostgreSQL ON CONFLICT、行锁、JSONB。显式固定 `sqlalchemy==2.0.43`、`psycopg[binary]==3.2.10`，完整传递版本进入 uv.lock。核对已安装实现与签名，以及 [SQLAlchemy 事务文档](https://docs.sqlalchemy.org/en/20/core/connections.html)、[PostgreSQL 17 锁文档](https://www.postgresql.org/docs/17/explicit-locking.html)。Context7 技能已读，但无 callable 工具，以上为实际核对来源。

C0 事件观察接口是同步的，因此使用同步短事务，不为 C1 改 Core 回调或另造异步队列。连接/语句/锁等待有期限；事务不跨 await，不跨模型与工具。阶段内单主机同 OS 用户单执行进程，用本机 flock 证明旧应用所有者已经释放生命周期；数据库 advisory lock 辅助防止同时持有执行连接。只靠 advisory lock 断开无法证明旧应用已退出，因此连接失效时本机锁仍保留，禁止自动重连及接管。

## 逐项设计符合性

| 设计要求 | 当前实现证据 | 测试/实际交互证据 | 状态 / gap |
|---|---|---|---|
| 独立应用执行层，包住 ExecutePort；不侵入 Core | execution/service.py、ledger.py、ports.py；composition.build_execution | 原 30 项 C0；core/contracts/openai SHA 校验均不变；依赖方向测试 | 通过 |
| PostgreSQL 分属仓库，事务由应用协调 | postgres/conversations.py 与 runs.py；Ledger.commit 通过 tx 公共接口 | memory/postgres 同一契约；原子故障断言 | 通过 |
| 成功会话增量、结果、运行终态原子保存 | 单 Connection.begin；conversations/messages/runs；终态非空约束 | 提交前执行全部写入后异常、SIGKILL；revision=0、无结果、无回答 | 通过 |
| commit_id 稳定、按内容幂等 | run:<run_id>；完整规范应用 Commit 存入 runs.commit_payload；重复比较后返回 | 同 ID 同内容不重复；变内容/换 commit ID 拒绝 | 通过 |
| expected_revision 与 run_id 幂等 | 会话行锁、revision；消息按 run 检查；run 唯一键与未完成会话唯一索引 | stale revision、同 ID 改请求、重复请求不增加模型调用/消息 | 通过 |
| 模型/外部工具不在数据库事务内 | send 先登记后调用 ExecutePort，结果回来再提交 | CheckedModel 每次调用断言 connection.in_transaction() 为 false | 通过 |
| Core succeeded 不等于持久完成 | ExecutionOutcome 区分 result 与 preview；状态通知在 commit 后 | 正在生成→正在保存→已完成；前提交失败没有已完成 | 通过 |
| 明确保存失败，仅重试保存 | pending Commit 保留同 ID；retry_save；不执行模型 | before 故障后 save_failed，模型仅调用一次，retry 后 1 个回答 | 通过 |
| 超时/响应丢失先确认，不重跑模型 | 按 commit_id 查询；查询不可达返回 confirming | after 故障返回已有记录；offline-after-commit 确认后不重执行 | 通过 |
| 事件可查询、顺序与父子关联保留 | EventSink；StoredRunEvent(root,sequence,time,event)；run_events | 与 C0 事件逐项相等；缺口/改序号内容冲突；tool/delegate/child IDs | 通过 |
| 事件存储故障不可吞成成功 | 同步观察不抛入 Core；失败尾部保留内存；提交前 flush | 全部事件暂失败时不发布；恢复后 10 个事件按序补齐，再完成 | 通过 |
| 模型/工具失败保存错误、不追加回答 | 非成功 Commit messages 为空 | unknown_tool、cancelled 持久化，revision 仍为 0 | 通过 |
| 用户界面故障不撤销完成 | observe/notify 与提交分离 | UI 回调抛错，结果仍可按 run_id 查询 | 通过 |
| 单进程实例及中断判定 | execution_instances UUID；flock + advisory lock；启动更新未终态 | 活着的进程拒绝接管；数据库连接被终止后也拒绝第二实例 | 通过；限同主机同 OS 用户 |
| 进程退出前后边界 | 原子提交 + 启动 interrupted；无计算恢复代码 | 3 种 SIGKILL；before/running 中断，after 成功；重复 send 不执行 | 通过 |
| 关闭重开 CLI 可查会话/结果/工具 | agent-records show；查询不依赖模型密钥 | stage-1-cli-restart.txt；独立 CLI 进程 demo→show；随后追问测试 | 通过 |
| C0 两种仓库，C0→C1 连通 | LedgerConversationStore；build_execution 使用同一 build_runner | C0 ConversationService 两种仓库；Scripted C0/C1；真实 SDK QueueModel C0/C1 | 通过 |
| 显式基础内存入口；DB 失败不降级 | agent-core；agent-records --storage memory/postgres 必选 | 两个内存 demo；断开真实 backend 后 SaveUnavailable | 通过 |
| 当前阶段真实模型小样本 | evals/stage1_persistence/run.py 已实现 | 真实评测退出 0；独立 CLI demo→show→send→show 全部退出 0；重启追问正确 | 通过 |
| 不提前进入 Stage 2+ | 新增仅 execution、repository、CLI、测试和评测 | Core 依赖方向；无并发路由/资料/树/证据/恢复/记忆模块 | 通过 |

根 RunRecord 是一次应用提交的单位，子 Core 运行通过标准事件的 parent_run_id / child_run_id 查询；未承诺独立发布子结果或存储原始 SDK 协议。模型选工具和工具返回计算语义沿用 C0，Stage 1 不改提示词、工具定义或委派方式。

## Gap 修复记录

1. 原 Session 独立 commit 无法保证“结果+终态+历史”原子性。最小修复为应用 Ledger 共享事务，仓库分别拥有表；没有强行扩展 SDK Session。
2. 仅存 Core completed 会造成保存失败仍显示完成。新增应用 Outcome 与 saving/confirming/save_failed；预览置于独立字段；回调异常不影响提交。
3. 仅有数据库 advisory lock 时，连接断开但旧进程可能仍运行。补本机 flock，连接失效禁止重连；真实 pg_terminate_backend 测试证明旧进程仍阻止接管。
4. 首次运行类型注解遇到 Protocol 方法 `list` 与内建类型同名，收集失败。使用 future annotations，保持接口无额外封装。
5. 故障测试初版直接 monkeypatch 内存事务仓库，deepcopy 会把测试闭包带入下一事务，出现 2 个内存测试失败。改成仅测试用代理包裹公开接口，不修改生产实现来迎合测试；内存与 SQL 故障契约现一致通过。
6. 核对发现同步事件持久失败不能直接抛回 Core（会改变模型执行错误）。EventSink 保留失败尾部、提交前强制 flush；补事件故障测试，明确崩溃时未落盘尾部可能丢失。
7. 补齐 C0 独立 ConversationService 的仓库桥接与 SDK QueueModel→C1 事务连通，验证不是只测试 repository 而绕开产品入口。
8. 查询指定 run_id 若属于另一会话会产生误导。CLI 明确报冲突，不拼接不同会话的历史与运行。

上述实现 gap 均已闭合。真实模型验收最初因缺凭据未运行（退出 2）；用户随后提供授权凭据，已补齐真实评测与独立 CLI 重启追问，最终验收 gap 已闭合。

## 实际命令、退出码与证据

- `UV_CACHE_DIR=/tmp/stage0-uv uv add 'sqlalchemy==2.0.43' 'psycopg[binary]==3.2.10'`：首次网络沙箱失败，自动审批后安装成功，退出 0。
- `initdb -D /tmp/agent-stage1-pg -A trust -U stage1 --no-locale -E UTF8`：首次共享内存权限阻止，获自动审批后完成。`pg_ctl ... -o '-h 127.0.0.1 -p 55439 -k /tmp' start` 退出 0。实际 PostgreSQL 17.11，无 Docker/mock/SQLite 替代。
- `UV_CACHE_DIR=/tmp/stage0-uv uv sync --frozen --offline`：退出 0，可运行 agent-core 和新增 agent-records。
- `TEST_DATABASE_URL='postgresql+psycopg://stage1@127.0.0.1:55439/postgres' .venv/bin/pytest -v`：**64 passed in 4.80s，退出 0，无 skipped**，完整列表见 [stage-1-tests.txt](stage-1-tests.txt)。原 C0 文件测试全部保留。
- `.venv/bin/agent-core --engine scripted --demo`：退出 0，见 [C0 回归演示](stage-1-c0-demo.txt)。
- `.venv/bin/agent-records --storage memory --engine scripted demo`：退出 0，见 [C1 内存演示](stage-1-memory-demo.txt)。
- 两个独立 `agent-records --storage postgres ... demo / show` 进程：均退出 0，见 [CLI 重开证据](stage-1-cli-restart.txt)。
- 三个独立真实 PostgreSQL worker 被 SIGKILL（退出 -9），随后独立 CLI 查询均退出 0，见 [崩溃后实际查询摘要](stage-1-crash-restart.jsonl)。running：1 条开始事件、0 条消息、中断；before：10 条事件、0 条消息、中断；after：10 条事件、2 条消息、revision=1、成功结果。可复跑测试 `tests/test_postgres_process.py` 与 worker。
- `DATABASE_URL=... .venv/bin/python evals/stage1_persistence/run.py`：补齐授权凭据后退出 **0**，[真实评测记录](stage-1-real-eval.json) 为 passed。密钥仅经关闭回显的终端输入进入测试进程环境，未写入文件或持久配置。
- `.venv/bin/python -m compileall -q src evals/stage1_persistence`：退出 0。
- 原 core.py、contracts.py、adapters/openai.py 开始与结束 SHA 校验相同。现有目录无 HEAD，未创建空 worktree、未重置或删除已有文件，未创建提交。

## 交付边界

数据库与模型入口不会自动降级。数据库失败时未提交预览只在当前进程存在；进程退出后不自动重新执行。保存冲突不覆盖历史，查询后可使用新的 run ID 由用户明确发起新请求。未提交的外部工具副作用不回滚，不承诺副作用恰好一次。

当前支持同主机同 OS 用户单进程；第二个独立 CLI 在第一个存活时会拒绝启动，交互内可 `/show`。同步数据库短操作可能占用事件循环至其语句期限，这是与 C0 同步观察接口匹配的当前执行契约。跨主机存活判定、异步批处理、自动恢复和 worker 不在 Stage 1。

所有本阶段必要验收现已通过。复现真实模型验收时仍需自行安全注入当前进程凭据；仓库不保存密钥。

测试收尾：`pg_ctl -D /tmp/agent-stage1-pg stop -m fast` 退出 0，已停止本任务启动的 PostgreSQL 测试实例，保留 `/tmp/agent-stage1-pg` 数据与证据。后续复现时先按 README 命令重新启动。


## 补齐真实模型验收

用户提供 DeepSeek 配置并授权后，使用 `https://api.deepseek.com`、`OPENAI_MODEL=deepseek-flash`（DeepSeek-V4.1-Flash），沿用 Stage 0 已验收的 SDK 默认 Responses 接口。未切换 Anthropic、未改 Core、工具或模型循环。

1. `evals/stage1_persistence/run.py`：实际退出 0。首轮调用 multiply 和 delegate 各一次，中文回答包含 96；关闭并重开数据库连接后读回首轮结果和事件，追问正确回答 96。两轮均依次通知“正在生成→正在保存→已完成”。共 5 次模型请求，输入 2388 / 输出 551 tokens。
2. 四个独立 CLI 子进程依次执行 `demo`、`show`、`send '刚才计算的结果是多少？'`、`show`，均退出 0。第一轮回答含 96，专家输出“数值为 96。”；demo 退出后新进程查到 revision=2、两份成功结果及 multiply/delegate 事件；第三个进程从持久历史追问得到“刚才计算的结果是 **96**（12×8 = 96）。”；最终查询 revision=3、三份成功结果。完整 stdout 与退出码见 [stage-1-real-cli.txt](stage-1-real-cli.txt)。共 6 次模型请求，输入 2860 / 输出 576 tokens。
3. 合计 11 次模型请求、输入 5248 / 输出 1127 tokens（SDK 报告）。查询进程没有模型调用。这是固定小样本语义与持久化连通验收，不代表广泛模型质量评测。
4. 随后完整 C0/C1 回归：64 passed in 4.80s，退出 0，无跳过。没有新增代码 gap；本次仅更新验收证据与交付状态。
5. 密钥只存在于临时测试父进程及其授权子进程环境，相关进程均已退出；未保存到代码、文档、数据库或持久配置。未使用 Codex/ChatGPT 登录令牌。
