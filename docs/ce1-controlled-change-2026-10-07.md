> 历史范围调整记录。当前真实验收已经通过，见 [当前记录](ce1-acceptance.md)。

# CE1：受控工具执行与逻辑数据隔离验收

2026-10-07。**范围调整与代码替换已完成；真实模型和 PostgreSQL 闭环仍待验收，未进入 CE2。** 当前要求见[受控执行决策](ce1-controlled-execution-decision-2026-10-07.md)。

## 本次变更

- 删除 CE1 Docker runtime、runner/coordinator Dockerfile、Compose、两个镜像构建脚本和容器验收测试。
- `sandbox/` 替换为 `controlled/`；可信服务内的 SDK runner 经 broker 调用固定模型与四个受控工具，没有双后端或兼容路径。
- 继续使用真实 PostgreSQL 中的预算、操作预占、取消、单写者与世代；新表名为 `controlled_*`，不迁移或修改历史数据库。
- 私有状态为服务选择的目录中的 SQLite + 内容哈希文件，完整工具结果在返回 SDK 前保存。私有工具本身也在 broker 的预占、验证与结算范围内。
- 取消等待 SDK 协程结束并释放写者，提交前复核授权；broker ACK 不等于正式发布，结果仍走 TaskLedger/TaskReceipt。
- live 脚本移除容器前提，以全新输出目录保存 blob/私有笔记；用独立进程重读真实文件，并检查保存重试没有新增模型调用。

## 结果与证据

首轮回归 **55 passed / 15 skipped**。受限环境复测 **38 passed / 4 skipped / 28 errors**，pytest 创建临时目录失败，shell 同时报告 no space left on device。复测重启及日志重命名均被空间不足阻止。[原始输出](ce1-evidence/controlled-regression-2026-10-07.txt) 保存的是受限环境失败记录，不能作为全绿证据。仅清理本轮两个已结束 pytest 的临时目录，未清理用户数据。**最终相同测试在提升执行权限、使用独立临时目录后通过：55 passed / 15 skipped**，见[最终输出](ce1-evidence/controlled-regression-final-2026-10-07.txt)。执行方式变化没有改变测试断言，也未访问真实模型或历史数据库。其中 4 个新执行账本的 PostgreSQL 用例因未配置专用数据库跳过，另 11 个为既有 TaskService 外部测试；跳过均不计通过。

已验证：真实文件/SQLite 原子保存和哈希、新进程重读、逻辑工作区分隔；SDK 公共接口契约、工具白名单、参数和身份拒绝；受控 engine 私有读写；取消等待 SDK 协程结束且不结算未确认请求；已有任务与架构回归。SDK/模型测试使用明确的测试响应，仅证明代码契约，不能证明真实模型语义或 PostgreSQL 持久化。

真实模型调用本轮 **0 次**；真实 PostgreSQL 验收本轮 **未运行**。宿主磁盘检查仅剩约 **125 MiB**，没有在接近满盘时新建 PostgreSQL 集群或向历史数据库写入，也没有为了验收删除文件或停止历史服务。

删除的容器/硬资源配额项记为“范围移除”，不记为通过。旧容器证据仅存档：[10 月 5 日](ce1-acceptance-2026-10-05.md)、[10 月 7 日旧方案续测](ce1-acceptance-2026-10-07.md)。

## 继续真实验收

先释放宿主磁盘空间，为独立 PostgreSQL 数据、WAL 和验收记录留出余量。无需恢复或启动 Docker。使用全新 `ce1_` 数据库与输出目录，保持现有 OS 执行锁/advisory lock；锁被占用时明确停止，不绕过。

凭据通过环境安全注入，不写入源码、命令历史或文档。用户已经授权 CE1 真实模型调用，不需要重复授权。`OPENAI_BASE_URL` 使用 `https://api.deepseek.com`，API 选择由当前提供方接口能力确定。

```sh
# CE1_TEST_DATABASE_URL 指向全新专用 PostgreSQL 数据库。
.venv/bin/python -m pytest -q tests/test_controlled_external.py

# OPENAI_API_KEY、OPENAI_BASE_URL 由进程环境注入；每次使用全新输出目录。
.venv/bin/python scripts/verify_ce1_live.py --api chat_completions --model deepseek-flash --allow-paid-model --output /tmp/ce1-controlled-complete
.venv/bin/python scripts/verify_ce1_live.py --api chat_completions --model deepseek-flash --allow-paid-model --scenario cancel --output /tmp/ce1-controlled-cancel
```

真实闭环需要同时核对原文 Marker、已确认数量 7、未知数量未被编造、私有读写哈希、有效正式回执、保存重试幂等、取消/迟到拒绝及未知用量保留。当前未声称这些全部通过。
