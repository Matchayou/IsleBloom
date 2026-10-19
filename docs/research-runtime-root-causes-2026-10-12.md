# 研究执行故障根因：与 DeerFlow、LangChain/LangGraph 的工程对照

审计日期：2026-10-12。结论依据本项目真实失败记录、实际出站请求、模型收据和当前源码。DeerFlow 固定版本为 `c89fed0be4c64b4d881942a0fd240f239e239e96`，公开源码快照保存于 `plan-mode-evidence/reference-audit/`。本轮只做诊断，没有执行待确认研究或调用付费模型。

## 结论

根因是模型运行层的几个契约没有形成闭环：压缩按字符阈值触发，截止时间与模型耗时脱节，调查转收尾的行为依赖模型服从提示，结构化输出策略没有经过当前提供商的真实验证。严格的授权、账本和结果校验成功阻止了失败被当作成功，但不能替代模型运行可靠性。

DeerFlow 也有工具调用和上下文问题，不能证明它在相同模型、同一任务、相同180秒限制下会成功。本次没有做两套系统的同条件性能实验。公开 issue 是故障报告，不是对当前版本仍失败的证明。它的工程实现可作为设计依据，不能作为零故障保证。

## 1. 不需要压缩时也进行昂贵的压缩——已证实

本项目 `context/compaction.py:22` 写死 `TRIGGER_CHARS=12000`，`compact()` 第102行按历史文本字符数触发，`prepare()` 第226行即使确定性投影已经能容纳输入，也尝试压缩旧完整轮次。

实际记录：runtime压缩输入16031字符；摘要请求输入保守上界21571，输出预留8192、余量4096，总计33859，低于65536窗口。压缩失败后的正常任务请求输入上界36593，加预留为48881，同样低于窗口。因而这次摘要不是避免窗口溢出的必要操作，而是主动字符阈值触发的额外工作。

摘要使用同一 `deepseek-v4-pro`；`controlled/runner.py:65` 的关闭思考特殊处理仅针对 `deepseek-flash`，对本轮模型没有作用。摘要获得45.101秒，最后超时，没有产生可复用摘要，却消耗了剩余调查时间。

DeerFlow 将触发条件、保留范围、摘要模型放在配置中；示例使用32000 tokens阈值，代码以活动运行模型的窗口作为压缩预算锚点，可指定其他摘要模型。其默认 `model_name:null` 仍沿用运行模型，不能声称默认就用了轻量模型。

修复方向：以实际模型窗口与已测用量决定是否需要压缩；先做无模型的定位读取、工具结果限长与完整轮次投影。只有输入确实无法适配时才承担摘要调用；摘要不得侵占有效交付时间。不同用途的模型策略需要明确验证，避免靠具体模型名称分支判断能力。

## 2. 收尾预算与真实耗时脱节——已证实

`controlled/progress.py:12` 固定 `min(30, timeout_seconds/4)`，收尾最多30秒，轮次预留最多2轮；摘要、调查、最终报告、格式修正共用一个硬截止时间。

真实轨迹：首次模型约28.55秒，第二次约62.23秒；搜索3.32秒，网页读取10.52秒；摘要约45秒超时。随后最终报告请求只余29.859秒，格式修正只余5.990秒，180秒截止取消运行。网络工具并非主要耗时来源，没有429或明确服务端5xx证据。

将单步延长到600秒会增加可用时间，但仍不改变30秒收尾上限，因此不能单凭新版预算宣称根治。DeerFlow固定源码中，内置子智能体默认总时限为1800秒，自定义智能体默认900秒，并支持按智能体覆写；其时限与本项目180秒设定不同，不能直接比较成功率。

修复方向：为任务模型、摘要和报告验证建立可测的耗时预算；调查动作开始前检查是否仍能完成交付，而非仅检查还能否发起请求。预算准入应覆盖允许的模型轮次、工具调用和必要修正。总tokens预算也不能只在波次间靠最终用量检查就保证每次请求都适配剩余额度；应审计现有Dispatcher对波次和执行中预算的实际约束，另作验收。

