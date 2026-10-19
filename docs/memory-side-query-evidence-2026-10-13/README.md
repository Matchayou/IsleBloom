# 2026-10-13 真实模型记忆检索与 PostgreSQL 验收

使用 `.env` 中获授权的 DeepSeek Responses 凭据、真实 `deepseek-flash`、本机独立 PostgreSQL 测试数据库。所有模型输出均来自真实供应商响应，没有替换模型响应。数据为固定合成记忆；每个案例使用独立 owner，并保留输入、响应、用量、数据库操作回执、HTTP 发送记录与 checkpoint。

## 检索质量

语料包括中文企业现金流经验、英文会议时区规则、英文不可逆操作审批规则，以及园艺、钢琴干扰项和不可用草稿。中文同义表达、英文同义表达、中查英、英查中、无关查询各执行两次；根据运行前固定的期望 ID 集合判断，额外命中同样计错。

| 指标 | 首次结果 | 收紧选择规则后 |
| --- | --- | --- |
| 固定查询 | 10 | 10 |
| 完全一致 | 6/10 | 10/10 |
| 应命中且命中 | 8 | 8 |
| 额外命中 | 5 | 0 |
| 漏检 | 0 | 0 |
| 微平均精确率 | 61.54% | 100% |
| 微平均召回率 | 100% | 100% |

首次结果中的弱关联干扰项揭示了提示中的边界不足：模型有时补入泛化审批提醒，即使当前问题只涉及财务或会议。修正明确只选择直接适用的记忆，排除假设中的操作和弱关联，limit 是上限而非应填满的数量。原始首次证据完整保留，未修改预设正确答案。

开轮选择和 `search_memory` 都与期望 ID 集合一致。相同查询及候选共用一条已确认的 side query 回执，因此每个固定案例只发送一次轻量模型查询。另行运行了查询切换案例：开轮检索中文财务经验，运行中改问上海与纽约会议，真实轻量模型重新查询并返回英文时区记忆；两条不同查询对应两次 HTTP 发送、两条 confirmed 模型操作。

- [首次质量与恢复汇总](live/summary.json)
- [最终质量与恢复汇总](final/summary.json)
- [运行中切换查询汇总](runtime-query-switch/summary.json)

## 强杀与恢复

每项均在真实模型调用链中暂停子进程，确认 `SIGKILL` 退出码为 `-9`，然后以新的进程、相同 run ID、相同数据库和私有目录恢复。

| 强杀位置 | 恢复结果 | 恢复前后 side query HTTP 发送次数 |
| --- | --- | --- |
| side query 响应及用量已原子保存，尚未保存自动选择 | 成功，复用模型回执 | 1 → 1 |
| 自动选择 checkpoint 已保存，尚未调用主模型 | 成功，复用选择 | 1 → 1 |
| `search_memory` 工具回执已保存，尚未保存后续 SDK 状态 | 成功，复用工具回执 | 1 → 1 |
| HTTP 200 已收到，模型操作回执尚未确认 | 停止，`memory_query_unconfirmed`，用量仍标未知 | 1 → 1 |

已确认结果不重新查询；未确认结果不自动重发收费请求，也不声称用量完整。所有检查比较实际 HTTP 发送记录与 PostgreSQL 操作数，无重试操作 ID。主模型可能先发现工具，再检索，故主模型调用次数按真实操作回执核对，不假设固定为两次。

各恢复目录含 `kill.json`、`http.jsonl`、`seed.json`、`worker.log` 和完整 `evidence.json`：

- [模型响应回执后](final/after_side_receipt/evidence.json)
- [自动选择保存后](final/after_initial_selection/evidence.json)
- [运行中工具回执后](final/after_runtime_receipt/evidence.json)
- [响应未确认](final/unconfirmed_side_response/evidence.json)

模型名、查询提示、输出 schema 和分批参数属于冻结合同；提示变化不会悄悄重查旧运行。

## PostgreSQL 与回归

记忆 CRUD、跨进程持久化、checkpoint fencing、模型/工具回执及九个原有强杀场景（含 DBOS）共 **30 项通过，无跳过**。新增记忆合同变化拒绝与 side query 单元/SDK 验证共 **21 项通过，无跳过**；其中 checkpoint 检查连接真实 PostgreSQL。动态上下文相关回归另有 **30 项通过**。

- [PostgreSQL 测试结果](postgres-results.xml)
- [冻结合同与 side query 测试结果](frozen-contract-results.xml)
- [原有强杀场景证据](postgres/)

首次运行暴露了测试隔离问题：旧强杀 worker 使用公共 owner，读到了其他测试留下的记忆。worker 已按持久化工作目录生成独立 owner；目录断言也允许同 owner 已存在的其他获准条目。最终回归已复测通过。`revised/` 是 owner 未完全隔离的中止批次，不纳入质量指标；正式重跑 `final/` 使用完整实验目录哈希隔离 owner。

验收脚本的最终统计按数据库 `confirmed` 状态、相同查询的回执复用规则和真实主模型操作数计算。早期控制台统计存在固定调用数假设，最终 JSON 汇总依据保存的原始证据重新统计，检索结果和 HTTP 记录未改写。

## 复现与边界

先创建名称以 `memory_side_live_` 开头的独立测试数据库，再运行：

```sh
.venv/bin/python scripts/verify_memory_side_query_live.py \
  --database postgresql+psycopg://USER@127.0.0.1:PORT/memory_side_live_FRESH \
  --directory docs/memory-side-query-evidence-FRESH --repeats 2
```

凭据在进程内从 `.env` 加载，不写入 argv、日志或证据。`--report` 只读取证据重算统计，不调用模型。当前默认还包含运行中切换查询案例；本次固定五类对照和独立切换案例分别保存。

这些结果证明小型固定语料上的语义匹配和当前合同下的恢复行为。提示修正使用了首次失败反馈，因此后续重复属于回归验证，不是独立留出集，不代表大规模或任意业务语料的检索准确率。尚未验证大量记忆分批合并的真实模型质量；分批协议已有确定性测试。未确认操作仍可能产生供应商费用，不能把“不重发”解释为“未计费”。
