# 主助手自主联网

## 实现

- 主对话及其分支注册 `search_web` / `fetch_web`，复用现有 `SearchService` 和 TinyFish Search/Fetch。
- 时效性问题主动搜索；稳定常识无需联网。搜索摘要只用于发现，不能作为正文证据。
- `fetch_web` 只接受同一次运行搜索得到的发现引用，抓取并保存正文。小文档返回带定位的正文，大文档通过 `search_source` / `read_source` 按需读取。
- 来源获取记录携带 `run_id`。只有本次抓取的正文可自动加入该节点，初始上下文快照保持不变。
- 最终回复、结果版本和证据记录保存来源版本，后续追问继续使用。网页正文不整页注入后续问答。
- 保留原文逐字匹配、作用域、撤权与跨分支隔离校验；网页指令作为不可信资料处理。
- 对话上限为 180 秒、12 次工具调用、12 轮模型执行。工具返回剩余调用次数；提示模型在证据充分时总结，预算耗尽时陈述未核实部分。
- 来源服务失败会返回明确错误观察，不伪装为抓取成功。一般网页引用不填写财务口径字段。

## 验证

2026-10-03，最终自动化回归：**60 passed，33 skipped**。跳过项为未提供测试数据库的 PostgreSQL 场景，不能视为已通过。

```sh
.venv/bin/pytest -q tests/test_research_web.py tests/test_evidence.py tests/test_chat.py tests/test_research.py tests/test_sdk_contract.py
```

新增测试覆盖搜索→抓正文→读取→引用发布、追问读取、并发分支隔离、来源撤权、拒绝旧运行发现引用及搜索摘要证据、提供方失败，以及拒绝误用发现引用后使用正文引用纠正。

最终版本真实验收已通过（研究 `6edabb4d-b305-449f-851e-43cb35b44bce`）：2 次 `search_web`、3 次 `fetch_web`、4 次 `search_source`，保存 3 份网页正文与 5 条原文引用；7 次模型请求，输入 65151 / 输出 4212 tokens。用户问题未指定任何工具名称。

后续追问也通过：4 次 `search_source` 读取已保存正文，未调用 `search_web` 或 `fetch_web`；证据记录非空，输入正文数组为空，按需检索生效。浏览器已核对可点击来源链接，截图为 `../frontend/web-tools-preview.png`。

真实验收记录：

- `workspace-web-first-pass.json`：最初的成功联网链路。
- `workspace-web-budget-failure.json`：复测中耗尽工具预算的失败，随后加入预算提示。
- `workspace-web-field-failure.json`：财务报告期字段误填导致的严格校验失败，随后澄清 schema 与指令。
- `workspace-web-source-failure.json`：误用来源引用的失败，随后允许模型在权限不变时纠正参数，拒绝返回非法来源内容。
- `workspace-web-live-verification.json`：最终版本的搜索、抓取、引用发布结果。
- `workspace-web-followup.json`：读取已保存来源的后续追问结果。

复跑脚本会产生真实模型与搜索调用：

```sh
.venv/bin/python scripts/verify_workspace_web_live.py
.venv/bin/python scripts/verify_workspace_web_followup.py
```

## 服务

新版入口：http://127.0.0.1:8770/ 。原 8769 服务保持运行，保留用户原有内存对话；旧进程未加载新工具。

```sh
uv run --env-file .env agent-research --storage memory --engine openai --blobs .stage4/web-live-preview-blobs serve --port 8770
```

模型凭据从 `.env` 加载，TinyFish 凭据来自进程环境。此预览采用内存存储，重启不保留研究记录。验收重启前的导出位于 `.stage4/web-live-preview-backup-*/`。

本次验收确认工具链路和引用程序校验；不代表所有联网回答的语义结论均已独立复核。