## 3. 最终模型返回的是工具协议标签，不是普通JSON格式错误——已证实；提供商内部原因未证实

上轮诊断需要补充：已确认的 `model:3` 返回390字符公开答案，内容称将继续检索官方文档，随后包含 DeepSeek `DSML tool_calls / invoke search_web` 标签。它没有生成研究报告。当前系统把这段作为最终答案尝试JSON校验，随后进入格式修正。

实际出站请求已发送 `tools:[]`、`tool_choice:"none"`、`json_schema`，指令含 `finalizing`。故障并非应用忘记关闭工具；模型/提供商没有按当前调用契约给出期望终态。结构化输出 `strict:false` 与 AgentSpec 默认一致，但仅改成true不能保证解决这个提供商行为问题。不能把DSML文字直接解释成真实可执行工具调用，绕过关闭工具和权限边界。

DeerFlow 使用具体提供商适配器，例如 `PatchedChatDeepSeek` 处理多轮 reasoning/tool 历史；这份适配器的存在不能证明它能自动修复本轮DSML泄漏。其主Agent `create_agent(...)` 没有强制每条最终答案成为本项目式研究JSON，因此有些本项目的格式失败在它那里并非相同输出协议的失败。

LangChain明确区分ProviderStrategy和ToolStrategy，按模型能力选择结构化输出方式，并要求模型支持工具与结构化输出的组合。本项目统一采用OpenAI Responses形状、同一JSON路径，尚无针对当前DeepSeek模型的完整契约验收。

修复方向：保留已安装Agents SDK，但为当前模型验证工具调用、多轮推理历史、tools关闭、强制JSON、结构化交付与取消计量等能力。调查和最终交付使用明确的调用策略；必须先确定该提供商真正支持哪条结构化交付路径。对于DSML泄漏保存明确的模型协议错误，不把它当成成功报告，也不只给模型补一句JSON提示词。

## 4. 历史要求与当前执行命令混在同一输入——首轮已证实，已部分修复

首轮把“当前只生成计划、等待确认”连同当前步骤作为执行输入，真实模型再次输出计划。现在已经添加批准阶段、版本和身份，并注明历史限制已解除；但 `research/service.py:283` 仍把完整原规划请求加入执行文本。

保持历史可审计是必要的；让完整规划请求继续占据当前执行输入，则增加语义歧义与上下文成本。长期应把研究目标与约束编译成冻结任务，把批准状态作为运行状态，把历史保留为可追溯数据。LangGraph的持久化interrupt/resume展示了用程序控制确认与续跑的模式。DeerFlow TodoList Plan模式是任务跟踪功能，不能直接等同于本项目的强制人工批准后执行。

## 5. 来源状态与研究证据完整性混淆——已证实

前一轮保存的URL是 `https://arxiv.org/abs/2210.03629`，是论文摘要页。解析ready只能证明该页面成功解析，不能证明获取了论文全文。此前“复用ReAct论文正文”的表述不够精确，应明确复用摘要页，必要机制论断仍需全文或其他一级资料。

另一个已修复问题是将root scope中搜索发现记录全部挂载为证据正文，导致 `Discovery summaries are not source bodies`。这说明发现记录、可读取正文与足够支持论断的证据是三个不同状态。简单排除discovery修复了挂载错误，尚需检验证据完整性。

修复方向：来源目录明确表达发现/已获取页面/全文及解析状态；引用校验和研究完整性分别验证，不用ready代替“资料足够”。

## 6. 用量未知包含平台限制与本地可观测性不足

两条请求已发送，分别为摘要TimeoutError、最终修正CancelledError。没有返回usage，未知状态正确，不能补零。DeerFlow的token middleware也依赖响应usage_metadata，没有神奇的办法从没有返回的响应恢复精确账单。

本项目 `context/audit.py:33` 保留usage和status，却未保留响应ID或请求关联头；非流式路径等到完整响应才记账。`controlled/streaming.py` 也主要在终态事件记usage，没有把response.created的ID持久化。因此本地很难给供应商逐请求对账。DeepSeek Responses无服务端存储进一步限制回取；记录ID仍对客服或提供商账单核对有价值，但不是可以自动找回usage的保证。

