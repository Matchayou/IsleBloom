# 本地 CLI 文件存储改造方案

状态：设计方案，本次尚未实施。用户选择先修旅行截断，另行整理文件存储方案；当前产品仍使用 PostgreSQL / DBOS。

## 目标与设计依据

采用用户指定的 Markdown / JSON / JSONL / SKILL.md 格式，保留真实模型、工具权限、会话恢复与人工审核能力。产品只提供本地文件存储入口，完成替换后删除 PostgreSQL 和 DBOS 产品路径，不增加双后端、自动回退或数据库迁移逻辑。

参考 [Claude Code 记忆](https://code.claude.com/docs/en/memory)的 Markdown 文件与分层作用域、[配置](https://code.claude.com/docs/en/settings)的 JSON 文件，以及 [Agent Skills 标准](https://agentskills.io/specification)的技能目录。现有 SkillCatalog 已读取 `skills/<name>/SKILL.md`，可继续使用；无需重新发明技能格式。

## 目录与唯一事实来源

```text
.agent/
  config.json
  memory/
    user/<id>.md
    projects/<project-id>/<id>.md
    specialists/<specialist-id>/<id>.md
  projects/<project-id>.json
  sessions/<session-id>/
    session.json
    runs/<run-id>.json
    artifacts/<sha256>
    workspace/
  logs/skills/<session-id>.jsonl
  resources/<resource-id>/metadata.json
  resources/<resource-id>/original
skills/<name>/SKILL.md
```

| 数据 | 格式及职责 |
| --- | --- |
| Memory | Markdown 正文；frontmatter 保留 id、version、scope、scope_id、status、source、expires_at、publication_id |
| Session | JSON；会话 revision、原始消息、分支关系、任务、批准计划、协作关系和固定资源引用 |
| Run | JSON；模型输入引用、固定预算、SDK RunState、操作状态、已确认用量、错误与审核收据 |
| Skill 日志 | 追加 JSONL；加载、版本哈希、使用结果及错误；不能替代会话和运行状态 |
| Skill 文件 | 标准 SKILL.md 与原有 references/scripts/assets 目录 |
| 配置 | JSON；模型、公开端点、上下文与运行默认值；密钥仍从现有环境读取 |
| 资料与工件 | 原文件及按哈希保存的固定正文；JSON 元数据维护授权和来源位置 |

项目目录保存真实 Project 身份与成员信息，领域记忆按固定专家身份隔离。JSONL 是诊断与学习输入，不承担授权、批准或操作确认；运行 JSON 才决定哪些步骤完成，避免依靠日志猜测是否执行过。

## 实现边界

保持服务、模型执行、存储三层。沿用现有 Pydantic 数据模型和 SDK Runner，用明确的文件 repository 替换对应 PostgreSQL repository；不模拟 SQL 表，不用一个万能 JSON 字典承载所有服务。现有 MemoryDatabase 只作为测试内存实现，不能直接宣称有持久化保障。

会话修改与运行确认在同一个会话目录内由单写者持有 OS 文件锁。JSON 通过同目录临时文件、flush/fsync、os.replace 与目录 fsync 提交；写入前检查 revision，CLI 中断后主文件应当始终是完整的旧版本或新版本。跨会话任务只保存固定交接消息，不依赖跨文件的隐式事务。

每个模型或工具操作先保存 `started`，取得完整结果再保存 `confirmed` 与其结果引用；恢复只重用已确认结果。进程中断留下的未知操作应明确显示并等待核查，不能默默重新执行。工具副作用、批准、审核状态由服务规则判断，文件存储不能改变这些规则。

普通 CLI 前台执行保留 SDK 导出/恢复。需要后台任务时，使用同一个本地 worker 命令读取 session/run JSON；删除 DBOS 队列协议和工作流版本兼容路径。先完成前台端到端产品，再接后台 worker，不能先删除可工作的入口而留下无法执行的半成品。

检索保持当前流程：先过滤 owner/Project/专家身份，再选择技能与记忆。正文从文件读取，SKILL.md 冻结哈希、资料引用与已确认记忆的版本仍有效。先用目录扫描和现有排名逻辑，不加入向量库或搜索服务。原有 SQLite 私有索引也应在这一轮文件存储替换中改成 JSON 索引，避免“移除数据库”之后仍要求本机数据库文件。

## 分层交付及验收

1. **会话与运行**：实现 JSON 会话、固定输入、用量和 SDK checkpoint；命令行完成创建 → 提问 → 调用工具 → 保存结果 → 重开继续。验收截断用量、重复命令、修改 revision、写入中断和未知副作用。
2. **记忆、技能与资料**：接入 Markdown、JSONL、SKILL.md 和资料文件；验收过期/撤销、三个记忆作用域、技能版本冻结、分页正文与原始引用。
3. **协作和后台任务**：会话 JSON 保存任务依赖与通知；本地 worker 完成调度、取消、审核与恢复。验收两个 CLI 同开、重复派遣、依赖变更、执行中终止和重新进入。
4. **收敛产品入口**：切换所有 CLI 与本机 API composition，删除 PostgreSQL adapters、ControlledLedger SQL 路径、DBOS 和相关必需依赖，替换旧产品测试与启动文档。验收新目录启动不要求 DATABASE_URL，所有当前入口可操作。

旧数据库不参与新运行，也不自动迁移或删除。替换完成后从新的文件工作区开始；历史数据的归档或导出需作为明确的独立需求处理。每层必须先验收工作产品，再继续扩大范围。
