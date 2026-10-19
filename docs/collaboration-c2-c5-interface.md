# 专家协作接口与运行说明（2026-10-04）

此说明对应 C2、C4、C5 的当前实现；状态与缺口以 [验收矩阵](collaboration-acceptance-status.md) 为准。

## 用户入口

从主任务的主线对话提出工作要求。主助手可用 `find_experts`、`read_context` 选择专家和资料，使用 `delegate_task` 派发；`wait_for_result=false` 可先回复，后台结果和失败通过持久通知续接主对话。`upstream_task_ids` 指定尚未完成的上游任务时，下游显示“等待复核”，只在指定上游版本取得 `passed` 回执后生成固定输入并执行。上游版本未执行即被替换、失败或取消时，下游明确失败。

专家节点默认显示完整会话。普通追问调用 `POST /api/expert_discuss`，只增加讨论消息，不重写任务输入或启动正式分析；用户的讨论、修订和取消产生持久主助手通知，通知输入同时包含当前可访问的最新运行及交付状态。专家页“更多 → 补充任务要求”调用 `POST /api/expert_revise`，模式 `save`、`after_current`、`replace` 分别为仅保存、当前版本结束后运行、请求停止旧版本后替换。任务停止调用 `POST /api/task_stop`，先返回停止请求，实际状态随后更新。输入被正式改变时保留旧版本与运行记录。

`GET /api/expert_thread?task_id=...` 返回会话；`GET /api/task_activity?research_id=...` 返回任务版本、运行、回执、派发状态、依赖边和协作消息边。`GET /api/chat_read?node_id=...` 返回主对话。前端每 5 秒刷新所选主任务及其会话，草稿按专家任务保存在本机浏览器。专家页“任务与执行”中的逐项表单调用 `POST /api/task_review` 记录独立复核；只有 `passed` 回执显示正式采用入口，调用 `POST /api/task_adopt`。主助手的 `adopt_result` 遵守相同门槛。专家成功执行和 `pending_review` 均不等于采用。

同一 `command_id` 重试必须保持参数一致。讨论使用 `message_id` 和预期会话版本，任务修订/取消使用预期任务版本；版本冲突应重新读取当前状态后处理。交接的资料、结果和专家消息片段仅在当前主任务及当前授权内可见；撤权后再次读取和发布均校验权限。

工具参数的 JSON 语法、缺字段和类型错误会返回 `invalid_input`、字段诊断、`executed=false` 及剩余调用数，模型可在同一对话中修正后用新的调用 ID 再试。错误尝试计入现有调用预算；有效参数通过校验后才执行工具。权限错误、任务取消及命令冲突继续阻止执行。

主任务协作预算当前限制为 8 个专家任务、委派深度 2、累计预留 80 模型轮次、已记录 300,000 tokens，执行队列最多同时运行 2 个独立专家任务。不确定用量会明确列出，但仍允许在任务数和轮次上限内补做，避免一次模型失败让主任务永久无法继续。该预算未计算准确的 USD 费用，且失败模型请求可能没有完整 token 用量；不能把它称为费用上限。

## 专家主动索要信息

正式专家执行中可调用 `request_manager_information(question, progress)`，一次汇总缺口并保存已有进度。专家工具对话等待主助手的有界信息检查；主助手只能读取当前主任务资料和对话。答复指定已读资料或对话消息作为来源，经权限校验后授权到原运行。没有信息时停止索要，专家完成可完成部分，报告和回执均保留缺失项；部分信息也按此规则处理剩余缺口。

`GET /api/task_activity` 的 `information_requests` 返回问题、进度、等待/答复状态及来源。专家聊天保留提问与主助手答复，“任务与执行”显示信息交接。`get_task_status` 也返回当前版本的信息请求，撤权后隐藏内容。原任务和初始交接包不改写，额外来源进入运行的授权记录及最终回执。

每个专家运行只请求一轮，重复调用返回已保存答复。服务重启按保存的进度恢复；已答复时不再次询问主助手。主助手咨询最多 4 轮、3 次读取、45 秒，恢复最多两次；失败或无信息均明确告知专家继续，权限失效及任务取消则阻止继续。正常执行保留同一 SDK 对话，重启恢复保存的任务进度，不是逐 token 恢复未保存的内部状态。主助手咨询用量计入专家运行用量，未知的恢复前用量明确标记不完整。