修复方向：在发送/响应头/首个事件/终态分别记录时间及非敏感关联标识；保持confirmed与unknown分账。为未知账单提供明确的人工核对入口，避免为了运行方便覆盖历史unknown状态。

## 7. 验收没有覆盖会失败的真实组合——已证实

`tests/test_planning.py:33` 的模型总是返回合法计划或报告；`scripts/verify_plan_browser_server.py:18` 等待0.15秒后返回正确JSON。它们验证事务、DAG、门禁、持久化和UI是有意义的，但不能验证长推理、网络资料、摘要超时、DSML终态和格式修正共存的运行行为。

DeerFlow上下文benchmark说明也明确限定为组件实验，禁用memory和summarization；不能拿这些组件成绩证明整个真实研究产品可靠。

修复方向：先保存这两轮真实失败作为回归样本，模拟实际时序和模型响应形状，然后做一个低成本真实提供商契约冒烟测试，最后做有明确成功标准的三步端到端验收。报告应分开“控制链正确”和“研究交付成功”。

## 建议顺序

1. 验证并修复当前模型的调查→收尾协议及结构化交付；DSML终态回归必须通过。
2. 将压缩变为窗口压力下的必要操作，重新测定交付预算；不要只提高总timeout。
3. 当前任务与历史数据分离，来源完整性明确表达。
4. 补齐请求时序、关联标识及上述故障组合测试。
5. 再生成新的待确认计划，执行真实三步验收。当前第3份600秒预算提案只是待确认草案，尚未证明这些根因已消除。

无需为此整体换成LangGraph或照搬DeerFlow。现有SDK、ResearchPlan、受控执行和冻结授权可以保留；应减少业务执行路径上的无必要模型工作，并补齐协议与阶段验收。

## 证据和外部参考

- 本地实际请求契约：[local-model-contract.json](plan-mode-evidence/reference-audit/local-model-contract.json)。
- 既有运行与摘要证据：[超时诊断](plan-mode-evidence/live-timeout-diagnosis.md)、[运行事件](plan-mode-evidence/live-recovery-step-1-run.json)、[用量审计](plan-mode-evidence/live-recovery-usage-audit.json)。
- [固定DeerFlow版本](https://github.com/bytedance/deer-flow/commit/c89fed0be4c64b4d881942a0fd240f239e239e96)。
- [DeerFlow压缩实现](https://github.com/bytedance/deer-flow/blob/c89fed0be4c64b4d881942a0fd240f239e239e96/backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py)、[配置示例](https://github.com/bytedance/deer-flow/blob/c89fed0be4c64b4d881942a0fd240f239e239e96/config.example.yaml)。
- [DeerFlow模型适配](https://github.com/bytedance/deer-flow/blob/c89fed0be4c64b4d881942a0fd240f239e239e96/backend/packages/harness/deerflow/models/patched_deepseek.py)、[超时配置](https://github.com/bytedance/deer-flow/blob/c89fed0be4c64b4d881942a0fd240f239e239e96/backend/packages/harness/deerflow/config/subagents_config.py)。
- [DeerFlow主Agent组装](https://github.com/bytedance/deer-flow/blob/c89fed0be4c64b4d881942a0fd240f239e239e96/backend/packages/harness/deerflow/agents/lead_agent/agent.py)、[Plan模式](https://github.com/bytedance/deer-flow/blob/c89fed0be4c64b4d881942a0fd240f239e239e96/backend/docs/plan_mode_usage.md)。
- [LangChain结构化输出](https://docs.langchain.com/oss/python/langchain/structured-output)、[LangGraph确认中断](https://docs.langchain.com/oss/python/langgraph/interrupts)。
- [DeerFlow工具问题报告](https://github.com/bytedance/deer-flow/issues/2319)、[上下文问题讨论](https://github.com/bytedance/deer-flow/discussions/5896)、[组件benchmark边界](https://github.com/bytedance/deer-flow/blob/main/backend/scripts/benchmark/context_snapshot/README.md)。
- [DeepSeek Responses协议](https://api-docs.deepseek.com/guides/responses_api/)。
