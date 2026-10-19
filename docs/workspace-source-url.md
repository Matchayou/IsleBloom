# 来源 URL 传入与说明规则

2026-10-03

## 修改

- 按不可变快照查询对应接入记录，取出 `url`、`actual_url`、`fetched_at`，不依赖聊天历史或正文文字。
- `ContextBuilder` 的来源目录和 `read_source` / `search_source` 的返回值携带上述字段。来自其他作用域的已授权资料仍使用该快照的原始记录；内容缓存不替换 URL。
- 来源链接使用抓取元数据；逐字校验仍适用于事实引用，网址不必出现在正文中。
- 一般回答直接附来源；只有时效性相关或用户问及时才说明“基于此前保存的网页，未重新联网”。仅询问联网状态时不追加来源证明和上一问的业务内容。

## 验证

自动化测试：`tests/test_resources.py` 和 `tests/test_research_web.py`，**29 passed，21 skipped**。未配置 PostgreSQL 测试环境的场景跳过；已有本地 HTTP 测试在允许网络的环境中通过。

新增场景检查：正文不含 URL、授权共享后目标作用域无接入记录、同正文缓存复用、同资源不同版本 URL 不混用、撤权后不能构造来源上下文。

真实验证脚本为 `scripts/verify_workspace_source_url.py`：先通过现有 TinyFish 接口取得 Python 官方文档，再向模型提出不含 URL 的问题，检查来源目录、最终链接和按需说明。记录在 `workspace-source-url-verification.json`；首轮较冗长的状态说明保留在 `workspace-source-url-first-check.json`。

最终真实验证通过：第一轮在用户消息不含 URL 的情况下正确附上 Python 官方来源链接，且未附加未核对或未联网说明；第二轮询问联网状态，回复仅为“基于此前保存的网页，未重新联网。”。两轮都没有重新执行联网工具。浏览器已核对，截图为 `../frontend/source-url-preview.png`。

## 运行

修复版运行在 http://127.0.0.1:8771/ 。原 8770 服务及其内存对话保持运行。8771 也使用内存存储，重启会清空研究记录；本轮重启前的验证数据已导出到 `.stage4/url-live-preview-backup-20261003T133401Z/`。
