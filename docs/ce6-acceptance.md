# CE6 增量压缩验收

2026-10-07–08。状态：当前 CE6 范围验收通过，允许进入 CE7。固定真实语义样本、跨进程/数据库重启与最终综合回归均已完成。

## 当前实现

继续使用 Agents SDK 0.22.2 公开异步 call_model_input_filter、Model 和严格输出 Schema，DBOS 3 与 PostgreSQL 原执行锁不变。完整上下文先外存，确定性缩减先运行，随后以“前次有效记忆+新增完整回合”分批压缩。已有业务历史在主任务回答前逐批推进，运行中旧工具回合继续滚动；失败、无进展或原交付保留线立即停止，没有摘要重试循环。

context/compaction.py 以累计 covered_ids/items 表达有效记忆，中央 attempt 单独记录本批 from/to_cursor 与新增 source_ids。source_id 直接采用完整原文 artifact_id，可用原有 read_context_page 回查，不维护另一套别名。新摘要只有在严格 Schema、完整响应、原文逐字 quote、全部累计来源和实质缩减校验成功后才原子替换 head。原文与旧摘要不覆盖、不删除；摘要正文不在可编辑 notebook 中维护，中央 head 受写者和授权世代约束。

当前要求和全部用户原句优先保留，绝不因摘要删掉。消息呈现为“历史 assistant 记忆 → 原样当前 user 请求 → 近期完整工具回合”；这把历史数据与新用户要求分开，并避免初始请求以合成 assistant continuation 结束。最近两组完整工具回合保留，平行调用/结果不可拆散。选择优先级不等于把历史摘要伪装成最新用户消息。

摘要、格式修复和任务调用都预占原账本，不增加预算。每次摘要重新计算剩余调查时间；finalizing 阶段绝不新启摘要。摘要自身检查窗口、Schema/指令开销、8192输出余量和4096误差空间。当前批次上限20000字节；完整回合过大时记录未调用，不截断工具协议。业务投影同时守住字符契约和保守 UTF-8 输入界限，防止中文字符未超限但字节窗口已超限。

