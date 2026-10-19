# 真实模型问答与工具验收

日期：2026-10-03。服务：http://127.0.0.1:8768/。

## 运行配置

- OpenAI Agents SDK / Responses API，DeepSeek `deepseek-flash`。
- 服务端从被 Git 忽略的 `.env` 加载凭据。
- 8768 已从脚本引擎切到真实模型；存储仍为内存，服务重启后研究记录不会保留。
- 切换前的 5 条预览研究及原件已导出到 `.stage4/scripted-preview-backup-20261003T121128Z/`。
- 原 8744 服务未变更。

## 实测结果

研究 ID：`30aded4a-c561-4ec9-aa74-0da84fddc065`。

| 验收项 | 结果 | 模型请求 | 输入 / 输出 tokens |
| --- | --- | --- | --- |
| 自包含问答：146 × 7 | 成功，返回 1022 | 1 | 1301 / 68 |
| 挂载合成资料后检索、读取、引用和计算 | 成功，随机标记准确，29 + 14 = 43 | 3 | 9480 / 2205 |

工具运行 `185a28d0-f3af-4d22-835d-47f61a482e70` 的后端事件记录包含 1 次 `search_source` 和 4 次 `read_source` 完成事件，无工具错误。随机标记仅出现在上传资料中，问题未提供其值。返回结果包含准确的原文行号及逐字引用。浏览器已核对问答结果与原始资料展示。

这是两轮合成资料验收，不能代表所有问题或工具的稳定性。

## 本次修复

首次问答失败：模型输出是合法 JSON，但把无引用的算术答案放进 `claims`，违反每条 claim 必须具备引用或明确证据缺口的校验要求。

在研究代理指令中明确该规则，并要求自包含算术、日常对话、纯创意在无需外部事实证据时使用空 `claims`。保留原有验证规则。重启后问答与工具链路均通过。

## 证据与复跑

- `workspace-live-verification.json`：成功运行、用量、工具事件、对话与资源记录。
- `workspace-live-first-failure.json`：首次失败记录。
- `workspace-provider-diagnostic.json`：定位首次失败所用的合成请求响应。
- `../frontend/live-model-preview.png`：浏览器验收截图。
- `.venv/bin/python scripts/verify_workspace_live.py`：通过 8768 HTTP API 复跑；会创建新研究并消耗模型用量。

启动命令：

```sh
uv run --env-file .env agent-research --storage memory --engine openai --blobs .stage4/live-preview-blobs serve --port 8768
```
