资料清单（3 份关键一级资料，均已确认全文可用并定位到机制证据）：

1. ReAct: Synergizing Reasoning and Acting in Language Models（arXiv PDF v3 全文）
   - 标题：ReAct: Synergizing Reasoning and Acting in Language Models (ICLR 2023)
   - URL：https://arxiv.org/pdf/2210.03629v3
   - 全文状态：可用（PDF v3 全文已抓取并解析）
   - 位置：#/texts/0（摘要）
   - 短摘录："In this paper, we explore the use of LLMs to generate both reasoning traces and task-speciﬁc actions in an interleaved manner..."（thought/act 交织机制）
   - 补充位置：#/texts/41 展示 Thought 1 → Action 1 → Observation 1 → Thought 2 → Action 2 的完整轨迹；#/texts/12 说明模型自主决定 thought/action 出现时机。

2. Human-in-the-loop — OpenAI Agents SDK（官方文档）
   - 标题：Human-in-the-loop
   - URL：https://openai.github.io/openai-agents-python/human_in_the_loop/
   - 全文状态：可用（官方文档全文已抓取并解析）
   - 位置：#/texts/1
   - 短摘录："Use the human-in-the-loop (HITL) flow to pause agent execution until a person approves or rejects sensitive tool calls."
   - 补充位置：#/texts/221 展示批准/拒绝与恢复执行：state.approve(interruption, always_approve=False) / state.reject(interruption) → await Runner.run(agent, state)。

3. Human-in-the-loop — Docs by LangChain（官方文档）
   - 标题：Human-in-the-loop
   - URL：https://docs.langchain.com/oss/python/langchain/human-in-the-loop
   - 全文状态：可用（官方文档全文已抓取并解析）
   - 位置：#/texts/0
   - 短摘录："When a model proposes an action that might require review—for example, writing to a file or executing SQL—the middleware can pause execution and wait for a decision."
   - 补充位置：#/texts/174 展示人工批准后恢复：Command(resume={"decisions": [{"type": "approve"}]})。

关键证据缺失：无。ReAct 的 thought/act 交织机制与两套 HITL 的 approve/plan 流程均已定位到带位置的短摘录，三份资料全文状态均确认可用。
