# R2 Jev 专家选择与模型路由：实现及验收交接

> 历史验收记录。当前运行时代码已移除 Jev，改为 [RouteLLM 模型路由](routing-routellm.md)；以下配置与测试结论仅描述 2026-10-01 的 R2 基线，不适用于当前部署。

日期：2026-10-01。状态：**离线控制、执行连通与 PostgreSQL 累计回归通过；真实 Jev 与双执行模型质量/成本验证待配置，因此 R2 产品验收尚未完成。** R1 的真人语义签收仍待办；本记录不把 Codex 或脚本检查当作真人签收。

## 复用依据和边界

- 复用项目现有 `TaskService`、`ContextBuilder`、`ScopeService`、`Dispatcher`、`CoreRunner`、`OpenAIEngine` 和同一份 R1 回执。锁定的 Agents SDK 内部 `Agent.model` 是每 Agent 字段；本项目按任务生成独立 `AgentSpec`，没有修改共享执行器的模型。R2 不创建第二套运行循环。
- [TypeSafe 官方 API](https://docs.typesafe.ai/api)规定 `POST /v1/systemone`、`Choice` 的 `criteria`、`choice`、`probabilities`、`confidence` 及 `usage`。适配器只实现这一契约，使用项目现有 `httpx`，没有增加依赖。
- [JevRouter](https://github.com/BillionsBobby/JevRouter) 的公开说明将候选预筛、原始概率与实际执行分开；[prismhq/jev-router](https://github.com/prismhq/jev-router) 的说明记录模型能力预筛和最小外发摘要。它们的运行时未导入，本轮没有复制或声称验证其内部代码；本项目的规则是失败阻断，不采用参考项目的静默降级。

## 实现与证据

| R2 要求 | 当前实现 | 证据与界限 |
|---|---|---|
| 两步依赖选择 | `routing/Router` 先筛专家并作一次 Choice，再依所选专家筛模型并作一次 Choice | `test_two_choices_freeze_and_change_actual_execution`；两次真实 Jev 请求待办 |
| 受控候选与排除 | 目录只接受发布方法的 ID、版本和摘要；模型显式声明任务类型、工具、容量、能力等级、价格及来源；预算、容量在 Jev 前筛除 | `test_low_confidence_api_failure_and_exclusions_block`、`test_invalid_choice_and_budget_exhaustion_never_execute`；运营目录待冻结 |
| 用户指定优先、唯一候选 | 用户指定须通过规则检查；单候选直接记录，不请求 Jev | `test_user_selection_and_only_candidate_skip_jev` |
| 失败阻断 | 无候选、低 confidence、`no_match`、无效响应、API 失败均保存 blocked 记录，零执行调用 | 上述失败测试；正式 confidence 门槛尚需开发样本校准 |
| 配置冻结、任务内保持 | TaskSpec 指向 RouteRecord；记录两步输入、候选描述、排除原因、原始响应、耗时、用量、固定专家和模型配置；继续默认固定专家和模型，显式重选限一次且禁止能力降级 | `test_continuation_keeps_route_and_reselection_is_bounded`、`test_frozen_model_config_and_cumulative_budget` |
| 范围隔离 | Jev 只接收显式获准的任务摘要和受控目录；原始资料/结果用于程序容量预览，不传给 Jev；路由记录绑定研究和资源版本，撤权后查询拒绝 | `test_two_choices_freeze_and_change_actual_execution` 的唯一标记、`test_http_route_record_and_revocation` |
| 实际执行 | 冻结的专家方法和模型进入原 Dispatcher/CoreRunner；回执含 route ID；实际装配后再核容量和剩余估计预算 | `test_two_choices_freeze_and_change_actual_execution`、`test_optional_input_change_rechecks_actual_capacity` |
| R1 交接连通 | R2 提取回执经独立逐项复核后，准确 result ID 进入 R2 分析任务 | `test_routed_extraction_to_reviewed_analysis`；测试 reviewer 为夹具，不是真人 |
| HTTP/CLI | `task_create` 可指定 `route`、摘要授权、预算和用户优先；`task_route` 可查记录；`agent-tasks create-routed/show/route`，`agent-research --catalog` 可加载目录 | HTTP 测试通过；没有真实模型凭据或浏览器 R2 操作记录 |

最终命令：`TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55441/r2_release PYTHONPATH=src .venv/bin/pytest -q`。结果 **225 passed in 29.36s，无 skipped**，原始尾部输出见 [r2-tests.txt](r2-tests.txt)。R2 专用临时 PostgreSQL 集群为 `/tmp/r2-pg-20261001`，数据库为 `r2_release`；旧 R1 集群及数据库未修改。`python -m compileall -q src tests/test_routing.py` 退出 0。离线 `JevClient` 测试用 `httpx.MockTransport` 验证官方请求形状与密钥只在请求头；不计真实 Jev 调用。

## 真实验收前需要固定

1. 提供可实际访问的 Jev 固定模型版本和运行环境中的 `TYPESAFE_API_KEY`。当前任务进程没有该环境变量；`jev-1.13.0` 仅为适配器默认，不代表该账户已授权。密钥不得写入目录、任务提示或日志。
2. 提供至少两种**已授权且可调用**的执行模型 ID、工具/容量实测依据、输入与输出价格及其来源。目录 JSON 需包含 `version`、`jev_model`、`minimum_confidence`、`models`；每个模型含 `id`、`description`、`available`、`tools`、`supported_kinds`、`capability_rank`、`max_input_chars`、`input_usd_per_million_tokens`、`output_usd_per_million_tokens`、`capability_source`、`price_source`。路由运行还需明确 `budget_usd`。目录不存密钥。执行模型继续使用已授权的 OpenAI 兼容运行配置。
3. 在开发样本上决定并冻结 confidence 门槛、中文研究任务与依赖短回复样本、可接受质量、总费用和阻断率；然后对留出样本比较固定模型、规则选择和真实 Jev 两步路由，记录两次路由、执行、复核、重试的用量与耗时。当前 `0.5` 只是离线控制默认值，未经校准，不能宣称正式质量验收。

`RouteRecord` 保存 Jev token 用量和专家运行回执保存执行 token 用量；美元预算按目录价格和预计/已报告 token 做准入检查。提供方的外部计费与网络失败请求费用尚不能由本程序保证绝对封顶；真实试跑必须对账。装配后输入超出冻结模型容量时阻断执行，需要用明确原因重新路由并创建新任务版本。R2 真实 Jev 选择收益、真实成本、浏览器交互和真人语义签收目前都不能标为通过。
