# R3 统一聊天、消息分支与选择性回传：实现及验收

日期：2026-10-01。状态：**R3 离线控制、持久化、真实 Chromium 页面和 Stage 0–4/R1/R2 累计回归通过；真实模型语义质量仍受 R1 真人签收及 R2 Jev/双模型配置阻塞。** 脚本输出仅证明控制链路，不代表研究结论可信。

## 复用与实现

先核对既有 `ResearchNode`、结果版本、`ResearchLedger`、C0 会话快照、`TaskService`、R2 `Router` 及本地参考阅读索引。采用已有研究节点作为唯一分支身份、已有 Scope/ContextBuilder/执行账本和任务单；React Flow 仅作导航。分支消息在研究仓库中另存独立 revision，不改任务目标节点 revision，因此同一分支的多个专家任务可同时执行。参考既有 Stage 4 固定版本和 [设计文档](../architecture-analysis/当前产品-增量开发与技术选型.md) 的只读快照/显式选择规则；未复制外部项目代码、加入依赖或建立第二棵同步树。

| 要求 | 证据 | 状态 |
|---|---|---|
| 统一聊天与消息截止点 | `ChatThread` 保存有序本地消息、分叉消息 ID、分叉时继承的完整消息快照；主助手 `chat_reply` 从该快照装配输入并原子发布消息和节点结果 | 内存/PG/浏览器通过 |
| 兄弟分支与同专家跨分支隔离 | 分叉只复制截至选中消息的内容；任务创建时冻结该分支聊天并经现有 ContextBuilder 装配。截止点后、兄弟标记不能进入该输入 | `test_message_cutoff_siblings_continuation_and_upstream_hint`、`test_two_experts_same_branch_and_cross_branch_context`、Chromium 场景通过 |
| 多专家并发及 R2 路由 | 专家任务仍独立固定方法、模型和 route ID；同节点两个任务能同时进入执行器。路由任务继续保留正确分支上下文，未发送未授权历史给 Jev | `test_two_experts_actually_overlap_on_one_branch`、`test_routed_task_keeps_branch_chat_context`、浏览器两个专家活动通过；真实 Jev 待 R2 配置 |
| 回传与采用关系 | 仅能从分支自己的完整助手/已复核专家消息回传，服务端复制正文、来源版本、必要假设与限制；在一个事务写主线消息和独立 `ChatTransfer`。`for_discussion` 与 `adopted` 分开；源分支不变 | `test_complete_transfer_adoption_stale_target_and_revocation`、Chromium 回传并采用通过 |
| 迟到、撤权、上游更新 | 回复/任务绑定当前消息与来源版本；消息或来源变更阻止迟到发布。读取历史消息重验研究根范围和当前分支范围；冻结的专家上下文在执行及活动、回执、路由读取前重验。目标聊天和节点 revision 过时拒绝回传。上游后续消息只显示提示，不注入已分出的快照 | 根范围及节点级撤权的 R3 内存/PG/HTTP 测试、页面通过 |
| 停止、保存失败、续聊 | 一次性失败注入只影响保存，重试保存后消息与结果一起出现，模型未重跑；可停止运行专家，取消结果不能交付；之后仍可继续聊天 | `test_chat_reply_save_retry_is_atomic_and_does_not_rerun`、独立 Chromium 故障场景通过 |
| Stage 0–4、R1、R2 回归 | 专用 PG 17 库 `/tmp/r3-pg-20261001`，浏览器实际 HTTP 服务；Stage 4 页面测试随 R3 导航调整了定位和选择时机 | **245 passed** Python、**3 passed** 主浏览器、**1 passed** 故障浏览器，均无 skipped |

## 浏览器证据

- [主线回传与采用截图](r3-browser-branch.png)，[多专家活动截图](r3-browser-experts.png)，[保存重试及取消截图](r3-browser-failure.png)。自动化结果为 [Stage 4 + R3 浏览器 JSON](stage-4-browser-tests.json) 和 [故障浏览器 JSON](r3-failure-browser-tests.json)；命令及终态见 [r3-tests.txt](r3-tests.txt)。
- 持久页面实例在专用 PG 库中完成两场景：主线 `588d2492…` 的消息在 `AFTER_SECRET_R3` 前分出两个兄弟；各自独立续聊；分支 `1e34c5f9…` 的完整脚本结论带回主线，回传 `70742d8f…` 从 `for_discussion` 显式变为 `adopted`。同一分支在活动面板运行了 `search` 与 `synthesize`，均保持待独立语义复核，未伪造通过。
- 可重复的故障浏览器服务为 `scripts/verify_r3_browser_server.py`，仅用于验收：首个主助手保存注入一次失败，页面出现“只重试保存”；重试后两条消息原子出现，续聊增加下一轮；20 秒可停止脚本专家，显示“已取消/需返工”，聊天没有自动插入专家占位结果。该服务使用临时内存库，验收后已关闭。

## 边界与待办

- 真实 Jev、两种已授权执行模型、价格/容量和质量基线仍未提供；R2 真实质量/成本验收未完成，R3 的路由连通为离线夹具验证。没有使用任何未授权外部模型或历史密钥。
- R1 的 DeepSeek 提取→分析及 Codex 来源核对记录仍有效，真人语义签收未完成。R3 页面脚本回复与专家占位不能充当该签收，也不证明真实模型分支结论质量。
- 首版回传单位是**完整消息结论块**，不切句、不自动解决冲突、不反向同步上游。来源显示固定身份；可点击原文和证据支持性属于 Stage 5。
- 当前运行边界仍为同主机单执行进程；进程退出后的未保存执行不自动恢复，Stage 6 才验收持久恢复。验收 PostgreSQL 与 blob 目录要成对保存；本轮浏览器/测试服务已关闭，专用 PostgreSQL 测试集群仍在 `/tmp/r3-pg-20261001` 端口 55442，未改旧 R1/R2 数据。
