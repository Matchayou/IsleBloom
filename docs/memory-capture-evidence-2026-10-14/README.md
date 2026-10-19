# 主助手后台候选提炼与完整记忆预算验收 · 2026-10-14

实现规则见 [长期记忆写入说明](../agent-memory-writing.md)。真实模型为配置的 DeepSeek Responses `deepseek-flash`，关闭 SDK 自动重试，使用独立 PostgreSQL 库和合成用户数据。凭据不写入证据。

## 最终真实模型结果

[最终报告](final/summary.json) 与 [测试汇总](acceptance-summary.json) 中四类用例与五个真实 SIGKILL 恢复点均通过。

| 用例 | 验证结果 |
| --- | --- |
| 用户长期回答偏好 | 一个 user 草稿，未自行确认；原消息 ID 对应一个任务 |
| 当前项目货币口径 | 一个 project 草稿，绑定真实 Project ID |
| 仅本次回答要求 | 无写入请求，无新增记忆 |
| 超大可选记忆与短记忆 | 超大条目排除，短条目正文完整；注入上界不超过 4,000 |

全部用例验证重复处理不会增加模型请求、其他 owner 无法处理任务、其他项目记忆没有进入请求或实际 checkpoint 内容、目标版本保持不变。预算用例同时验证既有等价记忆没有新增重复条目。

| 强杀位置 | 实际恢复结果 |
| --- | --- |
| 固定 ready 请求后 | 恢复同一请求/模型身份 |
| 写入 intent 后、工具回执前 | 从持久化 intent 恢复原回执 |
| 工具回执后 | 复用 checkpoint |
| 草稿与完成状态提交事务内 | PostgreSQL 回滚，恢复原请求后提交 |
| 原子提交完成后 | 读取 completed 状态，不再执行模型 |

退出码均为 -9。五个恢复用例均只产生一条写入请求、一个草稿、一次写入审查调用、一次受控工具操作；没有 retry 操作。提炼的两次主模型调用（提出工具调用、完成结果）未重复执行；审查另一次。重复处理不增加 HTTP 模型请求。每个目录保存 `evidence.json`、`seed.json`、`http.jsonl`、`intents.jsonl`、`kill.json` 和 worker 日志，包含实际操作、固定请求、正文、使用量、checkpoint 及验证结果。

## 测试与失败记录

- `postgres-results.xml`：61 项通过、无跳过。组合覆盖实际 PostgreSQL 回滚/重连、写入权限、记忆语义检索与正式领域发布；不能将每一项都称为真实模型测试。
- `final-targeted-results.xml`：40 项通过、无跳过。包含后台模型完成状态未知时停止、不自动重试的 PostgreSQL 操作检查。
- `write-budget-results.xml`：32 项通过、无跳过；包含每个后台任务只允许一个候选的程序约束。
- `regression-final-results.xml`：全量回归 583 项通过、297 项跳过，无失败/错误。最后增加的单候选约束在上述 32 项中单独覆盖；环境不具备的测试明确标为 skipped。

初次真实探针在 `initial` 中暴露内部唯一工具被普通词法发现隐藏的问题，已改为固定能力入口。`acceptance` 中项目用例未提炼，因为测试消息同时要求“不调用工具”；最终探针用原任务的服务端零工具预算控制主聊天，不再在用户偏好中混入这项要求。另有源聊天输出合同失败与预算证据读取路径错误，均保留原报告，未把失败算作通过。最终预算和故障验证使用 `final` 的新运行。

首次全量回归 `regression-results.xml` 遭遇临时磁盘空间不足：一项失败、30 项临时目录错误；使用独立临时目录完整重跑，最终结果以 `regression-final-results.xml` 为准。

## 实现边界

每个真实人类消息至多一个候选；前三条既有消息仅作解释，不扫描全部历史提炼。后台候选不自动确认、不自动修改现有条目。失败或未知完成状态终止，不用新请求掩盖原费用未知；存储失败可恢复同一 durable 任务。既有专家学习流程独立保留。

预算使用项目现有 UTF-8 保守 token 上界，包含完整正文及条目元数据；并非供应商 tokenizer 的精确值。控制的是开轮注入和单次 search_memory 返回，累计运行历史仍受整请求预算管理。测试证明固定样例的正文完整、预算、权限与恢复行为，不证明任意对话中的候选提炼召回率。

复现：配置凭据和独立名称以 `memory_capture_` 开头的 PostgreSQL 库，运行 `.venv/bin/python scripts/verify_memory_capture_live.py --database DATABASE_URL --directory NEW_DIRECTORY`。它会调用真实收费模型，并仅强杀自己启动的子进程。
