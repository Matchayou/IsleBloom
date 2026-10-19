> CE1 当前执行方案（2026-10-07）：[受控工具执行与逻辑数据隔离](docs/ce1-controlled-execution-decision-2026-10-07.md)，已移除 Docker 要求。入口、保留的验收项及实际结果见 [CE1 验收](docs/ce1-acceptance.md)。

# 研究工作台 · Stage 10 专家独立进化

## 主助手自主联网

新建主对话及其分支已注册 `search_web` 和 `fetch_web`：模型遇到时效性问题会搜索公开网页、抓取正文，再以来源链接和结构化原文引用回答。沿用现有 TinyFish Search/Fetch 接口，运行环境需要 `TINYFISH_API_KEY`，模型配置从 `.env` 注入。

抓取正文按本次运行保存为不可变资料版本，随回答进入证据记录与后续对话。来源目录及资料读取工具同时提供该版本记录的请求 URL、实际 URL 和抓取时间；来源链接无需在正文中出现。仅在时效性相关或用户问及时简短说明是否重新联网。搜索摘要不能充当正文证据；跨分支读取、撤权和引用原文校验仍生效。长网页在追问时按需检索，不整页塞入模型上下文。最新验收入口为 [8771 工作台](http://127.0.0.1:8771/)，旧的 8770 服务保留原有内存对话。

```sh
uv run --env-file .env agent-research --storage memory --engine openai --blobs .stage4/url-live-preview-blobs serve --port 8771
```

此预览采用内存存储，重启会清空研究记录。正式保存数据应使用现有 PostgreSQL 存储。真实验收脚本：`scripts/verify_workspace_web_live.py`。

Stage 10 已接入已核验反馈驱动的专家方法候选、固定开发与留出案例预览、下游端到端检查、成本/质量/范围门槛、用户确认发布和 CAS 回退。候选不影响 active 或在途任务，失败候选保留。合成夹具和 PostgreSQL 全量回归通过；真实 DeepSeek 在七份虚构材料上的当前方法均正确，生成器协议亦通过，但没有可归因的方法错误，因此没有正式真实候选或用户发布。真实候选语义收益与账单成本仍待后续案例。详情见 [Stage 10 验收记录](docs/stage10-acceptance.md)与[真实模型复核](docs/stage10-live-review.md)。命令入口为 `agent-evolution`，页面在“方法与流程版本管理”中查看候选及比较。

Stage 9 已接入经用户确认的偏好与经验记忆、显式选择、作用域检查和撤权/删除重验；真实 DeepSeek 控制案例保留一次 SDK 失败及重试记录，见 [Stage 9 验收记录](docs/stage9-acceptance.md)。Stage 8 的不可变专家方法、独立路由策略、研究模板、固定版本执行与高级管理仍在运行，见 [Stage 8 验收记录](docs/stage8-acceptance.md)。RouteLLM 与前序证据均保留。

候选流程使用 `agent-evolution propose|run|assess|compare|publish|rollback`；每个写命令读取一个 JSON 文件。生成器需已授权的模型配置。先将纠错在 `agent-evals` 中核验并冻结开发/留出案例，随后把两组案例 ID、获准的通用教训与预先固定的门槛写入 propose JSON。`publish` 要求完整通过的比较和用户输入精确版本确认；实际用户确认不能由测试夹具代替。可通过 `uv run agent-evolution --help` 查看参数。

Stage 7 已接入具体结果纠错、核验归因、固定开发/留出案例和离线前后及五策略模型路由对照。页面反馈可在刷新后查询；真实 PostgreSQL 控制链路和前序回归通过。路由现采用开源 RouteLLM；真实双模型收益与总成本门槛、Stage 5/6 真人语义签收仍待外部验收，详见 [Stage 7 验收矩阵](docs/stage7-acceptance.md)。

```sh
export DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55445/stage7_release'
uv run --frozen agent-research --blobs /private/tmp/stage7-pg-20261002/blobs serve --durable --port 8744
# 关闭服务后，在相同数据库和 blob 目录查询评测：
uv run --frozen agent-evals --blobs /private/tmp/stage7-pg-20261002/blobs compare EXPERIMENT_ID
```

Stage 0–4 的历史验收保留。R1–R3 的任务单、路由、聊天分支和回传已接入。Stage 5 的真实 TinyFish 项目 API、DeepSeek 三公司研究和技术回归已通过；关键数字、期间与口径仍待真人语义签收，见 [真实语义复核](docs/stage5-live-semantic-review.md)。Stage 6 已接入 DBOS 后台专家任务、步骤级恢复，并通过真实进程崩溃与 DeepSeek 三公司恢复测试；语义检查仍待真人签收，见 [Stage 6 验收记录](docs/stage6-acceptance.md)。

## Stage 6 后台任务

```sh
uv sync --frozen
npm run build --prefix frontend
export DATABASE_URL='postgresql+psycopg://USER@127.0.0.1:55444/stage6_release'
uv run agent-research --storage postgres --engine scripted \
  --blobs /tmp/stage6-pg-20261002/blobs serve --durable --port 8744
```

`--durable` 使网页的“派发专家任务”进入 DBOS 后台队列。关闭页面后重新打开，专家任务活动会从数据库读取排队、执行、完成或取消状态。需真实模型时改用 `--engine openai`，并在进程环境提供已授权的模型配置；`scripted` 只用于控制链路验证。数据库与 `--blobs` 目录须成对保留，worker 重启要使用同一地址与目录。本机仍只允许一个业务执行进程；普通 `serve` 保留直接执行模式。

外部调用也可用 `POST /api/durable_submit` 提交固定任务顺序：`{"job_id":"job-1","task_ids":["task-1","task-2"]}`。`GET /api/durable_status?job_id=job-1` 查询 DBOS 状态、各任务原运行 ID、回执和调用尝试；`POST /api/durable_cancel` 传 `{"job_id":"job-1"}` 取消。重复提交同一 ID 与相同任务只查询既有工作，不重新执行；不同任务会被拒绝。完成回执的程序检查和独立语义复核状态仍按原任务服务查询。恢复从 DBOS 已提交步骤继续，未提交的外部调用最多再试一次并记入尝试记录和预算。

## Stage 5 证据研究

研究页面可以上传资料、进行联网发现、抓取固定正文、让主助手或专家按结论列出原文引用，并从结论跳到片段和固定原始文件。证据面板将“引用文字存在”与“文字确实支持结论”的复核分开显示。搜索摘要只用于发现，不能成为正文证据；没有 `TINYFISH_API_KEY` 时明确报错。联网发现使用 [TinyFish Search 和 Fetch API](https://docs.tinyfish.ai/)，Search/Fetch 当前免费且需要 API key，由运行环境通过 `TINYFISH_API_KEY` 注入。Fetch 使用 `ttl: 0` 请求实时内容，保存提取后的 Markdown；它不等于原始网页 HTML 或 PDF 文件。

本轮控制例使用 Tesla、Ford、GM 的 2025 年公开年报，脚本输出三公司现金流与竞争风险比较，选择 Tesla 续研，并将完整结果回传主线。启动本机独立验收库后可查看 [控制例](docs/stage5-control-case.json)；`scripted` 只验证证据、权限、持久化和界面链路。运行真实模型时使用 `--engine openai` 并安全注入模型配置，结果仍须独立语义复核。完整命令、样本校验值、已验与待验见 [Stage 5 验收记录](docs/stage5-acceptance.md)。

## R3 聊天入口

```sh
uv sync --frozen
npm ci --prefix frontend
npm run build --prefix frontend
# 本机专用测试库 r3_release 已初始化；使用其他 PostgreSQL 时更换地址和库名。
pg_ctl -D /tmp/r3-pg-20261001 -o '-h 127.0.0.1 -p 55442 -k /tmp' -l /tmp/r3-pg-20261001.log start
export DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55442/r3_release'
uv run agent-research --engine scripted --blobs /tmp/r3-pg-20261001/blobs serve
```

打开 [本机研究页面](http://127.0.0.1:8744)，创建研究后在主线聊天输入问题。每条消息可作为分叉截止点；选中分支后，后续聊天只用该分支固定前文。专家任务在所选分支独立运行，结果经逐项复核后才可发布到聊天。可将完整助手或已复核专家结论带回另一分支供讨论，再明确采用。资料、来源版本、假设和限制随结论保留；上游更新只给提示。`scripted` 仅验收控制链路，真实模型质量仍受 R1 人工签收与 R2 授权配置限制。

## 模型路由入口

当前可用 [DeepSeek Flash 单模型目录](config/deepseek-flash.json)运行实际任务；模型能力与价格来自官方文档和账户可用模型检查。专家按任务类型选择发布的默认方法，财务提取专家可由用户显式指定。模型先经过工具、容量与预算筛选；经开发样本确认强弱配对后，再用本地 RouteLLM BERT 从两个合格模型中选择。服务可用 `agent-research --catalog /absolute/path/config/deepseek-flash.json serve` 启用目录；CLI 用 `agent-tasks --catalog /absolute/path/config/deepseek-flash.json create-routed ...`。`task_create` 的 `route: true` 请求须提交 `routing_summary` 与 `budget_usd`。路由记录可通过 `task_route` 或 `agent-tasks route ROUTE_ID` 查询。`OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 用于已授权 DeepSeek 执行模型，密钥只从进程环境读取。目录格式、固定权重下载和双模型验收边界见 [RouteLLM 路由说明](docs/routing-routellm.md)。

研究主助手（含后台汇总、专家回问时的主助手咨询）使用独立配置 `OPENAI_MANAGER_MODEL`，默认 `deepseek-v4-pro`。普通专家仍使用 `OPENAI_MODEL` 或任务显式指定的模型；修改主助手模型不会改写专家的固定任务版本。两个角色沿用现有提供方地址和凭据。修改环境配置后需重启服务。

## 研究工作台（第二阶段）

三栏页面在本机服务的 `/`：可创建研究、上传资料、进行主线和分支聊天、派发专家任务，并查看节点版本和专家交付回执。写操作使用同源真实 API；执行、交付复核与采用是不同状态。完整操作、隔离 scripted 验收和限制见 [工作台说明](frontend/WORKSPACE.md)。

## R1 运行与验收

使用新的专用 PostgreSQL 库；保留 Stage 0–4 的数据库和资料。数据库与 `.r1/blobs` 都要纳入备份。服务与测试不能同时持有本机执行锁。

```sh
uv sync --frozen
npm ci --prefix frontend
npm run build --prefix frontend
# 本机本轮临时实例已初始化，重启即可；使用其他 PostgreSQL 时自行替换地址和角色。
pg_ctl -D /tmp/r1-pg-20261001 -o '-h 127.0.0.1 -p 55440 -k /tmp' -l /tmp/r1-pg-20261001.log start
# r1_release 已创建；仅在新实例首次运行时执行：
# createdb -h 127.0.0.1 -p 55440 -U r1_owner r1_release
export DATABASE_URL='postgresql+psycopg://r1_owner@127.0.0.1:55440/r1_release'
uv run agent-research --engine scripted --blobs .r1/blobs serve --port 8751
```

打开 [本机 R1 页面](http://127.0.0.1:8751)。选择研究、勾选获准资料，在自然语言指令后点击“交给提取专家”；任务单由系统生成。展开“专家任务活动”查看输入、运行、回执、缺失和逐项检查。程序检查通过后仍待独立语义复核；逐项填入实际核对结论与依据后才可交给分析专家。缺单位显示 `partial_pending_review`，复核后的部分结果仍不能进入数值分析。运行结束、交付通过和用户采用在界面分别显示。

CLI 也可创建、执行、检查及查询任务。`--ref` 接受 `agent-research upload` 返回的完整 JSON 引用，分析任务用 `--result` 指定已通过的 `task-result:...`；用户不需要手写任务单。

```sh
uv run agent-tasks create RESEARCH_ID extract '提取青松公司现金流及单位' --ref '{"resource_id":"...","snapshot_id":"...","parse_id":"..."}'
uv run agent-tasks run TASK_ID
uv run agent-tasks show TASK_ID
uv run agent-tasks review RESULT_ID /absolute/path/numbered-review.json --reviewer local-user
uv run agent-tasks create RESEARCH_ID analyze '分析现金流扣除资本开支' --result RESULT_ID
```

复核文件为 `{"1":{"status":"passed","evidence":"实际核对依据"}, ...}`，需覆盖任务单所有编号。更新目标或输入时以原 `TASK_ID` 加 `--task-id TASK_ID` 生成新版任务单；旧运行不能覆盖新版。当前个人本机部署不提供审查者身份认证，`reviewer` 是本地审计标识。

```sh
# 先关闭 serve；新专用库可用于完整 C0–C4 + R1 回归。
export TEST_DATABASE_URL="$DATABASE_URL"
uv run pytest -q
uv run python scripts/verify_r1.py
```

`verify_r1.py` 只验证脚本控制、真实 PostgreSQL 原子记录与独立进程查询。真实模型需安全注入当前获准 `OPENAI_API_KEY`，并显式设置当前提供方的 `OPENAI_BASE_URL`、`OPENAI_MODEL`；运行 `uv run python evals/r1_tasks/run.py` 会生成 [本轮真实模型状态](docs/r1-real-eval.json)。该入口在缺配置时退出 2，模型产物成功后仍待人工逐项核对原文位置、数值和单位，随后通过 `agent-tasks review` 记录；未提供凭据时不调用模型，也不借用历史凭据或旧验收结果。

## Stage 4 启动

```sh
uv sync --frozen
npm ci --prefix frontend
npm run build --prefix frontend

pg_ctl -D /tmp/agent-stage1-pg -l /tmp/agent-stage1-pg.log \
  -o '-h 127.0.0.1 -p 55439 -k /tmp' start
export DATABASE_URL='postgresql+psycopg://stage1@127.0.0.1:55439/stage4_demo'
# 本机 stage4_demo 已创建；全新实例先 createdb ... stage4_demo。
export RESEARCH_TESSDATA="$PWD/.stage3/tessdata"
export RESEARCH_OCR_LANGUAGES=eng+chi_sim
uv run agent-research --engine scripted --blobs .stage4/eval-blobs serve
```

打开 [本机研究页面](http://127.0.0.1:8744)。scripted 仅检验交互控制；真实生成请安全向启动进程注入 OPENAI_API_KEY，设置以下非敏感配置，再以 `--engine openai` 启动。服务启动后独占本机执行锁；关闭服务后才能用独立 CLI/测试库进程执行。

```sh
export OPENAI_BASE_URL=https://api.deepseek.com
export OPENAI_MODEL=deepseek-flash
uv run --env-file .env agent-research --engine openai --blobs .stage4/eval-blobs serve
```

库 `stage4` 保留迭代与测试数据，`stage4_demo` 用于最终产物契约演示；前序 stage1/2/3 库不改动。原始材料默认 `.stage4/blobs`，演示/评测用 `.stage4/eval-blobs`，备份必须包含数据库与对应文件目录。没有旧契约迁移或协议 fallback。服务器只监听 127.0.0.1，当前是个人本机应用。

## 使用路径

1. 创建研究，可上传文本、PDF、扫描件等 C3 支持的资料，查看 ready/partial/failed。
2. 写节点问题，勾选该节点资料，创建后明确执行。
3. 勾选父结果的具体版本，输入新指令，创建独立分支。默认不读取兄弟。
4. 勾选参与比较的结果版本，明确比较；节点详情可回看历史结果、实际来源版本和缺失项。
5. 续研会产生新版本；旧比较只提示上游更新。选择新版本，点击重新汇总。
6. 排除停止新派发并取消受影响运行；保留历史，禁止包含该节点的混合结果进入后续输入。拖动只改变布局。

比较表和报告以 Markdown 展示。模型产物契约为 `ResearchAnswer(report, missing)`，正文只保存在原 runs；节点版本保存 run_id 引用。完整证据链/点击结论回到原文属于 Stage 5，当前未实现。

## CLI 与 API

```sh
uv run agent-research create '独立创意探索'
uv run agent-research upload RESEARCH_ID tests/fixtures/stage4/company-1.txt
uv run agent-research command /absolute/path/command.json
uv run agent-research show RESEARCH_ID
uv run agent-research result RESULT_ID
uv run agent-research plan /absolute/path/plan.json
```

command.json 最小示例：

```json
{"command_id":"stable-create-1","research_id":"RESEARCH_ID","kind":"create","goal":"头脑风暴社区旧物共享"}
```

使用返回的 node_id 发起 `kind=run`；所有命令必须稳定 command_id，同 ID 改内容拒绝。kind 支持 create、branch、select、exclude、move、mount、run、continue、retry、compare、adopt、chat_reply。resource_refs 为完整 `{resource_id,snapshot_id,parse_id}`；result_ids 为明确结果版本 ID。continue/compare/adopt 显式提供本次输入，retry 固定失败运行输入且重验权限。`expected_revision` 可拒绝过时操作；chat_reply 绑定当前聊天 revision。

本机 HTTP：GET `/api/researches`、`/api/projection?research_id=...`、`/api/resources?research_id=...`、`/api/result?id=...`、`/api/run?id=...`；POST `/api/researches`、`/api/command`、`/api/plan`、`/api/scope`、`/api/retry_save`。上传为 POST `/api/upload?research_id=...&name=...`，body 是文件字节并设置真实 Content-Type。命令返回 202 时查询 projection；只有原 C1 与节点版本原子提交后才显示成功。

R3 聊天 API：POST `/api/chat_start`、`/api/chat_send`、`/api/chat_branch`、`/api/chat_reply`、`/api/chat_publish`、`/api/chat_transfer`、`/api/chat_adopt`；GET `/api/chat_read?node_id=...`、`/api/chat_transfers?research_id=...`。发送、分叉及回传使用聊天 revision 做过时请求检查。

## 验收与复现

```sh
# 先关闭 serve，再运行；不得同时持有 C1 数据库锁。
export TEST_DATABASE_URL='postgresql+psycopg://stage1@127.0.0.1:55439/stage4'
uv run python scripts/verify_stage4.py
# 两案例：真实文件、PostgreSQL、独立 CLI 查询；脚本控制替身。
uv run python evals/stage4_research/run.py --engine scripted --report docs/stage-4-scenarios.json
# 安全注入本进程密钥后，实际调用固定模型；缺条件退出 2。
uv run python evals/stage4_research/run.py
```

公司验收夹具是三家虚构公司明确同期间/口径的结构化财务记录；程序从正文读取 company、period、currency、unit、basis 并校验。普通财报继续使用 C3 解析，Agent 必须说明未知口径；不宣称通用财报属性抽取通过。本阶段 DeepSeek-V4.1-Flash 真实小样本已通过：18 次运行、6 个维度实际读取资料、两次独立 CLI 查询；生成报告已人工复查。详见真实模型报告中的 semantic_review。

[设计符合性与 gap](docs/stage-4-acceptance.md) · [两案例及跨进程证据](docs/stage-4-scenarios.json) · [页面实际交互](docs/stage-4-ui-acceptance.md) · [真实模型状态](docs/stage-4-real-eval.json)

---

## Stage 3 已验收入口

Python 3.12+。Stage 0–2 已验收。Stage 3 已实现真实资料解析/OCR、版本存储、只读挂载、范围隔离与 CLI/API；**146 项 C0–C3 测试无跳过通过**，真实 PostgreSQL/联网验证通过，**DeepSeek-V4.1-Flash 真实模型与跨进程 CLI 验收通过，Stage 3 已完成**。Stage 4 入口见本文开头。

## Stage 3 安装和运行

```sh
uv sync --frozen
# 本机必须安装 tesseract；macOS 可用 brew install tesseract。
# 中文 OCR 数据放在项目内，校验固定 SHA-256：
uv run python scripts/setup_stage3_ocr.py
export RESEARCH_TESSDATA="$PWD/.stage3/tessdata"
export RESEARCH_OCR_LANGUAGES=eng+chi_sim

pg_ctl -D /tmp/agent-stage1-pg -l /tmp/agent-stage1-pg.log \
  -o '-h 127.0.0.1 -p 55439 -k /tmp' start
export DATABASE_URL='postgresql+psycopg://stage1@127.0.0.1:55439/stage3'
# 本次已创建 stage3；全新实例需 createdb -h 127.0.0.1 -p 55439 -U stage1 stage3。

uv run agent-resources create --url 'https://www.apple.com/newsroom/2025/05/apple-reports-second-quarter-results/'
# 从上条输出复制 scope.id 为 SCOPE；以下每条都是独立进程。
uv run agent-resources --scope SCOPE upload /absolute/path/A.txt
uv run agent-resources --scope SCOPE upload /absolute/path/B.pdf
uv run agent-resources --scope SCOPE show
# REF 为输出 ref 中 resource_id:snapshot_id:parse_id 三个值，用冒号连接。
uv run agent-resources --scope SCOPE select A_REF \
  --url 'https://www.apple.com/newsroom/2025/05/apple-reports-second-quarter-results/'
uv run agent-resources --scope SCOPE search revenue
uv run agent-resources --scope SCOPE fetch 'https://www.apple.com/newsroom/2025/05/apple-reports-second-quarter-results/'
uv run agent-resources --scope SCOPE run '只研究公司A，说明已获得与缺失的信息' --engine scripted
uv run agent-resources --scope SCOPE run-show RUN_ID
uv run agent-resources --scope SCOPE read A_REF
uv run agent-resources --scope SCOPE revoke A_REF
```

`select` 不给 REF 表示空范围；不给 `--url` 会清空联网授权。上传、搜索摘要和抓取正文各自登记；scope 只授予本次接入关系，哈希/缓存命中不扩权。正文只能挂载 ready/partial 的固定版本；partial 返回缺失位置。解析进度即时写 stderr，结构化结果写 stdout。相同会话标签不自动加载旧历史。

Stage 3 资源 CLI 的原有搜索是对明确批准 URL 目录的有界查询。Stage 5 研究页面另接入 TinyFish 全网发现与网页提取；其真实调用仍待凭据验收。两条入口的发现摘要都不能当作正文挂载；重定向不能绕过抓取限制。HTML/CSV/DOCX 表格使用 Docling；PDF 原生文本保留页与 bbox，扫描页使用 Tesseract OCR。复杂 PDF 表格的财务列口径不能仅凭解析文字自动判定。

原始文件默认保存到 `.stage3/blobs`，可用 `--blobs` 指定持久目录。不要只备份数据库而遗漏这个目录。解析失败保留原文件与错误，DB 不可用不降级。新表由资源/上下文模块分别拥有，使用新数据库，无旧结构迁移或兼容层。

## Stage 3 API 与验证

`composition.build_resources` 装配 `ResourceService + ScopeService`；`ContextBuilder.build` 固定原 RunScope、只读 mounts、PreparedInput 和 run_id；`build_scoped_execution` 为每个 Agent 绑定独立 scope，返回公开 ExecutePort，可直接交给原 Dispatcher。资源读取/写便签/联网工具都经 ScopeService 校验。`ScopeService.grant(target, source, ref)` 支持应用显式只读跨分支挂载授权。

撤权、选择变化或禁用会取消相关在途任务，提交前和保存重试时再次检查；迟到正文不发布，已保存结果的幂等重放也检查当前范围。Core/contracts/SDK adapter/Dispatcher 算法保持不变。关闭 ScopeService 会停用 scoped research，原基础入口继续运行。

```sh
TEST_DATABASE_URL="$DATABASE_URL" uv run pytest -v
# 真文件、真实外网、独立 CLI 重启；脚本引擎仅验证控制：
uv run python evals/scope_isolation/run.py --engine scripted --report docs/stage-3-live-web-cli.json
# 通过安全方式向当前进程注入 OPENAI_API_KEY，再运行真实模型验收：
export OPENAI_BASE_URL=https://api.deepseek.com
export OPENAI_MODEL=deepseek-flash
uv run python evals/scope_isolation/run.py
```

真实评测退出 0=通过、1=失败、2=外部条件缺失；未提供 TEST_DATABASE_URL 或中文训练数据会明确跳过对应测试，不能算作通过。当前真实网络报告为 infrastructure_passed，真实模型报告为 passed。单主机/同 OS 用户/单执行进程限制沿用 C1，解析同步运行，无后台恢复。

[Stage 3 逐项矩阵与边界](docs/stage-3-acceptance.md) · [完整测试输出](docs/stage-3-tests.txt) · [真实网络与 CLI 记录](docs/stage-3-live-web-cli.json) · [真实模型验收记录](docs/stage-3-real-eval.json)

---

## 已完成 Stage 0–2 的基础入口

## 安装与立即运行

```sh
uv sync --frozen
uv run agent-core --engine scripted --demo
uv run agent-records --storage memory --engine scripted demo
uv run agent-dispatch --storage memory --engine scripted compare \
  '请三个专家分别比较方案的成本、速度与限制'
uv run agent-dispatch --storage memory --engine scripted compare \
  '请三个专家分别比较方案的成本、速度与限制' --fail-expert speed
```

最后一条是显式验收故障注入：成本和限制成功，速度显示“缺失”，退出 1。ScriptedEngine 仅验证执行控制，不是实际模型分析。内存模式退出丢失记录；存储不可用不自动降级。

依赖固定：openai-agents 0.22.2、SQLAlchemy 2.0.43、psycopg[binary] 3.2.10；完整传递依赖见 `uv.lock`。参考源码摘选不作为运行依赖。受限环境可设 `UV_CACHE_DIR=/tmp/stage0-uv`。

## PostgreSQL 与独立进程查询

Stage 2 当前表结构增加 dispatch_groups，runs.conversation_id 允许为空以保存独立执行。使用新的专用数据库；不提供旧结构兼容或迁移。Stage 1 原验收库保留在 postgres 数据库，Stage 2 使用 stage2 数据库。下面是本次已创建的本机测试实例启动方式：

```sh
pg_ctl -D /tmp/agent-stage1-pg -l /tmp/agent-stage1-pg.log \
  -o '-h 127.0.0.1 -p 55439 -k /tmp' start
export DATABASE_URL='postgresql+psycopg://stage1@127.0.0.1:55439/stage2'

uv run agent-dispatch --storage postgres --engine scripted compare \
  '请三个专家分别比较方案的成本、速度与限制'
# 从输出复制 group_id；该命令在新进程查询，不需要模型凭据。
uv run agent-dispatch --storage postgres show GROUP_ID

# C1 会话依然可独立使用。
uv run agent-records --storage postgres --engine scripted --conversation example demo
uv run agent-records --storage postgres --conversation example show
uv run agent-records --storage postgres --engine scripted --conversation example send '追问结果'

pg_ctl -D /tmp/agent-stage1-pg stop -m fast
```

全新机器先用 `initdb -D /tmp/agent-stage1-pg -A trust -U stage1 --no-locale -E UTF8` 创建不存在的数据目录，启动后执行 `createdb -h 127.0.0.1 -p 55439 -U stage1 stage2`。trust 只用于本机隔离验收库。应用自动建当前版本表；数据库账号须有建表/读写权限。

`show` 返回原计划、输入绑定、策略、子 run IDs、独立运行记录、事件和按原顺序排列的结果。失败、取消、保存未确认及未启动项均明确展示缺失。组 `settled` 不意味着所有子项成功；CLI 仅在全部子项已提交成功时返回 0。

## 路由与执行策略

`compare` 显式选择三个专家：成本、速度、限制。每个专家只收到自己的 PreparedInput，运行与提交独立，不追加父会话。普通 `agent-core` / `agent-records` 不调用规划器，原串行 delegate 行为不变。

```sh
uv run agent-dispatch --storage postgres --engine scripted compare '比较方案' \
  --concurrency 2 --max-runs 3 --completion all_settled
```

- `--concurrency`：同时活动的专家上限，默认 3，最多 32。
- `--max-runs`：严格的专家启动次数预算，默认 3；已获准的任务可以完成，超出预算的项显示缺失。
- `--completion all_settled`：默认等待每项；`fail_fast`：首个失败或保存异常后取消兄弟并停止新任务。
- `--token-budget N`：累计已报告 token 达到阈值后停止新增并取消在途任务；用量未知也停止。**这是观测阈值，不是硬费用封顶**，单次请求与在途并发可超出阈值。
- `--group-id ID`：指定新分派 ID。重复 CLI compare 不代表恢复，使用 `show ID` 查询。应用代码以同一原计划重复 dispatch 只读状态，不重跑计算。

组期限默认 120 秒，可通过 DispatchPolicy 配置；C0 每个运行的轮数/调用数/期限继续有效。当前组仅允许一层专家；递归委派超出深度限制。取消会等待活动子任务清理并保存终态。

## 真实模型验收

通过安全方式向当前进程环境提供 `OPENAI_API_KEY`，不读取历史凭据、任意密钥文件或 Codex 登录令牌。模型仍使用 SDK 默认 Responses 接口，没有协议回退。

```sh
export OPENAI_BASE_URL=https://api.deepseek.com
export OPENAI_MODEL=deepseek-flash
# DATABASE_URL 按上文配置；OPENAI_API_KEY 通过本机安全方式注入。
uv run agent-dispatch --storage postgres --engine openai compare \
  '请三个专家分别比较方案的成本、速度与限制。方案A每月100元、每项10秒、可离线；方案B每月200元、每项5秒、必须联网。'
uv run python evals/stage2_orchestration/run.py
```

评测启动真实 CLI：三专家正常执行→关闭→新进程查询，再执行单专家故障混合场景→关闭→查询。退出码 0=通过，1=失败，2=外部条件缺失而未运行。本阶段已实际通过 DeepSeek-V4.1-Flash（API 标识 `deepseek-flash`）正常三专家与单专家故障混合验收，两次均通过关闭后新进程查询。真实报告为 `passed`；完整交互见 `docs/stage-2-real-cli.txt`。复现仍需向当前进程安全提供凭据。

## 测试与边界

```sh
uv run pytest -q
# 专用测试数据库；包括事务故障与杀死测试自己的子进程。
TEST_DATABASE_URL="$DATABASE_URL" uv run pytest -v
```

未提供 TEST_DATABASE_URL 时真实 SQL/进程测试跳过，不能算作持久化验收通过。最终真实库测试 **105 passed in 8.32s，无跳过**：原 64 项 C0/C1 + 新增 41 项。并发用同步屏障证明，不依赖毫秒速度断言。

- Core/contracts/SDK adapter/既有工具/会话结构保持不变。
- execution 增加独立 ExecutePort 入口，复用原原子提交、事件和保存重试；Core succeeded 不是应用已保存。
- orchestration/router.py、dispatcher.py、collect.py、models.py 分别负责策略、调度、收集和值对象；composition 注册三个专家。
- PostgreSQL 保持同主机同 OS 用户单执行进程限制，flock + advisory lock。事务不跨模型/工具 await；不自动重连。
- 进程死亡后组与未完成子运行标 interrupted，仅查询，不恢复计算。原 C1 会话 run_id 幂等、revision 检查及只重试保存继续有效。
- 上述 C0–C2 入口不读取 Stage 3 资料；Stage 3 资料入口见本文前半部分。研究树、证据、后台恢复、记忆与进化的当前验收状态以各阶段验收文档为准。

[Stage 2 验收矩阵与 gap](docs/stage-2-acceptance.md) · [测试原始输出](docs/stage-2-tests.txt) · [CLI 跨进程证据](docs/stage-2-cli-restart.txt) · [真实评测状态](docs/stage-2-real-eval.json) · [Stage 1 历史验收](docs/stage-1-acceptance.md) · [Stage 0 历史验收](docs/stage-0-acceptance.md) · [每阶段验收流程](docs/development-acceptance-process.md)


## 受控执行与上下文交付（2026-10-07）

CE2、CE3、CE4 已按顺序完成当前范围验收。真实模型产品入口使用统一 controlled broker；长上下文在字符检查前投影，完整原文按固定哈希分页读取；模型请求保留精确发送清单。执行在原预算内预留收尾，报告保存后保持待复核，全文与简报分开读取。

- [CE2 多入口与隔离验收](docs/ce2-acceptance.md)
- [CE3 投影、分页、请求清单验收](docs/ce3-acceptance.md)
- [CE4 预算收尾、全文交付与保存恢复验收](docs/ce4-acceptance.md)

正式方案为本机可信服务与受控工具，不提供容器或 OS 强隔离。CE5–CE8 未实现，既有研究质量未完成项仍见原协作记录。