无工具请求显式 tool_choice=none。deepseek-flash 摘要提取使用提供方文档确认的 reasoning.effort=none，主任务设置不变，其他模型不发送未经验证的推理参数。参考[官方 Responses 参数](https://api-docs.deepseek.com/zh-cn/api/create-response/)与[兼容说明](https://api-docs.deepseek.com/guides/responses_api/)（2026-10-08核验）；parallel_tool_calls 在该提供方被忽略，所以实际边界由 SDK 配对及 broker 执行控制保证。

HTTP hook 保存实际 status/incomplete_details/usage；截断与 length 拒绝。错误保留类型、状态、code/param和响应元数据；不可解析正文仅记哈希/长度。cached_tokens 为实际 input_tokens 的子集，另记 SDK 缓存值，不重复加总。发送结果未知保留预占，不能当作零成本。显式窗口拒绝最多重投影一次，输入未缩小就不重复发，第二次拒绝停止。

## 需求—实现—证据

| 要求 | 证据 |
|---|---|
| 完整外存、累计游标、只输入新增批次 | compaction单元与真实PG/SDK；真实 attempt15 0→2→5→7→9 |
| 中段、否定、纠错、版本、已做/未知 | [原始真实记录](ce6-evidence/live-attempt15/live.json)与[独立语义核验](ce6-evidence/live-attempt15/independent-review.md) |
| 空/Schema/length/缺引/无收益不覆盖 | fake模型契约覆盖，成功head不推进；原文可重读 |
| 工具配对、近期保留、用户原句保护 | 平行调用完整分组、未完成组拒绝；当前请求不被摘要替换 |
| 摘要和修复共用预算、保护收尾 | 多批原账本计数；预算预留/阶段finalizing专项；CE4联合回归 |
| 撤权不复用、固定引用回查 | 真实PG scope版本变更后旧摘要读取拒绝；source_id直接回查 |
| 一次窗口补救 | 无缩减停止；长草稿缩减后仅一次重发，再次拒绝停止 |
| 分页错误可修正而不越权 | 授权原文越界offset返回 invalid_input/真实长度/同引用restart_cursor；未知引用仍拒绝，不记虚假阅读 |
| provider缓存不重复计费 | 真实usage逐项相加与中央账本一致；完整wire哈希验证 |
| 新进程、PG重启、保存幂等 | [重启前](ce6-evidence/live-attempt15/before-restart.json)与[重启后](ce6-evidence/live-attempt15/after-restart.json)经cmp完全一致，含实际笔记哈希；模型调用0 |

## 实际运行

独立 PostgreSQL17.11 集群 `.ce2/20261007/postgres`，127.0.0.1:55462；当前成功数据库 ce6_live_20261008_attempt15，原历史8771/8782/8783及.ce1未动。保留原每OS用户应用执行锁，各应用测试顺序运行。凭据只经授权的无回显输入进入当前进程环境，未写证据或命令。

- 真实：`CE6_TEST_DATABASE_URL=… .venv/bin/python scripts/verify_ce6_live.py --output 新目录 --hidden-key`。每次新库、新目录，不覆盖失败。
- 新进程：`scripts/verify_ce6_saved.py 证据目录 --output 记录`，NoModel禁止调用。
- 综合回归：`sh scripts/verify_ce6_regression.sh`，每个阶段有独立测试库。
- 最终综合 [regression-accepted.txt](ce6-evidence/regression-accepted.txt)：**266 passed，无跳过**，退出0。此前48项联合专项与综合结果重叠，不相加。
- 成功样本：8次真实HTTP200，4次摘要+4次任务模型，4次工具；原上限14模型/10工具/360秒。input56627、output7703、complete=true。实际read_source、两次固定原文回查及write_private_note，报告完整保存，仍pending_review/for_discussion。
- 备份 `.ce6/backups/ce6_live_attempt15.sql`；独立集群真实停止/启动后，新进程重查与重启前逐字一致。

## 失败保留与修复

| 样本 | 失败与修复 |
|---|---|
| attempt1 | 中文字节窗口超过预算但字符投影未触发；补保守UTF-8界限。0模型调用 |
| attempt2 | 无标签UUID/角色成为业务未知、旧待确认未撤回；正文只抽取content，原始元数据仍外存 |
| attempt3 | 额外title被拒，后段未覆盖；摘要与报告写作指令分离 |
| attempt4 | 内容保真但报告重造截短UUID；明确不透明引用不可改写 |
| attempt5 | 另算source_id不能直接回查；删除冗余身份，直接使用artifact_id |
| attempt6 | 越界offset终止；改为可修正参数错误，未知引用仍拒绝 |
| attempt7 | 一次未知摘要、后段未覆盖；已有业务历史先在原预算内分批推进 |
| attempt8 | finalizing仍启动摘要，超时未交付；禁止收尾新摘要 |
| attempt9 | 无工具响应产生DSML文本；未执行，显式tool_choice=none |
| attempt10 | 摘要未知、语义不完整；已验证模型摘要关闭默认长思考，补失败诊断 |
| attempt11 | 增量输出丢前序；显式required_source_ids与累计记忆契约 |
| attempt12 | 增量/累计覆盖列表混淆、报告漏项；统一累计covered_ids，历史数据用assistant角色 |
| attempt13 | 初始task HTTP400；合成assistant结束输入，修正历史消息顺序 |
| attempt14 | kind=factory被本地拒绝；启用请求端SDK严格Schema，不做值修补 |
| attempt15 | 当前固定样本语义、实际工具执行、用量与重启核验通过 |

每轮目录保存原始live.json与独立审阅，不把程序控制通过等同于语义通过。另有一次旧CE3假模型用例未供摘要响应导致综合259passed/1failed，已更新当前路径的响应计划与独立任务清单断言；一次新增单元fixture缺exclusions已修正。所有失败输出保留。

当前证据限于上述固定样本与明确契约。中央校验保证来源/结构/覆盖，不保证自由文本推理自动正确；语义仍须独立复核。CE8需横向固定评测，历史C4/C5广泛质量缺口未因一个样本通过而关闭。没有自动采用报告、没有部署，也没有宣称容器/OS强隔离或任意代码测试。
