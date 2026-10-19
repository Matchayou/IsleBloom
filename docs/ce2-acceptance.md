# CE2：统一受控入口与逻辑协作隔离

2026-10-07。本阶段验收时状态：当前 CE2 范围通过，产品入口已切换。后续 [CE3](ce3-acceptance.md) 和 [CE4](ce4-acceptance.md) 已按顺序验收通过；以下保留 CE2 当时的实现/证据。CE3 为普通讨论新增受限分页读取工具，取代本记录当时的零工具配置。

## 当前执行边界

本机可信服务内执行是正式方案。保留 OpenAI Agents SDK 0.22.2、DBOS 3.0.0 和 PostgreSQL/SQLAlchemy；不增加 Docker、容器、任意 Shell/Python、OS 强隔离或硬资源配额。CE5–CE8 不在本轮范围。

`composition.build_controlled_services(database, scopes, private_root=..., model_provider=...)` 接入主助手正常对话、续接、主助手咨询、专家正式任务与普通讨论。执行仍沿既有 Core、ScopeExecutionGate、TaskLedger/ResearchLedger 与协作数据模型；broker 是唯一模型出口。咨询的身份由服务端临时准入绑定，讨论输出允许纯文本且没有工具。主助手没有读取专家私有目录的工具。

授权世代使用研究 scope 的身份/版本，正式任务额外绑定自身 scope 的身份/版本。自动抓取和获准资料扩充不递增撤权版本，不再因正常目录增加误终止。显式授权变化后，旧笔记/结果不再可用；私有原始文件保留但新世代不能通过工具寻址。世代检查覆盖操作前后、保存重试、报告读取、讨论历史和 API 输出。这个策略保守地使整代衍生内容失效，不尝试猜测笔记中的隐含依赖。无获准原始输入时明确拒绝，不能复制旧笔记“继续”。

## 需求—实现—证据矩阵

| 需求 | 实现 | 当前证据与边界 |
| --- | --- | --- |
| 四入口统一 runner/broker | controlled/engine.py、admission.py、composition.py、projects/service.py、collaboration/information.py | 新 SDK/PostgreSQL 用例检查 manager/expert/consultation/discussion 账本及正常/续接两次 manager 调用 |
| 两专家并行、私有状态分隔 | 原 DBOS 双并发队列、服务端 workspace ID、原 PostgreSQL 写者 fencing | 两专家运行重叠；不同目录；实际读私有工具不能取得同伴笔记；主助手只读取正式报告 |
| 能力交集、拒绝未知工具 | 每入口固定允许工具 ∩ 业务准入生成的工具；原 scope/method/packet 检查 | CE1 工具拒绝回归；普通讨论工具列表为空；咨询只有 read_information 与自己的笔记工具 |
| 依赖和固定版本报告交接 | 原 TaskDependency/ContextPacket/ReportGrant | 现有循环/未知前置/失败分支/固定上游回归；新 broker 用例验证授权前拒绝、实际阅读后授权、未读报告不得授权 |
| 消息送达与消费确认 | 原 AgentMessage delivered/consumed + consumer_turn_id | 重复事件不再执行；讨论消费后原子回执；关闭后新进程确认同一回执和唯一消息 |
| 信息等待、唤醒与恢复 | 原 InformationRequest waiting/resolved、DBOS 专家步骤、保存的答复重放 | 真实 DBOS 中等待咨询期间没有专家模型轮询；解除等待继续；在 waiting/resolved 保存边界 SIGKILL 子进程后原事件/原 run 恢复，已答复不重问 |
| 撤权、取消、迟到发布拒绝 | ControlledEngine.validate_run + 现有 TaskService/ScopeService gates | 在途模型退出、未知用量保留、写者释放；旧代结果/API/保存重试拒绝；新任务工作区无旧笔记 |
| 保留 CE1 | 原 PostgreSQL reserve/settle/fencing、原文存储、任务回执 | CE1 与相关研究/协作回归一并运行；没有替代数据库或取消锁 |
| 真实模型、真实双专家、语义核验 | scripts/verify_ce2_live.py | 两轮各 25 次 HTTP 200；第一轮语义未完全通过，修复后第二轮独立核对通过 |
| 删除旧业务执行路径 | 在新入口必要外部验收后切换产品入口并删除对应旧路径 | research_cli / tasks_cli 的真实模型入口统一接入 broker；删除对应 OpenAIEngine 直连与 DBOS 全局 client 设置，无自动 fallback |

