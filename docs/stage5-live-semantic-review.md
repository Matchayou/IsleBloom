# Stage 5 真实网络与模型语义评测（2026-10-02）

## 执行身份与边界

- 研究案例：`a570c3d9-55d9-43dd-9b06-d1b8d5b920d7`，在独立 PostgreSQL `stage5_release` 中。开始 `2026-10-02T11:42:12Z`，最后一项模型输出发布 `11:46:15Z`。
- 指定显示版本 DeepSeek-V4.1-Flash。DeepSeek `/models` 对本次凭据返回 `deepseek-flash`、`deepseek-v4-pro`；[官方型号说明](https://api-docs.deepseek.com/quick_start/pricing/)将 `deepseek-flash` 对应到 DeepSeek-V4.1-Flash。项目 SDK 请求 ID 为 `deepseek-flash`；另一次同 ID 判分请求的三份 API 响应 `model` 都是 `deepseek-flash`。未改用 Pro。
- 模型生成经过本项目 `OpenAIEngine → ResearchService → EvidenceService → PostgreSQL` 执行与发布。三公司比较、Tesla 续研、中文官网分析均由模型生成。前两项引用经过固定逐字片段包；包内每行由年报原 PDF 解析片段自动抽取，`stage5-live-eval.json` 保存逐行原始 PDF 位置和文本映射。原始 PDF 仍在同一研究库中。没有把旧脚本控制例计作真实模型输出。
- 程序发布检查固定 ref、片段位置、逐字 quote、字段字符串及权限；无法判断结论是否由引用支持。DeepSeek 第二次判分、下方 Codex 独立复核和真人签收是三种不同状态。数据库引用关系仍标记 `unreviewed`，不代表真人认可。

## TinyFish MCP 与项目实际网络调用

本 Codex 会话工具列表没有 TinyFish MCP 工具。对 `tinyfish` 服务器执行只读 `resources/list` 和 `resources/templates/list` 均在启动前失败，报错为 `Environment variable TINYFISH_API_KEY for MCP server 'tinyfish' is not set`。只读配置显示 Codex 已注册 `mcp_servers.tinyfish` 远端地址；本会话不能声称 MCP 工具实际调用成功。没有重装或修改认证。

项目自己的 `TinyFishSearchProvider` 用已授权 shell 环境中的 key 调用成功；它与 MCP 验证分开记录：

| 步骤 | UTC 时间 | API 状态 | 结果 |
|---|---|---:|---|
| 中文查询 `2025 年 特斯拉 福特 通用汽车 年报 经营现金流 比较`，`language=zh` | `11:42:14.663` | 200 | 10 个结果；发现摘要保存为 `discovery`，不作为正文证据 |
| Fetch 通用汽车[官方中文新闻稿](https://news.gm.com.cn/zh/home.detail.html/Pages/news/cn/zh/2026/jan/0127-gm-earnings.html)，`format=markdown`、`ttl=0` | `11:42:16.805` | 200 | `errors=[]`，最终 URL 与所选 URL 相同，Markdown UTF-8 3,835 字节，SHA-256 `598cce34787629755aa9c4367a2837f0003e29562b167028f5862dc5a924dc43` |

发现 ref 为 `docs/stage5-live-eval.json` 的 `selected_discovery`；正文固定 ref 为 `web.ref`。网页模型结论 5 条引用全部通过 `EvidenceService.locate` 回查到同一 `web.ref.snapshot_id`。三次已发布结果共有 59 条原文引用，全部可按固定快照回查。原始无密钥响应、URL、时间和状态保存在 [TinyFish 原始记录](stage5-tinyfish-raw.json)，实际生成输出与回传身份保存在 [实时评测 JSON](stage5-live-eval.json)，发布前模型原始输出保存在 [JSONL](stage5-model-raw.jsonl)。

## 真实模型输出与独立复核

三公司比较结果 `result:1c242610-6781-4071-be86-49a9b1a16faf` 有 11 条 claim、34 条引用；Tesla 续研结果 `result:44aa3948-7134-41c6-9b3c-bfb8bc23efb7` 有 6 条 claim、20 条引用；官方网页结果 `result:aa429e7b-780c-4592-a1b3-8ee5b26da88b` 有 4 条 claim、5 条引用。Tesla 续研经研究分支回传，`transfer` 状态为 `adopted`。这些是模型完成并程序发布的结果，尚无真人语义签收。

下表 C、T、W 编号对应各结果 `bundle.claims` 的零基下标。模型判分为独立请求，但使用同一 DeepSeek 型号；Codex 复核直接对照固定摘录与原 PDF 位置、网页抓取正文。`部分`表示引用或固定材料只能支持结论的一部分；缺证没有被当成反证。

| 条目 | 结论主题 | DeepSeek 判分 | Codex 独立复核 |
|---|---|---|---|
| C0 | Tesla 2025 合并经营现金流 14,747 百万美元 | 支持 | 支持。PDF 56 页的表名、2025 列、单位、行名和数值相符；美元符号还可由 45 页交叉确认。 |
| C1 | Tesla 45 页再次列示 14,747 | 支持 | 支持。45 页为 `Dollars in millions`，同一行有 `$` 与 14,747。 |
| C2 | 三家均使用合并口径 | 支持 | 支持。Tesla 与 GM 表名明确，Ford 89 页为 `Consolidated` 列；Ford 该页属于补充现金流信息。 |
| C3 | Ford 2025 合并经营现金流 21,282 百万美元 | 支持 | 支持。89 页 2025 表，合并列与经营活动现金流行相交。公司完整法定名称不在所选摘录中，字段留空是合理的。 |
| C4 | Ford 8,351 + Ford Credit 12,931 + 抵销 0 = 21,282 | 支持 | **部分**。数字、列与加法正确；“抵销 0”取自包内 `-` 行，但这条 claim 的引用没有直接挂该行，引用集合不完整。 |
| C5 | GM 2025 合并经营现金流 26,867 百万美元 | 支持 | 支持。PDF 61 页公司、合并表名、2025 列、单位和数值吻合。`$` 出现在表内其他行，非该数字旁，币种需人工确认表格惯例。 |
| C6 | GM > Ford > Tesla 的绝对值排序 | 支持 | 支持算术排序；仅限该合并经营现金流指标，不能转成经营质量排序。 |
| C7 | Tesla 披露汽车市场竞争激烈及新进入者 | 支持 | **部分**。已引 15 行支持竞争激烈和预计加剧；“制造商进入或计划进入”在相邻 16–17 行，但该 claim 未直接引用它们。风险属披露，不表示未来竞争必然发生。 |
| C8 | Ford+、成本与质量进展及不利后果 | 支持 | **部分**。已引 20、23 行支持 Ford+ 与成本/质量进展；财务不利后果在 25–26 行，未直接挂引。表述是条件风险。 |
| C9 | GM 竞争、消费者需求和业务受损风险 | 支持 | **部分**。已引 17 行仅涵盖加剧竞争及交付产品等前半句；消费者偏好及业务可能受损在相邻 18 行，未直接挂引。 |
| C10 | Ford Credit 占比约 61%，三家金融结构不同，绝对金额不等于经营质量 | 部分支持 | **部分**。12,931/21,282≈60.76% 支持约 61%；只见 Ford Credit 分拆，摘录未证明 Tesla/GM 的金融结构，比较限制应写成分析判断和缺证。 |
| T0 | Tesla 主体、期间、单位、合并表 | 支持 | 支持，引用 56 页对应表头片段。 |
| T1 | Tesla 2025/2024/2023 三年经营现金流 | 支持 | 支持，56 页列次与同一行三个数值吻合。 |
| T2 | Tesla 45 页交叉印证 2025 现金流 | 支持 | 支持，单位、美元符号、行名与 14,747 均在 45 页。 |
| T3 | Tesla 2025 资本开支约 $8.53 billion、2024 约 $11.34 billion | 支持 | 支持，45 页文本有年份的 `respectively` 对应关系；没有投资活动逐行调节。 |
| T4 | 现金流减资本开支约 6,217 百万美元 | 部分支持 | **部分**。14,747−8,530=6,217，2024 的 14,923−11,340=3,583 算术正确；资本开支源数只到 0.01 billion，差额是近似推算，不能写成公司正式自由现金流。 |
| T5 | 未在挂载来源发现正式自由现金流定义/调节表 | 缺证 | **缺证**。当前固定摘录没有该表；这不能证明完整年报没有正式定义。保留为后续检索事项。 |
| W0 | GM 官网披露 2025 调整后汽车业务现金流 106 亿美元 | 支持 | 支持，官网摘要原文逐字一致。此数并非合并经营活动现金流。 |
| W1 | GM 2026 展望为调整后汽车业务现金流 90–110 亿美元 | 支持 | 支持，网页明示预计值及 2026 年。预测不等于已实现。 |
| W2 | 官网指标是“调整后汽车业务现金流”，不能等同合并经营现金流 | 部分支持 | **部分**。名称可直接支持口径不同；官网未给定义或调节表，不能量化两指标差异。PDF 61 页另列 2025 合并经营现金流 26,867 百万美元（268.67 亿美元），也反证把官网 106 亿当该合并数。 |
| W3 | 网页发布时间 2026-01-27，报告 2025 全年 | 支持 | 支持，日期与开头段落相符。 |

### 有引用但不支持：负例与反证

负例声明“通用汽车 2025 年**合并经营活动现金流**为 106 亿美元”，引用官网固定片段“调整后汽车业务现金流达106亿美元”。`EvidenceService.prepare` 接受这个合法 ref、位置和逐字 quote，关系初始仍为 `unreviewed`；DeepSeek 判分为 `unsupported`，Codex 也判不支持。原 GM 年报 61 页 2025 合并经营活动现金流为 26,867 **百万美元**，即 268.67 亿美元，与负例的 106 亿美元在数值及口径上直接冲突。负例没有发布为项目结论，程序试验与模型判分均见 [模型判分 JSON](stage5-model-judge.json)。

模型判分共覆盖 22 个条目（21 个真实 claim 与 1 个负例）和 60 次逐引用判断。其结论为：比较 10 支持、1 部分支持；续研 4 支持、1 部分支持、1 缺证；官网 3 支持、1 部分支持；负例不支持。Codex 复核比模型判分更严格，另发现 C4、C7、C8、C9 的引用集合没有覆盖完整结论。`stage5-model-judge.json` 保存每次请求的固定来源、逐条判词、API 响应模型 ID 与 token 用量，便于复查。

## 剩余签收与运行问题

1. 真人仍需对三家现金流数字、Ford 合并列、GM 美元单位、Tesla 资本开支精度和网页“调整后”口径作最后语义签收；本报告没有替真人签收。
2. TinyFish MCP 在本 Codex 桌面进程启动时缺 `TINYFISH_API_KEY`，因此本会话没有 MCP 工具调用成功。项目 Search/Fetch 成功不改变这一结论。
3. 模型首次直接读完整 PDF 时触发工具输出/调用上限；现已让大文档读取返回明确的定位指引，并改用可回查原 PDF 的固定逐字摘录。官网模型首轮 `basis` 字段改写后被发布校验拒绝，续研同一网页时将不确定字段留空，结果通过。拒绝记录及原始输出保留在评测 JSON/JSONL。运行路径和错误记录供后续产品改进；本次三项最终产物已发布。

## 复现

用新的私有凭据文件设置 `STAGE5_CREDENTIAL_FILE`（字段 `api_key`、`base_url`），并从配置中给进程提供 `TINYFISH_API_KEY`。在仓库根目录用现有 `.venv` 与 PostgreSQL `stage5_release`：

```sh
DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55443/stage5_release' \
STAGE5_BLOBS=/tmp/stage5-pg-20261002/blobs \
PYTHONPATH=src .venv/bin/python evals/stage5_research/live.py

DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55443/stage5_release' \
STAGE5_BLOBS=/tmp/stage5-pg-20261002/blobs \
PYTHONPATH=src .venv/bin/python evals/stage5_research/judge.py
```

这两个命令会新建研究案例和评测输出；本次固定原始结果已保存在上述文件。凭据不写入仓库或报告。使用模型 API 与 TinyFish API 的复现需要网络和有效凭据。
