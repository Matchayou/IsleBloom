# Raven 调研：对多 Agent 与上下文工程的补充

日期：2026-10-05。范围：公开文档、固定版本源码与相关测试源码的静态阅读；未安装 Raven/EverOS/BoxLite、未运行下载的代码或测试、未调用模型、未更改生产实现。

## 1. 阅读版本与总体判断

固定仓库：`EverMind-AI/Raven@3632e6040c7038a60ec418ce39ccae185c72c19f`，提交时间 2026-10-03 16:07:09 UTC。该提交 `pyproject.toml` 声明 Raven 0.2.4、Python ≥3.12、Apache-2.0；README 标记 pre-alpha。这是源码版本记录，不代表核实了对应包的发布或生产稳定性。[项目](https://github.com/EverMind-AI/Raven/tree/3632e6040c7038a60ec418ce39ccae185c72c19f)、[包声明](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/pyproject.toml)。

重点阅读了上下文装配与 Curator、窗口压缩、任务 Charter、DAG、后端契约、沙箱、工作目录、记忆接口与插件、无进展检测、研究收尾、代码检查点。来源清单和哈希见[取证索引](raven-architecture-sources-2026-10-05.json)。下载清单不等于逐行读完全部文件；以下结论对应实际检查的入口与方法，也不构成完整安全审计。

**Raven 值得作为产品架构参考。** 相比 ShareAI 的机制教学，它提供了更具体的上下文清单、任务约束、节点结果、截断提示、专业 Agent 完成检查和诊断记录。建议吸收这些契约，继续保留本项目 SDK、DBOS、中央权威仓储以及每 Agent 独立执行的要求。整体换成 Raven 会同时引入它自己的运行循环、会话、权限、模型适配和记忆系统，并不能省掉我们的固定版本、授权撤销与证据验证。

## 2. 最值得补入当前方案的六项能力

### 2.1 上下文不仅要压缩，还要生成“本轮选取清单”

Raven 的 `ContextAssembler` 先组装身份、基础信息、记忆和技能等片段，再让依赖完整前缀的 Curator 选择历史。Curator 为消息生成 manifest，记录 ID、估算 token、相关性、保护标记、归档引用；`HistoryTrimmer` 成组处理工具调用与返回，检查结构并按预算选择历史。[装配器](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/context_engine/assembler.py)、[清单与归档](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/context_engine/curator.py)、[历史选择器](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/context_engine/history_trimmer.py)。

**本项目新增决定：每次模型请求生成可审计的上下文清单。** 在现有投影记录中保存：选入/排除的消息与片段 ID、固定版本、选取原因、原文/摘要/预览形式、截断范围、估算输入量、保护约束、摘要覆盖游标、授权世代和完整输入哈希。阅读账本回答“读过什么”，上下文清单回答“这一次真正发送了什么”，二者不能混为一谈。

先采用确定性排序、必需项保护和预算检查；需要生成摘要时才调用模型。不一开始复制 Raven 的多工具 Curator 子循环。必需权限、当前要求或引用资料不可用时显式失败，不把这些内容视为可忽略的普通片段。清单和原始请求正文受私有存储与审计权限约束，不进入公共日志。

### 2.2 让上下文预算可测量，并避免压缩本身失控

Raven 区分系统、工具、输出预留和历史预算；其运行中压缩利用最近的提供方用量读数，先缩减旧结果，再按配置摘要。`summarize_head` 区分未调用、调用失败与替换成功，拒绝空摘要或 `finish_reason=length` 的截断摘要；摘要请求自身也检查输入与输出能否共存于窗口。[Memory 策略](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/harness/memory.py)、[压缩预算](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/window/compaction.py)、[摘要执行](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/window/shrink.py#L282)。

**本项目补充：** 压缩需记录触发原因、压缩前后大小、摘要调用用量及结果状态。非空但被截断的摘要不能替换历史；摘要没有实质缩减也不能反复重试。不同提供方的缓存 token 统计口径需归一化，缓存命中不代表内容不占模型窗口。压缩、格式修复和收尾共享真实的运行预算，不能藏在额外无限调用中。主输入与摘要输入均保留估算误差余量，不照搬 Raven 的具体常数或推理强度配置。

Raven 还将稳定系统前缀与动态信息分开，避免每轮变化破坏可复用前缀。我们可保持稳定指令和工具定义的顺序，把动态检索片段放在后面；但是否获得缓存收益取决于当前提供方，必须测量，且撤权检查优先于缓存效率。[稳定前缀实现](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/context_engine/assembler.py#L46)。

### 2.3 专家的任务契约要包含“可以做什么”和“怎样算完成”

Raven 区分 roster Agent、临时 worker 职责、某次委派的 Charter 和可复用 Playbook。Charter 能携带任务 brief、停止条件、工具集合和期限，其工具限制与已有权限取交集，期限只能收紧。[Charter](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/subagent/charter.py)、[委派与图契约](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/docs-site/docs/orchestration.zh.md)。

**本项目补充：把这些字段纳入现有 task/spec 和授权输入包，不增加第二套任务模型。** 固定任务职责、允许资源、工具集合、允许写入范围、代码基线、时间/调用预算、成果 schema 和验收条件。专家类型描述基础能力，当前任务再收窄。修改职责、输入或权限必须形成可追踪版本，不能用续聊暗中改变原任务。

只在确有依赖时表达任务图：独立节点可并发，需要前置成果的节点等待固定产物。图验证拒绝循环、未知依赖和失败产物输入；已完成分支可引用复用，失败分支不拖掉无关结果。调度和恢复仍交给 DBOS，不引入 Raven 的 DAG 运行时或新的工作流 DSL。

### 2.4 区分“运行结束”“有成果”“验收通过”

Raven 的节点 verdict 会结合输出、任务和可用轨迹判断是否完成，后端契约也将“没有最终回答”表示为错误，而不是成功文本。[后端契约](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/contracts/subagent_backend.py)、[节点 verdict](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/subagent/dag_verdict.py)。

**本项目补充：** 持久事件明确区分运行终态、产物保存状态、验收状态与正式采用状态。研究成果检查固定来源、引用和结论支持；代码成果检查冻结快照、任务相关改动及测试结果。模型评审提供质量意见，不能代替确定性权限或 schema 检查；评审不可用不能记成已通过。

Raven 的 `clamp_output` 将给主助手的短回复与完整 `out.md` 分开，明确提示截断，并保存完整结果。这很适合我们的跨专家交付：简报携带完整产物 ID/hash、完整性与截断状态，主助手可以按需回查；界面显示、实际保存及下一轮读取不能各自得到不一致的“完整报告”。[结果截断实现](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/subagent/backends/base.py#L99)。

### 2.5 在硬额度之外识别“无新增信息的循环”

Raven `NoProgressGuard` 基于工具、参数与结果识别重复，并考虑其他调用是否产生新内容；从提示到拒绝、最终结束调查分级处理。其实现也说明稳定轮询和无意义重复有时具有相同轨迹，不能凭字节相同准确区分全部情况。[无进展检测](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/loop/no_progress.py)。

**本项目补充：** 用阅读账本和响应哈希识别“相同授权版本、相同请求、相同结果、期间无新证据”的重复。先提示现有结果位置，持续无进展才提前收尾。资料分页、纠错复核、新版本和合法重读不算无进展。等待专家或外部任务优先由调度器挂起/事件唤醒，不能把正常等待当作研究失败。检测用于结束空转，不代替幂等、预算和权限检查，也不宣称解决了语义上的全部重复。

### 2.6 为不同专家增加少量领域检查，保留统一执行底座

Raven 将 Memory、Planning、Capability、Action 分开，研究和编码产品再通过领域逻辑提供额外检查。默认 Planning 本身只是传递消息，不一定增加规划模型；四个模块名称也不意味着四个新 Agent。[Memory](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/harness/memory.py)、[Planning](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/harness/planning.py)、[Capability](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/harness/capability.py)。

我们的模块边界可对应为：上下文投影、任务/依赖、工具授权、执行/验收。在现有 SDK 接口与服务中保持职责清晰，不复制整套可插拔策略框架。研究专家重点检查证据与不确定性；编码专家重点检查代码快照、修改范围与测试。增量扩展可以是明确的任务类型检查函数，不必建立运行时动态生成 Python 策略的平台。

## 3. 记忆与知识库：借鉴分层，暂不直接引入 EverOS

Raven 的记忆接口区分用户轨道和 Agent 经验轨道；上下文 Memory 片段按当前问题召回用户记忆，技能检索另行处理。子 Agent 的记忆记录显式携带所有者身份，缺少身份时不写入共享默认轨道。可借鉴“身份先于存储”“用户约束、工作状态、可复用经验分开”的原则。[记忆接口](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/contracts/memory.py)、[记忆片段](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/context_engine/segments/memory.py)、[子 Agent 记忆记录](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/subagent_memory.py)。

但 user_id/agent_id 过滤不是独立数据库或 OS 沙箱，跨会话经验也不等于本研究的权威证据。本项目继续每研究/会话独占存储，来源撤权时处理派生记忆；已确认的用户偏好与研究事实分离，不自动将所有专家轨迹写进共享长期记忆服务。

当前提交的 EverOS 适配实际位于 `plugins-dist/everos-memory`，插件包声明 1.4.0，固定依赖 `everos[multimodal]==1.4.1`，并说明使用非公开内部 API。仓库 `docs/memory-plugin-architecture.md` 部分历史段落仍描述另一种目录/分发方式，不能只按该文判断当前依赖。[当前插件声明](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/plugins-dist/everos-memory/pyproject.toml)。

因此本轮只借鉴记忆契约，暂不增加 EverOS、LanceDB 或自动经验抽取。若以后明确需要跨任务经验检索，再单独评估召回效果、中文处理、数据隔离、撤权删除和部署成本；本次未审计 EverOS 底层全部实现。

## 4. 沙箱：BoxLite 值得纳入比较，Raven 的接法不能直接满足全部要求

Raven 提供可选 `BoxliteExecutor`，该提交固定 `boxlite==0.9.5`。通过它派发的 shell 和 stdio MCP 进程在微虚拟机内执行；文件工具仍直接访问宿主路径，工作目录挂载到 VM。默认 backend 为 `none`，启用 auto/boxlite 后依赖缺失会报错，不静默回宿主。[沙箱说明](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/docs-site/docs/sandbox.zh.md)、[执行器工厂](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/sandbox/__init__.py)、[VM 挂载实现](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/sandbox/boxlite_executor.py)。

**覆盖范围必须按调用路径判断。** 项目有微虚拟机能力，不意味着所有 Agent 循环、文件工具、私有存储及第三方后端已经逐专家隔离。本项目要求整个专家本地执行及私有数据处于边界内；主助手同样遵守。这个要求保持不变。

BoxLite 官方提供独立内核的微虚拟机、OCI 镜像与嵌入式无 daemon 运行方式；这使它成为 Docker 的有意义候选，尤其对本地应用和更强内核隔离。平台和虚拟化条件也需要核实，例如 macOS Apple Silicon、Linux KVM；本次没有做本机启动、挂载或性能实测。[BoxLite 官方仓库](https://github.com/boxlite-ai/boxlite)。

| 事项 | Docker 既定候选 | BoxLite 新候选 |
| --- | --- | --- |
| 基本边界 | 容器，通常共享所在系统的内核 | 微虚拟机，独立内核 |
| 运维形式 | 容器运行时/daemon | 可嵌入应用的运行库 |
| 两者仍需完成 | 私有卷、最小挂载、网络限制、密钥隔离、取消、恢复、worktree/Git 受控访问 | 同左，VM 本身不解决业务授权 |
| 本轮决定 | 仍是统一方案的实施基线，尚未落地 | 纳入实施前的定向验证，不立即安装或替换 |

实施阶段 1 前，以同一个单专家闭环比较：SDK 循环整体进入边界、独占存储重启保持、绑定通信、无外网、停止子进程、受控 worktree/Git、资源限制和故障恢复。通过相同验收后选定一个运行时；不为了“可切换”先实现两套后端，也不把容器不可用等同于 VM 自动可用。

## 5. worktree 与检查点：Raven 没有替我们解决的部分

Raven 的工作目录与 Agent home 分开，保护记忆/技能目录不被当成代码目录；DAG 文档明确并行节点不会自动获得独立 worktree 或文件排他锁。这支持我们保留“每编码任务 worktree + 私有状态卷”的区分。[工作目录实现](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/workdir.py)、[DAG 工作区边界](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/docs-site/docs/orchestration.zh.md)。

其 shadow-git 检查点用独立 Git 元数据保存工作目录的回合快照，源码明确它只覆盖文件状态，不保存对话执行状态。这是代码撤销的参考，不是 DBOS 或模型回合检查点的替代。[文件检查点](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/loop/checkpoint.py)。

我们继续用任务 worktree 和固定代码产物记录代码状态，用私有运行快照加 DBOS 记录执行状态；不为已有 Git 任务再增加一套 shadow-git 历史。保存代码时排除凭据及无关产物；取消或崩溃不丢未提交代码，恢复仍绑定相同授权和代码基线。

## 6. 哪些实现不照搬

以下边界来自固定版本的具体代码，不用于推断 Raven 整体不可用：

| 实现事实 | 本项目需要的规则 |
| --- | --- |
| `ContextAssembler` 捕获片段构造异常并记 degraded；Curator 也有确定性后备选择 | 可选经验缺失可以显式跳过；权限、当前要求、必要证据或强制约束缺失必须阻止相应执行。不能把所有片段统一视为可降级 |
| `emergency_shrink` 以无回查 ID 的通用占位替换旧工具正文；归档读取按整条消息计预算 | 我们移出正文前确认持久保存，返回受控引用、实际范围和续页游标，不把空结果当完整读取 |
| `CuratorArchiveStore.retrieve` 中下一条消息超预算时直接 break，`truncated` 仅按已返回 tokens 判断 | 单条大于预算也要能继续分段读；部分返回且剩余不足时截断标志必须为真。这个静态边界应加入分页回归 |
| `dag_verdict.judge` 超时、异常或无有效 verdict 时返回 accomplished=True | 必需验收失败保持 pending/failed，不把评审缺席转换成通过 |
| 研究 `ForcedFinalizeGate` 的 salvage 提示要求选定候选，并限制“未确定”措辞 | 只借鉴有界收尾；保留证据不足时的未解结论，禁止为了基准得分强制确定答案 |
| `load_config` 对坏 JSON 警告后使用默认配置，默认沙箱 none | 本项目隔离配置无效就拒绝启动；不创建无沙箱专家 |
| DAG 历史可显示 interrupted，后台运行不等于进程结束后仍可续跑 | 保留 DBOS；持久结果读取和实际恢复执行分别验收 |

补充源码：[归档读取](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/context_engine/curator.py#L269)、[运行中缩减](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/agent/window/shrink.py#L80)、[研究收尾](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/agents/raven-research/plugins/research-flow/research_flow/gates/finalize.py)、[配置加载](https://github.com/EverMind-AI/Raven/blob/3632e6040c7038a60ec418ce39ccae185c72c19f/raven/config/loader.py#L445)。

## 7. 自进化与异构 Agent 的取舍

Raven 统一调用内置、ACP、CLI 和 OpenAI 兼容后端，体现了“专家职责”与“实际执行后端”的区别。不过后端支持的会话续接、工具收窄、取消和进度能力不同，统一接口不能使它们自动具备相同保证。当前本项目不需要同时接入四类后端；先完成现有 SDK 路径。如果日后接入外部编码 Agent，必须逐项确认能力，不能对不支持的硬限制仅给提示后继续执行。

Evolver 与实验性 Curator 展示了用轨迹诊断、评测和候选验证改善策略的方向。本项目现阶段借鉴离线评测纪律：固定任务、模型、输入版本和预算；记录失败；候选经过回归才采用。不采用在线自动改权限、改执行策略代码或自动安装技能，也不复制一套研究型自进化框架。项目 README 的性能和案例属于作者报告，本轮没有独立复现或据此推断我们的收益。

## 8. 对统一技术选型的实际修改

维持：OpenAI Agents SDK、DBOS、中央 PostgreSQL、每 Agent 私有工作库、独立执行环境、编码 worktree、受控交接与证据校验。

增加或明确：

1. 上下文投影记录增加每请求选取清单与排除原因。
2. task/spec 明确任务职责、能力交集与完成条件；有依赖的任务才表达图。
3. 短交付携带完整产物引用、截断及完整性状态。
4. 运行结束、成果保存、验收、正式采用分开记录。
5. 账本支持有边界的无进展检测；正常等待由持久事件驱动。
6. 压缩记录输入/输出大小和实际调用状态，拒绝被截断的摘要，校准缓存 token 口径。
7. BoxLite 纳入阶段 1 前的单一运行时选择验证，暂不改变 Docker 基线。

新增验收覆盖：保护项缺失、缓存用量重复计数、空/截断摘要、单条超大归档、界面与完整产物不一致、正常等待误判空转、评审超时伪成功、任务声明扩权、坏隔离配置及跨专家文件访问。参考 Raven 测试源码中的结构保护、摘要预算和稳定前缀测试，但本轮未运行其测试，不将“仓库有测试”表述为已通过。

统一决策入口：[多 Agent 与上下文工程完整选型](multi-agent-context-architecture-2026-10-05.md)。