## 复用依据

本地类型核验确认为 SDK 0.22.2 / DBOS 3.0.0。使用 SDK 的公开 Model.get_response 和现有 FunctionTool/Runner，保留既有编排。参考 [SDK Model 接口](https://openai.github.io/openai-agents-python/ref/models/interface/) 与 [DBOS 工作流通信](https://docs.dbos.dev/python/tutorials/workflow-communication)。不增加自定义 DAG、消息平台或模型轮询循环。信息恢复只承诺已经保存的业务交接边界；任意在途模型调用的精确重放属于 CE7，本轮没有实现。

## 已执行验证

- 新 CE2 集群：PostgreSQL 17.11，`.ce2/20261007/postgres`，127.0.0.1:55462。
- 新控制测试库：`ce2_controlled_acceptance`；前序专项新库：`ce1_ce2_regression`。不复用 `.ce1` 或历史 8771/8782/8783 数据。
- [必要综合回归第二轮](ce2-evidence/combined-regression-attempt2.txt)：191 passed，无跳过。包括 CE1、CE2、SDK、任务、协作、报告授权、DBOS、项目、研究与联网控制测试。
- [CE2 独立用例第五轮](ce2-evidence/controlled-entrances-attempt5.txt)：11 passed；其后补充主助手工具派发/汇总及 API 撤权两项，已包含在 191 项综合回归中。
- [独立前端构建](ce2-evidence/frontend-build.txt)：成功，输出 `.ce2/20261007/frontend-initial`；保留 Vite chunk size 提示。没有修改历史构建目录。
- 测试中的模型是确定性 SDK Model 夹具，这些结果不能替代付费真实模型或语义验收。

## 失败与修复

1. 首次 initdb 被受限环境禁止创建共享内存；申请提升执行权限后在新目录成功初始化。没有绕过应用执行锁。
2. 原两个 CE1 Engine 测试的准入桩没有入口种类，新增入口契约后失败；更新桩后通过，未添加旧协议兼容。
3. 首次新测试工具构造器的 name 参数与工具内容字段冲突；修正测试参数名。
4. 首次四入口专家执行失败：既有 DBOS 步骤分配较小的单次预算，而新准入错误要求与总预算完全相等。改为严格验证请求预算不得大于固定任务额度；持久预算仍累计，不放大额度。
5. 找到并移除了协作/信息回归测试覆盖 PostgresDatabase.LOCK_PATH 的旧代码，再启用相应数据库测试。历史服务脚本未运行、未改动。第一轮 179 passed/8 skipped 不算全部验收；第二轮配置新库后 191 项全部执行。
6. 旧讨论和 API 只查显式资料引用，无法阻止未列出引用的旧代衍生内容。新增生成消息 run_id 与世代验证，覆盖讨论、回执、历史报告和 API 输出。
7. 真实模型预检最初因本进程没有凭据停止，未发送请求；用户随后授权凭据，已通过关闭回显的输入仅传入验收进程。日志不保存密钥。

## 运行方法

先创建全新 `ce2_` 数据库、全新输出目录；不得并行启动第二个执行应用或覆盖锁。模型固定使用 Responses API，不自动改用 Chat Completions。

```sh
export CE2_TEST_DATABASE_URL='postgresql+psycopg://chenyouran@127.0.0.1:55462/ce2_live_NEW'
# OPENAI_API_KEY 通过无回显输入或受保护的进程环境提供，不写入项目。
.venv/bin/python scripts/verify_ce2_live.py --allow-paid-model \
  --model deepseek-flash --base-url https://api.deepseek.com \
  --output docs/ce2-evidence/live-NEW
```

脚本要求空数据库和不存在的输出目录；真实主助手使用工具派发两位专家，DBOS 并行运行、主助手咨询/续接、专家讨论都走 broker。保存完整结果、原合成源、操作类型、HTTP 状态、预算和未采用回执。检查成功仍要求逐份独立比较原文与完整报告，不能把工具调用成功等同于判断质量通过。

## 真实闭环、产品切换与最终验收

- [第一轮完整结果](ce2-evidence/live-attempt1/live.json)及[独立审查](ce2-evidence/live-attempt1/independent-review.json)：25 次 Responses HTTP 200，控制检查通过；目录未列新增工具、咨询时点的空报告目录被沿用成当前状态，因此未记为完整通过。修正能力目录与咨询/汇总/讨论的时间边界后新建第二个空库重跑，保留失败记录。
- [第二轮完整结果](ce2-evidence/live-attempt2/live.json)与[独立原文复核](ce2-evidence/live-attempt2/independent-review.json)：25 次 HTTP 200，deepseek-flash；两位专家均真正读取原始 source、保存并重读独立私有笔记、向主助手咨询、提交正式报告。Marker、数量 7、market size unknown 与原始 blob 一致；未编造目标年份。汇总读取两份完整报告，明确纠正旧时点目录信息，讨论保留时点限定。报告仍待独立业务评审、未采用。
- [专家运行时间](ce2-evidence/live-attempt2/expert-overlap.json)：2026-10-07 13:58:34 至 13:59:18（+08:00），两专家真实运行重叠约 39.29 秒。私有笔记同名但哈希、目录不同，主助手仅取得正式报告。
- 产品 CLI 已使用 `build_live_services` → `build_controlled_services`；只允许 PostgreSQL 真实模型执行，显式 scripted 仍仅为演示/测试。保留 SDK 适配器作为 broker 内运行器，不再用作这些产品入口的直连后端。既有方法候选/预览补服务端准入并单独回归，不新增方法演化能力。
- [最终扩展回归](ce2-evidence/final-regression.txt)：215 passed / 1 skipped，退出 0。唯一跳过为方法演化持久性检查的 memory 参数，PostgreSQL 对应项已执行通过；原 191 项必要回归没有跳过。此轮增加 evaluation/evolution 两套。
- [产品切换专项](ce2-evidence/product-cutover.txt)：3 passed / 13 deselected，退出 0；验证实际 Responses provider、共享 broker、拒绝 live + memory，以及候选/预览的实际 SDK 受控调用。另 [纯单元回归](ce2-evidence/final-unit-regression.txt) 45 passed。
- 全新进程调用 `scripts/verify_ce2_saved.py` 后，备份本轮集群至 `.ce2/20261007/accepted-cluster.sql`，仅重启本轮 55462 PostgreSQL，再用新进程重读。[重启前](ce2-evidence/live-attempt2/before-restart.json)与[重启后](ce2-evidence/live-attempt2/after-restart.json)一致：准入身份、累计预算、回执、讨论与笔记哈希保留；无活动写者，无模型调用。两次脚本、备份与 restart 均退出 0。

真实模型只验证此固定合成单行材料闭环，不能外推复杂研究质量，也不改变历史 C4/A15 尚未完整通过的结论。重启读取不宣称任意在途模型精确恢复；CE7 未实现。CE2 当前需求门槛已关闭，可按顺序进入 CE3；CE5–CE8 未推进。

产品运行示例：

```sh
# 通过受保护进程环境提供 DATABASE_URL / OPENAI_API_KEY / OPENAI_BASE_URL。
.venv/bin/python -m agent_core.research_cli --storage postgres --engine openai \
  --blobs .controlled/blobs --private .controlled/private serve --durable
```

