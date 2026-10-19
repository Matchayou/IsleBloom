## 一、定性对照表：ReAct 论证循环 vs 先规划+人工确认+再执行

| 维度 | ReAct 论证循环 | 先规划+人工确认+再执行（HITL 批准流程） |
|---|---|---|
| 核心机制 | LLM 以交织方式（interleaved manner）同时生成推理轨迹（Thought）与任务相关动作（Action），形成 Thought→Action→Observation 循环；模型自主决定两者异步出现的时机。 | Agent 执行被暂停，直到人工通过 approve/reject 对敏感工具调用做出决定后恢复执行；OpenAI Agents SDK 用 state.approve(...)/state.reject(...)，LangChain 用 Command(resume={"decisions":[...]})。 |
| 控制权归属 | 完全交给模型：LLM 自主决定何时思考、何时动作、何时观察。 | 关键节点交给人工：敏感动作必须经人批准才执行，控制权在拦截点转移。 |
| 典型失败模式 | 三类：reasoning error（错误推理轨迹，含无法从重复步骤恢复）、search result error（搜索无结果或无用）；HotpotQA 随机抽样中 reasoning error 47%、search result error 23%、hallucination 0%。 | 人工审核延迟或拒绝导致执行中断；过度批准（always_approve=True）绕过人工把关；误批准/误拒绝风险。 |
| 适用场景 | 需要外部信息交互、可更新知识、可解释轨迹的知识问答（HotpotQA）、事实核查（Fever）、交互决策（ALFWorld/WebShop）；一两轮少样本即可启动。 | 敏感工具调用（写文件、执行 SQL 等）、高后果操作、需要合规或审计把关的动作。 |
| 成本构成（定性） | 每次交互产生额外行动与观察 token，多步轨迹带来的上下文增长；无人工监督成本；论文未给实测数字。 | 模型暂停等待造成延迟与人时成本；每次中断需要状态持久化与恢复；人工审核成本随敏感调用频次线性增加；官方文档未给实测数字。 |
| 可靠性来源 | 通过外部检索缓解事实幻觉与错误传播，但性能因任务而异（HotpotQA 27.4 略低于 CoT，Fever 60.9 略高）。 | 来自人工把关本身，但可靠性取决于人；过度批准或误判仍可引入错误。 |

## 二、来源依据

### 2.1 ReAct 机制与性能

ReAct 论文摘要直接描述核心机制：“In this paper, we explore the use of LLMs to generate both reasoning traces and task-speciﬁc actions in an interleaved manner”（#/texts/0）。轨迹格式为 Thought→Action→Observation 循环，论文实证展示“Thought 1: I need to search Bermuda Triangle…… Action 1: Search[Bermuda Triangle] Observation 1……”（#/texts/41），并说明“we let the language model decide the asynchronous occurrence of thoughts and actions for itself”（#/texts/12），即模型自主决定思考与动作时机。

性能方面，论文 Table 1 给出 HotpotQA 上 ReAct 为 27.4、CoT 为 29.4、CoT-SC 为 33.4、Act 为 25.7；Fever 上 ReAct 为 60.9、CoT 为 56.3、CoT-SC 为 60.4、Act 为 58.9（#/texts/13）。失败模式取自 Table 2 的人工评估抽样：#/texts/14 显示 reasoning error 47%、search result error 23%、hallucination 0%（56% 为相邻列数值，属表格排版保留，不并入本体百分比）。任务泛化证据为“On ALFWorld and WebShop, two or even one-shot ReAct prompting is able to outperform imitation or reinforcement learning methods trained with 103∼ 105 task instances”（#/texts/11）。

### 2.2 两套 HITL 机制

