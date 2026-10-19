# CE7 安全边界检查点与恢复验收

日期：2026-10-08。状态：**CE7 当前安全边界恢复范围验收通过**。最终综合回归 293 项通过，最终代码真实模型/DBOS/强杀恢复通过；可进入 CE8。

## 范围与实现

继续使用 OpenAI Agents SDK 0.22.2 的 Runner；DBOS 3.0.0 仅做持久编排。FunctionTool.needs_approval 在工具执行前形成内部暂停边界，应用在重新验证原授权后自动接续，不引入用户审批流程。采用 RunResult.to_state、RunState.to_json/from_json、get_interruptions、approve 和 Runner.run 的公开 API，没有修改 SDK 或访问其私有执行字段。

公共 API 实测发现：已完成的 RunState 重新 Runner.run 会再次调用模型。因此完成结果保存为经原 schema 校验的 RunResult，恢复直接返回该结果。暂停状态才交给 SDK 恢复。格式修复与上下文窗口重投影仍在原适配器循环内，检查点保存循环元数据与模型游标。

`controlled/checkpoints.py` 只负责存储、恢复和稳定操作标识。私有快照先通过 ContextArtifacts 写入内容寻址文件、fsync 文件和目录、提交索引，再向 PostgreSQL 发布哈希引用。响应回执、已确认用量与对应代码快照引用在同一 PostgreSQL 事务内提交。工具结果先完成原有证据读取回执，再发布操作回执。事务外执行模型与工具，不跨 await 持有数据库事务。

恢复绑定 run/task/spec、方法摘要、模型、完整固定输入摘要、工具与输出 schema、授权世代、来源依赖，以及编码契约。代码执行快照独立保存工作树文件清单、HEAD、完整受控状态摘要。私有文件哈希不符、账本回退、身份变更、当前撤权/取消、代码变化均拒绝接续。

新进程只有取得原有 OS 用户 flock 和 PostgreSQL advisory lock，才能替换旧进程写者；同进程的第二个执行者仍拒绝，旧 epoch 不能发布。无租约超时接管、无数据库断线重连、无跨主机假定。

## 操作和预算

- 任务模型操作使用检查点内的单调游标；摘要请求使用内容摘要；工具使用原 SDK call_id。恢复前校验操作类型与参数摘要。
- 已保存响应重放原回执，不重新执行工具或再次请求模型。准备后的模型输入和设置也保存，避免恢复时重新压缩改变同一操作的输入。
- 请求未发送、已发送未确认、已保存响应分别保留 manifest/operation 状态。未确认模型操作可在原预算内以新的 retry ID 重试；原预占不释放，旧用量保持未知，不能宣称模型 exactly-once。
- 工具已执行但回执未确认时，停止执行并标为 operation_unconfirmed，不能盲目重做副作用。需检查已保存操作后再决定新的授权任务。
- DBOS 不再平分整段模型/工具额度。恢复沿用原 run 的总预算与绝对截止时间；请求超时还受剩余时间约束。已保存完成结果可在截止时间后确认提交，不产生新调用。
- DBOS 应用协议为 ce7-v1；旧协议不迁移。正式结果仍经过原任务检查与事务提交，交付为 pending_review，不自动采用。

## 对照与证据

| 要求 | 实现与验证 |
| --- | --- |
| SDK 导出/恢复边界 | tests/test_checkpoint_sdk.py；sdk-public-state.json；证明完成 RunState 会额外请求模型 |
| 固定身份、来源、方法/模型、权限绑定 | controlled/engine.py、checkpoints.py；test_checkpoints 的七种身份变更；test_durable_process 的真实新进程任务变更、权限变化、取消 |
| 安全写者与事务发布 | controlled/ledger.py；同进程冲突、旧 epoch 拒绝、真实 SIGKILL 后接管 |
| 固定代码快照 | coding/workspace.py execution_snapshot；实际 Git 文件改变后恢复被拒；CE5 编码回归 |
| 请求前、响应后落盘前 | before_request.json、after_response.json；未确认预占保留、重试另扣 |
| 事务写入未提交 | inside_transaction.json；真实 PG 事务回滚、私有孤立文件不形成悬空引用 |
| 提交后 SDK 未收到 | after_receipt.json、after_tool_receipt.json；重放已提交响应、只发生一次笔记写入 |
| SDK 暂停与已完成结果 | sdk_pause.json、result_saved.json；新进程从边界接续，完成结果不再请求模型 |
| 未确认副作用 | after_effect.json；保留一次已发生写入，恢复停止，不重复副作用 |
| DBOS 唯一编排器 | sdk_pause-dbos.json；串行固定专家、并行独立任务、重复提交、旧应用协议拒绝 |
| 真实外部样本 | live-attempt1/acceptance.json 和 live-attempt2/acceptance.json；独立人工审阅分别保存 |

## 真实外部验收

最终实现哈希清单见 implementation.json；源码快照和最终数据库备份保存在 .ce7/backups。

