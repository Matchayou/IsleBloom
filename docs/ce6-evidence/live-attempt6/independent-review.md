# CE6 attempt6：越界游标终止，未交付

4 次真实 HTTP 200、两次摘要有效。read_context_page 收到超出该固定内容长度的 offset，抛出 ValueError，SDK 终止。本轮未交付，不能宣称语义通过。

修复通用参数边界：有授权且哈希正确的原文发生越界时，返回 invalid_input/executed=false、实际长度和同一 artifact/pointer 的 restart_cursor；不记录虚假的阅读、不返回原文，也不释放本次工具预算。未知引用或授权问题仍拒绝；新增专项验证。
