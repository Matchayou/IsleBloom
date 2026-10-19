# 引用与同比修复及八题复验

## 修复内容

旧现金流复验被拒绝的直接原因是第五条声明：模型为“历史里提到的未批准 v2”构造了文件引用，把同一个 64 位工作记忆 ID 填入 resource_id、snapshot_id、parse_id。前四条年报引用本身正确。生产 EvidenceService 按固定输入校验后拒绝了整份结果。

生产摘要指令存在歧义：它在介绍 context source_id 后要求 “Copy the full reference unchanged when citing it”，容易让模型把上下文检索标识误用作文件引用。本次修改：

- 摘要渲染明确 source_id、artifact_id 和 summary_artifact 仅用于读取历史，不能填入文件引用的三个 ID。
- 专家指令和 CitationDraft 字段说明要求完整复制资料工具返回的 ResourceRef、位置和原文。
- 只有历史依据的声明保留历史来源，并说明缺少的原始资料；不会伪造文件引用，也不会丢弃相关历史约束。
- 原有固定引用范围及逐字引文校验保持有效；没有删除错误引用后强行放行的处理。

旧现金流正文把“2023 与 2024 年份不同”当作不能同比的原因，还要求同一年度资料。本次在通用分析原则中明确：同比比较上一年同期，年份不同本身正常；应判断指标、主体、币种/单位、统计口径与期间覆盖长度是否可比。指令没有写入八题的预期数值或答案。

涉及生产文件：`src/agent_core/context/compaction.py`、`src/agent_core/evidence/models.py`、`src/agent_core/prompts.py`。

## 回归验证

专用临时 PostgreSQL + 真实 SDK + 确定性模型响应，执行：

```text
tests/test_prompt_harness.py
tests/test_compaction.py
tests/test_compaction_controlled.py
tests/test_evidence.py
tests/test_research_quality_eval.py
tests/test_tasks.py
tests/test_methods.py
162 passed in 32.37s，0 skipped
```

新增反例复现“记忆哈希填入文件引用”的交付拒绝；新增正例确认“有效文件引用 + 明示证据缺口的历史约束”可以正常保存并保持待审核。确定性测试验证执行合同，不代表真实模型能力。

## 冻结复验

新批次：`.agent-evals/baseline-fixed-2026-10-11`。八题每题三次，共 24 次，随机打散顺序；使用现有 `deepseek-flash` / `https://api.deepseek.com`。每次独立数据库、资料和私有目录。只发送合成评测资料。

冻结协议 SHA-256：`60f66f1e00b305c80ced8d6599fcec890511d2f1fb5b83b8c2ae743e614da6d5`。每题最多 14 次模型调用、12 次工具调用、360 秒；摘要与重试共用预算。运行中不修改源码、固定答案或评分规则。

旧 smoke 与 cashflow-recheck 批次保留原样；这是修复后开发基线，不是随机配对的版本提升实验。

首轮实际执行 24/24：15 次正式交付，9 次失败。失败中 8 次为引用的可选元数据被模型改写或拼接，1 次为读取不存在的私有笔记。总模型调用 104 次，已知 token 745,199，用量记录完整。没有把失败草稿算作交付，也没有修改冻结评分规则。

## 第二层修复：引用错误的有限修正

首轮表明仅修改指令不足以稳定完成引用交付。复用现有 SDK 输出修正循环，加入只读的证据预校验：

- 专家输出先调用现有 EvidenceService.prepare；原文匹配错误作为逐项校验反馈返回。
- 格式和引用修正共用最多三次无效输出的上限，且始终受原任务模型调用与时间预算约束。
- 修正阶段关闭工具，只能使用已有资料；不重新调查。可选元数据不能逐字填写时置空，解释留在正文。
- 引用越权、资源撤销和生命周期错误直接终止，不进入修正。
- 错误草稿和错误原因保存在 output_repairs；发布时仍再次执行原有权威引用校验。没有自动删除错误引用或放宽原文匹配。

涉及 `src/agent_core/contracts.py`、`src/agent_core/adapters/openai.py`、`src/agent_core/tasks/service.py`、`src/agent_core/controlled/engine.py`。新增验证器是否启用也进入运行身份哈希。

第二层修复测试：第一组 214 passed、9 skipped；为缺少 CE4 数据库而跳过的九项单独配置临时数据库后，9 passed。合计 223 项全部实际通过，包含真实 PostgreSQL、SDK、预算、错误草稿、权限拒绝、交付和检查点测试；全部使用确定性模型响应。

最终完整复验另建批次 `.agent-evals/baseline-validated-2026-10-11`，八题各三次。协议 SHA-256：`15a7e9af610ac54a01c73b90b7f494bcbea9e6f475843acb9271c0327e962f00`。模型、题目、评分规则、顺序种子及预算与首轮相同，生产源码重新冻结。

## 数据保存与磁盘处理

首轮结束后磁盘不足。清理项目虚拟环境中可自动重建的 Python 字节码，并将本任务临时评测库导出为 pg_dump 自定义格式。24 份备份均校验目录及 SHA-256；一个因磁盘不足而处于未完成删除状态的临时库恢复连接后重新导出，并已在新库中实际恢复成功。随后移除已备份的临时在线副本及本任务的旧确定性测试库。原始评测 JSON、工具轨迹、模型输出及旧批次均保留。

最终批次同样完成 24 份数据库备份及 SHA-256 核验，临时在线副本已清理，本任务 PostgreSQL 已停止。最终冻结源码检查通过。

## 最终结果

24/24 正常交付，24/24 程序检查通过；独立保存的 Codex 非盲法 AI 逐项复核为 19/24，通过率 79.2%。三次现金流引用错误都在原预算内修正成功。同比规则虽已改正，正文仍存在基期方向、分子解释和跨年可比性矛盾等残余问题，不能宣称语义质量全部修好。

人工评审记录仍待审。完整解读、八题分项成绩、五次语义失败原句和证据链接见[八题评测结果](</Users/chenyouran/Documents/ChatGPT/New project 2/docs/agent-evaluation-results-2026-10-11.md>)。
