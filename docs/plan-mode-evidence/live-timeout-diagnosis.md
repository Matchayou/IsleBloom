# 未确认用量核对与超时诊断

核对时间：2026-10-12。运行0493e159-04c6-438f-ad26-73dd962de3d6。数据来源：controlled_operations、context_manifests、SDK收据、context_compactions、运行事件。只读取计数、schema诊断和元数据，没有导出隐藏推理内容。

## 用量

|请求|状态|输入|输出|其中推理|
|---|---|---:|---:|---:|
|model:1|已确认|3305|2241|2094|
|model:2|已确认|7265|4342|4190|
|model:3|已确认|5397|1474|1360|
|summary:3ee04…|已发送，TimeoutError，unknown|未知|未知|未知|
|model:4|已发送，CancelledError，unknown|未知|未知|未知|

第二轮确认小计：输入15967+输出8057=24024 tokens，其中输出推理7644（94.9%），input已包含缓存命中5504，不另加。第一轮确认执行9503 tokens；前两轮执行确认总计33527。最初生成计划另8239，三条已确认运行合计41766。未知请求计费另列，不能填0，也不能用字符估算补写成实测。

这两条 unknown 在服务重启后仍无 response_metadata、usage 或响应ID，也没有已提交收据。它们的wire_body已保存，证明发送而非本地未发送。没有客户端后续到账记录可用于自动对账；DeepSeek Responses协议不支持store/background，不能从本地取回缺失响应。精确账单仍需提供商使用记录或客服核对；本次不伪造数字，不修改原usage.complete=false。

官方依据：https://api-docs.deepseek.com/guides/responses_api/ 。该文档说明stateless、store不支持、完整响应usage用于计量。

## 超时因果链

1. 10:44:12.405启动，截止10:47:12.424，约180.019秒，为批准的单步硬期限；不是HTTP限流或权限异常。
2. 首次模型至搜索开始约28.55秒；search_web 10:44:40.957→10:44:44.280，3.32秒。第二次模型到fetch开始约62.23秒；fetch_web 10:45:46.515→10:45:57.038，10.52秒。联网工具合计13.84秒，两次成功。
3. 网页返回后runtime上下文压缩触发。代码TRIGGER_CHARS=12000；压缩输入记录before_chars=16031，purpose=summary，同一deepseek-v4-pro模型。摘要请求timeout=45.101秒，实际记录TimeoutError，未得到usage或摘要；这是额外模型请求，消耗收尾前的剩余时间。
4. 剩约29.859秒进入finalizing，model:3工具关闭、已返回usage。SDK检查点记录交付草稿390字符，不是合法JSON（json_invalid，expected value at line 1 column 1），因此没有成为研究结果，触发有界输出修正。
5. model:4启动时timeout仅5.990秒，最终被整个run硬截止取消，账本保存timeout，后两步等待而不运行。

推断：重推理模型的多轮响应、额外上下文摘要和输出格式修正共同耗尽预算。推理token比例与长响应时间支持这一判断，但没有逐token时序，不能把每个推理token换算成确定耗时。网络工具耗时和错误状态已核对，不是主要时间占用。

## 本轮处理

延长时限并提高预算，复用已保存论文和上游定位，压缩研究交付长度，步骤显式要求JSON输出；不更换模型、不重发未知请求。新增规划资料筛选修复：排除discovery摘要，仅挂载正文。这修复了联网后生成下一份计划时Discovery summaries are not source bodies的错误。

自动审批拒绝了将内部诊断载荷发给外部DeepSeek生成新计划；已改用完全本地编制、既有ResearchPlan校验与待确认保存，没有发送被拦截载荷。
