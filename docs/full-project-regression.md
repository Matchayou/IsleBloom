# 全项目回归：2026-10-12

最终结果：整个 `tests/` 共 **792 passed、0 failed、0 errors、0 skipped**，用时 296.16 秒。整个 Playwright `frontend/tests/` 共 **12 passed、0 failed、0 skipped**，用时 23 秒。前端生产构建通过。

## 环境与原跳过条件

本轮扫描到 36 处潜在 skip 条件，完整文件/行号/原因见 `full-regression-evidence/skip-conditions.json`。不是将这些条件绕过，而是为实际测试配置相应环境：

| 环境 | 覆盖范围 | 本轮配置 |
|---|---|---|
| TEST_DATABASE_URL | 执行、资料、研究、聊天、专家任务、方法、评估、memory、规划、CLI 与进程恢复 | 每轮全新通用 PostgreSQL 库 |
| SECOND_TEST_DATABASE_URL | 独立数据库进程锁与 URL 别名 | 单独第二库 |
| CE1–CE7_TEST_DATABASE_URL | 外部执行、Agent 协作、投影、交付、代码执行、压缩、检查点与耐久恢复 | 七个不同数据库，保留要求的 ceN_ 名称前缀 |
| COLLABORATION_TEST_DATABASE_URL / BLOBS | 专家协作与信息咨询跨进程恢复 | 专用库及本轮独立 blob 目录 |
| QUALITY_TEST_DATABASE_ADMIN_URL | 隔离质量评估试验建库、受控产物收集 | 本机隔离集群的 postgres 管理库 |
| OCR | 中文扫描识别、位置与解析状态 | 本机 Tesseract 与项目 `.stage3/tessdata/eng.traineddata`、`chi_sim.traineddata`；中文文件从官方 tessdata_fast 补齐 |
| Python 依赖 | 真正的 SDK、数据库、DBOS、文档解析 | 项目 .venv，未添加包；版本与 OCR 校验和见 environment.json |
| 浏览器依赖 | 当前 UI、Flow、专家讨论、规划、流式聊天、证据与刷新 | 已安装 Node/npm、Playwright、Chromium；两个隔离服务端口 18744/18769 |

PostgreSQL 为 Homebrew 17.11，隔离集群位于 `/private/tmp/agent-node-pg-20261012`、端口 55473，未连接业务数据库。本轮测试库留存；测试服务及集群在完成后关闭。默认业务端口 8744 已有服务，因此改用独立端口；脚本在启动前检查端口空闲，不会停止原服务。

方法候选的“memory 后端进程重开”参数本身不适用，已移除这个参数；同一测试的真实 PostgreSQL 参数继续执行，不把内存后端计为持久化通过。所有当前收集到的自动化测试均实际执行。

## 失败定位与修复

第一轮完整运行：764 passed / 27 failed / 1 skipped，保留在 first-run.log 和 first-run.xml。修复后第二轮因测试仍读取原话旧位置而有 3 项失败；更新定位后，第三轮在全新库上全量通过。不是只重跑失败用例来拼接最终结果。

产品修复：

- 当前 user 原话原先同时出现在 history 与最后独立消息，重复占用预算。ContextBuilder 仅移除与当前原话完全一致的最后历史 user 条目，保留最后独立消息及 `current_message_id`；更早用户约束不变。预算投影也保留该消息 ID。新增长原话测试验证字节内容、历史约束、来源 ID 和总预算。
- 前一轮修复的领域轨迹事务和 Broker 全工具发现，在此次真实 PostgreSQL、跨进程、SIGKILL、代码执行、规划/讨论/咨询、压缩和审核回归中全部通过；没有增加嵌套事务兼容层，也没有放宽授权工具集合。

测试与验收场景修复：

- SSE、方法工作流、报告续接和进程故障夹具改用当前 ResearchAnswer 的 blocking_missing 契约，移除废弃 missing 字段。
- 代码、规划及其他 SDK 夹具先发现工具再调用；发现由 Broker 执行时按实际 private 操作核验审计，仍核验持久工具预算。
- 压缩压力输入现在明确与检索查询相关，确保长历史确实进入本轮冻结输入后测试压缩；没有禁用动态检索。
- 预算测试使用已经提供 schema 的工具来验证超额调用关闭；超时测试保留未确认操作断言。
- 讨论与续接夹具识别当前模式和发现工具；专家子任务、原专家重评、结果回主线的流程保持原断言。
- 证据拒绝夹具提供当前支持的三轮格式/证据修复响应，最终仍要求 evidence_invalid、无产物、无法发布。
- 崩溃夹具保留完整事务仓储，使测试真正到达目标 SIGKILL 边界，而不是提前因缺 memory 仓储退出。

## 浏览器覆盖迁移

原 5 项研究/R3/Stage5 浏览器场景依赖已移除的控件和历史数据库 ID，现改为公开 API 在当前产品中创建全新项目、主任务、固定输入、分支、结果与转移，并由当前项目 UI 验证展示/刷新。旧版本完整保留在 `full-regression-evidence/previous-ui-tests/`，不恢复旧 UI 路径。

目前 12 项包括：计划生成/修订/确认/问题阻断/短窗口；项目 Flow 独立展开、延迟加载不串会话、跨主任务搜索、专家执行对话；分支截止与兄弟隔离、明确转移采用、两个专家任务不自动插入主线；普通节点固定输入、结果挂载、比较及刷新；固定合成证据定位与持久读取；缺在线搜索凭据不能制造结果。

浏览器模型提供器采用真实 SDK Response/流式事件与用量结构，但响应是合成控制夹具。证据样本也明确为合成数据；此次没有声称重新审核历史真实财报或证明在线模型质量。

## 复现

从项目根目录执行（需要本机数据库共享内存、监听及子进程权限）：

```sh
pg_ctl -D /private/tmp/agent-node-pg-20261012 -l /private/tmp/agent-node-pg-20261012.log -o '-h 127.0.0.1 -p 55473 -k /private/tmp' start
sh scripts/verify_project_regression.sh
sh scripts/verify_browser_regression.sh
pg_ctl -D /private/tmp/agent-node-pg-20261012 -m fast stop
```

Python 脚本每次建立十座全新隔离库，保存 JUnit 并断言 failures/errors/skipped 均为零。浏览器脚本另建两座库、构建前端、启动两个测试服务，执行全套 Playwright，退出时关闭测试服务。

逐项 Python 结果：`full-regression-evidence/results.xml`；完整日志：`regression.log`；浏览器逐项结果：`browser-results.json`；构建结果：`frontend-build.log`；版本和 OCR 校验和：`environment.json`。

当前注册的自动化用例没有无法执行项。需要真实模型/搜索凭据的人工 live 验收脚本未在本轮执行，不属于本轮自动化通过数，也未作为跳过通过处理。