1. `ce7_live_20261008_attempt1`：在笔记回执已提交后 SIGKILL。DBOS 新进程恢复同一 run；2 个真实 HTTP 200，1 次实际笔记写入，已确认 usage 为输入 6215、输出 760，complete=true。
2. `ce7_live_20261008_attempt2`：第一个真实 HTTP 200 响应到达、未形成持久响应回执时 SIGKILL。新进程保留 model:1 unconfirmed，单独预占 model:1:retry:1，最终共 3 次真实发送、1 次笔记写入。已确认输入 6247、输出 1133；第一次调用用量未知，complete=false，不能把这组数字当作总费用。

3. `ce7_live_20261008_attempt3`（最终代码）：再次执行响应未落盘前强杀，3 个模型预占、3 次真实发送、1 次笔记写入，已确认输入 6220、输出 897，complete=false。SDK 序列化、写者检查、冻结输入和 DBOS 操作状态均为最终实现。人工核对通过，见 live-attempt3/independent-review.md。

三例均交付 pending_review，人工核对 v1、7 台、收入未知、实际笔记名/文本/hash 和未审核状态。样本 1 把“单位”笼统列为未知，设备单位实际已知为台；该语义瑕疵保留，不能计作 CE8 完整质量通过。这一阶段验收恢复与账本，不以模型自评替代语义评审。

密钥只经隐藏终端输入传递，未写入命令参数、文件或证据。外部网络调用由原真实客户端完成，max_retries=0，无备用后端。

## 失败与修复记录

- 初始 SDK 状态证明发现完成状态重新运行会再次请求模型；改为独立保存完成结果。
- 响应转储未使用公开别名/未设置字段，造成恢复时工具审批原始对象有冲突的额外字段。首次三个失败样本保存在 initial-serialization-failures.json。
- 尝试统一排除 None 时移除了必填但可为空的 response_id，造成验证失败；required-response-id-failures.json 保留失败。最终使用公开别名并排除未设置字段，且输出项省略可选 null；顶层必填可空 response_id 保留。补充实测显式 async:null 的 SDK 恢复同样失败，因此通过统一 response_json 转储，并加入参数化公共 API 恢复验证。
- 将工具交付投影及业务读取回执移至操作回执提交前，避免强杀后只有工具回执而丢失内存中的读取回调。
- 综合回归首次 41 项失败：将 DBOS 的引擎限制误放在协作共用的固定 run 接口；移回 DBOS 真正入口后协作用例恢复。失败输出保留在 regression-initial-failure.txt。
- 第二轮复用失败库后 291 passed / 1 failed：旧的待续主任务消耗了当前假模型预设响应，失败输入属于旧的 Examine sample 研究，而非当前 CE2 样本。单例和整组 16 项均通过；验收脚本现每轮创建新数据库，不删除旧失败记录。见 regression-reused-database-failure.txt 与 manager-group-diagnostic.txt。
- 去除 DBOS 整段预算平分路径；历史测试源码留存在 historical-tests，只读历史证据不作为当前架构通过数。当前测试重新使用真实 SDK+PG 的受控路径。

## 命令与当前结果

- `CE7_TEST_DATABASE_URL=... .venv/bin/pytest -q tests/test_durable.py tests/test_durable_process.py tests/test_checkpoints.py tests/test_checkpoint_process.py tests/test_checkpoint_sdk.py`：26 passed，见 checkpoint-regression.txt。
- `sh scripts/verify_ce7_regression.sh`：每轮创建独立数据库（可用 CE7_RUN_TAG 指定唯一标签），293 passed（129.54 秒，最终代码），见 regression.txt。此前 292 项与 16 项 SDK 子集有重叠，不相加。
- `scripts/verify_ce7_live_crash.py --directory ... --point after_tool_receipt|after_response`：三个真实样本控制检查全部通过，均执行实际 SIGKILL。

- 最终受影响集曾将所有阶段放在一个新库：64 passed / 3 failed，原因是底层账本夹具不含 research_id/entrance，而 CE2 全表断言要求完整业务身份；并非执行链失败。保留 final-affected-regression.txt。最终改用原综合脚本，逐阶段新库隔离。

## 运行入口

现有入口继续可用：配置 DATABASE_URL、OPENAI_API_KEY 与提供方 OPENAI_BASE_URL 后，执行 `.venv/bin/python -m agent_core.research_cli --storage postgres --engine openai --blobs <资料目录> --private <私有目录> serve --durable --port <空闲端口>`。保持资料与私有目录不变，重启后 DBOS 接续同一固定任务。脚本或内存后端不能启动 DBOS；不修改历史服务端口。

## 明确边界

不提供任意 await 恢复、跨主机接管、模型请求恰好一次语义，或对未确认工具副作用的自动重做。未发布孤立文件允许保留，不删除用户历史。此阶段未完成 CE8 固定基准与旧产品路径收敛，也未关闭历史 C4/C5 广泛语义质量缺口。
