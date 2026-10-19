# CE6 attempt5：回查接口歧义导致失败

6 次真实 HTTP 200，三次摘要有效；模型把 source_id 当作 read_context_page.artifact_id 使用。旧 source_id 是另算的流/索引/内容哈希，不是原文文件哈希，因此按既有权限规则拒读并终止。
不增加别名兼容层：移除这套多余身份计算，新 source_id 直接采用完整原文 artifact_id。一个固定引用可直接回查，仍由会话索引与授权世代核验。保留此前失败数据。
