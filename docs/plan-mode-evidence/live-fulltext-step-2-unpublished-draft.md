# 未交付草稿

这份草稿来自失败运行的结构化响应，尚未通过引用校验，不是正式研究结果。预算保护在下一次模型请求前停止执行。

本步骤基于步骤1已核对的三份一级资料（ReAct 论文 PDF v3 全文、OpenAI Agents SDK 官方 HITL 文档、LangChain 官方 HITL 文档）补齐失败模式、适用场景与成本构成的定性证据，产出对照表及逐项来源。

## 定性对照表（分类标签为归纳）

| 维度 | ReAct 论证循环 | 先规划 + 人工确认 + 再执行 |
|---|---|---|
| 机制 | LLM 以交织方式生成 reasoning trace 与 task-specific action，Thought → Action → Observation 循环，模型自主决定 thought/action 出现时机（arXiv PDF v3 #/texts/0、#/texts/41、#/texts/12） | agent 执行前先形成计划工具调用，敏感工具声明需批准；HITL 中间件检查每个工具调用，暂停执行等待人工决定，通过 approve/reject 或 Command(resume=...) 恢复（OpenAI #/texts/1、#/texts/221；LangChain #/texts/0、#/texts/129、#/texts/174） |
| 失败模式 | HotpotQA 上 ReAct 主要失败为 reasoning error（47%）与 search result error（23%）；论文指出纯 CoT 会导致 fact hallucination 与 error propagation，ReAct 通过外部环境接地缓解（#/texts/14、#/texts/10） | 官方文档侧重描述暂停-批准/拒绝机制，未给出失败率数字；归纳为：工具调用被误批准、延迟/阻塞、策略配置遗漏导致敏感操作未被拦截（LangChain #/texts/0 表明策略检查是拦截前提） |
| 适用场景 | QA、事实核查、文本游戏、网页导航等需要与外部环境交互、检索知识的任务；通过 ground truth 接口纠正推理（#/texts/11） | 写文件、执行 SQL 等可能需审查的敏感操作；需要人工监督、可暂停恢复的流式 agent 执行（LangChain #/texts/0、#/texts/120；OpenAI #/texts/1） |
| 成本构成 | 与外部环境动作多次交织，每次循环需生成推理 token 并触发工具，成本随轨迹增长；论文没有给出具体 token 或美元数字（#/texts/0、#/texts/41，归纳） | 除模型生成 token 外，还包括人工审核者的时间成本、中断时的暂停等待成本；文档未给出具体数字（OpenAI #/texts/1、LangChain #/texts/0，归纳） |
| 可靠性 | HotpotQA 上 ReAct True positive 94%，False positive 6%，Hallucination 0%；Fever 上 60.9 优于 CoT（#/texts/14、#/texts/11） | 官方文档未给出可靠性数字；可靠性来源为人工监督，但没有量化数据（归纳） |

## 关键结论

- ReAct 论文提供系统性的失败模式统计，显示主要失败来自 reasoning error 与 search result error，而不是幻觉本身；与 CoT 相比，ReAct 通过外部接地显著降低幻觉（表2）。
- HITL 官方文档只描述"暂停—批准/拒绝—恢复"的控制流，没有实验数字。
- 成本构成：两份 HITL 文档均未给出任何定量成本数据；ReAct 论文也没有给出 token 成本或延迟测量。

## 允许的未知

- 两范式的 token 成本、美元成本、延迟数字在所有三份资料中均无实测值，已明确标为未知，不构成 blocking。
