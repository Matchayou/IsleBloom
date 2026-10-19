# Stage 3 资料接入、作用域与只读挂载验收

日期：2026-09-20。依据最新《增量开发架构与阶段验收》Stage 3 全节（含责任、挂载、撤权附则）、源码阅读索引、产品设计 D/E 节和 development-acceptance-process。

**代码、CLI/API、真实文件/OCR、受控外网接入、146 项 C0–C3 回归及本阶段 DeepSeek-V4.1-Flash 真实模型与跨进程 CLI 验收全部通过。Stage 3 已完成。未推进 Stage 4。**

## 实现与复用调查

- 读取固定 SDK context、OpenViking assembler models/budget、Letta `resolveScopedMemoryDir`、PaperQA docs/content_hash、Deep Agents subagents 隔离实现。借鉴显式范围、定位条目、预算、内容身份和独立子输入；未复制 AGPL OpenViking 代码，也未引入上述完整运行框架。
- 参考 GPT Researcher [混合资料接入](https://docs.gptr.dev/docs/gpt-researcher/context/local-docs)、[Deep Agents 文件后端](https://docs.langchain.com/oss/python/deepagents/backends)。本地 GPT Researcher 仅有摘选/README，不冒充完整可运行依赖。上传和网络通过同一 ResourceService 登记，工具使用独立作用域闭包，不共享文件目录给模型。
- 查阅 Docling 官方 NativePdfFormatOption、DocumentConverter、OCR 文档，再核对实际安装的公开类型/源码。完整依赖 **docling==2.129.0**；以实际中文 HTML 表格、真实 PDF、PNG 验证。原生 PDF 解析使用 NativePdfPipeline，扫描页明确使用本机 Tesseract，不下载布局模型、不静默换解析器。Docling 的标准布局/OCR 大模型管线没有运行，不能声称已经验收它。
- 显式直接依赖固定 httpx 0.28.1、BeautifulSoup 4.15.0、Pillow 12.3.0、pypdfium2 5.13.0，均来自已安装依赖能力；完整传递依赖锁在 uv.lock。原 openai-agents 0.22.2、SQLAlchemy 2.0.43、psycopg 3.2.10 保留。
- Context7 技能已阅读，当前无对应 callable 工具；使用固定本地参考与官方文档，不声称调用了 Context7。
- SQL 仓库沿用 C1 单执行进程数据库连接和短事务；resources/context 分别拥有自己的表，通过公开仓库接口互读。原始文件进入 FileBlobStore，SHA-256、原子落盘、读取校验；哈希不是授权凭据。
- CoreRunner、contracts、SDK adapter、Dispatcher 算法 SHA-256 与开始时一致。C1 仅新增通用 `validate_commit` 组合钩子，每次保存重试在提交前执行；默认恒等，不引入作用域类型。ScopedExecutor/ScopeExecutionGate 包住原公开执行端口。

## 逐项符合性矩阵

| Stage 3 要求 | 实现证据 | 测试/交互证据 | 结果 |
|---|---|---|---|
| 资源、不可变快照、接入记录、解析状态、原文件 | resources/snapshots.py、service.py、ports.py；filesystem adapter | dedup_name_url_versions、real_corrupt_file；真实 CLI upload/show | 通过 |
| 每次接入独立授权，哈希去重不扩大权限 | 独立 ResourceIngestion；scope.grants 引用完整 ResourceRef | A/B 相同哈希复用解析但互读拒绝 | 通过 |
| 同名不同内容、URL 更新固定旧版本 | snapshot ID 不覆盖；parse ID 固定 | 上传更新、真实 HTTP V1→V2；旧 ref 仍读 V1 | 通过 |
| 缓存匹配内容/媒体/解析器与 OCR 配置 | parser_key 含库版本、OCR 版本/语言/训练数据哈希、页数限制 | parser_configuration_cache_isolation；跨 scope 缓存读检查 | 通过 |
| pending/processing/ready/partial/failed 可观察 | observe_parse + 数据库状态；CLI stderr 输出阶段 | pending→processing→ocr:page:1→partial 断言 | 通过 |
| 真实扫描件进入 OCR、失败保留原件 | DocumentParser、Tesseract、真实 PNG/PDF 夹具 | 中文“收入/12345”识别；损坏 PDF failed；空白页 missing | 通过 |
| 部分成功仅暴露可定位成功部分 | Fragment.location；ParseIssue；read 只返回 fragments | 两页扫描 PDF 第一页成功、第二页缺失 | 通过 |
| 正文/表格/位置 | Docling HTML/CSV/DOCX、PDF 原生页/bbox；OCR 页码 | 中文 HTML 表格保留 100 万元；真实 PDF/OCR 定位 | 通过；复杂 PDF 表格结构未承诺 |
| 不猜公司/期间，不把文件名当事实 | name 仅展示；无自动财务属性补齐 | 通用资料无公司必填字段；不从 A.txt 生成属性 | 通过；未实现财务属性提取器 |
| RunScope 由程序授权，工具不能选择 scope | scopes.py、ScopedTool、NetworkTools；Pydantic extra=forbid | forged_scope、伪造资源 ID、空范围、跨 owner 拒绝 | 通过 |
| 父历史污染与同 conversation 范围切换 | Builder 每次生成单个干净输入，不加载基础历史 | clean_conversation；原输入含 B，新输入只含 A | 通过 |
| 混合摘要整份拒绝、派生依赖闭包 | SourceSnapshot.dependencies；ScopeService.check/select | mixed_parent_summary；搜索摘要→note 依赖检查 | 通过 |
| 父只读、子便签独立、写入归属固定 | ContextMount read_only；write_note 新建资源，不暴露改写路径 | read_tool_and_write_ownership；旧 blob 不变、另一 scope 无权读 note | 通过 |
| 挂载固定版本、装配清单可查 | ContextSnapshot 固定输入/原 RunScope/refs；context_mounts | SQL 读回；新 CLI run-show；旧 source 版本不变 | 通过 |
| 每次读取、创建挂载、结果发布检查权限 | mount/read、ScopeExecutionGate、validate_commit | revoke_fixed_mount、inflight_revoke、save_retry_revalidates | 通过 |
| 已带入旧资料的运行取消、迟到结果不得发布 | scope 版本变更取消活动 task；提交前失败结果无正文 | 抑制取消的引擎仍不能发布 LATE_SECRET | 通过 |
| 已保存结果不是永久访问许可 | ScopedExecutor 在幂等重放前检查，CLI run-show 检查当前版本 | saved_result_replay_after_revoke_is_denied | 通过 |
| 搜索摘要先登记后检查再返回，不冒充正文 | Discovery kind；允许精确 URL；发现记录不能 mount | discovery_filter_before_return；B 标题/摘要未进入返回 | 通过 |
| 受控联网补充，默认只进入发起 scope | HttpSources、NetworkTools；自动接入限原 URL 授权 | live-web-cli 9 个独立 CLI；真实 Apple 原文与哈希 | 通过 |
| 联网途中范围变化也不能返回旧信息 | await 后复核 scope/version/actual_url | discovery_scope_change_while_network_pending | 通过 |
| 网页更新、重定向不扩权 | 限确切 URL、显式拒绝未批准跳转、5 MB 上限 | 本地真实 HTTP 服务 V1/V2 与 redirect 测试 | 通过 |
| 并行任务 scope 独立、C2 无算法变化 | composition 为每个 agent 绑定不可变 scope；Dispatcher 原样组合 | c2_parallel_scopes 同步屏障、两个输入无交叉标记 | 通过 |
| C0/C1/C2 全回归与 SDK 实际映射 | 原测试保留；新 SDK QueueModel 驱动真实 Runner | stage-3-tests.txt，含 PG/重启/SIGKILL | 通过；替身不等于真实模型 |
| 关闭作用域则 scoped 入口停用 | build_scoped_execution 拒绝 None；scope.enabled 检查 | disable 测试；原 agent-core/records/dispatch 回归 | 通过 |
| 本阶段真实模型语义与工具使用 | evals/scope_isolation/run.py 默认 openai | stage-3-real-eval.json：passed；真实 read_source、A 隔离、报告期语义与重启查询 | 通过 |
| 不实现 Stage 4/5 | 无页面、研究树、节点结果版本或结论证据关系 | 新代码目录和依赖方向检查 | 通过 |

## 最小链路与实际证据

1. 创建 scope，上传 A/B 真实文本文件，选择 A 并保留一个明确获准的 Apple 官方财报 URL。
2. `search` 从批准 URL 目录作有界查询，返回 discovery_only；`fetch` 通过真实 HTTP 保存原文、实际地址、时间、哈希与解析版本。
3. `show` 展示接入、解析、可挂载状态。`run` 通过 Builder→ScopedExecutor→原 Core/SDK→C1 提交；每次 CLI 退出，`run-show` 用新进程读取完全相同结果。
4. `docs/stage-3-live-web-cli.json` 保存逐条 argv、退出码、stdout/stderr、输入清单和结果，状态 `infrastructure_passed`。这是**真实网络＋真实文件＋脚本引擎**，不标记为真实模型通过。
5. `docs/stage-3-ocr-cli.json` 保存额外 5 个独立 CLI 进程：真实中文扫描 ready、双页 PDF partial、损坏 PDF failed，以及新进程查询；stderr 实际显示 OCR 处理阶段。
6. `TEST_DATABASE_URL=postgresql+psycopg://stage1@127.0.0.1:55439/stage3 .venv/bin/pytest -v` 输出在 `stage-3-tests.txt`；最终 **146 passed in 15.29s，退出 0，无跳过**。原 C0–C2 105 项与新增 41 项一并运行。使用 Stage 3 专用库，不删改 Stage 1 postgres 库与 Stage 2 stage2 库。
7. `evals/scope_isolation/run.py` 默认真实模型验收，缺凭据退出 2。首次因缺凭据为 `not_run`；用户随后授权本轮凭据，现报告 `passed`。没有复用前序 DeepSeek 证据，也没有读取旧密钥或 Codex 登录令牌。
8. 中文 OCR 官方训练数据 SHA-256：`chi_sim=a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730`；实际英文数据 `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`。脚本 setup_stage3_ocr.py 校验中文固定哈希，不静默接受新内容。

## Gap 修复记录

- 最初只收窄本地资料，联网 URL 仍可能带回 B；select 现在默认清空联网权限，显式选中的 URL 必须是已有授权子集。
- 搜索摘要最初未进入派生摘要依赖；现在获准 discovery 也登记独立授权，write_note 自动记录全部当前来源，混合来源不能由模型自行“声明已忘记”。被拒绝候选只登记，不授权、不返回。
- 在途撤权后，抗取消引擎仍可能返回成功；添加执行后与 C1 每次重试保存前的检查，成功预览转换为无正文的 scope_invalidated 失败结果。
- 审查发现已提交 run 的幂等重放会绕过内层执行检查；新增外层 ScopedExecutor，撤权后旧结果重放和保存查询均拒绝。
- 原配置缓存测试假设 PG 完全空库，第二轮碰到前轮同配置缓存；改用每个测试唯一内容证明配置隔离，不删除数据库掩盖错误。
- 中文测试最初使用不存在的 PingFang 常规路径；使用实际可用字体生成并保存真实 PNG/PDF 夹具，测试阶段不依赖字体路径。
- 解析进度最初只写状态而 CLI 最后才输出；增加公开观察回调，stderr 即时显示 pending/processing/OCR/终态，回调失败不改变解析结果。
- SDK 工具可能在子 asyncio task 中执行；运行归属改用 ContextVar 传播，实际 SDK write_note 测试证明 ResourceIngestion.run_id 正确。
- ContextSnapshot 进一步固定原 RunScope 的工具、预算、URL 和授权清单；后续范围变化不覆盖历史装配策略，读取仍以当前授权优先。

## 当前边界

- 本轮真实模型验收已补齐，结果见下节。固定小样本不代表广泛模型质量保证。
- 网络搜索是对用户批准的精确 URL 目录进行有界内容查询，不是通用搜索引擎。原授权之外的跳转拒绝；联网语义相关性不能单靠关键字保证，资料访问权限与主题相关性分开。
- 解析支持 UTF-8 文本、Markdown、HTML、CSV、DOCX、PDF、PNG/JPEG。PDF 原生模式保留文本 cell/page/bbox；扫描 OCR 保留页码。复杂 PDF 表格、图表理解及财务属性提取未宣称已实现。空白/不可读页作为缺失，而非空文本成功。
- 自动联网接入/便签只能增加既有 URL/当前材料授权之内的资源关系，同一策略版本不变；初始输入及 refs 固定，运行内接入记录通过 run_id 关联。用户选择、撤权、禁用均产生新版本并取消旧运行。
- 范围 API、仓库与 raw BlobStore 是受信应用接口，不作为任意模型工具开放。CLI 属于同 OS 用户的个人研究应用，不是多租户身份认证服务器。
- 沿用 C1 同主机同 OS 用户单执行进程和协作取消边界。解析同步执行，单文件上限由联网 5 MB/PDF 100 页限制；不提供后台解析队列或执行恢复。原始文件可能在 DB 失败时成为孤立 blob，但不会因此获得权限。
- 有意不继承基础 conversation 历史；scope 修改后须新 RunRequest/新 ContextSnapshot。旧记录保留供受信审计，受限研究入口不返回当前已禁资料。

## 收尾状态

最终完整回归：146 passed in 15.29s、退出 0，无跳过。最终外网 CLI 退出 0、9 个独立进程全部成功；额外 OCR CLI 5 个进程全部成功，分别明确显示 ready/partial/failed。`uv sync --frozen --offline` 成功；compileall 成功；Core/contracts/SDK adapter/Dispatcher 原始 SHA 校验全部一致，见 stage-3-core-integrity.txt。本轮使用既有未提交工作区，没有空 worktree、reset、迁移、删除前序成果或创建提交。

测试实例已通过 `pg_ctl -D /tmp/agent-stage1-pg stop -m fast` 停止，数据和原始 BlobStore 保留。下次运行按 README 启动实例。用户补充授权凭据后，真实验收已完成；Stage 3 无未解决验收项，范围不会自动推进 Stage 4。


## 本轮真实模型验收补齐

用户在本任务提供凭据后，经无回显终端输入注入临时评测进程及其 CLI 子进程。使用 `https://api.deepseek.com`、API 模型 `deepseek-flash`（沿用已确认的 DeepSeek-V4.1-Flash 标识）、openai-agents 0.22.2 默认 Responses 接口；未改协议、产品代码、Core 或提示词。

- `evals/scope_isolation/run.py` 实际退出 0，报告 `passed`。9 个独立 CLI 进程全部退出 0：创建 scope、上传 A/B、选择 A、真实联网发现与正文接入、查看状态、真实模型执行、退出后新进程查询。
- run_id：`d2942d67-7954-479a-ac70-28016af9e61a`。两次 `read_source` 均成功；SDK 报告 2 次模型请求，输入 14546 / 输出 1056 tokens，usage.complete=true。
- 人工对照本次保存的官方原文 `#/texts/25`、`#/texts/28`、`#/texts/30`：正确回答季度收入 954 亿美元（$95.4 billion），报告期为 2025 财年第二季度、截至 2025-03-29；明确发布日期为 2025-05-01，没有把发布日期当成报告期。未读取的财务报表 PDF 明细标为未知。
- A_SCOPE_742 正确保留；B_SCOPE_953 和 77123 均未进入该次 PreparedInput 或回答。上传 A 仅提供身份，不含财务数字，模型明确说明数字来自获准官方正文。此测试验证一个实际样本，不承诺模型永不提及预训练中的其他公司。
- 新进程 `run-show` 结果与原模型结果精确一致，确认保存后可查。结构化证据及完整 CLI 交互分别保存到 `stage-3-real-eval.json`、`stage-3-real-cli.txt`。
- 随后重新执行完整 C0–C3 回归，146 项通过、无跳过，见最新 `stage-3-tests.txt`。未发现需修改产品实现的新 gap。
- 凭据未写入文件、脚本、报告或持久配置；临时评测进程和子进程均已退出。数据库在验收收尾停止，既有资料与结果保留。

本阶段设计符合性、必要回归与真实交互全部通过，Stage 3 完成；不自动推进 Stage 4。
