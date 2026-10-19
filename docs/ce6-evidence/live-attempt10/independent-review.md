# CE6 attempt10：摘要未知响应，语义不完整

8 次 HTTP 200，其中第三次摘要未被 SDK 确认。报告保存但缺少 v2/历史待做，未通过。未知用量保留，不补造 cache 统计。

提供方官方 Responses 文档明确 reasoning.effort=none 可关闭默认思考模式： https://api-docs.deepseek.com/zh-cn/api/create-response/ 。已验证的 deepseek-flash 摘要调用显式采用该参数，仅用于结构化记忆提取；主任务模型参数和原预算不变。其他模型不发送未经文档验证的参数。增加失败类型/HTTP 状态/响应元数据（正文无法解析时只记哈希与长度），改善未知请求诊断，不输出密钥或敏感正文。
