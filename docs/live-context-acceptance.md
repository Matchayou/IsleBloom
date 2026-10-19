# 真实模型动态上下文验收：充值后重跑

状态：**修复后，所有要求的真实模型场景通过**。这是生产 Responses API 与 PostgreSQL 的验收，未使用 ScriptedEngine、QueueModel、响应注入或模拟 HTTP 成功。文档及初始化用户/项目 memory 是明确标注的合成验收样本，模型回答、工具参数、领域候选和验证结论均来自真实提供方。

## 配置和环境

- 现有项目 `.env` 在进程内加载；凭据存在且有效，未写入参数、日志或报告。
- 端点 `https://api.deepseek.com`，主助手、规划、咨询、领域生成/验证使用 `deepseek-v4-pro`；专家执行和专家讨论使用 `deepseek-flash`。
- 独立 PostgreSQL 数据库 `ce2_live_retry_20261012`，端口 `55473`。测试源码、资料、发布及审计记录均与业务数据库隔离。
- 最终通过场景有 **47 次真实 HTTP 200 模型调用**，响应与用量全部确认。各任务 usage 可包含嵌套咨询，因此不能把下面的 requests 简单相加作为提供方调用数。

## 最终成功场景

| 入口 | 固定 run_id | 任务报告 requests | 验收 |
|---|---|---:|---|
| preflight_main | c1e87d1c-d8fa-4cfc-9142-6f21ce9bb83e | 1 | 通过，用量确认 |
| preflight_execution | 99115afb-5796-48cd-82f3-a6d63746ef94 | 1 | 通过，用量确认 |
| main | 7b90988b-11c4-41c1-8bfa-6139319960e5 | 6 | 通过，用量确认 |
| temporary | 0d75df04-2f08-4489-9d39-3a87af4cf6e1 | 6 | 通过，用量确认 |
| specialist | 9e872c3f-6a25-4e15-a72e-dacfc5d2cbe1 | 11 | 通过，用量确认 |
| temporary_consultation | information:0d75df04-2f08-4489-9d39-3a87af4cf6e1:manager:1 | 2 | 通过，用量确认 |
| specialist_consultation | information:9e872c3f-6a25-4e15-a72e-dacfc5d2cbe1:manager:1 | 6 | 通过，用量确认 |
| planning | e1f45c53-1773-49e9-8489-b0abefe48154 | 8 | 通过，用量确认 |
| discussion | 27b704c4-ad5e-44fb-97c2-623c947319e3 | 2 | 通过，用量确认 |
| learning_source | b3aaeb36-c9bd-4dae-a834-241ae6da1296 | 4 | 通过，用量确认 |
| domain_generate | ab6e521c-d3eb-4c3d-8ebc-58cbe780b5fc | 4 | 通过，用量确认 |
| domain_verify | 65466497-046f-49a9-9576-855b291ee16e | 2 | 通过，用量确认 |
| retrieval | 95e885e3-f137-4a9b-8017-6e642754df06 | 2 | 通过，用量确认 |

主助手实际加载项目 skill、读取原文，成功派发一个临时 Agent 和一个专精 Agent。两个专家分别加载 skill、读取固定来源、通过信息咨询取得年份原文，再实际读取获准的补充来源。字段保持 `confirmed_units="7"`、`market_size="unknown"`、`target_year="2025"`，引用由程序核对原文。临时 Agent 没有领域轨迹；专精 Agent 保存了冻结轨迹。

规划实际生成两步计划，停在 `awaiting_confirmation`，未执行节点。专家讨论基于固定报告作答，没有重新执行任务。咨询只授予本轮实际读过的补充来源。主助手与子任务的目录均在权限层关闭联网，其他研究的资料未进入上下文。

## 领域生成、验证、发布和检索

独立领域验收场景有自己的完整用户输入和两份获准合成来源。轨迹先生成隔离候选，源报告未独立复核时没有发布。验收代码逐一核对字段和所有实际引用，并核验数量、未知项、年份都有原文支持后提交源报告复核。随后真实验证模型评审 grounded/generalizable/privacy_safe/permission_safe；程序还要求每条引文为正式轨迹字符串的连续子串，并检查工具权限、来源复核状态及摘要封印。

正式发布：

- publication：`b3aaeb36-c9bd-4dae-a834-241ae6da1296`
- memory：`domain-memory:b3aaeb36-c9bd-4dae-a834-241ae6da1296`
- skill：`domain-dbcd347e5799a9c9d6c4d814`

新的专精任务实际调用 `search_memory`，返回了该 memory ID；实际 `load_skill` 返回了该 skill 的非空正文，再通过 `read_source` 读取单一获准来源，正确交付数量和未知市场规模，没有咨询、联网或读取补充年份。候选描述进入初始上下文，正文按需加载。发布后的域 memory/skill 对主助手、临时 Agent 和其他专精角色不可见。

