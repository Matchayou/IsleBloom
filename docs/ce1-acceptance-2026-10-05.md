> 历史容器方案，已由 2026-10-07 受控执行决策替代。

# CE1：单专家沙箱与私有草稿纸验收记录

**最新（2026-10-07）：真实容器部分验收通过；凭据核验成功，但协调器构建遇到磁盘空间不足，真实模型/数据库闭环仍未完成。详见 [10 月 7 日续测记录](ce1-acceptance-2026-10-07.md)。以下保留 10 月 5 日的实施与历史验收记录。**

日期：2026-10-05。**状态：实现切片已落地，真实外部验收未通过／未运行，CE1 不宣布完成，CE2–CE8 未开始。**

开发依据：`multi-agent-context-incremental-development-plan-2026-10-05.md` 的 CE1、统一架构、`development-acceptance-process.md`、协作进度和 Raven 调研。已完整阅读这些文档。目录及祖先未发现额外 AGENTS.md 文件；遵循用户提供的 AGENTS.md。开始时 `git status --short` 显示全部项目内容未跟踪；未重置、清理、提交或覆盖历史文件。原入口尚未经过新沙箱验收，因此保留原产品，新 CE1 入口明确指定 Docker，失败不会调用原入口。

## 本阶段实现边界

入口为 `agent_core.composition.build_ce1_tasks`，执行已有 `TaskService.run` 的独立正式专家任务。输入使用现有固定 TaskSpec、TaskRun、ContextSnapshot，输出使用 ExpertAnswer / MarketAnswer 与现有 TaskLedger / TaskReceipt 的提交和验收语义。主助手、普通讨论、信息咨询与协作任务入口属于 CE2，本入口对 `context_packet_id` 明确拒绝；不宣称四入口已隔离。

CE1 能力取当前工具集合与 `read_source / search_source` 的交集，另提供只写本会话 `/state` 的 `write_private_note / read_private_note`。无任意 HTTP 转发、宿主路径、宿主 shell、自动联网或编码 worktree。私有记录包括完整工具结果、协议 transcript 和最终结果，SQLite 只作私有索引，不授予证据、权限或提交资格。

SDK 0.22.2 的 `Model.get_response`、`ModelResponse`、`FunctionTool`、`Runner.run`、公开输出 schema 接口已实际导入检查；输入过滤公开字段已探测，未提前实现 CE3。保留 DBOS 3.0.0，不改写编排。沿用现有提供方环境配置与冻结模型标识，协调端的 HTTP 自动重试设为 0；调用前预占预算，未知响应保留预占，不声称 exactly-once 或调用级恢复。

