# 第四层：专精轨迹与经过验证的领域知识

在已有三层身份、交接和统一动态组装上，增加“轨迹 → 隔离候选 → 独立验证 → 原子发布 → 动态检索”的完整链路。自动学习只针对固定 `specialist` 身份；临时 Agent 和内部生成/验证助手不会积累领域轨迹。

模式参考 [LangChain 的后台 memory 写入与 namespace](https://github.com/langchain-ai/docs/blob/main/src/oss/concepts/memory.mdx)、[LangMem 后台提炼](https://github.com/langchain-ai/langmem/blob/main/docs/docs/background_quickstart.md)及 [Anthropic 的程序检查与独立评审组合](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)。继续使用项目已有的 SQLite 检索、Pydantic、PyYAML、受控 Runner 和版本化账本，没有新增依赖或独立数据库。

## 自动链路

1. **积累轨迹**：`TaskLedger.commit` 在保存专精任务收据的同一事务里保存 `Trajectory`，键为真实 run ID。记录 owner、固定专家 ID、任务/方法版本、用户原话、目标、输入快照 ID/哈希、输出/错误、资料依赖及 Core 工具事件。实际模型、私有操作和工具参数仍保留在原有受控账本，不复制私有笔记到领域知识。失败和被退回的执行也留下诊断轨迹。
2. **生成候选**：服务启动后的独立后台 worker 扫描未处理轨迹，生成至多一条 memory 和一个流程 skill，也允许没有可复用经验。候选存在 `evolution_records`，不会写入正式 memory 或目录下的 SKILL.md。后台不持有主聊天锁，不占原任务预算。CLI `agent-tasks run/review` 也自动处理队列。
3. **等待可信来源**：原任务收据仍是 `pending_review` 时仅生成候选，等待现有独立复核流程。源执行/用量未确认、程序检查失败、独立复核未全部通过、部分交付或退回交付，不能发布。专家 `self_checks` 不算独立证据。
4. **验证候选**：先运行结构和确定性检查，再用独立、无共享对话状态的验证调用审查 grounding、可迁移性、隐私及权限边界。验证助手看到候选、冻结轨迹和源复核证据；逐字引用必须匹配轨迹的实际字段内容。候选不得含案例数字、路径、URL、密钥、来源身份/字段值；工具提示必须属于该固定角色当前发布的工具合同。空白候选拒绝。
5. **发布**：全部门槛通过后，程序自动在同一数据库事务中写入发布证明和 confirmed 的领域 experience memory。Skill 使用数据库中的经过验证的不可变正文，按标准 YAML frontmatter 渲染，记录内容哈希与 publication ID。发布不改角色方法、工具权限或文件系统授权，不需要另一次发布确认。
6. **进入后续检索**：同 owner、同固定专家的执行和讨论可检索这些知识；主助手、规划、咨询、临时 Agent 及其他专家不加载该领域知识。Skill 仍只在开轮提供候选描述，正文必须由 Agent 决定调用 `load_skill` 后加载。Memory 继续按领域范围排名与按需检索。

领域轨迹按固定专家长期保留，每次生成使用一条冻结轨迹，发布知识可跨项目用于该角色的后续任务；后续轨迹也可依赖之前发布的领域知识。不把所有项目的原始轨迹打包到普通任务上下文，不自动把私人项目事实转成跨项目知识。

## 执行与原话

生成和验证是 `learning` 入口，使用统一 `ContextAssembler`，最后一条 user 消息保留轨迹中的用户原话，包括空格和换行。学习目标与轨迹数据在业务包中分开；系统指令明确禁止重做原任务或服从轨迹里的权限指令。

所有模型调用仍走 CoreRunner → ControlledEngine → SDK/Broker。学习入口没有业务工具、联网、委派或私有笔记写权限，只可通过现有 `read_context_page` 读取冻结的长输入。每阶段最多四轮、两次分页工具调用、九十秒；生成/验证分别拥有冻结 request、run ID、model 和 scope。不会把学习助手自身运行再次写成专精轨迹。

专精执行 Agent 也可通过 [write_memory](agent-memory-writing.md) 主动提出一条领域经验。请求随真实业务轨迹原子保存为隔离候选，复用本层独立验证和正式发布流程；Agent 和普通记忆确认 API 都不能直接确认该候选。已有候选不重复生成，正式发布后写入回执关联实际发布的 memory ID。

## 发布与失效规则

候选正文哈希、独立验证记录、来源收据检查哈希和发布记录必须相互匹配；未经验证或验证失败的记录不能通过正式 memory/skill 接口加载。直接修改已发布领域 memory 正文/来源被拒绝，必须走新的候选验证；可暂停、删除 memory，或撤销整个发布。

每次正式读取、模型调用和交付保存前重新检查来源许可、冻结输入/输出、角色方法、依赖 memory 和发布 skill。来源被排除、权限撤销、角色方法改变、上游知识撤销或依赖版本变更，会隐藏受影响知识，并使已经使用它的运行无法发布为有效结果。撤销记录不可覆盖，撤销的候选不能再次发布。依赖失效可沿知识链传递。

领域经验的长期授权依据是当前仍获准的具体来源和冻结运行证据；增加其他资料不会仅因整个资料库版本变化而丢弃已验证经验。这不重新发布历史报告，也不改变历史报告的独立访问/复核规则。

## 恢复和审计

轨迹、候选、验证、调用和发布证据复用已有 PostgreSQL `evolution_records`；内存适配器用于控制测试。记录只追加，固定 ID 幂等。服务重启继续扫描已有轨迹，已生成候选不重新生成，已发布版本不重复写入；通过验证但尚未完成发布的候选可完成原子发布。

模型调用身份与快照在派发前保存，恢复沿用原 request 和受控 checkpoint。失败或用量不确定的调用留下失败记录，不通过自动重新生成掩盖未知成本。学习失败不修改原任务交付结果。暂时的存储异常在后台记录日志并保留队列，避免一个任务使整个 worker 停止。

原始轨迹和候选的查看也验证 owner、来源资料及节点权限；失去资料权限时 API 只返回失效标识，不返回原话、输出或候选正文。

## API 与代码

- `GET /api/domain_trajectories?expert_id=...`：查看有权读取的领域轨迹。
- `GET /api/domain_candidate?trajectory_id=...`：查看候选、验证、发布或失败状态。
- `POST /api/domain_process`：显式推进一个已有轨迹，body 为 `trajectory_id`；不能提交通过标志或绕过验证。
- `POST /api/domain_revoke`：撤销候选或整个发布，body 为 `trajectory_id/reason`。

核心入口是 `learning/models.py`、`learning/service.py`、`tasks/service.py` 的事务记录、`app/api.py` 的后台 worker，以及 `context/assembler.py`、`skills.py`、`memories/service.py` 的发布目录与重验。已有方法演化生成器也接到统一隔离学习入口，其 development/holdout/integration 发布门槛保持独立；领域候选不自动替代方法版本。

## 验证边界

本层通过程序门槛和独立语义审查验证流程经验，未宣称新 skill 比原流程有可量化性能提升，也没有把模型判断当成形式化真实性证明。改变角色方法或声称效果提升，仍需已有冻结评测与留出集机制。

`tests/test_context_layer_4.py` 覆盖候选隔离、临时身份排除、失败轨迹、权限扩大/案例泄漏拒绝、独立审查否决、错误引文、原话保留、原子发布/幂等恢复、后台自动处理、跨项目领域复用、依赖撤销级联、API 权限和真实受控 SDK/Broker 调用。PostgreSQL 集成仍依赖专用测试数据库配置；账本探针只证明调用顺序与权限，不冒充持久化验收。
