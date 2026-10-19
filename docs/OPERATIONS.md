# 受控研究与专家协作

系统设计、完整请求链路及源码函数讲解见 [HTML 项目 Wiki](wiki/index.html)（可离线打开，含流程图、搜索和源码查看），也可阅读 [Markdown 原稿](wiki/project-systems-wiki.md)。

本地研究工作区支持主研究、独立专家、协作总管及协作专家。真实执行统一经过 PostgreSQL 受控账本和原有 SDK Runner；模型、工具、摘要、恢复共用固定运行预算。交付先进入待审核状态，不自动审核、采用或发布。

CE1–CE8 当前固定范围已验收；CE8 完成固定质量对照、旧路径收敛、四入口与真实模型强杀恢复。开放研究质量仍有边界，详见验收记录。阶段边界和实际证据见 [开发计划](multi-agent-context-incremental-development-plan-2026-10-05.md)、[CE7 验收](ce7-acceptance.md)和 [CE8 当前验收记录](ce8-acceptance.md)。

## 启动

安装 Python 3.12+、PostgreSQL 和项目锁定依赖；前端使用现有 package-lock。模型配置通过环境提供，密钥不放入命令行、日志或项目文件。DATABASE_URL 指向独立 PostgreSQL 数据库，OPENAI_API_KEY 使用获授权凭据；OPENAI_BASE_URL 和 OPENAI_MODEL 选择支持 Responses 的端点及模型。

```sh
uv sync --locked
cd frontend
npm ci
npm run build
cd ..
uv run agent-research --blobs .stage4/blobs --private .controlled/private serve --durable --port 8744
```

