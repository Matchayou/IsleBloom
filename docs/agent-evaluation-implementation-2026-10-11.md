# 项目评测首版落地与验证

本文保留首版落地时的历史记录。后续已完成引用修复和八题各三次的完整复验，最新结果见[八题评测结果与解读](</Users/chenyouran/Documents/ChatGPT/New project 2/docs/agent-evaluation-results-2026-10-11.md>)。

日期：2026-10-11。首版已可执行，入口为 `python -m evals.research_quality plan/run/report`。使用方法见[评测说明](</Users/chenyouran/Documents/ChatGPT/New project 2/evals/research_quality/README.md>)。

## 已实现范围

- 八道固定开发题，案例和答案分离；基线可重复，方法指令候选可成对 A/B。
- 直接调用现有 TaskService / ControlledEngine；每题独立 PostgreSQL 数据库、资料和私有目录。
- 运行前写入 trial，冻结输入、规则、源码、模型、依赖和预算；失败、取消、无正式产物均保留。
- 检查保存的完整产物 hash、来源版本、数值/字段、真实工具读取、授权边界与正常结束。
- 语义评审包、逐项人工评分记录、证据/rubric 哈希校验；待审不会自动通过，也不改变产品审核状态。
- JSON / Markdown 汇总，包括全部计划次数、失败、未知用量、调用量、延迟及成对差异。费用没有价格依据时为未知。

未引入新依赖，未改生产执行接口、数据库模型或产品审核行为。现行 synthesize 不支持自定义 required_fields 入参，评测在公开任务要求里指定 ExpertAnswer.fields 的键和格式。

## 确定性验证

实际运行：

```text
QUALITY_TEST_DATABASE_ADMIN_URL=<本次独立 PostgreSQL>
python -m pytest tests/test_research_quality_eval.py tests/test_tasks.py tests/test_methods.py -q
69 passed, 28 skipped
```

其中新评测测试 41 项全部通过，包含真实 PostgreSQL + SDK + 受控工具的 Q01/Q02/Q08、候选方法隔离及模型异常用量保留。另有相关服务测试 28 项通过、28 项因未配置它们各自要求的数据库环境变量而跳过；跳过不计为持久化通过。所有测试中的模型响应均为确定性夹具，不是 Agent 能力成绩。

反例校准涵盖错数字、错主体、旧版本、空报告、无回执、过期产物 hash、字段正确但正文矛盾、越权请求、失效评审、修改冻结清单和中断尝试。

## 真实模型验证

使用项目已有 DeepSeek Responses 配置（`deepseek-flash`，`https://api.deepseek.com`），仅发送合成设备/现金流资料、合成历史及产品通用指令；每次使用全新评测数据库。没有使用已有业务数据库或用户文档。每题最多 14 次模型调用、12 次工具调用、360 秒，摘要和重试共享预算。

| 批次 | 任务 | 执行结果 | 模型调用 | 已知 token | 总耗时 |
|---|---|---|---:|---:|---:|
| 初始 smoke | Q01 设备 | 正式保存；程序与过程检查通过；语义待人工审阅 | 6 | 54,074 | 39.291 s |
| 初始 smoke | Q02 现金流 | 正式保存；两项等价主体表述被初版评分器误判 | 5 | 37,524 | 29.777 s |
| 修正后独立复验 | Q02 现金流 | 产品引用校验拒绝，没有正式报告产物 | 5 | 38,778 | 32.220 s |

三次尝试的供应商 token 记录完整，费用未估算。上表耗时为含初始化和证据采集的 trial 总耗时；修正后 Q02 的任务执行耗时另计为 31.010 s。两批规则不同且样本极小，不合并为质量成功率；没有执行 24 次正式基线或 48 次 A/B。

原始报告与证据：

- [初始两题报告](</Users/chenyouran/Documents/ChatGPT/New project 2/.agent-evals/smoke-2026-10-11/report.md>)、[完整 JSON](</Users/chenyouran/Documents/ChatGPT/New project 2/.agent-evals/smoke-2026-10-11/report.json>)。
- [现金流独立复验报告](</Users/chenyouran/Documents/ChatGPT/New project 2/.agent-evals/cashflow-recheck-2026-10-11/report.md>)、[完整 JSON](</Users/chenyouran/Documents/ChatGPT/New project 2/.agent-evals/cashflow-recheck-2026-10-11/report.json>)。

三个 trial 目录均已保存 `database.dump` 和带 SHA-256 的 `database-backup.json`，备份哈希核验通过。本任务启动的临时 PostgreSQL 已停止；只读 report 命令无需启动数据库。原始评测目录 `.agent-evals/` 被 Git 忽略，需要与数据库备份一同保留。

## 本轮发现

1. **评分器误判已经修正并增加反例测试。** 原 Q02 对“合并集团口径”“仅海外子公司”的等价表达过于严格。新案例规则明确接受这些等价词，同时仍拒绝把集团与子公司互换。原批次 manifest 和成绩没有改写；新批次另行冻结。
2. **原 Q02 正文存在待正式语义裁决的推理问题。** 报告把“年度不同：2023 vs 2024”当作不能同比的理由，还要求“相同年度”的对照资料。跨年本身不是同比不可比的理由；这道题的核心限制在于主体、币种及可比口径。该发现来自 Codex 诊断阅读，并非盲法人工评审，不替代 review.json。新 rubric 已明确检查这一问题。
3. **复验捕获了真实交付失败并保留其费用。** 生产校验返回 `evidence_invalid: Citation is outside the fixed run input`。回执为 rework，artifact 为空；评测没有把流式草稿或私有笔记当成成功结果，也没有从统计中删除这次尝试。

首版评测能区分评分规则缺陷、正文推理风险和真实交付失败。当前正式语义审阅仍待完成，不能宣称两题质量通过。

## 边界与下一层

当前是专家组件评测。Q04 的两份专家报告是固定输入；主助手真实派发、专家回传、自动续接和最终用户交付仍需独立端到端专项。真实网络研究、强杀恢复和记忆淘汰不在这八道题的新验收范围内。

首次两题验证已经完成；完成语义校准后，可用现有命令跑八题三次的正式基线。候选明确后再跑 A/B，不以本轮诊断结果冒充版本改进证据。
