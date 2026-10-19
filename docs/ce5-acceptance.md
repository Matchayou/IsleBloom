# CE5 编码工作区与集成验收

2026-10-07。状态：当前受控编码范围验收通过；CE6–CE8 待按顺序推进。本聊天已获授权按 CE5→CE8 顺序继续。此前止于 CE4 的授权描述是历史记录。

## 执行边界

保留可信本机服务、Agents SDK 0.22.2、DBOS 3.0.0、PostgreSQL 和单进程锁；没有任意 Shell/Python/HTTP 工具，没有容器或 OS 强隔离。编码模型只获得固定文件和固定基线授权。源仓库由操作员登记；服务复制 Git 对象到受控 bare 仓库，再创建任务 worktree，不改源 checkout。参考 [Git worktree](https://git-scm.com/docs/git-worktree)、[Git hooks 配置](https://git-scm.com/docs/git-config)、[diff 扩展控制](https://git-scm.com/docs/git-diff)；使用 Git 自身的分支、index、commit-tree、update-ref 和三方 apply，不另造版本控制。

可信测试 profile 是操作员登记的外部固定 argv、环境身份及超时。模型只能传 profile 名；命令没有模型参数。子进程收到不含 .git 的代码副本和清理过的环境，不继承 API 凭据。profile 必须将代码作为数据处理，不能解释仓库脚本为宿主命令。当前真实样本使用 Python AST 验证赋值结构和值，不执行生成的仓库代码；不宣称覆盖任意项目的运行时测试。需要执行不受信任代码的项目不属于这个测试契约。

## 需求—实现—证据矩阵

| 要求 | 实现 | 当前证据 |
|---|---|---|
| 编码任务、方法、路由和成果 | TaskSpec.coding、CodingContract、coding 方法、路由能力交集；CodingAnswer 返回固定 candidate_ref，服务补充 CodingDelivery/candidate/code_patch | tasks/methods/routing/SDK/PostgreSQL 回归，真实模型候选 |
| 专属分支/重试复用 | coding/workspace.py；固定 repository/task/spec/授权世代/基线/文件范围/profile 哈希；SQLite 保存身份和冻结状态 | 真实 Git 双工作区；同任务重取一致；主 checkout 不变 |
| 文件和 Git 边界 | 精确路径白名单；拒绝越界、控制文件、凭据路径、符号/硬链接、子模块；只允许 working/baseline 读取；串行 Git 作业；干净环境/config、关闭 hooks/diff/textconv/fsmonitor | tests/test_coding.py 负向验证；最终合并回归通过 |
| 构建/测试授权 | TestProfile 外部可信程序；命令与程序文件哈希在任务准入冻结；测试后核验输入副本和工作区未改变 | 真实可信子进程；profile 变更拒绝；失败/旧测试不能冻结 |
| 完整冻结和可审查交付 | checkpoint 包含获准新增文件；冻结后拒写；完整元数据与 diff 随原 TaskLedger 事务保存；报告哈希覆盖机器附加内容 | broker/保存重试专项；真实第四轮 12 次 HTTP 200，独立语义核对通过 |
| 独立集成 | 新任务 contract 引用同代同基线冻结候选；独立 worktree 三方 apply；必须运行组合 profile；冲突保留工作区和 resolution_task，允许同任务解决冲突；清除冲突后重新测试才可冻结 | 组合与冲突真实 Git 测试；真实模型两候选的程序组合验证 |
| 取消/崩溃/归档/撤权 | 既有 broker/TaskService fencing；测试进程取消终止进程组；未提交文件保留；归档先 checkpoint；旧授权无法读代码、日志或正式候选 | 真实 SIGKILL 保留新文件；主助手撤权阻断测试；独立 PostgreSQL 重启前后逐字一致 |
| 主助手与产品展示 | 操作员 grant_coding；find_experts 列已授权 coding_contract_id；delegate_task 固定选择；正式结果含原始 diff；TaskArtifact 展示提交、diff 和测试日志 | 真实 SDK+PG 主助手契约测试；前端构建、真实结果 Chromium 浏览器验收通过 |

## 运行入口

- Python：构造 `CodingService(root, repositories, profiles)` 并传入 `build_controlled_services(..., coding=...)`。
- CLI：`agent-tasks --engine openai --coding-config <operator.json> grant-code <research> <name> <contract.json>` 登记研究级契约；随后 `create <research> coding <request> --coding-contract <name>`、`run <task>`。也可由主助手从 find_experts 选择 coding_contract_id。
- operator.json 必须含 root（专用存储根）、repositories（仓库 ID 到操作员授权路径）、profiles（name/argv/environment_id/timeout_seconds）。不能由模型提供配置或路径。
- contract.json 含 repository_id、40 位 baseline、read_paths、write_paths、test_profiles；集成另列 candidate_workspaces。命令和可信程序哈希由服务冻结，不由模型填写。
- 真实验收：`scripts/verify_ce5_live.py --output <新目录>`；凭据仅用 OPENAI_API_KEY 环境或 `--hidden-key` 无回显输入。数据库环境 CE5_TEST_DATABASE_URL，必须新库。
- 新进程核验：`scripts/verify_ce5_saved.py <证据目录> --output <记录>`，禁止所有模型调用。

## 已执行结果与失败保留

- 首轮旧任务/方法/路由：41 passed / 38 skipped（未配置 PG），不计持久性通过。
- 首轮 Git：11 passed / 2 failed；修复获准但尚未创建文件引起的 Git pathspec 错误。随后 14 passed。
- 首轮完整 PG：93 passed / 1 failed；工具 JSON 不能包含 tuple，改为 JSON 数组。
- 第二次 broker：因测试 fixture 未解包 ToolResult.value 失败；同时将候选内容与 remaining_tool_calls 分开，避免污染固定元数据。修复后 1 passed。
- 第一轮真实模型：17 次 HTTP 200，两候选均写入和测试；A 回写完整候选时丢失日志末尾换行，被严格校验拒绝。改为固定 candidate_ref，由可信服务附上原始机器元数据。B 虽成功，不能把本轮记为双专家通过。
- coding-v2 回归：124 passed / 6 skipped（DBOS/协作专用环境未设）；主助手新增专项 2 passed；真实 Git/进程/篡改/授权专项 18 passed。
- 第二轮真实模型：程序检查全部通过；后续补齐正式 code_patch 和可信 profile 内容哈希，不能代替最终版本验证。
- 第三轮真实模型：程序检查通过，13 次 HTTP 200；独立语义核对发现 A 虚构“源文件有冲突嵌入文本”。详见 live-attempt3/independent-review.md。保留原文，修正通用观察规则并用 coding-v3 新库复验。
- 新进程核验脚本最初把 datetime 与序列化字符串直接比较而失败；修正比较表示，不改实际预算。

原始输出位于 docs/ce5-evidence。最终综合回归 [combined-final.txt](ce5-evidence/combined-final.txt)：**238 passed，无跳过**。随后补齐活动写者禁止归档的守卫，[编码专项最终检查](ce5-evidence/writer-final.txt) **23 passed**；不把这两份重叠结果相加。

第四轮 [真实记录](ce5-evidence/live-attempt4/live.json) 程序与[独立语义审阅](ce5-evidence/live-attempt4/independent-review.md)通过。12 次真实 HTTP 200；两候选及独立组合测试通过，正式回执仍待复核、未采用。代码是固定合成 fixture，模型与 Git/测试子进程/PostgreSQL 都真实运行，不外推任意代码的运行时质量。

[新进程核验](ce5-evidence/live-attempt4/before-restart.json)与[独立集群重启后核验](ce5-evidence/live-attempt4/after-restart.json)经 cmp 完全一致：固定 Git 内容、完整 diff、原回执、预算与 wire 清单均保持，保存重试零模型调用。备份保留在 `.ce5/backups/ce5_live_attempt4.sql`。

[浏览器](ce5-evidence/live-attempt4/browser.json)从真实已保存报告检查固定提交、代码 diff、新增文件、对应测试日志；零页面错误，[截图](ce5-evidence/live-attempt4/browser-code.png)已视觉核对。8795 本轮预览已停止。前端构建通过，原有大 bundle 提示仍在。

当前 CE5 范围通过，允许进入 CE6。没有自动采用候选，没有推送/合并到用户源码仓库或部署。仍不提供任意仓库脚本执行、容器/OS 强隔离；这与本阶段冻结的可信验证条件一致。历史 C4/C5 广泛语义质量缺口未因此关闭。
