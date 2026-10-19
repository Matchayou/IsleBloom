# 专家协作验收状态（2026-10-05）

本表对应 `multi-agent-collaboration-incremental-plan.md` 第 9 节。通过只表示所述范围内有实际证据；确定性测试与真实模型运行分开记录。专用验收库为 `collaboration_recovery_verify`，运行预览库为 `collaboration_verify`；均未使用 8771/8782 的历史数据。

| 编号 | 状态 | 已观察证据与剩余条件 |
| --- | --- | --- |
| A01 | 通过单专家范围 | `collaboration-c1-live-verification.json` 记录主助手自然语言调用、固定交接、专家回执和续接；后台先回复后完成的续接由 `test_background_experts_notify_manager_after_accepted_reply` 覆盖。 |
| A02 | 通过 | `test_handoff_is_atomic_idempotent_and_reuses_expert_session`；C1 DBOS 验证中第二条用户消息复用同一会话。 |
| A03 | 通过单专家范围 | 固定交接和任务输入包含用户原文、主助手说明及选定来源；相关性选择质量尚无独立标注评估。 |
| A04 | 通过业务契约范围 | `test_reviewed_upstream_result_keeps_provenance_in_handoff` 核对结果版本、来源、作者会话、假设、局限和依赖边；`test_expert_discussion_excerpt_is_frozen_with_its_session` 核对会话片段引用与撤权。真实模型跨专家内容选择质量仍待验收。 |
| A05 | 部分通过 | 普通提问不新增正式任务；补充消息可携带资料/结果供下次讨论，正式任务版本保持不变；重做产生新版本及运行。8783 浏览器已提交一项合成结果的逐项独立复核，观察交付变为 `passed`；另一已通过的合成结果从“供讨论”采用后显示“已采用”。专家主动索要信息已通过真实模型的有信息/无信息两分支及 PostgreSQL 恢复测试；综合浏览器修订流程仍归 C5 验收。 |
| A06 | 通过确定性范围 | 运行中取消、修订排队与替换竞态测试覆盖旧输入不可改写。 |
| A07 | 通过业务契约范围 | 命令幂等、版本冲突及重试测试通过；未做多进程同一主任务并发写入压力测试。 |
| A08 | 通过已实现授权范围 | 外部主任务结果和未批准协作者被拒绝；撤权检查覆盖读取、交接与结果。 |
| A09 | 通过确定性范围 | 在途模型响应后的撤权校验阻止发布；外部调用已经发出的事实保留。 |
| A10 | 通过派发与结果范围 | 专用 PostgreSQL 重启恢复测试及 `collaboration-c1-crash-recovery.json` 强杀证据；`test_postgres_reopens_waiting_dependency_and_user_notice` 另验证未来依赖与用户修订通知在重开数据库后保留。新增后台失败通知的强杀故障点未单独演练。 |
| A11 | 通过确定性范围 | `test_completed_background_result_waits_for_new_user_turn` 核对新用户消息先到时不被旧事件覆盖，结果进入下一轮固定输入。 |
| A12 | 通过一个真实公开样例 | `collaboration-c3-market-attempt-13.json` 记录 Census 官方页面/PDF/FAQ 正文快照、逐字引用及待独立复核状态；不代表其他站点成功率。 |
| A13 | 通过确定性范围 | 无联网能力、抓取失败、来源不足、伪造引文均返回明确缺口。 |
| A14 | 部分通过 | DBOS 双任务重叠执行、固定结果版本依赖、未来上游结果等待与复核后固定、环检查及祖先取消传播通过确定性测试；未来依赖在专用 PostgreSQL 重启后仍可恢复。真实模型自然语言选择依赖及浏览器等待状态未完成。 |
| A15 | 部分通过 | 回执保留冲突、失败与待复核；正式采用须独立复核。[初次真实模型双专家案例](collaboration-c4-live-verification.json)包含一侧失败。工具格式错误现可反馈模型修正；[后续专家 A 的 v3 复验](collaboration-c4-format-repair-verification.json)成功，B 原成功结果保留，已有两份待复核报告；[后续比较验收](collaboration-c4-disagreement-review.md)保留既有推理缺陷。新增[真实模型自主补查](collaboration-c4-autonomous-research-review.md)：最新机制复验中双方成功交付，A 最终 JSON 真实出错并在同次执行纠正；后台汇总经长报告读取修复及操作员重试后成功，四份原报告保持不变。自动等待与汇总控制链路通过；历史真实报告仍有调整口径表述过窄、误称阶段一原文不可得的问题。2026-10-05 已补齐报告读取与授权工具，并通过真实模型合成验证；已运行新的[完整判断质量复验](collaboration-c4-quality-retest-2026-10-05.md)：联网执行与自动汇总成功，但过度断言、跨年推断和合成案例算术解释仍未通过。 |
| A16 | 按当前范围通过 | C3 真实运行记录 4 请求、39,518 输入 tokens、9,811 输出 tokens；C4 对同一主任务限制任务数、深度、预留轮次、已记录 tokens 和并发。不确定用量显式列出，允许在任务数和轮次上限内补做。按用户确认，C4 当前不设金额上限，完整 USD 费用预算不属于本次完成门槛。 |
| A17 | 部分通过 | 8783 浏览器验证主对话、专家会话、右侧资料和 Flow 图；[合成四任务 Flow](collaboration-c4-flow-verification.json)显示真实后端依赖、子任务与消息边。专家草稿在切换及刷新后保留，主对话不手动刷新即显示专家讨论通知的后续回复；复核表单可提交，交付与采用状态在刷新后可见。慢请求覆盖新选择尚未专项验收。 |
| A18 | 通过当前隔离范围 | 8783 使用专用构建 `/private/tmp/collaboration-preview-dist`、数据库及 blob；8771 保持监听。8782 在后续检查时已停止，按既有脚本从原数据库、blob、构建目录重新启动，三服务目前分别监听；未清空历史数据。 |