OpenAI Agents SDK 官方文档明确：“Use the human-in-the-loop (HITL) flow to pause agent execution until a person approves or rejects sensitive tool calls”（#/texts/1）。恢复执行经由代码示例 state.approve(interruption, always_approve=False) / state.reject(interruption) → await Runner.run(agent, state)（#/texts/221）。另存在 state.approve(interruption, always_approve=True)（#/texts/273），可以自动批准中断，构成过度批准风险的代码佐证。LangChain 文档描述：“When a model proposes an action that might require review—for example, writing to a file or executing SQL—the middleware can pause execution and wait for a decision”（#/texts/0），批准后以 Command(resume={"decisions": [{"type": "approve"}]}) 恢复流式执行（#/texts/174）。两者差异仅在 API 形式：OpenAI 用 interruption 对象加 state 方法，LangChain 用中间件加 Command 恢复。

### 2.3 成本与可靠性缺失确认

ReAct PDF v3 全文未给出任何 token 数量、API 费用、机器成本、时延的实测数字；OpenAI HITL 文档与 LangChain HITL 文档均未给出人工时延、人力成本、审核吞吐量或被审查动作占比的量化统计。对照表中成本构成列仅作基于流程机制的定性归纳，未编造数字。可靠性方面，HITL 官方文档未提供任何失败模式统计；always_approve=True 的精确风险后果属归纳而非原文陈述。ReAct 与 HITL 在可靠性维度无同基准可比数字，ReAct 有任务性能数值，HITL 无对应量化指标。

## 三、工作流建议

1. **默认采用 HITL 拦截敏感动作，内部复杂推理用 ReAct 式循环。** ReAct 适合需要外部检索与多步推理的知识密集型任务，HITL 适合写文件、执行 SQL 等有副作用或高后果动作。两者可以叠加：在 ReAct 推理循环中，当 Action 属于敏感类型时，由中间件暂停等待人工批准。

2. **对『审批通过后自动放行同类动作』要慎用。** OpenAI 的 always_approve=True 能降低重复审核成本，但会绕过人工把关。建议将自动批准限制为已审计的只读查询、白名单操作或低风险动作，写操作与删除操作不得自动放行。

3. **用可观测性补齐 HITL 的可靠性缺口。** 官方文档没有提供失败模式统计，工程落地上应自建日志采集：中断频率、批准/拒绝率、端到端时延、每次中断的 waiting time。实测失败率与拒绝率产出后，可与 HITL 机制描述结合形成真实可靠性基线。

4. **成本预估靠自测而非论文/文档。** 三个来源都缺少 token、费用与延迟实测值，依赖它们做预算会失真。应在 PoC 阶段测量每轮 ReAct 轨迹的平均 token、每类敏感动作的 HITL 中断时延、人力审核时长，才能形成可比的“ReAct 自主循环 vs HITL 暂停等待”成本曲线。

5. **HITL 的可靠性上限取决于人。** 若审核员快速点“批准”而不看内容，有效可靠性会退化到接近无监督执行；应在工作流中周期性复核已批准动作样本，并对拒绝率异常低的动作类型重新评估风险阈值。

## 四、研究局限

- **实测费用不可得。** ReAct 论文及两份 HITL 官方文档均未提供 API 调用费用、机器成本、token 成本实测数字。
- **Token/时延实测不可得。** 没有任何来源给出每轮轨迹 token 数、人工审核时延、暂停等待时长或端到端延迟实测数字。
- **失败率实测不可得。** ReAct 的 47%/23%/0% 仅来自 HotpotQA 随机抽样人工评估，非全量或跨任务通用；HITL 没有任何失败率统计。
- **可靠性维度无同基准可比数字。** 本报告未对 ReAct 与 HITL 进行数字意义上的可靠性对比，仅作定性机制对照。
- **热点失败模式为机制归纳。** 人工延迟、over-approval、误批准未在官方文档中出现逐字表述；always_approve=True 有代码位置佐证，但风险后果属推断。
- **本报告有限资料边界。** 本步骤仅综合两份已校验的上游结果；前序步骤未对本轮 mentions 中的 Google Research Blog 与 arXiv HTML v3 补充资料逐段比对，这两份的正文未被纳入本表证据。
