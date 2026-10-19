# 固定研究任务评测

参考 [DeerFlow 2.0 上下文专项](https://github.com/bytedance/deer-flow/blob/main/backend/scripts/benchmark/context_snapshot/README.md)：固定案例、真实生产组件、保存产物独立评分、产物正确与正常完成分开报告。这里评测专家交付，主助手真实派发与汇总属于后续独立的端到端评测。

八道开发题覆盖设备/现金流/订单、冲突材料、获准修订、历史行为、无关长历史和不可信资料指令。Q01–Q03 从 CE8 固定材料及历史改编，增加公开结构字段要求；Q04 使用固定专家报告，不能据此声称真实多专家协作通过。预期答案、语义 rubric 和参考正文不会输入 Agent。

## 执行

从项目根目录运行，使用已安装的锁定依赖。无需新包。`OPENAI_API_KEY` 仅通过环境传入；`OPENAI_MODEL`、`OPENAI_BASE_URL` 指定项目现用的 Responses 模型。命令不会自动读取 `.env`。

```sh
# 首次仅两题，各一次；plan 不调用模型。
uv run python -m evals.research_quality plan --output .agent-evals/smoke --cases Q01 Q02 --repeats 1

# EVAL_DATABASE_ADMIN_URL 指向专用 PostgreSQL 实例，可创建数据库。
# 例如 postgresql+psycopg://USER@127.0.0.1:PORT/postgres
uv run python -m evals.research_quality run .agent-evals/smoke --live

# 只读产品证据，更新评分包与 JSON/Markdown 汇总；不调用模型。
uv run python -m evals.research_quality report .agent-evals/smoke
```

每个 trial 创建 `evalq_<随机ID>` 数据库，并使用独立 blobs/private 目录。不会读取 `DATABASE_URL`，不会复用、清空或自动删除已有数据库。数据库名称记在 trial.json，数据库与原始证据由操作者统一保存/清理。模型调用最多 14 次、工具最多 12 次、产品执行上限 360 秒/题；摘要和内部重试共用上限。费用依赖所配模型，报告没有价格数据时保持未知。

批次开始前冻结所有题目、评分规则、候选指令、源码内容哈希、依赖版本、模型、端点、预算与顺序。执行时发现变更则拒绝继续。模型采样沿用生产设置；`--seed` 只控制评测顺序，不是供应商随机种子。

每次尝试先落盘，再连接数据库或模型。成功、无结果失败、取消和用量不完整均有记录。重复运行同一批次被拒绝；中断后先 report 保存现状，再建立新批次，不拼接两次独立尝试。强杀时 `running` 会保留为未决，不会自动标记成功。生产引擎内部恢复语义没有改变。run 对执行失败返回非零退出码；语义待审不伪装为运行故障。

## 人工语义评审

每个 `trials/<id>/` 中有：

- `trial.json`：状态、身份、耗时、数据库名和证据哈希。
- `evidence.json`：正式回执、完整结果、固定资料、工具响应、模型操作账本、请求清单。
- `review-packet.json`：供独立评审使用的脱敏材料，不带 arm、模型或成本；原文和任务保留。
- `review.json`：人工填写的逐项 `pass / fail / unknown`、报告与原文位置、评审者。
- `grade.json`：程序检查、过程检查和语义项。

只向独立评审者提供 `review-packet.json` 及对应 `review.json`，不要提供目录中的执行与模型信息。填写 review.json 的 reviewer；每项使用 status 和 evidence，pass/fail 必须说明报告位置及其来源/轨迹依据。保持 evidence_sha256 和 rubric_sha256 不变。重新运行 report 即可合并。过期、损坏或不完整评审保持待定，不自动放行。

评测只记录独立评分，不调用正式审核或采用接口。`pending_review` 不是质量失败，也不等于已经通过。初版没有自动语义裁判：正文与字段矛盾需要独立审阅，不能仅靠关键词验收。

report 会校验冻结清单和 trial 身份。每次评分记录实际评分器与 rubric 哈希；评分器源码与运行时不同时，报告明确标记为重评，不与旧评分版本混合。修改已冻结答案会被拒绝，应另建批次。

## 指标

- `artifact_correct`：正式产物 hash、来源版本、必需字段与全部语义项通过。未评语义为 null；已确认错误为 false。
- `clean_success`：产物正确，加上正常执行、资料真实读取、无越权请求、无联网、预算合规和无强制收尾。`reserved_turns/tool_budget/reserved_time` 等触发的受限收尾单列为流程未通过，即使产物正确。
- 已知 token、用量完整性、模型/工具调用数与延迟。以模型操作账本去重合计，含摘要、失败及内部重试，绝不再叠加 RunResult 的同一份用量。任一试验用量不完整，批次平均 token 为 null。调用次数完整性与 token 完整性分开；未确认调用仍可有已知调用次数。
- `execution_seconds` 计任务服务调用到返回/失败；`latency_seconds` 另计含数据库初始化和证据采集的完整 trial 耗时。缺少早期证据中的执行耗时时保持 null，不从总耗时估算。
- 所有计划 trial 保留在分母。待审和失败分别报告；`confirmed_clean_success_rate` 是已确认成功占计划次数比例，仍有待审时不能当最终质量成绩。

数值接受等价十进制表示；类别字段使用 cases.json 中的明确等价词。引用语义、期间口径和正文事实由独立评审核对。可在冻结新批次前补充等价表达，不能为让当前结果通过而改写已冻结评分。

## 基线和 A/B

```sh
uv run python -m evals.research_quality plan --output .agent-evals/baseline --cases Q01 Q02 Q03 Q04 Q05 Q06 Q07 Q08 --repeats 3

# 仅在已有具体候选时使用：48 次任务，不是 48 次模型调用。
uv run python -m evals.research_quality plan --output .agent-evals/paired --cases Q01 Q02 Q03 Q04 Q05 Q06 Q07 Q08 --repeats 3 --candidate-instructions /absolute/path/candidate.txt
```

目前唯一 A/B 变量是 `expert_synthesize` 的 method instructions：在每个候选专用数据库内，用现有版本化方法服务保存并激活候选，其他字段保持不变。基线来自当前源码初始化的默认方法，版本记录在 manifest 中，不读取已有产品数据库内的自定义方法。生产数据库不变；候选与基线相同会失败。不要在两臂中另改模型、工具、预算或题目。顺序按随机打散的案例/重复配对，相邻执行并交替臂顺序；报告逐对正常完成差异，未知不作胜负。

先用两题验证评分闭环，再建立 24 次当前基线；正式对照重新配对运行两臂。八题是开发回归集，三次重复不是三个独立新题，不给泛化或显著提升结论。

## 验证评测器

```sh
uv run python -m pytest tests/test_research_quality_eval.py -q
# 可选：真实 PostgreSQL + SDK + 受控工具，使用确定性模型响应。
QUALITY_TEST_DATABASE_ADMIN_URL="$EVAL_DATABASE_ADMIN_URL" uv run python -m pytest tests/test_research_quality_eval.py -q
```

校准覆盖参考产物、数值/版本错误、空报告、旧 hash、正文矛盾、待审/失效评分、越权读取、失败用量、中断登记，以及真实数据库的产物采集与候选隔离。确定性响应的测试成绩不是 Agent 能力分数。真实模型调用只有显式 `run --live` 才发生，不进入默认 CI。
