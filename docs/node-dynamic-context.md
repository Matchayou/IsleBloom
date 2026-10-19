# 普通研究节点的动态上下文

无聊天输入的 run、continue、compare、adopt 和获批计划步骤现在与聊天入口共同调用 ContextAssembler；ResearchService 不再直接调用 ContextBuilder。ContextBuilder 仍是统一组装器内部的预算投影与快照构建组件。

普通节点初始输入包含冻结目标、指令、明确挂载的资料及上游结果、项目信息、skill 候选和用户/当前项目 memory。资料检索目录仅来自本次节点 scope 中已选择的 refs，不调用主聊天的全库候选检索，不加载聊天历史或其他专家报告。普通节点没有专精领域 memory 权限。普通非证据模式继续预载显式挂载正文；证据模式保留按需读取。

节点命令没有单一聊天原文字段：node_request 保存原始节点 goal/instructions，node_command 保存本次命令 kind/goal/instructions，所有文本保持原样。独立 user 消息按本次 instructions、goal、节点 instructions、goal 的顺序选择首个非空字段；检索查询另外组合当前执行目标和指令，不替代该消息。已批准计划沿用步骤执行合同，原规划请求仍保存在 proposal 中。

skill 正文、工具 schema 和额外 memory 延续现有渐进加载机制，不能增加资料或工具权限。memory 目录在执行与提交时重新校验；撤销会阻断结果发布。模型与工具仍经 CoreRunner、受控引擎和原有范围校验入口执行。

retry 不调用组装器或检索器：检查原权限及版本后复制冻结 ContextSnapshot，只更换快照和运行身份。后续新增 memory/skill 不进入重试输入。

验证：tests/test_node_dynamic_context.py 覆盖 SDK 激活 skill、原话及结构化命令保留、目录外资料读取拒绝、领域 memory 隔离、重试不重组装以及运行中 memory 撤销。tests/test_research.py 继续覆盖版本、上游结果、权限撤销和原子发布；故障注入事务需暴露新增检索所需的 memories 仓储。依赖真实 PostgreSQL 的验证未配置数据库时跳过。
