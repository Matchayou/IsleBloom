# Stage 5 证据与研究资料验收记录（2026-10-02）

## 当前结论

上传资料的固定证据链、三公司比较控制例、续研与回传已通过程序、PostgreSQL 和浏览器验收。**本次增加了真实网络和真实模型验收**：项目 TinyFish Search/Fetch 完成中文查询、官方网页抓取、固定快照与引用回查；DeepSeek-V4.1-Flash 完成三公司比较、Tesla 续研回传和抓取网页分析，并对 21 条结论与一个错误引用负例完成第二次模型判分及 Codex 独立语义复核。详见[真实评测报告](stage5-live-semantic-review.md)。**Stage 5 尚待真人语义签收**；TinyFish MCP 在本 Codex 桌面会话因启动环境缺 key 仍不可调用。R1 真人签收和 R2 真实 Jev/双模型配置仍是各自独立的待办。

## 样本和证据边界

样本为 2025 财年的原始公开年报 PDF，固定文件保存在本机 `/tmp/stage5-public-2025`；运行所需原文与解析记录进入独立 PostgreSQL 库 `stage5_release` 和 `/tmp/stage5-pg-20261002/blobs`。这些 `/tmp` 路径只用于本轮验收，不作为生产持久存储。

| 公司 | 原始来源 | SHA-256 | 解析情况 | 比较使用的原文页 |
|---|---|---|---|---|
| Tesla | [投资者关系 2025 年报](https://ir.tesla.com/_flysystem/s3/sec/000162828026003952/tsla-20251231-gen.pdf) | `65dd654744c3d7cfad92dbee66706a6f7e808b59adbb213cdffcdedeb968135a` | partial；8,217 个片段；第 111/118 页无可读 OCR 文字 | 现金流 56；风险 13 |
| Ford | [投资者关系 2025 年报](https://s205.q4cdn.com/882619693/files/doc_financials/2025/ar/__Ford-2025-Annual-Report__.pdf) | `636d1c1a3790fccca4ad8a9db0807f667ccdf8ee41fc22d2ef65a09160a23845` | partial；16,240 个片段；第 2/210/211 页无可读 OCR 文字 | 现金流 89；风险 24 |
| GM | [SEC 年报文件](https://www.sec.gov/Archives/edgar/data/1467858/000146785826000027/gmannualreportfinal.pdf) | `417d855c389a33ea06ad898676964e1c456500bf53ac1a3650e837ad2539baae` | partial；10,641 个片段；第 119/120 页无可读 OCR 文字 | 现金流 61；风险 17 |

比较控制例逐页核对 2025 年合并经营活动现金流：Tesla 14,747、Ford 21,282、GM 26,867，单位均为百万美元。Ford 使用第 89 页合并列；第 71 页叙述中的 21.3 十亿美元是四舍五入值。三家金融业务和口径结构不同，报告明确禁止以绝对金额排序推断经营质量。风险表述均作为公司披露，不当作风险已发生。控制例与固定引用见 [JSON](stage5-control-case.json)。

## 逐项验收

| 要求 | 证据 | 状态 |
|---|---|---|
| 固定上传、解析、重复文件、损坏文件、扫描件、表格 | 现有 ResourceService/Docling/OCR；`tests/test_resources.py` 覆盖重复 blob 与 parse cache、损坏文件、中文扫描 OCR、真实 HTML 中文表格；本轮三份年报保留 partial 与缺页原因 | 已通过程序检查；复杂 PDF 表格的逐列语义仍需人工核对 |
| 发现摘要和正文分开 | TinyFish 结果登记为 `discovery`；仅显式抓取后的 `web` 快照可挂载和引用；模拟提供方负例拒绝摘要 | 已通过离线契约测试 |
| 一个真实搜索提供方 | TinyFish Search 使用 `query`、`language=zh`；Fetch 请求 `format=markdown`、`ttl=0`。两者均用 `X-API-Key`；检查逐 URL 错误、最终 URL 和非空正文，响应限制 25 MB | 项目提供方真实调用通过：搜索及 Fetch 均 HTTP 200，官方网页固定正文已入库；本会话 MCP 启动仍失败 |
| 结论、原文和支持关系 | `Claim`、`Evidence`、`ClaimEvidence` 随结果同事务提交；固定完整 ref、精确片段位置、逐字 quote；支持反证和缺证 | 已通过 PostgreSQL 与浏览器控制例 |
| 公司、期间、币种、单位、口径 | 模型提议字段；程序检查字段出现在已解析来源（PDF 除公司名外在同页）；界面展示未知值；独立语义复核判断列与结论是否相符 | 程序检查与真实模型发布通过；独立复核发现 Ford 公司全名、GM 美元符号的摘录边界和网页“调整后”口径须真人核对 |
| 错误引用、错误单位/期间、越权、撤权、旧版本 | `tests/test_evidence.py` 检查错误 quote、缺 claim、错误单位/期间、外部 scope、撤权及原资源新版后旧引用不变 | 已通过内存和 PostgreSQL |
| “有引用”与“确实支持”分开 | 真实网页 quote 的错误“合并经营活动现金流 106 亿美元”负例通过程序位置检查，但真实模型及 Codex 判为 unsupported；数据库关系仍为 unreviewed | 真实模型和独立复核通过负例；真人签收待办 |
| 三公司比较、专家、续研、回传、原文点击 | `evals/stage5_research/run.py` 脚本控制、[浏览器截图](stage5-browser-evidence.png)、`frontend/tests/stage5.spec.js`；另有 [真实模型比较、续研和回传](stage5-live-eval.json) | 脚本控制、真实浏览器和真实模型研究分支通过；专家回执仍为 pending_review |
| 累计回归 | `TEST_DATABASE_URL` 指向本轮独立库时 `pytest -q`：274 passed；本轮 Playwright Stage 5：2 passed；Vite build 通过 | 已通过 |

既有浏览器截图是在无密钥的旧进程中拍摄，显示 `TINYFISH_API_KEY` 错误且无伪搜索结果；它是历史控制证据，不代表本次真实网络结果。截图中的“复核支持”是 Codex 对一条固定原文的检查记录，**不是**真人签收，也不代表整份报告已获语义认可。

### 查看本轮本机控制例

本轮测试库当前使用 `127.0.0.1:55443/stage5_release`。若临时 PostgreSQL 已停止，先执行 `pg_ctl -D /tmp/stage5-pg-20261002 -o '-h 127.0.0.1 -p 55443 -k /tmp' -l /tmp/stage5-pg-20261002.log start`。在仓库根目录运行：

```sh
npm run build --prefix frontend
DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55443/stage5_release' \
PYTHONPATH=src .venv/bin/python -m agent_core.research_cli \
  --engine scripted --blobs /tmp/stage5-pg-20261002/blobs \
  serve --port 8745 --frontend frontend/dist
```

打开 [本机控制例](http://127.0.0.1:8745)，选择“2025 Tesla、Ford、GM 现金流与竞争风险比较”研究。服务和测试不能同时持有本机执行锁；关闭服务后再以 `TEST_DATABASE_URL` 指向该库运行 `pytest -q`。

## 复用与实现位置

现有 ResourceRef、SourceSnapshot、解析片段、ScopeService 权限和研究结果事务继续是唯一来源身份及权限机制；没有复制来源注册表或改 Core。参考 PaperQA 的文档/片段/上下文分层和 SDK 金融示例的搜索结果与来源分离，具体实现采用本项目原有的输入快照与结果发布契约。证据模块在 `src/agent_core/evidence/`，PostgreSQL 表适配器在 `src/agent_core/adapters/postgres/evidence.py`，界面在 `frontend/src/EvidencePanel.jsx` 和 `frontend/src/DiscoveryPanel.jsx`。

## 完成 Stage 5 还需

1. 真人对[真实评测报告](stage5-live-semantic-review.md)中的关键数字、期间、币种、单位、口径和部分支持项作最终语义签收；脚本控制和 Codex 复核均不能替代此步。
2. 若必须验收 Codex 桌面内的 TinyFish **MCP 工具**，需让桌面 MCP 进程得到 `TINYFISH_API_KEY` 后在新会话实际调用。项目自己的 Search/Fetch 已成功，但不等于 MCP 工具成功。

## TinyFish 接入更新（2026-10-02）

根据用户选择，当前唯一提供方改为 TinyFish，已删除旧提供方实现。复用现有 httpx，不增加 SDK。只调用免费 Search 和 Fetch；原文位置定位到已保存的 Markdown 提取快照，不能声称它保留原 PDF 页码或原始 HTML。提供方返回的 `final_url` 与所选发现 URL 不一致时，当前范围规则拒绝接入。TinyFish 负责远程抓取中的私有地址及重定向目标限制；本机只连接固定 TinyFish API 主机。

[官方价格](https://www.tinyfish.ai/pricing)：Search 30 次/分钟、500 次/小时；Fetch 150 URL/分钟、1,000 URL/天；均不扣 Wallet。免费接口仍要求账号和 API key。接口按 [Search 文档](https://docs.tinyfish.ai/search-api/reference) 和 [Fetch 文档](https://docs.tinyfish.ai/fetch-api/reference) 实现。2026-10-02 的获准评测进程已有密钥，真实项目调用证据见[网络记录](stage5-tinyfish-raw.json)；桌面 MCP 进程仍缺环境变量。

本轮 TinyFish 替换验证：274 个内存/PostgreSQL 测试通过；2 个 Stage 5 浏览器测试通过；前端构建通过。覆盖 Search/Fetch 请求契约、认证/额度/限流错误、单 URL 错误、空正文、URL 变化、响应体上限，以及 Markdown 抓取快照解析、发布和引用定位。上述网络响应使用 MockTransport，不能计为真实外网验收。
