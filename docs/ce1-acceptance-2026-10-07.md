> **历史容器验收记录。** 同日后续用户已取消容器要求；下文的 Docker/硬配额阻断不再适用。当前状态见[CE1 验收入口](ce1-acceptance.md)。

# CE1 真实验收续测：2026-10-07

**结论：部分通过，CE1 仍未完成，未推进 CE2。** 用户本轮已授权使用提供的 DeepSeek 凭据验收，不再缺少模型调用授权；真正的阻断是协调器构建失败、宿主磁盘空间不足及随后 Docker 无响应。真实模型尚未调用，不将凭据可用或容器测试通过等同于模型闭环通过。

## 实际结果

| 检查 | 本轮结果 | 原始证据 |
| --- | --- | --- |
| DeepSeek 凭据及模型目录 | HTTP 200；返回 deepseek-flash / deepseek-v4-pro；仅查询目录，0 次模型调用 | `ce1-evidence/provider-preflight-2026-10-07.json` |
| Docker Desktop 启动 | 通过权限升级启动成功，Engine 29.0.1 / Linux arm64；上次 LaunchServices 错误不能证明安装损坏 | `ce1-evidence/docker-info-2026-10-07.json` |
| 专家镜像 | 构建成功；不可变 ID `sha256:dedb4e94f8542d9fa2b64b4e2aeecfd50f3996c1f1648fb391445963544b69f8` | `ce1-evidence/build-runner-2026-10-07.txt` |
| 真实容器隔离、私有笔记重建及停止 | 第一次失败，修复启动就绪竞态后 **1 passed / 2 deselected** | `ce1-evidence/container-attempt-1-2026-10-07.txt`、`container-attempt-2-2026-10-07.txt` |
| 独立 PostgreSQL | 新建 `research-ce1-20261007` 项目的数据库、卷、网络并启动成功；未完成 SQL 验收 | `ce1-evidence/postgres-start-2026-10-07.txt` |
| 协调器镜像 | **失败**：全项目依赖引入 GPU 库，nvidia-cudnn-cu13 解包出现 I/O 错误；检查时宿主磁盘约剩 257 MiB | `ce1-evidence/build-coordinator-2026-10-07.txt` |
| Docker 恢复 | 构建 CLI 已结束；API 五秒探测超时，Desktop 常规重启未能在 30 秒内停止旧进程 | `ce1-evidence/attempt-summary-2026-10-07.json` |
| 最后一次定向本地回归 | **55 passed / 11 skipped** | `ce1-evidence/regression-final-2026-10-07.txt` |
| 文本解析与资源回归 | 初次 **35 passed / 21 skipped / 1 failed**；失败是本机 socket 绑定权限，单项提权复测 **1 passed / 1 skipped** | `ce1-evidence/text-parser-regression-2026-10-07.txt`、`socket-retest-2026-10-07.txt` |

真实容器通过项包括：非 root UID、只读根目录、无外网、未暴露宿主用户目录、Docker socket 或模型/数据库密钥，启用 seccomp/no-new-privileges，创建子进程后移除容器，替换容器后私有笔记内容和 SHA256 一致。后续新增的精确 cgroup 数值断言没有重新执行，不算已通过。

## 本轮修复及后续准备

- `sandbox/docker.py`：创建 Docker attach 进程不等于容器已运行；现在等待实际 Running 状态，退出或超时明确失败。这是第一次真实测试暴露的问题，修复后真实复测通过。
- `sandbox/broker.py`：保留 SDK 合法的 `reasoning` 文本输入项，继续禁止远程媒体及 hosted 操作；有公开接口往返契约测试。不以受控响应测试冒充真实模型结果。
- `sandbox/engine.py`：失败诊断只报告异常类别和数字 HTTP 状态，避免输出凭据或完整异常正文。
- `compose.yaml`：协调器使用固定 hostname 和专用 `/tmp` 卷，在协调器重建时保留 OS 锁路径对应的文件；没有删除、改写或绕过应用锁。
- 原协调器镜像尝试安装全项目依赖，RouteLLM → torch → GPU 库的体积不适合这个纯文本验收切片。新版构建配方仅安装 CE1 实际调用的 SDK、PostgreSQL、DBOS、HTTP 和测试依赖，继续受现有 uv.lock 版本约束；生产 pyproject/uv.lock 未改动。新版配方尚未构建成功，不能声称其运行已验证。
- 将现有 DocumentParser 的 UTF-8 按行解析逻辑提取到 `adapters/parsing/text.py` 共用，正式 DocumentParser 和纯文本验收入口使用同一实现，没有假解析数据。验收入口显式只接受 text/plain，不声称覆盖 PDF/OCR/路由模型；不需要为这个样例安装 GPU 运行库。
- 外部测试补充真实 PostgreSQL 的新进程读取、预算/取消/fencing 保留，以及 broker 伪造身份、重复操作和迟到结果拒绝。这些新增外部测试尚未运行。
- 真实模型脚本补充完整交付的保存重试、笔记重建前后哈希比较，以及 `--scenario cancel` 的真实在途模型请求取消检查。两种模式均未运行，不能据脚本存在宣布通过。

源文件摘要见 `ce1-evidence/implementation-hashes-2026-10-07.json`。

## 资源故障和保留状态

失败构建未响应普通取消，最后仅对核实属于本次 CE1 的 Docker build / Buildx CLI 进程执行终止；Python 构建器退出码 1，原始构建失败日志保留。Docker 启动时记录有 137 个停止的历史容器、26 个镜像，未进行全局 prune、删除历史镜像/卷或清理用户文件。未停止或改动 8771/8782/8783 宿主服务。

Docker API 后续无响应，因此**不能确认失败构建缓存已完全回收，也不能确认本轮 PostgreSQL 容器当前状态**。不把“已结束构建 CLI”写成“磁盘已经恢复”。磁盘余量见带字节数的 attempt-summary；最后检查仍只有约 267 MiB。继续构建、数据库或模型验收在此条件下不可靠。

密钥通过关闭回显的交互输入传给一次性的凭据核验进程，没有写入源码、`.env`、证据文件或专家镜像。本轮没有真实推理请求。用户对 CE1 的模型验收授权保留，后续无需重复请求同一授权。

## 继续条件与命令

先释放足够的宿主磁盘空间并恢复 Docker API；恢复后先识别并清理本轮失败构建缓存，不能顺带清理历史数据。然后：

1. 用新版 `scripts/build_ce1_coordinator.py` 构建精简协调器，核查实际依赖与镜像。
2. 设置 `CE1_TEST_IMAGE` 为上述实际 runner ID，在同一隔离项目执行 `docker compose -p research-ce1-20261007 -f docker/ce1/compose.yaml run --rm coordinator`，运行全部真实外部测试。
3. 把已授权凭据只传给可信协调器，使用 `verify_ce1_live.py --api responses --model deepseek-flash --allow-paid-model --output /acceptance/<新目录>`；正向和 `--scenario cancel` 各保留独立证据。
4. 检查语义内容、真实提交回执、未知用量、进程终止及持久状态，再补齐必要负向用例。
5. 私有 named volume 的硬磁盘配额仍是独立未关闭要求。应用级单条大小上限和本次 cgroup 参数不等于持久卷硬配额。

以上全部通过前保持 CE1 待验收，不进入 CE2，不删除尚未由验收后的新路径替换的旧产品入口。
