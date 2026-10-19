# 第三层：规划、专家讨论和信息咨询

三个入口均通过 `research.context.assemble` 生成冻结 `ContextSnapshot`；再用 `context_tools` 绑定 skill/tool/memory 的渐进加载。移除了规划的直接 Builder 路径，以及讨论、咨询独立调用 `project_payload` 的组装路径。低层预算投影由统一 Builder 负责。

继续采用 [Anthropic 的独立任务上下文与明确交接模式](https://www.anthropic.com/engineering/multi-agent-research-system)和 [Agent Skills 的渐进披露规范](https://agentskills.io/specification)。共用组装器统一输入格式与生命周期，入口权限仍由程序分别裁定。

## 检索范围

| 入口 | 历史 | 资料与结果 | Memory |
| --- | --- | --- | --- |
| 规划 | 原节点当前分支的继承历史及当前消息；全部用户陈述、最近三条、相关六条 | 冻结规划输入。主线可包含当前研究资料库，分支只包含继承/选定资料；显式结果按不可变版本挂载。不搜索兄弟分支或其他主任务 | 当前 owner 的用户与所属 Project；无领域范围 |
| 专家讨论 | 本专家会话历史；不搜索其他专家会话 | 当前任务授权目录、冻结报告的资料依赖及会话中的显式附加资料；当前任务的保存结果版本按相关性选择，必需上游结果与显式附加结果保留。`read_context` 可补读冻结目录中的报告版本或本会话消息 | 用户、当前 Project；固定专精身份另加自身领域，临时身份无领域范围 |
| 信息咨询 | 委派主助手所在分支的继承与当前对话；不读取其他分支历史 | 当前主任务获准资料及保存的专家报告目录。可超出请求专家已有输入，用于解决信息缺口；不能跨主任务或 Project 检索资料。排除不可访问报告和被排除资料 | 用户、当前 Project；不读取请求专家领域 memory |

Project memory 可以跨同项目主任务共享，领域 memory 可以跨项目用于同一固定专家；这不扩大上述资料、历史和结果的范围。规划、咨询以 assistant 身份组装；讨论沿用任务的 specialist/temporary 身份，不因使用专家能力模板改变 memory 权限。

## 加载时机

- **开轮**：选取相关历史、资料/报告目录、已确认且未过期的分范围 memory；冻结候选和版本。技能只提供名称、描述、哈希与匹配理由。显式 `$skill` 优先匹配，仍需 Agent 激活。
- **运行中**：Agent 用 `load_skill` 读取正文，用 `read_skill_resource` 读取支持文本。`allowed-tools` 只与该入口实际获准工具求交集。`search_tools` 检索加载更多 schema；`search_memory` 在本轮冻结、分范围目录内补充检索。
- **正文**：规划及讨论通过 `search_context` 找目录，再用 `search_source/read_source` 读取；讨论通过 `read_context` 读取本轮目录内报告或会话消息。咨询只用 `read_information` 列目录或读资料/报告，维持实际阅读记录。
- **结束及保存前**：重新验证 memory 目录、资料权限及入口业务版本；记忆撤销、修改、过期或权限变化使本轮失效。讨论还检查会话 revision，避免将旧回复保存到已改变的会话。

规划保持“不执行计划”的约束：不联网、不派遣、不采用、不修改业务对象。讨论允许解释和读取，不重跑或修改任务。咨询不派遣、不追问用户；回答只能授权本轮确实读过的资料/报告，或确实提供的对话消息。技能和 memory 均不能成为资料事实或授权依据。

## 用户原话与查询

原话不会 trim、改写或被检索查询替换；最后一条独立 user 消息保持字节内容：

- 规划：`GeneratePlan.request`；原分支历史中的用户约束同样保留。
- 讨论：本轮讨论内容；原任务用户要求保留于固定 `task.request`。
- 咨询：交接包的 `user_instruction`。专家的 `question/progress` 作为独立业务字段，不冒充用户新指令。检索查询组合专家问题、进度及原话。

讨论查询组合任务目标与当前讨论原话；规划使用规划原话。`ContextSnapshot.mode` 与输入 `context_mode` 标明入口，`selection` 记录本轮选择。

## 统一执行与预算

三个入口继续走 CoreRunner → ControlledEngine → SDK Runner/BrokerModel → Broker。受控入口检查 request 与快照 input、agent ID、入口模式一致，冻结 scope 身份和版本到执行绑定；能力加载与资料读取计入工具调用预算并审计。三个只读入口不提供私有笔记写工具，仅保留受控原始上下文分页读取。

规划和讨论继续使用 8 次模型轮次、8 次工具调用上限。咨询上限从 4 轮/3 次工具调用调整为 6 轮/5 次，时间仍为 45 秒，为发现/加载与正文读取留出空间；没有重试环或无限扩张。后续获批计划步骤也可使用相同渐进发现工具，业务工具权限仍由冻结步骤合同决定。

## 代码与验证

- `context/assembler.py`、`builder.py`、`mounts.py`：入口模式、业务合同、原话预算与冻结输入。
- `research/planning.py`、`plan_service.py`：规划组装、只读工具、冻结 memory 重验。
- `projects/service.py`：专家讨论组装、限定资料/报告/消息读取、保存前校验。
- `collaboration/information.py`：咨询组装、正文消费记录、可追溯信息交接。
- `controlled/engine.py`：入口白名单、快照绑定和只读私有工具边界。
- `tests/test_context_layer_3.py`：真实 SDK 的加载与读取流程、分支隔离、身份与 memory 范围、原话保留、撤销阻断保存，以及三个入口的真实受控绑定和 Broker 调用记录。

领域轨迹自动生成 memory/skill 候选及验证发布现已在 [第四层](agent-context-layer-4.md) 接入。测试中的账本探针只验证调用与控制顺序，不代表 PostgreSQL 持久化验收；专用数据库集成测试在缺少测试数据库配置时跳过。