最近一次完整命令结果：`PYTHONPATH=src .venv/bin/python -m pytest -q` 为 291 passed、176 skipped；专用 PostgreSQL 协作与信息请求测试为 47 passed；`frontend` 独立 Vite 构建通过。跳过项主要依赖专用数据库环境变量或外部运行条件，不能视为通过。

本次 C2 专家主动索要信息 → 等待 → 主助手授权或说明无信息 → 同任务继续已完成。真实模型两例成功，均使用合成资料；全量回归 251 通过、176 跳过，专用 PostgreSQL 协作套件 47 通过，见 [证据](collaboration-c2-information-live-verification.json)。

仍需完成的阶段门槛：C4 的分歧判断质量复验（追问链路已通）与自然语言依赖选择；C5 的慢响应浏览器验收、截图留存及全流程矩阵。C4 当前按用户确认不设金额上限。C2 的本次功能缺口已闭环；C4、C5 仍未完成，跨功能的浏览器矩阵继续保留未验收项。接口及复验命令见 [C2–C5 说明](collaboration-c2-c5-interface.md)。

本次报告比較规则与读取说明变更后，协作和信息请求定向回归为 **39 passed / 8 skipped**。这不等于真实模型判断质量通过；五轮实际结果见 [验收审阅](collaboration-c4-disagreement-review.md)。

后续分歧补查规则已加入：要求分别补查新数据、回传双方专家并再次判断。新增两条受控模型/网页提供方的控制链路测试，覆盖两侧有依据与单侧无结果，合计 **41 passed / 8 skipped**；不等同于真实模型自主补查通过，A15 仍为部分通过。

全体专家联网能力已在隔离 8783 发布，见 [发布记录](collaboration-expert-web-release.json)。六位专家逐一搜索/抓取、主任务工具授权边界、旧会话新任务/修订采用当前方法共 13 项受控检查通过；完整回归 **274 passed / 176 skipped**。A/B 补查测试已改为双方直接联网，没有第三位代查专家；真实模型自主补查质量仍待验收。

真实模型自主补查的两轮原始证据已保存。实际工具调用与原会话复用通过，本轮完整语义/交付验收未通过；修正额度提示和派发额度后的完整回归为 **275 passed / 176 skipped**。见 [审阅记录](collaboration-c4-autonomous-research-review.md)。

报告纠错与后台汇总已实现，见 [机制说明](collaboration-report-delivery.md)。真实格式修正和双侧交付汇总已有证据；真实流程有中途修复和操作员重试，不记为无干预全链路通过。


## 2026-10-05：历史报告工具补齐

已扩展主助手咨询的 `read_information`、专家受限的 `read_context` 和 `request_manager_information`，支持目录发现、正文读取、描述未知报告及固定版本授权交接。主助手必须实际读过报告才能授权，交付时复核权限；专家取得信息后继续同一任务，无信息则保留缺口完成。原报告及复核/采用状态保持不变。实现与边界见 [工具说明](collaboration-report-information.md)。

真实专家与真实主助手咨询成功完成读取自己的初稿、按描述索要另一份初稿、收到授权后继续，并正确返回正文独有标记。历史初稿使用合成夹具，见 [真实模型记录](collaboration-report-information-live.json)。完整回归 **291 passed / 176 skipped**；跳过项不计通过。工具缺口已补齐，C4 完整分歧语义与自然语言依赖选择、C5 浏览器矩阵仍未完成。


## 2026-10-05：C4 完整判断质量复验结果

已运行新的真实双专家联网案例（attempt 4）及原合成案例第 6 轮。联网案例四项任务全部成功，原会话与初报保留，主助手在一次系统自动重试后汇总成功，没有人工补答或重跑专家；六项执行检查均通过。合成案例执行与原记录保留检查也通过。

**完整语义验收仍未通过**：联网汇总把已确认缺陷说成唯一缺陷，并从历史序列调整推导出未经证实的跨年不可比；原合成案例仍把业务解释限制写成“不能相减”，且混淆历史追问与本轮行为。来源读取成功不能替代结论正确性检查。详见 [本次审阅与证据](collaboration-c4-quality-retest-2026-10-05.md)。C4/A15 继续部分通过，自然语言依赖选择、相反专家建议裁定与 C5 未因此通过。


## 2026-10-05：主助手改用 deepseek-v4-pro

主助手及信息咨询现使用独立 `OPENAI_MANAGER_MODEL=deepseek-v4-pro`，专家保持 `deepseek-flash`。定向回归 **22 passed / 2 skipped**。复用报告测试中，合成案例第二次成功，未再禁止相减并保留归一后的未知；但未直接解释 9 个百分点，完整语义不能据此记为通过。官方案例先超时，再因工具调用上限失败，没有可评估最终报告。配置已在隔离 8783 加载；详见 [试验记录](collaboration-c4-manager-pro-trial.md)。C4/A15 维持部分通过。
