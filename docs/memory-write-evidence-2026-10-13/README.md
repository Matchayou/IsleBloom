# 长期记忆写入验收 · 2026-10-13

实现说明见 [Agent 长期记忆写入](../agent-memory-writing.md)。真实模型使用配置的 DeepSeek Responses 服务、`deepseek-flash`，关闭 SDK 自动重试；持久化使用独立 PostgreSQL 测试库。每个用例有独立 owner，所有用户内容均为合成测试数据。未保存 API 密钥。

## 真实模型结论

以下 14 类用例均获得通过记录，汇总见 [acceptance-summary.json](acceptance-summary.json)。采用显式工具调用探针，验证真实模型审查、权限和持久化链路；不代表任意自然对话中模型主动写入的精确率。

| 用例 | 结果 | 证据目录 |
| --- | --- | --- |
| 主助手保存明确用户偏好 | confirmed | live/main_preference |
| 临时 Agent 保存当前项目约定 | 真实项目 namespace | live/temporary_project |
| 专精 Agent 保存自身领域经验 | draft，待独立验证 | final-specialist/specialist_candidate |
| 临时 Agent 写专家范围 | 拒绝，无请求无条目 | live/temporary_domain_denied |
| 修改其他项目目标 | 拒绝，目标版本不变 | live/foreign_project_denied |
| 用户纠错替代 | 同 ID v1→v2，保留旧版本审计 | final-recovery/correction |
| 用户要求忘记 | 同 ID v2，draft，退出检索 | live/forget |
| 仅本次要求 | 不保存 | live/temporary_request |
| 推断的偏好 | draft，不自行确认 | live/inferred_draft |
| 项目约定扩大为用户全局 | 不保存 | project-scope-retest/project_to_user_denied |
| 用户明确确认已有草稿 | 原 ID v1→v2 confirmed | draft-confirmation/draft_confirmation |
| 用 create 绕过纠错 | 不保存，须 correct | conflicting-create/conflicting_create_denied |
| 修改其他用户草稿 | 拒绝，目标不变 | foreign-owner/foreign_owner_denied |
| 子 Agent 真实用户交接 | 正确追溯原人类消息并保存 | handoff-write/handoff_user_preference |

各目录 `evidence.json` 包含实际结果、memory_writes、记忆行、受控操作、模型 manifest、checkpoint、HTTP 请求次数和 intent 执行次数；`summary.json` 汇总逐项检查。新运行记录包含请求/结算时间，早期原始记录保留原样。

首批 `live/specialist_candidate` 发生一次供应商连接超时，未调用写入工具、未写入记忆，用量不完整。这个失败原样保留在 [首批报告](live/summary.json)，未自动重试未知完成的原运行。另开独立运行的 [复测](specialist-retest/summary.json) 通过；最后代码的 [专家复测](final-specialist/summary.json) 也通过。不能把首批失败计作通过。

## 强杀与恢复

[最终代码报告](final-recovery/summary.json) 的四个故障点全部通过；父进程发送实际 SIGKILL，子进程退出码均为 -9，随后启动新进程恢复相同运行。

| 强杀位置 | 恢复行为 |
| --- | --- |
| intent 持久化后、工具回执前 | 用 intent 还原原工具回执 |
| 工具回执后 | 复用受控 checkpoint |
| 业务提交事务内部 | PostgreSQL 回滚，恢复原请求后原子提交 |
| 业务事务提交后 | 读取已提交业务结果和记忆 resolution |

四个用例均：一个持久化请求、一次 intent 执行、一次写入审查模型调用、同一记忆仅 v1→v2；无 retry 操作，旧版本审计仍在。写入时冻结目录仍有效、最终业务成功、终态二次重放成功。外部后续版本变化仍阻断重放，确定性测试覆盖此区别。首批另外四个强杀结果也通过，保留在 `live/summary.json`，最终结论以 `final-recovery` 为准。

## 测试记录与边界

- `final-targeted-results.xml`：38 项通过、无跳过；覆盖写入协议、身份隔离、伪造用户来源、专家候选禁止手工确认、并发版本冲突、当前轮冻结、自己终态纠错重放、实际 PostgreSQL 回滚/重连，以及正式领域发布后复用。
- `postgres-results.xml`：43 项通过、无跳过；已有记忆、checkpoint、SIGKILL 恢复与写入检查的组合，不全是新增写入测试。
- `write-contract-results.xml`：28 项通过、无跳过，早期写入与恢复合同检查。
- `regression-results.xml`：最终全量回归 565 项通过、291 项跳过、无失败。缺少专用环境的测试标为 skipped，不视为持久化通过。首次沙箱执行唯一失败是已有 HTTP 测试无法绑定本地端口；允许 localhost 后完整重跑通过。

`tests/test_context_layer_4.py` 的数据库复用测试用确定性 semantic response；真实模型证据由 `scripts/verify_memory_writes_live.py` 单独产生。字段伪造、跨专家范围、并发修改、当前轮与后续外部修改等边界由确定性测试覆盖，真实模型不负责决定权限。真实模型探针不替代长期、多轮内容质量评测。

复现：配置模型凭据；创建名称以 `memory_write_` 开头的独立 PostgreSQL 库；运行 `.venv/bin/python scripts/verify_memory_writes_live.py --database DATABASE_URL --directory NEW_EVIDENCE_DIRECTORY`。脚本会调用收费模型并真实强杀自己的测试子进程；不触碰产品数据库。
