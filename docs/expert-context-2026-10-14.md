# 子 Agent 上下文入口修复

原因：ExpertView 只显示任务目标、专家对话和产物，未接入已保存的 ContextSnapshot；/api/run 仅支持主对话 binding。评审 Agent 使用独立讨论/评审运行，不能从普通任务 binding 列表获取。

修复：子 Agent 页右上角“上下文”打开只读弹窗。GET /api/expert_context?task_id=… 返回保存的交接包、可选执行/评审运行和开轮快照；run_id 可选择具体历史版本。展示原始交接要求、快照输入消息、附带正文、角色身份、工具和选择明细。未开始/未保存快照有明确提示，不重新拼接历史。

边界：这是开轮冻结的业务上下文，不是运行中每一次模型请求的最新投影。角色字段也不宣称是 SDK 完整 system prompt。后续工具结果、增量摘要和消息会改变实际输入。本接口不读取私有 notebook、供应商 wire body 或凭据。

授权：验证项目归属、任务固定资料及输入结果、交接包、受控运行、快照 scope、资料 grants、记忆和技能可用性。跨任务指定 run_id 返回 403，资料撤权禁止返回正文。响应 no-store，弹窗刷新会清除旧正文并重新验证。

验证：test_expert_context + test_automatic_review 共 14 passed；test_projects + test_workspace_api 共 8 passed；expert-context.spec 浏览器实际 API 链路 1 passed；前端构建通过。浏览器使用临时内存服务与确定性模型，不代表真实 PostgreSQL 或模型质量验收。

部署：frontend/dist 已重建；已运行的 Python 后端需要正常重启才能加载新接口。代码修复没有自动重启原有 8744 服务，未更改其数据库/资料/私有运行配置。
