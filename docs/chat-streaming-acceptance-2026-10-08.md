# 聊天流式回复与重试修复

主线及分支聊天每次回复上限由 180 秒调整为 900 秒，模型及工具调用次数仍沿用既有冻结预算。前端用 EventSource 订阅同源 SSE，服务端经受控 broker 消费供应商 Responses 流；SDK Runner 仍在完成、审计及持久化模型响应后推进工具与结果提交。

仅将结构化输出中的 report 正文作为临时预览发送到聊天框。推理事件、工具参数、内部摘要不发送；截断、失败和缺少用量的流不算成功。等待模型和读取资料显示阶段状态。断线重新连接同一命令，失败后的手动重试使用新的命令，消息本身不会再次保存。未完成的正文不写入正式聊天消息。

重放已有命令时复用原冻结命令，验证调用者提交的节点和聊天版本，不再用当前节点版本重建请求。外层执行时限被准确记录为 timeout，同时保留受控账本中的已知及未知用量。

## 验证

- 隔离 PostgreSQL 数据库 ce4_chat_streaming_20261008 上，chat、controlled delivery、core、controlled、workspace API、research web、SDK contract 以及新增 streaming 测试：103 passed，0 skipped。
- 真实供应商首次流式联调发现 nullable cache_write_tokens 不能重新按字典校验。改为沿用 SDK Usage 对响应详情对象的归一化；新增专门回归后 streaming、SDK contract、controlled：50 passed。
- 真实项目服务上的独立“流式输出验证”项目：前端提交，供应商 stream=true，SSE 观察到模型仍在 running 时已有正文，随后 60 次正文快照更新，终态 succeeded，正式聊天消息保存成功。
- 成功的 run_id：f1756cdf-a996-4f65-a000-f0a5c965899c。响应清单 responded；输入 7130 tokens、输出 776 tokens，缓存输入 7040 tokens 包含在总输入中。
- 同一命令再次 POST 返回 HTTP 200 和同一 run_id。失败后通过界面重试只新增一次执行；整个联调保留首次失败及修复后成功两条记录。
- 刷新前端后，正式助手消息为 1 条、临时预览为 0 条；填写新消息后发送按钮可用。最终前端生产构建成功。
- 完成前的响应不是完整交付；刷新能重连当前进程中的临时预览，重启服务后读取账本的终态，不自动重复模型调用。

首字时间取决于模型推理与工具阶段；流式展示减少开始阅读所需的等待，不承诺模型请求立刻产生正文。
