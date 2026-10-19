# CE6 attempt14：消息顺序有效，但枚举拼写错误使摘要拒绝

9 次 HTTP 200、input 71297 / output 9138、用量完整。主请求不再发生 HTTP 400；前三批中第三批输出 kind=factory（合法值是fact等四种），严格本地校验拒绝，旧摘要停在5。报告保存但缺v2与历史已做，未通过。

使用 SDK 已有 strict_json_schema=True 给摘要请求发送严格 JSON Schema；此前仅本地严格校验、请求端 strict=False。保留本地 schema/引用/覆盖/缩减检查，不把无效 factory 映射为 fact，不增加修复循环。