打开 [本机研究页面](http://127.0.0.1:8744)。数据库、资料目录和私有运行目录须一同保存；重启使用相同配置。执行独占按 PostgreSQL 数据库划分：不同数据库可同时运行，同一数据库只允许一个执行进程。数据库内保存的身份与数据库 OID 决定本机进程锁，连接 URL 的别名不会绕过独占；数据库连接断开后，旧进程关闭前仍禁止接管。不同实例须使用独立资料目录、私有运行目录及监听端口。DBOS 恢复已确认的步骤；未确认模型用量保留为未知，未确认工具副作用停止等待核查。

主线和分支聊天每次回复的执行上限为 15 分钟。回复正文通过 SSE 随生成显示；资料处理和等待模型时显示进度，模型推理与工具参数不发送到页面。流式正文在完整校验及保存前是临时预览，中断不视为完整交付。刷新或短暂断网会连接原命令，不重复调用模型；已确认失败后的手动重试使用新命令，保留原消息。专家任务仍使用各自冻结的运行预算。

普通 Agent 默认不限费用与模型回合数，`RunLimits.max_turns=null`；明确指定的单次任务限制仍执行。上下文预算配置为原始窗口 200,000、有效窗口 180,000 tokens；有效窗口内包含完整模型输入、16,384 输出预留和 4,096 安全余量。占用达到 60% 时裁剪较旧工具正文，裁剪后仍达到 70% 时生成引用原文的整体工作摘要。最近 3 个工具结果优先保留；若它们本身超出硬窗口，使用真实正文分页和前进游标。空闲至少 5 分钟后，在下次继续会话的首次请求前微压缩旧工具正文，不启动空闲模型请求。预算值见 `controlled/model_projection.py`，详细规则与验证见 [预算调整记录](budget-and-travel-failure-2026-10-13.md)。窗口值是本项目指定的执行预算，实际模型名称沿用当前配置。

产品入口不再提供 `--engine` 或 `--storage` 选择，也不提供脚本演示作为产品执行。直接执行与 DBOS 后台队列都通过同一受控服务。只读查询在真正调用模型前不会要求 API 密钥。

## 操作入口

- `agent-research`：研究创建、资料上传、待确认研究计划和本机 UI/API。
- `agent-tasks`：专家任务、路由、执行、结果收据与人工审核/采用；`--coding-config` 读取操作员固定仓库和可信测试配置。
- `agent-methods`、`agent-evolution`：版本化方法与隔离候选；运行均经过受控执行。
- `agent-evals`：人工反馈、冻结案例与评测记录。
- `agent-resources`：资料和授权管理，以及历史 `run-show` 查询，不再执行模型任务。
- `agent-records --conversation ID show`、`agent-dispatch show GROUP_ID`：历史记录查询，不重跑旧协议。

各入口用 `--help` 查看当前参数。`agent-core`、旧 records 的 send/chat/demo、旧 dispatch 的 compare、旧 resources 的 run 已移除。旧资料和运行未删除、未迁移；过去使用说明保存在 [历史 README](ce8-evidence/historical-entrypoints/README-before-ce8.md)，其中命令不是现行启动方式。

## 编码与证据

编码权限绑定固定仓库基线、明确读写路径和操作员可信测试。每项任务用独立 Git worktree，冻结交付包含完整差异、提交及测试证据；集成冲突需解决并重测。不提供任意模型 shell、Docker/OS 沙箱或硬资源隔离保证。

主助手按需加载工具组，状态查询只读关联任务；目录检索、分页列举和正文读取分开。初始资料目录与每次发现结果都有上限，最新工具结果在下一轮保留真实内容。接口、恢复行为及回归证据见 [工具路由与上下文边界](tool-routing-context.md)。

每轮聊天根据用户原话检索项目技能、获准工具、已确认记忆、相关历史、资料与专家结果。用户原话单独保留，历史用户约束不会被相关性筛选丢弃；技能和工具可在执行中继续加载。配置、检索边界与代码入口见 [动态上下文组装](dynamic-context.md)。

长期记忆开轮选择与 `search_memory` 使用独立轻量 LLM side query；`OPENAI_MEMORY_MODEL` 默认 `deepseek-flash`。查询经过同一受控账本、原运行预算及 checkpoint，候选先按权限过滤，模型只选择条目，实际加载数据库原文。记忆检索不再使用 BM25；其他上下文检索沿用现有词法排名。

主助手和执行中的子 Agent 可调用 `write_memory` 主动保存长期偏好、纠错与经验。权限由服务端按用户、当前项目和固定专家身份裁定；显式用户原话经过轻量模型检查后确认，推断与经验先保存候选。写入随业务结果原子提交，当前轮继续使用冻结记忆，恢复复用持久化回执。规则见 [长期记忆写入](agent-memory-writing.md)，真实模型与 PostgreSQL 证据见 [写入验收](memory-write-evidence-2026-10-13/README.md)。

主助手可委派任务级临时 Agent 或固定身份的专精 Agent。两类子 Agent 与主助手共用上下文组装器，交接包区分必需输入和可自主检索的授权目录。身份、交接、执行边界与阶段范围见 [多 Agent 上下文第一层](agent-context-layer-1.md)。

完整上下文保存在私有资料工件中；模型接收可追溯的投影与分页引用。增量摘要保留原文引用、固定版本、纠错和未知，摘要不是独立事实审查。完整模型输入及可获得的供应商用量由账本记录，缓存包含在输入用量中。

## 验证

单元与契约测试在 `tests/`；缺失真实 PostgreSQL 环境变量时相应测试会跳过，不能算持久化通过。真实模型评测与确定性测试分别登记。CE8 固定三组对照在 `evals/ce8/`，只在评测程序中提供输入策略比较；产品没有旧后端或比较开关。详见 [每阶段验收流程](development-acceptance-process.md)。

当前固定研究任务评测在 [evals/research_quality](../evals/research_quality/README.md)：八道固定题、独立试验数据库、重复基线/方法指令 A/B、实际保存产物校验、人工语义评审和全量用量汇总。使用 `python -m evals.research_quality plan/run/report`；真实模型执行须显式 `run --live`。评测通过不改变产品审核状态，未评语义不会自动计为通过。

## Plan 模式

聊天输入框选择“计划”，输入目标后点击“生成计划”。规划只读取已授权资料；查看分析、依赖、资料版本、工具权限和预算后，点击“确认并执行”。需要澄清时先修改计划；修改产生新的提案，旧确认不可复用。切换回“执行”不会确认任何计划。

确认保存后在后台执行，沿用 ResearchPlan、Dispatcher 与受控 SDK/账本。刷新可以找回待确认或执行状态；计划卡片和资料结果栏可查看固定交付。确认不自动审核、采用或发布结果。执行节点的目标、指令和输入不可修改；后续研究使用新分支或新计划。

CLI 使用 `agent-research plan-generate FILE`、`plan-show ID`、`plan-revise FILE`、`plan-confirm ID --revision N`、`plan-reject ID --revision N`、`plan-cancel ID --revision N`。生成文件包含 research_id、origin_node_id、expected_chat_revision、request_id、request；修改文件包含 proposal_id、expected_revision、request_id、feedback。直接 `plan FILE` 和 `/api/plan` 已移除。方法工作流通过 `agent-methods propose ID` / `/api/workflow_propose` 生成待确认提案，再使用相同确认入口。

生产启动沿用 `serve --durable`。DBOS 工作流代码版本为 plan-v8，旧代码版本的工作流不自动接管。已提交结果在恢复时复用；执行完成状态未知时暂停检查，不能刷新页面重跑收费步骤。实现和验证范围见 [Plan 模式验收](plan-mode-acceptance.md)。

当前动态上下文第二层已接入主助手及临时/专精执行：skill 候选先于正文、工具按需加载，memory 按用户/Project/固定专家范围检索。详见 [第二层实现说明](agent-context-layer-2.md)与 [完整组装过程](dynamic-context.md)。

规划、专家讨论和信息咨询现已接入统一动态上下文，入口范围及加载时机见 [第三层实现说明](agent-context-layer-3.md)。

第四层已接入专精轨迹后台提炼、隔离 memory/skill 候选、独立验证和自动发布；正式检索只加载已发布领域知识。门槛、恢复与撤销规则见 [第四层实现说明](agent-context-layer-4.md)。

研究节点生成的文件现在显示在“资料与结果 → 交付文件”，支持预览、下载和固定结果版本。模型通过 `ResearchAnswer.files` 提供文件名与完整 UTF-8 内容，和报告一起原子保存；支持 HTML、Markdown、文本、CSV、JSON、SVG、CSS、JavaScript。HTML 手册可在隔离窗口中执行地图和预算交互。实现范围与验收见 [文件交付验收](file-delivery-2026-10-13.md)。

Flow 下方现在提供只读运行分析：主对话/专家历史事件回放、失败事件定位、两次运行指标与工具调用次数对比、按 Agent 的 token 汇总及模型请求耗时。缺失用量、耗时和计费金额保留未知，读取遵循当前授权。使用及范围见 [运行分析说明](run-analysis.md)。

项目专家任务现已支持独立 Agent 自动评审、最多两轮自动修订和版本绑定的协作 Flow。点击交接边查看建议，在执行节点比较修订前后的产物；无需人工填写评审意见。实现边界与闭环验收见 [自动协作说明](automatic-collaboration.md)。
