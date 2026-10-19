# CE4：有界调查、预算内收尾与完整交付

2026-10-07。状态：当前 CE4 范围验收通过。193 项回归、真实模型、浏览器及独立进程/数据库重启验证全部完成。

本阶段在 CE3 真实验收通过后实施。继续使用可信本机服务、Agents SDK 0.22.2、DBOS 3.0.0、PostgreSQL 和原有执行锁。没有增加容器、任意代码工具、独立调度器或 CE5–CE8 能力。

## 需求—实现—证据

| 要求 | 实现 | 验证 |
| --- | --- | --- |
| 原预算内预留收尾 | controlled/progress.py；总轮次至少 4 时预留 2 轮，否则预留 1 轮；预留时间为 min(30 秒, 总时限/4) | 真实 SDK 轮次/工具限额、4 秒超时用例；实际 DeepSeek 请求第三轮 tools=[] |
| 关闭调查工具 | broker 每次执行前检查；实际提供方 tools=[]；同批后续调用以 denied 记账并返回 executed=false | 同批工具调用只执行额度内操作；被拒私有写无落盘；收尾格式修复保持无工具 |
| 超时、修复、撤权 | 调查模型/工具超时后用原账本剩余额度收尾；未知模型请求和未确认工具操作不伪结算；SDK 原有格式修复仍计入总限额 | 模型超时后第二轮无工具、用量不完整；工具超时账本 unconfirmed；收尾期间撤权不能发表报告；无效最终格式为 incomplete |
| 重复读取有界 | 工具名+完整参数+完整结果指纹，忽略额度提示；第一次复读给已保存引用，连续三次无新增结果进入收尾 | 四次同源读取后停止；新分页范围、新版本内容重置计数；信息等待/任务状态不作为无进展重复 |
| 全文与简报分开 | TaskLedger 同事务保存完整结果和 TaskReceipt.artifact：固定 result_id、SHA-256、字节/字符长度、complete、1200 字符节选与 brief_truncated | 长报告哈希/末尾核对；API 活动列表删除旧内联 output，全文使用 task_artifact，撤权后拒读 |
| 保存≠复核≠采用 | 复用 TaskReceipt 的 delivery/adoption；执行 phase 的 delivered 只表示 schema 合格输出，正式保存仍以业务事务与回执为准 | 评审缺证据拒绝；评审事务写后异常回滚到 pending；真实报告仍 pending_review / for_discussion |
| 保存响应丢失 | 原 commit_id 幂等；新服务实例仅确认已提交结果；未确认且无在内存中的待存提交时明确拒绝，绝不重生成 | 实际数据库提交后注入响应丢失；清空执行器后 retry_save；后续新进程/重启验证 |
| 界面状态与全文 | 专家对话和详情显示节选、截断提示、完整报告；独立全文请求；保存待复核/需返工/交付通过/已采用/正在收尾 | Chromium 使用新 PostgreSQL 合成数据实测，截图及 browser.json |

这些规则复用 SDK 公开 Model.get_response、RunConfig.call_model_input_filter、ModelSettings.timeout、Runner 的原有 max_turns/格式修复路径及本项目 reserve/settle，不另起模型循环平台。调查超时只续接该次调用前已知的 SDK 输入，未知响应不能成为证据。任意在途调用精确恢复不属于本阶段。

## 实际验证

- [最终回归](ce4-evidence/combined-final.txt)：193 passed，覆盖 CE4 专项 9 项、CE1–CE3、四入口协作、SDK、任务、报告、信息交接、项目、保存和 API。
- [前端构建](ce4-evidence/frontend-build-final.txt)：成功；Vite 仍提示大 bundle，未将此性能提示解释为验收失败。
- [真实模型记录](ce4-evidence/live-attempt1/live.json) 与 [独立语义核对](ce4-evidence/live-attempt1/independent-review.md)：新库 `ce4_live_20261007_attempt1`，DeepSeek `deepseek-flash` Responses API，3 次 HTTP 200，10117 输入/3308 输出 token。原限额 4 模型轮次、2 工具调用，实际 read_source→write_private_note→关闭工具交付。报告保留 marker、7、未知市场规模和缺失年份，明确未回读/未核对年份，没有假独立复核或假采用。
- 本例全文输出 3982 字符，简报 1200 字符，截断标志 true；complete=true 仅代表全文保存完整，报告内容仍明确存在研究缺口。
- [浏览器记录](ce4-evidence/browser.json) 4 项检查全部通过，page_errors=[]；[全文截图](ce4-evidence/browser-full-report.png)、[已采用截图](ce4-evidence/browser-adopted.png)已视觉检查。浏览器使用 8794 端口和独立 `ce4_ui_*` 数据库。模型为明确标识的确定性合成 SDK fixture，浏览器结果不冒充真实模型证据；真实模型另列如上。

重启验证：[重启前](ce4-evidence/live-attempt1/before-restart.json) / [重启后](ce4-evidence/live-attempt1/after-restart.json) 完全一致。新进程以拒绝所有模型调用的 provider 检查全文哈希、原回执、阶段、预算及 3 份 wire 清单，并执行保存重试；新增模型调用为 0。集群备份保留在 `.ce4/20261007/accepted-cluster.sql`，本轮 8794 临时服务已关闭，55462 验收集群验证结束后停止；历史 8771/8782/8783 服务及 CE1 数据未操作。

复现入口：`scripts/verify_ce4_live.py --allow-paid-model --output <新目录>`、`scripts/verify_ce4_saved.py <证据目录> --output <校验记录>`。使用 `CE4_TEST_DATABASE_URL` 指向新建专用 PostgreSQL，模型凭据只从环境取得。`scripts/serve_ce4_ui.py` 和 `frontend/scripts/verify-ce4-ui.mjs` 为一次性新库浏览器验收入口。

## 失败记录与修正

首次后端合并回归 191 passed / 1 failed：测试混淆了模型 token 用量完整性与工具完成状态；改为直接查询工具账本，真实未确认操作为 unconfirmed。第一次改正断言拼写为 reserved 仍不匹配；最终专项及全部回归通过，失败日志保留。工具行为和用量口径没有为测试改写。

浏览器第一份种子遗漏聊天用户指令，正确被委派门槛拒绝；后续新增指令并使用新库。首轮浏览器到第二次导航时受本地保存的选中会话影响；第二轮采用后正常刷新关闭了详情窗，旧断言误报。检查实际回执已 adopted 后，修正导航/重新打开详情逻辑并在第三个新库重跑。历史失败证据保留。截图路径的 URL 空格编码已修正，截图已移回本工作区，误建空目录已清理。

## 范围

没有实现 CE5 编码工作区、CE6 模型摘要、CE7 任意调用恢复或 CE8 广泛质量基准。已保存内容与固定引用的进程重启恢复在当前范围内；未持久提交且进程已经消失的未知生成不在本阶段重新执行。对不配合取消的本机任意代码不提供 OS 硬中止保证；正式产品也未开放此类工具。