## 分歧后的补充调查

主助手先向双方专家明确缺口，再安排补查，取得答复后重新判断。`send_agent_message` 没有搜索工具，只用于讨论；需要新数据时使用 `delegate_task` 创建范围明确的正式补查任务，保留原任务与报告。所有内置专家均具备 `search_web`、`fetch_web`，A、B 各自复用原会话创建补查任务，自行搜索和读取正文，再由主助手汇总。已移除交给第三位联网专家代查的规则。普通讨论仍不启动搜索；主助手通过正式派发触发各专家补查。

补查交接使用具体待核查问题和原始资料。待复核报告可以读取，也可以通过讨论消息的 `result_ids` 转交，但不能通过正式任务的 `result_ids` 作为已复核依据输入。搜索摘要不能替代正文；收到两侧新结果或明确失败后，主助手比较证据、说明原结论是否改变及剩余缺口。找不到资料时停止重复搜索，保留未知项，不强迫形成一致答案。

仍遵守用户指定的资料范围。旧 A/B 样例的数字明确虚构且旧验收指令禁止联网，因此不能拿无关真实数据补成样例依据；纯算术错误应直接纠正。

`tests/test_disagreement_followup.py` 通过真实 Core、协作工具、搜索/抓取与来源授权链路，使用受控模型与网页提供方覆盖“两侧取得新正文”和“B 侧没有搜索结果”；验证补查新建两个任务、来源与待复核结果回到原专家、主助手最后再判断、原版本/运行/报告不变。此测试现直接由 A、B 各自搜索，并验证没有创建第三个专家会话。这是控制链路测试，不代表真实模型已自主完成外部补查。

## 隔离预览与复验

在仓库根目录运行：

```sh
(cd frontend && ./node_modules/.bin/vite build --outDir /private/tmp/collaboration-preview-dist --emptyOutDir)
PYTHONPATH=src .venv/bin/python scripts/serve_collaboration_preview.py
```

