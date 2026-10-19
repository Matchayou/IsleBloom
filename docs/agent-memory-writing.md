# Agent 长期记忆写入

主助手与执行中的临时、专精 Agent 可调用 `write_memory`。规划、讨论、咨询和自动恢复通知不开放写入。采用 [LangMem 的显式记忆工具](https://langchain-ai.github.io/langmem/guides/manage_memories/)、[LangGraph 的 namespace 隔离](https://docs.langchain.com/oss/python/langgraph/persistence)与项目现有独立领域验证、受控账本和事务；不新增依赖。

## 什么会被保存

Agent 应在用户明确要求记住、纠正长期偏好或规定项目长期约定时提出写入，例如“以后先给结论”“这个项目统一用万元”。正文是一条可复用的偏好或经验，必要时包含适用条件。用户原话必须提供连续、逐字引用；子 Agent 的引用通过真实交接包追溯原始人类消息，管理 Agent 生成的指令不能冒充用户来源。

独立轻量 LLM 检查内容是否有证据、长期有用、安全、是否确属用户明确表达，以及是否重复。明确用户表达可保存为已确认记忆；推断偏好、Agent 自己总结的经验保存为 draft，不进入检索。仅本次回答的要求、未获支持的结论、凭据、秘密、第三方私密信息和整段原始对话不会保存。项目约定不能扩大为全局用户偏好。检查使用 `OPENAI_MEMORY_MODEL`，沿用原运行预算、Broker 和 checkpoint，未知完成状态不自动重发。

专精 Agent 只能在自己的 specialist 范围创建经验候选，每轮至多一条；随后走已有轨迹来源审查、独立验证和原子发布。候选不能通过普通记忆确认接口直接激活。当前用户指令始终优先，记忆不是事实证据或权限来源。

## 工具与权限

| 操作 | 含义 | 要求 |
| --- | --- | --- |
| create | 新建偏好或经验 | scope、kind、content、basis、reason |
| correct | 替代旧内容或确认已有草稿 | 用户原话、target_id、expected_version；原 ID 的版本递增 |
| forget | 停用旧记忆 | 用户原话、target_id、expected_version；转为 draft 并递增版本 |

`basis` 为 `user_statement`、`inferred_preference` 或 `experience`。用户表达必须提供 `quote`；correct/forget 只允许明确用户表达。可选 `applicability`、带时区的 `expires_at`；纠错未指定期限时保留原期限。永久删除仍由用户删除 API 完成。重复的已确认内容返回 duplicate，矛盾的 create 被拒绝，必须显式 correct。

| 调用者 | user | 当前 project | 自身 specialist |
| --- | --- | --- | --- |
| 主助手、临时 Agent | 创建/纠错/停用 | 创建/纠错/停用 | 不可访问 |
| 固定专精 Agent | 创建/纠错/停用 | 创建/纠错/停用 | 仅经验候选 |

owner、真实 Project ID、固定专家 ID、确认状态与写入 ID 均由服务端决定，工具参数不能指定。目标必须是当前授权目录里的固定版本，或同权限范围内的草稿/过期条目。其他用户、项目和专家的目标均拒绝。发布时再次检查项目归属和目标版本；并发修改不覆盖新版本。

## 当前轮与恢复

工具先持久化 pending 请求和 queued 回执；回执明确说明尚未生效。当前轮继续使用原冻结目录，不能检索自己刚提交的条目。业务输出完成依赖检查后，在保存结果的同一个事务中发布记忆和写入 resolution；撤销、取消、失效运行不发布。普通回答失败不抹掉已核实的显式长期偏好。后台只补齐已提交终态运行，不能提前发布仍可恢复的运行。

纠错保留旧内容、版本、原话来源哈希和审查结果。只有已提交终态运行才能用自己的精确写入回执解释旧版本，确保自己的纠错不会让终态重放失效；其他运行或后续外部修改仍触发正常版本失效检查。

受控工具操作、请求与 payload 哈希建立稳定身份。请求已经保存而工具回执尚未保存时，恢复从该请求还原原回执；不重调审查模型、不重复执行写入。业务事务中被强杀时数据库回滚，恢复重新提交同一请求；事务后被强杀时读取已提交结果。合同版本为 `ce7-v4-memory-capture-budget`，不接管旧合同的在途运行。

`GET /api/memory_writes?research_id=...&run_id=...&specialist_id=...` 返回当前 owner 与授权 namespace 的请求和结果，包括 pending/published/rejected、旧版本、审查、queued 回执、最终 resolution 和时间。`published` 表示请求已结算，专家 resolution 可能仍是 awaiting_independent_validation。

实现入口：`memories/write_models.py` 定义协议，`memories/writing.py` 处理权限与事务，`context/memory_tools.py` 暴露工具，`controlled/broker.py` 执行独立审查，`controlled/checkpoints.py` 恢复回执，业务 ledger 原子提交，`learning/service.py` 验证领域候选。验证结果见 [2026-10-13 验收](memory-write-evidence-2026-10-13/README.md)。


## 主助手后台候选提炼与注入预算

成功提交真实主助手聊天结果时，在同一事务中创建 `memory_captures` 任务。任务键由 owner 与真实人类 message ID 生成，分支继承和重复结束事件不会对同一消息重复排队；自动恢复通知、规划执行和无真实消息 ID 的运行不排队。

后台只提炼当前新消息，前三条既有消息仅用于解释上下文；每个任务至多持久化一个用户或当前项目候选，不执行原任务、不访问专家范围、不自动纠错或停用。所有自动候选都为 draft，即使用户原话明确表达偏好也不会自行确认。用户确认仍通过 `correct` 的真实原话、目标 ID 与版本检查完成。其他既有记忆矛盾应回到普通纠错流程。

`pending → ready → completed/failed`：ready 保存固定模型、请求和快照，再开始收费模型调用。提炼模型使用 `OPENAI_MEMORY_MODEL`；调用统一经过 Core、ControlledEngine、Broker 和 checkpoint，单任务上限 12 次模型调用、2 次工具调用、90 秒。独立写入审查复用原 `write_memory` 链路；原聊天结果已经完成，提炼单独记账。候选发布和 completed 状态同事务提交。失败、用量未知或来源失效的任务不自动重发，并拒绝未发布请求；临时存储异常保留 durable ready 状态，后台记录日志后可恢复。成功及失败终态重复处理均不发起模型调用。

项目归属、原消息、原运行提交状态和授权代际会重新校验。`GET /api/memory_captures?research_id=...` 查看队列、固定请求与结果；来源不可用时只返回 ID 与 source_unavailable，不泄露正文。

长期记忆正文与条目元数据统一使用 4,000-token 注入上限，沿用项目的 UTF-8 保守上界估计，并标明 `utf8_upper_bound`，不将它当成供应商实际 token 用量。开轮首先保留显式必需条目，随后按 user/project/specialist 范围及各自语义顺序接纳完整可选条目。超大可选条目在选择前排除；组合放不下时跳过该条，仍可接纳后续较小条目。必需条目整体超限在请求组装时失败，不静默丢弃。

`memory:initial` checkpoint 保存注入预算、完整正文和排除 ID/版本；`search_memory` 每次返回的正文也使用相同上限。该预算控制开轮注入与单次检索结果，整段运行历史累计成本仍由已有整请求预算与压缩机制控制。没有截断记忆正文、没有固定用户画像百分比，也没有 tokenizer 或服务新依赖。

真实模型、PostgreSQL 和强杀证据见 [2026-10-14 验收](memory-capture-evidence-2026-10-14/README.md)。
