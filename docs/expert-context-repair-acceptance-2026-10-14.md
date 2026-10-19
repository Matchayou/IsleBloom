# 当前工作区 Agent 字段修复与真实页面验收

2026-10-14。用户明确授权修复旧任务、重启 8744，并实际检查执行/评审上下文。

## 修复

当前库中 6 份 TaskSpec 和 6 份 TaskRun 缺少 agent；3 份 ExpertSession 缺少 agent；6 份 ContextPacket 缺少 agent/project/limits；6 份开轮 ContextSnapshot 未保存 Agent 身份。

通过任务 expert_id、已提交运行 request.agent_id、会话 expert_id/research_id 和任务/实际运行预算一致性校验后，恢复 specialist 身份。交接预算来自已保存任务及运行，未记录的历史 project 元数据设为空字典，明确表示未知，未把今天的项目状态伪装成历史上下文。

27 条关联记录在一个事务内修复，完整旧记录备份在 .controlled/repairs/agent-record-repair-20261014T113018Z.json（仅属主读写）。未修改历史输入消息、资料正文、模型结果、用量、回执审核或采用状态。

scripts/repair_workspace_agent_records.py 是本次明确授权的数据修复工具，不在产品启动或读取时运行，不增加旧格式读取 fallback。证据冲突会整体拒绝写入；重复执行无变更。

18 项修复计划、上下文接口和自动评审测试通过。原 8744 服务正常停止后，以相同环境、数据库、blobs/private 路径及 durable 参数重启，新的后端 PID 为 11190。

## 真实页面验收

1. “专家派遣验证 → 通用提示词协作验证”的旧执行 Agent 恢复显示，弹窗中能读取原用户要求、主助手交接和原 2026-10-11 冻结输入。
2. 当前库原先没有评审记录。新增“子 Agent 上下文实测（合成数据）”，通过现有 API、DBOS、真实模型、受控执行和自动评审完成一次小任务：5+7+9=21，并声明合成数据。不是把确定性响应伪装成模型运行。
3. 同一任务的执行 Agent 上下文显示 execution 快照；评审 Agent 显示 discussion 快照、独立评审角色、固定候选报告和原始验收要求。浏览器真实打开两个弹窗并保存截图，评审刷新后仍可读取。
4. 评审建议 passed，自动修订 0 轮；人工审核/正式采用状态仍待用户处理。没有借验收自动批准历史结果。

身份、运行 ID、快照 ID、API 验收状态见 expert-context-live-verification-2026-10-14.json。截图：

- expert-context-fixed-executor-2026-10-14.jpg：修复后的旧执行上下文。
- expert-context-fixed-live-executor-2026-10-14.jpg：新实测执行上下文。
- expert-context-fixed-reviewer-2026-10-14.jpg：真实评审上下文。

本次验证仅确认上下文保存/授权/展示链路；单道合成题不是一般研究质量评测。开轮快照不等于后续每次模型请求的完整最新投影。
