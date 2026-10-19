# 多 Agent 动态上下文：第一层

本层完成临时/专精身份、冻结交接包、授权资料目录和统一上下文组装。主助手与两类子 Agent 共用 `ContextAssembler`，模型与工具继续通过 Core/ControlledEngine 执行。没有增加独立模型循环、Agent 私有 shell 或绕过 Broker 的执行路径。

## 身份与生命周期

- `assistant`：主聊天/分支节点身份。
- `specialist`：已发布专家注册表中的固定身份，例如 `expert_search`、`expert_synthesize`。职责来自冻结的方法版本；同一研究内可复用专家会话。
- `temporary`：`temporary:<task_id>`，职责由本次委派的 `role_instructions` 给出。`expert_id` 仅选择方法/能力和输出合同模板；实际 SDK Agent ID 为临时身份。其他任务不会复用该临时会话。

专精身份不是常驻模型进程，也不会自动加载全部历史。每个任务 run 保留独立 scope 与上下文。身份记录在 `TaskSpec.agent`、`TaskRun.agent`、`ContextPacket.agent`、`ExpertSession.agent`、`ContextSnapshot.agent` 和受控执行绑定中。修订同一任务保留身份与授权目录。

## 冻结交接包

主助手通过现有 `delegate_task` 创建交接包：用户原话、委派子目标、交付要求、选中历史、必需资料/结果、授权目录、项目/代码范围和调用预算。

`resource_refs` 是本次明确输入，`authorized_refs` 是子 Agent 可以自主检索的资料范围。必需资料自动包含在授权目录中。授权引用必须属于当前研究的有效 scope，不可跨研究、使用被排除材料或超出父任务授权。路径文字与角色提示不授予权限。

代码目录仍使用操作员批准的 `CodingContract.read_paths/write_paths`。交接和项目上下文只提供 repository ID、基线和受控相对路径；没有任意宿主文件路径或 shell 授权。

## 统一组装

```text
Agent 身份 + 用户原话 + 任务目标/交接
  + 后端提供的项目信息
  + 获准历史、资料目录与显式上游结果
  + 当前 skill/memory 选择
→ ContextAssembler
→ ContextBuilder 的预算投影
→ ContextSnapshot / PreparedInput
→ 现有受控模型与工具执行
```

用户原话保留为最后一条独立 `user` 消息；主助手整理的子目标放在业务包中，不覆盖原话。历史用户陈述仍保留。角色和项目信息也属于预算保护字段。子 Agent 只执行交接子目标。

主助手以当前原话检索；子 Agent 以委派目标加原话检索，以便多个子任务获得各自相关的上下文。检索查询属于程序输入元数据，不替换用户消息。

两类子 Agent 使用相同工具入口，在授权目录中通过 `search_context` 搜索，再通过 `search_source/read_source` 读取固定版本。目录搜索只返回有上限的引用与元数据，不向模型塞入全目录正文。当前明确输入正文仍按原有证据模式处理。

目录权限不等于读取记录。快照挂载只含明确输入；目录范围保存到 scope。产物的资料依赖保守包含获准目录，实际读取记录另存工具/操作账本。授权资料撤销后，旧运行不能继续读取或发布为有效结果。

## 调用方式

主助手工具的临时委派示例：

```json
{
  "agent_mode": "temporary",
  "expert_id": "expert_synthesize",
  "role_instructions": "本次仅核对选定资料中的口径和缺口。",
  "task_instruction": "核对这组资料",
  "delivery_requirements": "返回结论、依据和未知项",
  "resource_refs": [],
  "authorized_refs": []
}
```

引用数组应提供当前研究获准目录中的完整 `ResourceRef`。专精委派使用 `agent_mode: specialist` 和已发布 `expert_id`，不覆盖角色职责。

`/api/task_create` 支持 `agent_mode`、`role_instructions`、`authorized_refs`。CLI `agent-tasks create` 支持 `--agent-mode`、`--role-instructions`、`--authorized-ref`。任务身份及角色合同没有旧结构迁移或兼容解析路径。

## 验证与后续层

`tests/test_agent_context_layer.py` 验证主助手发起两类委派、原话和版本保留、真实 SDK 的授权目录搜索与读取、越界拒绝、撤销失效、会话复用和修订。

第一层完成后，技能候选与激活、分范围长期记忆已在 [第二层](agent-context-layer-2.md) 接入。规划/讨论/咨询的模式化组装已在 [第三层](agent-context-layer-3.md) 接入；专精轨迹后台生成 memory/skill 候选及验证发布已在 [第四层](agent-context-layer-4.md) 接入；仍不执行技能脚本。

代码入口：`agent_identity.py`、`context/assembler.py`、`context/directory_tools.py`、`collaboration/models.py`、`collaboration/service.py`、`tasks/service.py`。
