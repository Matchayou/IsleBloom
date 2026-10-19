# R1 任务单与检查交接：实现及验收

日期：2026-10-01。范围止于 R1；Stage 0–4 历史结论保留，未实现 R2 Jev、R3 聊天分支或 Stage 5 证据链。

**状态：控制、真实 PostgreSQL、跨进程查询、本机页面交互及 DeepSeek 真实模型提取→分析已验收。Codex 已独立逐行核对模型结果并记录两张 passed 回执；真人语义签收仍待完成，因此不将其表述为完整人工验收。**

## 复用与边界

| 来源 | 固定版本 / API | 本轮采用 | 边界 |
|---|---|---|---|
| 现有 Core / Agents SDK | `openai-agents==0.22.2`；`CoreRunner`、`AgentCatalog`、`AgentSpec`，公开 `Runner.run` 和结构化输出 | 专家方法只配置 Agent 和输出契约；非严格 Pydantic schema 在适配器用 `AgentOutputSchema` 包装 | DeepSeek Responses 实际调用已验收 |
| 现有 C1/C2 | `Ledger.commit_in`、`ExecutionService`、`Dispatcher`、`DispatchPlan` | 任务运行走原执行端口；应用事务把终态与任务回执、检查、依赖一起保存 | 同主机单进程；不提供 Stage 6 恢复 |
| 现有 C3/C4 | `ScopeService`、`ContextBuilder`、`ResearchService` | 每次任务从已授权研究库组装独立 scope 和快照；固定资源/结果版本；研究节点修订用于迟到保护 | 来源位置字段不等于 Stage 5 证据核验 |
| PostgreSQL | 17.11；SQLAlchemy 2.0.43、psycopg 3.2.10 | 新任务表由 tasks 仓库拥有，短事务由应用协调 | 使用 `/tmp/r1-pg-20261001` 中的新 `r1_release` 库，旧库不变 |
| [multi-agent-flow 固定提交](https://github.com/YuanYii/multi-agent-flow/tree/22633f3c546bb592483a98bc14941bd3d48f3648) | 产品设计 5.8 给出的 `22633f3...` 文件路径 | 仅借鉴任务契约、必须/可选交接、检查点的设计；全部 R1 代码在本项目新写 | 本轮无法从上游取得固定提交原文件与许可，未复制、导入或运行其代码；公开 API 与可靠性不宣称已验证，不采用自动全局合并和固定部门角色 |

本地依赖还包括 Pydantic 2.13.5、Starlette 1.6.0、Uvicorn 0.53.0；未新增包。`uv.lock` 保留。`uv run --offline` 在本环境因新 `/tmp` 缓存没有 hatchling 失败，已安装的 `.venv` 可以直接执行；没有改依赖或读取旧凭据。

## 需求符合性矩阵

| 要求（设计章节） | 实现 | 本轮证据 | 状态 |
|---|---|---|---|
| 任务单系统生成、任务/运行分离、固定专家/模型/方法（5.3） | `tasks/models.py`、`tasks/service.py:create`、`tasks_cli.py`；版本与方法摘要固定，跨版本总尝试上限 2 | `test_late_old_spec_and_cancelled_dependency`、CLI/页面创建 | 控制通过 |
| 必需/可选资料及独立上下文（5.4） | `TaskService._prepare` 经 ScopeService 授权；ContextBuilder 单次快照；可选缺失写入快照和回执 | 缺资料零调用、可选缺失、同专家跨任务唯一标记测试 | 控制通过 |
| 编号条件、自检与程序/语义分离（5.5） | `AcceptanceItem`、`ExpertAnswer.self_checks`、`checks`、`review`；语义逐项依据和 reviewer 单独保存 | 自报全通过仍待复核；HTTP/页面展开检查；真实模型两张回执由 Codex 逐项复核 | 控制与 Codex 复核通过；真人签收待办 |
| 部分交付依赖规则（5.5/5.7） | `partial_pending_review` 在复核前阻断；复核后 `partial` 仅预先允许时交接；数值分析要求字段及单位完整 | 缺单位、取消依赖、部分综合携带 missing 测试 | 控制通过 |
| 原子结果/回执/检查/依赖、重试保存（5.6） | `TaskLedger.commit` 在一个应用事务调用 `Ledger.commit_in` 并保存 receipt；result IDs 在同一 receipt；原 `ExecutionService.retry_save` | 事务写后故障回滚，下游阻断；重试模型调用数仍 1 | 内存与 PG 通过 |
| 旧版本/目标修订/撤权迟到保护（5.6） | 提交前核任务单版本、目标 node revision 和当前 root scope；复核/采用再次核对 | 旧 spec 迟到、节点排除、撤权测试 | 控制通过 |
| 运行、交付、采用三态（5.5/5.6） | C1 run status；receipt.delivery；receipt.adoption | 页面和 API 显示 `succeeded`/`passed`/`for_discussion` 分立 | 页面通过 |
| 提取→分析最小链路（R1） | `TaskService.run` 通过原 Dispatcher；分析挂载通过检查的准确 result ID | `verify_r1.py` 两次脚本调用；DeepSeek 两次成功运行、两张 passed 回执、独立 CLI 查询 | 脚本、数据库、真实模型通过 |
| 展开专家活动与自然语言任务（5.3/9） | `app/api.py` task endpoints；`frontend/src/main.jsx` 任务活动面板及逐项复核 | 本机浏览器勾选已授权资料、输入自然语言、执行并展开 partial 回执 | 交互通过 |

## 本轮控制与持久化证据

- 最终命令：`TEST_DATABASE_URL=postgresql+psycopg://r1_owner@127.0.0.1:55440/r1_release PYTHONPATH=src .venv/bin/pytest -q`；原始输出见 [r1-tests.txt](r1-tests.txt)。脚本模型只验证控制，不计真实语义。
- `DATABASE_URL=... PYTHONPATH=src .venv/bin/python scripts/verify_r1.py`：使用仓库固定虚构公司材料 `tests/fixtures/stage4/company-1.txt`，2 次脚本执行；提取回执通过夹具检查后交给分析，分析结果保持待独立复核。数据库关闭后，脚本启动新的 `agent-tasks show` 进程，查到同一完整回执、5 条程序检查、5 条语义检查及 C1 commit ID。原始记录见 [r1-scripted-e2e.json](r1-scripted-e2e.json)。该脚本的 reviewer 为 `script-fixture`，不是人工语义验收。
- 真实 PostgreSQL 17.11 在新的 `/tmp/r1-pg-20261001` 集群、55440 端口运行。初始化的沙箱共享内存限制经自动审批处理；旧数据库没有启动、删除、迁移或修改。`r1_acceptance`、`r1_final` 保留早期 gap 试验数据，`r1_release` 为当前专用验收库。BlobStore `.r1/blobs` 与数据库须一起保留。
- 本机浏览器访问 `127.0.0.1:8751` 的真实 React 页面和真实后端（迭代库 `r1_final`）：选择脚本验收研究，展开提取活动看到任务单、5 项条件及分立状态；在页面勾选 `company-1.txt`，自然语言提交另一提取任务，看到 `partial_pending_review`、缺字段与独立复核表，没有该任务的分析交接按钮。期间 `/api/task_activity` 返回 200。服务验收后已关闭，未占执行锁。最后只把状态文案翻成中文，并完成生产构建；该文案改动未重新走浏览器操作。
- 最终累积回归 **206 passed in 26.58s，无 skipped**；真实模型适配修复后再次运行了内存与 PostgreSQL 的 R1 测试（22 passed）。`python -m compileall -q src evals/r1_tasks scripts/verify_r1.py` 和 `npm run build --prefix frontend` 均退出 0，构建日志见 [r1-frontend-build.txt](r1-frontend-build.txt)。

## 真实模型结果与独立核对

- 用户提供的 DeepSeek OpenAI 兼容地址调用 `deepseek-flash`。唯一外发资料为仓库中标注为虚构的 12 行 `company-1.txt` 测试夹具；密钥经临时隐藏输入进入进程，未写入文件或命令参数。原始结果、用量、回执与失败诊断见 [r1-real-eval.json](r1-real-eval.json)。
- 提取成功运行：2 次模型请求，输入 3797 token、输出 871 token。模型给出青松公司、2025年度、经营现金流 120、资本开支 40、单位百万元，并准确引用原文第 3、4、8、9、6 行。5 项程序检查与 5 项 Codex 独立来源检查通过，依据见 [r1-real-extraction-review.json](r1-real-extraction-review.json)。
- 分析成功运行：2 次模型请求，输入 5000 token、输出 2611 token。任务单只依赖上述已通过的提取 result ID；模型计算 120−40=80 百万元，未把缺失上年数值按零计算。2 项程序检查与 2 项 Codex 独立来源/算术检查通过，依据见 [r1-real-analysis-review.json](r1-real-analysis-review.json)。退出进程后用新的 `agent-tasks show` 进程查询到完整回执。
- Codex 的逐项复核不是独立真人签收。两张回执的 reviewer 均明确为 `codex-source-review`；若验收必须由人负责，需对两份复核依据和原始输出做真人签收。

## Gap 及最小修复

1. 首次跨进程 `agent-tasks show` 因列表未递归序列化而退出 1；修正 CLI 的 tuple/list 处理后，独立进程查询退出 0。失败发生在展示层，数据库中结果未丢失。
2. 页面活动加入 C1 `created_at` 后，HTTP JSON 无法序列化 `datetime`；早期全回归曾出现 2 failed / 202 passed。补标准 ISO 序列化后，R1 HTTP 内存与 PG 测试复测通过；当前完整回归见上方 206 passed。
3. 页面实际操作发现缺单位结果在独立复核前显示 `partial`，可能被允许部分结果的下游读取。改为 `partial_pending_review`，交接只认复核后的 `partial`；新增前后阻断测试。语义失败仅在相应程序缺项范围内允许部分交付，否则返工。
4. 补目标节点 revision 与排除检查，旧任务迟到不能发布或复核；补可选资料缺失记录与来源撤权后的下游门禁。方法内容用摘要固定，变更须创建新版任务单。
5. 初始 UI 仅能看到历史节点，不显示任务回执；在现有研究页面加任务活动、展开的逐项检查和自然语言派发入口。没有创建第二套研究树。
6. 首次真实模型任务在调用前遇到 Agents SDK 严格 JSON schema 配置错误；适配器改用其官方 `AgentOutputSchema(..., strict_json_schema=False)`。后续响应曾出现无效 JSON；提取方法明确字段名、来源键和合法 JSON 形状后，新任务成功。失败尝试保留在数据库。
7. 首次真实分析产出正确算式，却因“缺少上年比较”等非必需资料被标成返工。现在回执仍保存缺口，但交付由编号条件和独立复核判定；数值分析下游只接收 `passed` 提取。新增内存和 PG 双后端回归测试。

## 尚待验收与运行限制

- 初始环境没有模型或数据库配置；本轮仅使用用户提供的 DeepSeek 配置和新建的专用临时 PG 库，未读取历史凭据。真实模型结果及 Codex 独立核对已完成；真人语义签收尚未提供。
- `evals/r1_tasks/run.py` 为单次真实模型提取入口，成功产物会停在待语义复核；后续分析经 `agent-tasks` 明确交接与复核。程序检查和模型自检不能代替来源逐行核对。
- 四类方法为只读固定 `*-v1` 配置。R1 只验证提取→分析；搜索/综合没有通用搜索发现或独立语义质量承诺。Jev 决策、消息分叉、结论级证据支持与持久跨进程执行属于后续里程碑。
- 语义复核由本机操作者逐项提交，`reviewer` 是审计标识，不提供身份认证；源定位是否真实需人工核对。程序检查只校验字段、数值格式、权限、版本和门禁，不会把字符串非空当作事实验证。
- 单 OS 用户、单执行进程、协作取消；进程退出后未提交保存不自动恢复。结束演示服务后再运行 CLI/测试，保持数据库与 blob 目录成对备份。
