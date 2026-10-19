# 主助手普通问答修复

2026-10-03

## 原因

主助手统一套用了“外部事实仅依据明确挂载资料”的研究证据规则，而且系统未向模型提供实际的请求模型标识。因此无资料时，模型身份和普通常识也容易被拒答。

“最新 iPhone”另有能力限制：`search_source` 只搜索挂载资料，应用没有接入互联网搜索工具。

## 修复

- 普通知识、解释、写作与推理允许直接回答。资料分析与用户要求的证据核验继续受原有证据规则约束。
- OpenAI 适配器将实际选择的字符串模型标识提供给代理；不注入密钥等环境配置。
- 指令明确工具只搜索挂载资料，对时效性信息说明无法实时核实，不虚构最新结果。

## 实测

修复服务位于 http://127.0.0.1:8769/ ，使用真实 DeepSeek 调用与内存存储。原 8768 服务继续运行，保留用户原对话；其进程尚未加载此次修复。

通过 `scripts/verify_workspace_chat_live.py` 实测三轮：

1. 模型身份：正确回答配置为 `deepseek-flash`，不再要求上传资料。
2. 法国首都：直接回答“法国的首都是巴黎。”
3. 最新 iPhone：明确无法联网实时核实，提供官方核实途径，未声称执行互联网搜索。

完整记录：`workspace-chat-live-verification.json`。浏览器展示已核对，截图：`../frontend/chat-fix-preview.png`。

现有 SDK 与研究测试：26 passed，16 skipped（未运行的 PostgreSQL 场景不计为通过）。

启动修复版：

```sh
uv run --env-file .env agent-research --storage memory --engine openai --blobs .stage4/fixed-live-preview-blobs serve --port 8769
```
