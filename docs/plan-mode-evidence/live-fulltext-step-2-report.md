## 定性对照表：ReAct 论证循环 vs 先规划+人工确认+再执行

| 维度 | ReAct 论证循环 | 先规划+人工确认+再执行（HITL 批准流程） |
|---|---|---|
| 机制 | LLM 以交织方式生成推理轨迹（Thought）与任务相关动作（Action），形成 Thought→Action→Observation 循环；模型自主决定两者异步出现时机。[ReAct PDF #/texts/0, #/texts/12, #/texts/41] | agent 执行被暂停，直到人工通过 approve/reject 对敏感工具调用做出决定后才恢复执行；OpenAI SDK 用 state.approve(...)/state.reject(...)，LangChain 用 Command(resume={"decisions":[...]})。[OpenAI #/texts/1, #/texts/221；LangChain #/texts/0, #/texts/174] |
| 典型失败模式 | 三类：reasoning error（错误推理轨迹，含无法从重复步骤恢复，HotpotQA 上 47%）、search result error（搜索无结果或无用，23%）；论文在 HotpotQA 上未观察到逻辑幻觉（hallucination 0%）。[ReAct PDF #/texts/14] | 人工审核延迟或拒绝导致的执行中断；过度批准（always_approve=True）会削弱安全护栏；需人工恢复流，异步协作成本与误批准风险。[OpenAI #/texts/221, #/texts/273（归纳）] |
| 适用场景 | 需要外部信息交互、可更新知识、可解释轨迹的知识问答（HotpotQA）、事实核查（Fever）、交互决策（ALFWorld/WebShop）；一两轮少样本即可启动。[ReAct PDF #/texts/11, #/texts/0] | 敏感工具调用（写文件、执行 SQL 等）、高后果操作、需要合规或审计把关的动作。[LangChain #/texts/0；OpenAI #/texts/1（归纳）] |
| 成本构成（定性） | 每次交互产生额外行动与观察 token、多步轨迹带来的上下文增长；无人工监督成本。[ReAct PDF 无实测数字（归纳）] | 模型暂停等待造成延迟与人时成本；每次中断需要状态持久化与恢复；人工审核成本随敏感调用频次线性增加。[OpenAI #/texts/221；LangChain #/texts/174（归纳）] |
| 可靠性 | 通过外部检索缓解事实幻觉与错误传播，但动作循环消耗资源且性能因任务而异（HotpotQA 27.4 略低于 CoT，Fever 60.9 略高）。[ReAct PDF #/texts/0, #/texts/13] | 可靠性来自人工把关，但 100% 可靠取决于人；过度批准或误判仍可引入错误。[归纳] |

## 逐项来源依据

- 机制（ReAct）：#/texts/0 摘要明确推理轨迹与任务相关动作以交织方式生成；#/texts/41 展示完整 Thought→Action→Observation 轨迹；#/texts/12 明确模型自主决定异步出现时机。
- 机制（HITL）：OpenAI #/texts/1 明确暂停执行直到人工批准或拒绝敏感工具调用；OpenAI #/texts/221 展示 approve/reject 与恢复执行；LangChain #/texts/0 明确中间件暂停等待决策；LangChain #/texts/174 展示批准后恢复。
- 失败模式（ReAct）：#/texts/14 提供 Table 2 中 reasoning error 47%、search result error 23%、hallucination 0% 等数值。
- 失败模式（HITL）：官方文档未提供失败模式统计；待批准/拒绝流程与 always_approve 在 OpenAI #/texts/221、#/texts/273 有代码佐证，具体风险评估属归纳。
- 适用场景（ReAct）：#/texts/11 明确适用于 HotpotQA、Fever、ALFWorld、WebShop 等任务；#/texts/0 强调通过 Wikipedia API 交互。
- 适用场景（HITL）：LangChain #/texts/0 明确写文件、执行 SQL 等需审查动作。
- 成本构成：两份 HITL 官方文档与 ReAct 论文均未提供 token 数量、时延或人力成本的实测数值，定性描述基于流程机制归纳。
- 可靠性：ReAct 论文 #/texts/0 与 #/texts/13 提供任务表现数据（HotpotQA EM 27.4、Fever Acc 60.9）；HITL 可靠性来源为人工把关本身，属流程归纳。