参考 [SDK Model 官方接口](https://openai.github.io/openai-agents-python/ref/models/interface/)和 [Docker run 官方参数](https://docs.docker.com/reference/cli/docker/container/run/)，实际兼容性以本地固定 SDK 和待执行的容器验收为准。

## 需求—实现—证据

| CE1 要求 | 当前实现 | 已验证 | 剩余验收 |
| --- | --- | --- | --- |
| 完整本地 SDK 循环在容器 | `sandbox/runner.py` 复用 OpenAIEngine；`sandbox/model.py` 通过公开 Model 转发 | 真实 SDK + 受控响应的接口与工具循环契约；镜像白名单独立导入 | 真实容器内真实提供方完成任务，未运行 |
| 非 root、只读根、无网络、限制 CPU/内存/PID | `sandbox/docker.py` 固定参数，非可配置降级；要求 Linux/seccomp/资源能力、固定镜像 ID | 参数契约、坏镜像 ID/路径拒绝、daemon 不可用不触发模型 | Docker 实际边界负向测试跳过 |
| 不携带凭据/中央模块 | runner 构建从显式源码白名单生成临时上下文；不复制仓库根、`.env`、数据库适配器或 broker | 最小上下文能导入 runner；无 SQLAlchemy/psycopg/DBOS 导入 | 镜像构建和实际环境检查未运行 |
| 身份绑定、序号、操作 ID、大小和超时 | broker 身份仅来自协调器对象；4 MiB JSONL；拒绝重复字段、身份附加项、非有限 JSON、未知操作、远程媒体；运行时限及每 250ms 复核 | 攻击输入、乱序、错误工具集、撤权后响应不确认 | 容器攻击流与真实取消联测待验收 |
| 固定权限和合法提交 | 每次转发前后检查冻结任务/权限；清理结束后再次检查；最终调用原 TaskLedger | 已有任务/提交必要回归；客户端不能伪造 usage；重复完成帧拒绝，ACK 明确未发布 | 真实数据库中迟到提交、回执丢失和重复发布的联测待验收 |
| 私有草稿原子持久化 | `workspace.py` 内容寻址、临时文件 fsync+rename、目录 fsync 后提交 SQLite；读取校验 hash；无跟随符号链接 | 真 SQLite/文件重开、中文内容与哈希、索引提交失败不覆盖旧笔记、越界与链接拒绝 | 同卷替换真实容器保持内容；单机文件测试不算 OS 隔离 |
| 单写者与 fencing | 中央 PostgreSQL 锁行；writer token+epoch；先记录容器名再创建；接管必须确认删除旧容器；不按年龄抢占 | 代码检查；真实 PG 测试已编写但跳过 | 并发写者、旧 token、运行中接管和真实进程崩溃待验收 |
| 持久模型/工具预算 | `sandbox_runs / sandbox_operations` 预占调用、固定 deadline、确认用量；未确认不退额度；所有本地笔记调用也占工具额度 | broker 顺序契约，模型响应撤权时保留未确认 | 真实 PostgreSQL 预算重开和耗尽测试跳过 |
| 持久取消和停止进程 | TaskService.stop 注入中央取消写入；任务版本取消后不能重启；watchdog/取消清理调用 Docker rm --force，确认后释放 writer | 当前任务回归；取消存储测试已编写 | PostgreSQL 持久取消、实际子进程终止与禁止发布联测待验收 |
| 磁盘硬限额 | 当前为 Docker named volume；单记录大小上限仅应用限制 | **未验证硬配额** | 必须在目标部署实测/选择可强制配额的卷配置；这是 CE1 阻断 gap |

仅新增当前阶段实际使用的 sandbox 模块与中央表，没有 CE2–CE8 空表、第二沙箱后端、兼容层、迁移或降级路径。

## 已执行命令与结果

原始证据：[`ce1-evidence/preflight.json`](ce1-evidence/preflight.json)、[`ce1-evidence/tests.txt`](ce1-evidence/tests.txt)、[`ce1-evidence/cancellation-regression.txt`](ce1-evidence/cancellation-regression.txt)。

| 命令／检查 | 退出码 | 结果 |
| --- | --- | --- |
| `docker version` | 1 | CLI 29.0.1；desktop-linux socket 不存在，无服务端连接 |
| `docker info --format '{{json .SecurityOptions}}'` | 1 | 同上；不能确认真实 seccomp/cgroup 能力 |
| `open -a Docker` | 1 | 未找到可启动的 Docker 应用 |
| 打开 Docker.app 及其内嵌 Docker Desktop.app | 各 1 | LaunchServices 报 `kLSNoExecutableErr`；未修复/重装运行时 |
| 本地导入 SDK、DBOS、Model/ModelResponse/RunConfig | 0 | openai-agents 0.22.2，DBOS 3.0.0；具体接口/依赖版本见 JSON |
| `python -m compileall -q`（新增模块及脚本） | 0 | Python 语法通过；不代表镜像能构建 |
| 下列定向 pytest 命令 | 0 | **87 passed / 28 skipped / 0 failed**；其中新增 11 passed / 3 skipped |
| `python -m pytest -q tests/test_collaboration.py -k cancel` | 0 | **4 passed / 1 skipped / 31 deselected**；补查停止回调对协作取消路径的影响 |

```sh
.venv/bin/python -m pytest -q \
  tests/test_sandbox.py tests/test_sandbox_external.py \
  tests/test_sdk_contract.py tests/test_core.py tests/test_execution.py \
  tests/test_tasks.py tests/test_architecture.py
```

跳过项明确包括新增真实 Docker 测试 1 项、真实 PostgreSQL 测试 2 项，以及既有 PostgreSQL 分支 25 项；未设置历史数据库 URL，也没有借历史通过数宣称新架构通过。SDK 受控响应测试只验证公开接口映射，不代表真实模型语义通过。

失败与修复：新增镜像导入测试第一次 **8 passed / 1 failed**，失败是测试错误硬编码 `openai==2.*`，与项目实际锁定依赖不符；改为核对 `openai-agents==0.22.2`，复测通过。另在静态依赖检查中补入 runner 需要的 `orchestration.models`；在提交窗口复查中补入持久取消及清理后再验证，随后重跑上述回归。

## 隔离验收入口

当前尚未执行以下构建／运行命令。Docker 必须先恢复可用。镜像内容和部署规则需经实际检查后才能算通过。

1. 在项目中运行 `.venv/bin/python scripts/build_ce1_image.py`。构建脚本创建独立临时目录，只复制 runner 白名单和从现有 uv.lock 生成的约束；输出不可变 image ID。
2. 运行 `.venv/bin/python scripts/build_ce1_coordinator.py`。协调器镜像单独安装项目锁定依赖和 Tesseract；这是可信测试环境，**不是专家镜像**。uv 使用本机核实版本 0.12.5。
3. 设置 `CE1_TEST_IMAGE=sha256:...` 为步骤 1 的实际 ID，执行 `docker compose -f docker/ce1/compose.yaml run --rm coordinator`。Compose 创建独立 PostgreSQL、卷和网络，无宿主数据库端口，不使用历史服务。默认仅跑不收费的真实容器／数据库测试。
4. 真实模型验收脚本为 `scripts/verify_ce1_live.py`，必须在独立协调器容器运行，显式提供已授权的模型、API 类型、协调器专用凭据及 `--allow-paid-model`。示意参数：`--api chat_completions --model <已冻结模型> --output /acceptance/<新的运行目录>`。本轮未执行、未获额外付费调用授权。

真实模型入口创建合成固定资料，经真实读取、私有笔记保存/重读、结构化结果及原 TaskReceipt 提交，再创建新容器检查同一私有笔记和工具 transcript。结果、blob 在新目录，输出标注合成资料。它只是一个正向样例，不能替代全部隔离、取消、撤权、并发和硬磁盘配额验收。

协调器可以访问 Docker socket、模型凭据和测试数据库；专家的创建参数不挂载这些内容。PostgresDatabase 原每 OS 用户/主机锁完整保留。新数据库不是再开宿主协调进程的许可；外部 PG 测试和 live 脚本均检查容器环境及 `ce1_` 数据库前缀。未停止/修改 8771、8782、8783，未使用或改变历史 blob、数据库、前端构建目录。

## 尚未关闭的门槛

- Docker daemon 不可用：未构建镜像，不能确认实际容器行为、OS 隔离、重建或资源限制。
- 独立 PostgreSQL/协调器环境未启动：事务、预算、取消、writer fencing 的真实数据库证据仍缺。
- 持久卷硬磁盘配额没有目标部署实证；named volume 默认不等于硬限额。
- 真实模型未调用；正式闭环、语义输出及与取消/撤权/重复提交的端到端联测仍待验收。
- 现有 SDK、TaskLedger、DBOS 机制继续保留；新 Docker 路径的真实 DBOS 重启、孤儿容器接管尚未联测，不声称调用级恢复。

下一步只补 CE1 的上述缺项和必要修复。CE1 验收前不移除历史可用入口，不自动进入 CE2。
