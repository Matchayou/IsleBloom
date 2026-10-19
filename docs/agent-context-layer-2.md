# 第二层：渐进能力加载与记忆范围

在第一层的临时/专精身份、交接包、统一 `ContextAssembler` 上实现。主助手和专家执行接入相同流程；规划模型、专家讨论和信息咨询在 [第三层](agent-context-layer-3.md) 接入统一组装，各自保留业务权限边界。

## 采用的模式

[Agent Skills 规范](https://agentskills.io/specification)采用 metadata → instructions → supporting resources 的渐进披露。[Anthropic 工具发现](https://www.anthropic.com/engineering/advanced-tool-use)按需加载工具定义。[LangChain memory](https://docs.langchain.com/oss/python/concepts/memory)将跨会话记忆放在明确 namespace 中。这里复用现有 SQLite 排序、SDK 工具启用状态与持久化 snapshot，不新增框架、服务或依赖。

## 从原话到运行

1. 原用户消息完整保留为最后一条 user 消息。主助手用原话检索；子 Agent 用交接目标加原话检索，不修改原话。
2. `MemoryService` 根据 owner、当前主任务所属 Project、Agent 身份计算可用范围；排除 draft 和过期条目。组装器再次校验范围，必需 memory 越界直接拒绝。
3. 三个 memory 范围由独立轻量 LLM side query 各自选择最多三条，显式选择的有效条目保留。程序先过滤权限并冻结目录，受控 Runner 首次发送主模型输入前完成语义选择；不用模型判断权限。候选完整正文按固定输入上限分批读取，再合并选择理由；没有 BM25 记忆召回或回退路径。
4. Skill 开轮只给名称、描述、哈希、匹配理由。显式 `$name` 优先，但仍需 Agent 判断后调用 `load_skill`。匹配候选不包含正文或工具启用提示。
5. 工具开轮按当前查询加载最多五个相关的获准 schema；发现工具保持可用。`search_tools` 加载更多 schema；`load_skill` 成功后，其 `allowed-tools` 与实际获准工具求交集，再开放下一轮 schema。`search_skills` 只返回候选；`read_skill_resource` 读取冻结的支持文本。
6. `search_memory(query, scope, limit)` 在本轮冻结目录中补充检索记忆正文。主助手和临时 Agent 不能指定 specialist 范围，专精 Agent 只能读自己的领域。
7. 模型及所有能力发现、skill 读取、memory 检索仍经 Core/ControlledEngine/SDK/Broker 统一入口、调用预算和审计。技能不授权新工具，不开放模型直接读取宿主文件系统。

## Memory 数据合同

| scope | scope_id | 可读取者 |
| --- | --- | --- |
| user | null | 同 owner 的主助手及子 Agent |
| project | 真实 Project ID | 同 Project 主任务的主助手及子 Agent |
| specialist | 已发布专家 ID | 同 owner 且固定身份匹配的专精 Agent |

领域记忆可跨项目复用；项目资料、项目历史不会因此跨项目检索。临时 Agent 即使使用某个专家的能力模板，也不获得该专家的领域 memory。owner 始终独立校验。未归属 Project 的研究只有用户范围，专精执行额外拥有自身领域范围。

创建 API：`POST /api/memory_create`，字段为 `kind/content/source/scope/scope_id/confirmed/expires_at`。`kind` 保持 `preference/experience`，手工确认和版本化修改继续有效。列表 API 通过 `research_id` 确定当前 Project；可加 `specialist_id` 查看该已登记领域。旧的 memory `research_id` 存储字段和创建参数已移除，没有兼容层或迁移。

`ContextSnapshot.memory_refs` 是显式必需条目；`memory_catalog` 是本轮允许读取的全部 ID/版本，不含正文。自动选择的 ID、版本、理由和原文保存于受控 `memory:initial` checkpoint，实际模型输入与用量进入账本。目录在工具调用和发布前重验；其中条目修改、删除、暂停、过期，或 Project 范围变化，会使本轮失效，下一轮重新组装。本轮不混用更新后的 memory，不读取本轮中新建条目。`ContextSnapshot.selection.memory_namespaces` 可查看本轮范围。

Memory 服务在研究服务初始化时创建，专家服务共享同一实例；单独运行研究/规划执行也能使用用户和项目记忆，重建专家服务不会清空已有记忆。

## 边界与验证

`search_memory` 保持只读；主助手与执行中的子 Agent 另有 [write_memory](agent-memory-writing.md)，规划、讨论、咨询和自动恢复通知保持只读。轨迹生成、隔离候选和验证发布已在 [第四层](agent-context-layer-4.md) 接入。Memory 正文仍是历史经验或偏好，不能作为资料证据，当前用户要求优先。候选正文按需读取；工具加载状态随现有 SDK checkpoint 恢复，权限仍由受控入口再次裁定。

`tests/test_context_layer_2.py` 验证同项目复用、领域隔离、临时 Agent 拒绝领域范围、原话保留、运行中 memory 加载与撤销阻断发布，以及通过真实 SDK 与 Broker 的 skill 激活、工具 schema 变化和 model/tool 预算记录。数据库集成依赖独立测试 PostgreSQL；没有提供测试数据库时相应测试跳过。

`tests/test_memory_side_query.py` 使用确定性模型通过真实 SDK/Broker 验证开轮和运行中语义选择、分批完整读取、选择理由合并、错误 ID/版本拒绝、原文加载、轻量模型路由、预算与审计、响应恢复、未知完成状态停止及查询中撤销。模型双只验证协议和运行链路，不证明真实模型的检索质量。