第二条命令在仓库根目录运行。它从项目 `.env` 加载当前获授权的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 等模型配置，使用本机 PostgreSQL 的 `collaboration_verify`、`/private/tmp/collaboration-verify-blobs` 和端口 8783。不要在命令行参数或验收文档中填写密钥。真实模型及其提供方可用性取决于运行配置；缺配置时只能执行不依赖真实模型的测试。

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q
COLLABORATION_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55449/collaboration_recovery_verify COLLABORATION_TEST_BLOBS=/private/tmp/collaboration-recovery-verify-blobs PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_collaboration.py tests/test_information_requests.py
PYTHONPATH=src .venv/bin/python scripts/verify_collaboration_c2_information.py
PYTHONPATH=src .venv/bin/python scripts/verify_collaboration_c4_live.py
```

最后一个脚本读取或建立明确标为合成数据的 8783 验收项目，写出 [真实模型多专家案例](collaboration-c4-live-verification.json)；现有案例包含一次成功交付和一次工具输入失败，不能当作真实市场研究结论。8771 和 8782 为历史实例，其数据库、blob 和构建目录不属于本次预览。

## 已知限制

- 费用策略：按用户确认，C4 当前不设金额上限，完整 USD 费用预算不属于本次完成门槛。任务数、深度、轮次、token 和并发的执行限制继续保留。
- 等待未来上游结果的派发已通过 Memory 和 PostgreSQL 测试，尚无真实模型自然语言选择该依赖路径的成功案例。
- 真实双专家案例初次有一名专家因工具参数解析失败；格式错误反馈机制已修正，专家 A 的 v3 重试成功，现有两份待复核报告。[比较验收](collaboration-c4-disagreement-review.md)已跑通主助手对两位专家的追问及回传，保留原报告；第 5 轮在余额恢复后执行成功，已保留统一口径后是否矛盾的未知状态；仍错误地禁止相减，语义质量未通过。
- 前端已检查草稿保留、切换、刷新、后台通知显示、复核表单和隔离合成项目中的正式采用；慢响应覆盖旧选择及可留存截图仍需补验。

## 复验现有 A/B 报告的比较

运行 `.venv/bin/python scripts/verify_collaboration_c4_disagreement.py --attempt 6`。既有 attempt 编号只返回保存结果，不覆盖证据或重新调用模型；使用新编号才启动新轮次。脚本检查主助手成功、原任务与报告不变、未调用联网或正式采用工具；判断是否正确仍需人工审阅 report 和 claims。执行成功不能直接判定 A15 通过。前五轮证据与缺陷见 [审阅记录](collaboration-c4-disagreement-review.md)。

## 全体专家联网能力

六个内置专家（搜索、提取、分析、综合、财务提取、市场调研）均发布联网搜索与网页读取能力。新建任务及用户显式修订使用当前发布的方法；复用会话不再把新任务锁定到会话创建时的方法。旧任务版本和已固定运行仍按原方法重现，不改写历史报告。主任务授权收窄或撤销仍会限制工具；用户明确不联网时按任务指令执行。

`tests/test_expert_web_capabilities.py` 对六位专家逐一实际调用搜索和抓取（受控网页提供方），并覆盖主任务不授予联网工具时不可调用，以及旧会话中新任务和修订采用最新方法、旧任务快照保留。

隔离 8783 的六位专家已启用上述能力，发布明细见 [发布记录](collaboration-expert-web-release.json)。完整回归 **274 passed / 176 skipped**；跳过项不视为通过。

## 真实模型自主补查验收

脚本 `scripts/verify_collaboration_c4_autonomous_research.py` 使用独立验收项目；先生成两份仅依据未核实简报的初报，再由自然语言触发双方自行联网补查。两轮结果见 [审阅记录](collaboration-c4-autonomous-research-review.md)：双方真实搜索/抓取已观察到，第二轮 A 成功、B 最终 JSON 失败，后台汇报了部分失败，完整流程未通过。

主助手派发专家任务现统一为 20 轮、24 次工具调用、360 秒；持久执行预留两次尝试，单次实际 10 轮、12 次调用。工具核心返回实际剩余次数，避免按任务总额度误判。主任务累计 80 轮等限制不变；金额上限仍不设置。完整回归 **275 passed / 176 skipped**。


## 最终报告纠错与后台汇总更新

两项机制已实现：同 SDK 对话最多两次格式纠正（禁用工具、计入原预算、保存草稿及诊断）；正式派发统一后台执行，删除 `wait_for_result`，同轮专家全部结束后续接，结果目录加按需读取正文，超大选取可恢复。通知只确认已纳入汇总输入的结果，同组共享两次汇总重试额度。

完整回归 **285 passed / 176 skipped**。真实复验双方交付成功，A 的最终 JSON 错误实际纠正一次；汇总在修复长资料读取后重试成功，专家报告未重跑或覆盖。过程与语义限制见 [最新审阅](collaboration-c4-autonomous-research-review.md)和[机制说明](collaboration-report-delivery.md)。C4 全部语义验收及自然语言依赖选择仍保留未完成状态。


## 2026-10-05：历史报告工具补齐

已扩展主助手咨询的 `read_information`、专家受限的 `read_context` 和 `request_manager_information`，支持目录发现、正文读取、描述未知报告及固定版本授权交接。主助手必须实际读过报告才能授权，交付时复核权限；专家取得信息后继续同一任务，无信息则保留缺口完成。原报告及复核/采用状态保持不变。实现与边界见 [工具说明](collaboration-report-information.md)。

真实专家与真实主助手咨询成功完成读取自己的初稿、按描述索要另一份初稿、收到授权后继续，并正确返回正文独有标记。历史初稿使用合成夹具，见 [真实模型记录](collaboration-report-information-live.json)。完整回归 **291 passed / 176 skipped**；跳过项不计通过。工具缺口已补齐，C4 完整分歧语义与自然语言依赖选择、C5 浏览器矩阵仍未完成。


## 2026-10-05：主助手改用 deepseek-v4-pro

主助手及信息咨询现使用独立 `OPENAI_MANAGER_MODEL=deepseek-v4-pro`，专家保持 `deepseek-flash`。定向回归 **22 passed / 2 skipped**。复用报告测试中，合成案例第二次成功，未再禁止相减并保留归一后的未知；但未直接解释 9 个百分点，完整语义不能据此记为通过。官方案例先超时，再因工具调用上限失败，没有可评估最终报告。配置已在隔离 8783 加载；详见 [试验记录](collaboration-c4-manager-pro-trial.md)。C4/A15 维持部分通过。
