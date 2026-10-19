# CE6 attempt9：提供方无工具收尾输出协议异常，未交付

收尾未再启动摘要；但 task 的无工具响应输出了 DSML 调用文本而不是符合 schema 的报告，现有格式修复继续占用原模型预算，最后请求未确认并达到总超时。未执行该文本中的任何调用，未发布报告，未知预占保持。
核对官方 Responses API 说明（2026-10-08）：tool_choice=none 明确要求只生成消息；parallel_tool_calls 参数被忽略。https://api-docs.deepseek.com/zh-cn/api/create-response/
无工具请求现在显式设置 tool_choice=none（包括摘要和收尾），不解析执行 DSML 文本、不使用提供方私有旁路。摘要提示改为按需核对关键疑点，而非机械重读每条历史；仍保留逐字引用与固定原文。
模型、方法、输出上限与原 360 秒/14 次模型/10 次工具预算不变。