每个最终入口都验证了冻结快照的最后 user 消息与本场景输入完全相等，包括前后空白和换行。交接包保留原话，子目标与目录分别限定；范围包括 user/project，只有对应专精角色额外拥有自己的 specialist namespace。模型与工具均通过 ContextAssembler → CoreRunner → ControlledEngine → Channel/Broker，模型不存在直接文件系统入口。失败准入的检索任务修复后曾用 `run_durable` 复用同一个 run、版本和冻结快照，快照相等检查通过。

## 实际发现和修复

- 临时派发工具说明未明确能力模板仍必需，模型发送两个 null 被参数验证拦截。明确 `expert_id` 与 `session_id` 含义，重跑真实派发通过。
- 静态隐私检查把通用 `unknown` 标记当作项目事实。允许这个明确的未知标记，仍拦截具体字段值、标识、数字、路径和密钥；回归同时验证具体公司值仍被拒绝。
- 领域学习输入含失败格式修复及作者 self_checks，导致验证引用混淆。学习只选择正式 report/fields/claims，完整原始轨迹仍不变；引用要求及拒绝原因明确，严格匹配未放宽。
- 验证器把系统的通用工具和 ResourceRef 协议误认作不可迁移的项目事实。明确复用范围为同一专精角色/工具契约下的不同任务和项目。
- 模型曾把辅助 `load_skill` 作为领域方法允许工具。生成 schema 现在使用已发布方法工具枚举，静态发布检查仍保留。
- 私有分页参数错误曾直接终止 SDK。现在经 Broker 审计、占用预算，返回不可执行的 `invalid_input`，允许模型以新的 call ID 纠正；路径穿越、额外 workspace 和未授权工具仍被拦截。
- PostgreSQL 中 memory 读取事务内再次验证域发布造成嵌套事务。读取冻结值后关闭读事务，再校验发布；新增真实 PostgreSQL 发布后选择及下一任务使用的回归。
- 验收脚本原先强制同一候选同时有 memory 和 skill，超出产品允许的可选输出契约，已纠正。补充场景曾继承旧年份目标，导致多交付字段；改为独立完整验收消息/明确结束旧目标的新消息，旧原话和快照保留，未把多交付的运行计为通过。

## 历史失败与限制

所有历史失败、拒绝候选和预检 HTTP 402 均保留。未验证或被拒绝候选没有进入正式目录。两次模型操作和一次工具操作仍为 `unconfirmed`，它们的完成状态和额外用量无法确认，不计通过，也不补造用量：

- `57c482f3-c854-4d31-8705-a258b889b3cc` / `model:5`，model。
- `8bc91851-20e5-4702-9a6c-91252274a568` / `tool:call_00_sEOMwPziP3DEaEpvX1fb7614`，tool。
- `information:8bc91851-20e5-4702-9a6c-91252274a568:manager:1` / `model:1`，model。

另有两次私有工具操作因预算关闭被明确 `denied`，这与未确认完成不同，审计分别记录。包含历史的已确认模型操作为 **109 次**，已确认 input/output tokens 分别为 **766728 / 79234**；这不含未确认请求的未知用量，不能据此宣称所有历史调用成功。

本验收覆盖本地后端、真实 SDK/提供方与持久化链路，资料是合成样本；不等于真实业务数据质量认证，也不使用合成模型回归替代真实模型结果。没有尚未执行的请求场景；历史未确认操作的提供方完成情况仍是上述限制。

## 回归与证据

最终事务修复后全项目回归：**796 passed / 0 failed / 0 skipped**，见 `full-regression-postgres-fix/results.xml` 和 `pytest.log`。新增 PostgreSQL 复用用例的针对性回归为 17 passed / 0 skipped。早期 794 项回归的失败记录也保留，未作为最终通过结果。

- `live-context-evidence/retry-20261012/acceptance-summary.json`：最终真实矩阵、角色隔离、原话摘要和未确认操作。
- `audit.json`：全部受控准入、真实 HTTP manifest、模型与工具操作。
- `state.json` 与各 `*-context.json`：逐步检查、输出及冻结上下文；历史检查失败保留，不能把整份 state 的历史检查都解释为最终成功。
- `retrieval-tools.json`：实际 memory ID、skill 正文及来源工具响应。
- `domain-history.json`：所有领域轨迹、候选、验证拒绝及唯一正式发布记录。
- `initial-main-failure.json`：第一次未完整确认的主助手运行和原始快照。
- `live-context-evidence/20261012/original-402-report.md`：最初余额阻断记录。

复现入口 `scripts/verify_live_context.py`，独立数据库参数必须包含 `/ce2_live_`；凭据只从本机环境或 `.env` 读取。代码改动位于 `collaboration/tools.py`、`controlled/broker.py`、`learning/models.py`、`learning/service.py`、`memories/service.py`，针对性验证位于 `test_controlled.py`、`test_controlled_external.py` 和 `test_context_layer_4.py`。
