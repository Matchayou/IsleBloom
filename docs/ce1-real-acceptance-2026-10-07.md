# CE1 真实模型与 PostgreSQL 验收

2026-10-07。**当前“受控工具执行与逻辑数据隔离”范围验收通过。** 不使用 Docker，不承诺 OS 强隔离；CE2–CE8 未实施。

## 真实结果

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 真实模型闭环 | DeepSeek `deepseek-flash` / Responses API，4 次 HTTP 200；资料读取 → 笔记写入 → 重读 → 结构化成果 | [正向完整记录](ce1-evidence/live-controlled-2026-10-07-attempt4/live.json) |
| 来源与语义 | 从真实存储的原始合成源独立核对 Marker、已确认数量 7、未确认值 unknown；不使用模型 self_checks 作为依据 | [原文核对与重启记录](ce1-evidence/postgres-restart-and-semantics-2026-10-07.json) |
| 持久预算与安全门槛 | 真实 PostgreSQL 验证模型/工具预算、截止时间、重复操作、旧写者、取消、未授权工具、跨逻辑工作区访问 | [最终回归](ce1-evidence/controlled-real-postgres-regression-2026-10-07.txt) |
| 真实取消 | 收到实际 HTTP 200 后，在 SDK 消费响应前用验收 hook 暂停，调用 TaskService.stop；执行退出、写者释放、迟到绑定拒绝，回执为 rework | [取消记录](ce1-evidence/live-controlled-2026-10-07-cancel/live.json) |
| 未知用量 | 取消请求保持 requests=1、complete=false；服务商已收到请求，不声称撤回或免计费 | 同上 |
| 私有持久化 | 新进程重读 SQLite/文件，写入与重读 SHA256 一致；数据库重启后再次一致 | 正向记录与重启记录 |
| 提交回执与幂等 | 正式结果及 TaskReceipt 已保存；retry_save 返回同一结果/回执，模型请求数不增加；重启后内容不变 | 正向记录与重启记录 |
| 综合回归 | **73 passed，无失败、无跳过**，其中新增 controlled 外部用例 6 项均使用真实 PostgreSQL | 最终回归 |
| 数据库重启验证 | 本轮专用 PostgreSQL 重启后，新进程 **16 项检查全部通过**，没有模型调用 | 重启记录 |

成功运行 ID：`f0667abc-c6cd-4df3-b64c-40ab3ff79bfa`。取消运行 ID：`74a0a9ff-f1f1-4145-ab07-59132fac7ed6`。

成功任务确认用量：4 次请求、13,343 输入 token、2,825 输出 token。3 次先前失败尝试和取消请求仍保留未确认账目；这些用量不包含在成功任务的 token 总数中。不能据此计算全部调用费用。

软件验收通过与业务采用分开：成功 TaskReceipt 的 `delivery=pending_review`、`adoption=for_discussion` 是预期状态；本次不把合成样本自动评审为真实研究结论或正式采用。取消回执 `delivery=rework`，没有成功产物。原文比较证明本样例的答案内容正确，不代表模型对任意研究任务的质量保证。

## 失败与修复

1. 原受限环境 `initdb` 被禁止创建共享内存；提升执行权限后成功初始化独立 PostgreSQL，没有绕过应用锁。
2. Chat Completions 第一次尝试失败，早期通用错误未记录 HTTP 类型；第二次补充安全诊断后确认 HTTP 400。SDK 会发送 JSON Schema，DeepSeek 的 [Chat Completions 接口](https://api-docs.deepseek.com/api/create-chat-completion/)只列出 text/json_object，而 [Responses 接口](https://api-docs.deepseek.com/api/create-response/)支持 JSON Schema，因此显式选用 Responses，不添加自动降级或兼容层。两次记录保留在 attempt1、attempt2。
3. Responses 第一次尝试连接超时，未收到 HTTP 响应（attempt3）。将客户端连接超时明确设为 20 秒、读写/池等待上限 90 秒；仍受任务总时限 120 秒及 SDK 持久预算控制，HTTP 自动重试仍为 0。下一次独立尝试通过（attempt4）。没有重用失败操作 ID 或抹掉失败预算。
4. broker 的错误诊断只保留异常类及数字 HTTP 状态，转为 CoreFailure，避免 SDK 把错误压成不可定位的通用提示；单测验证异常正文不外泄且预占不结算。
5. 取消验收使用真实 HTTP 响应事件作为同步点，避免“仅看到预算预占但尚未触达服务商”被误记成真实在途取消。测试 hook 控制时序，不构造响应、不替换模型。

## 数据、环境和复现

- Python 3.12；OpenAI Agents SDK 0.22.2、OpenAI 3.16.2、DBOS 3.0.0、SQLAlchemy 2.0.43、psycopg 3.2.10；原依赖锁未升级。
- PostgreSQL 17.11（Homebrew），独立集群 `.ce1/20261007/postgres`，只监听 `127.0.0.1:55460`；专用数据库 `ce1_controlled_acceptance`。
- 已备份到 `.ce1/20261007/ce1_controlled_acceptance.dump`，验收结束后已停止本轮 PostgreSQL，原数据仍在。`.ce1/` 已加入忽略列表。
- 没有启动 Docker、改动历史数据库、停止历史服务、提交或推送。凭据通过关闭回显的输入传入临时验收会话，只存在进程内存/子进程环境；会话已退出，源码和证据没有密钥。
- 正向与取消各使用独立 blob/私有目录。完整请求/响应记录仅来自合成验收数据；正式产品仍需按私有数据管理。

在同一工作区可重新启动本次测试数据库，运行无模型复核：

```sh
pg_ctl -D .ce1/20261007/postgres -l .ce1/20261007/postgres.log -o '-h 127.0.0.1 -p 55460 -k /private/tmp -c max_connections=20 -c shared_buffers=32MB' -w start
export CE1_TEST_DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55460/ce1_controlled_acceptance'
export TEST_DATABASE_URL="$CE1_TEST_DATABASE_URL"
.venv/bin/python -m pytest -q tests/test_controlled.py tests/test_controlled_external.py tests/test_sdk_contract.py tests/test_tasks.py tests/test_architecture.py
.venv/bin/python scripts/verify_ce1_persistence.py --complete docs/ce1-evidence/live-controlled-2026-10-07-attempt4 --cancelled docs/ce1-evidence/live-controlled-2026-10-07-cancel --output /tmp/ce1-reopen-verification.json
pg_ctl -D .ce1/20261007/postgres -m fast -w stop
```

需要重新调用真实模型时，通过环境注入凭据与 `OPENAI_BASE_URL=https://api.deepseek.com`，运行 `scripts/verify_ce1_live.py --api responses --model deepseek-flash --allow-paid-model --scenario complete|cancel --output <全新目录>`。原进程锁与数据库 advisory lock 保持不变；有其他执行实例时明确拒绝。

本轮通过的是 CE1 单专家、合成源、受控工具范围；多专家入口和协作撤权传播属于 CE2，任意代码执行、容器与硬资源限额均不在当前范围。
