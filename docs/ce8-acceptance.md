# CE8 质量评测与路径收敛

日期：2026-10-08。状态：**当前固定范围验收通过**。CE1–CE7 当前范围已经验收。本阶段不扩大隔离、自动审核或任意恢复保证。

## 设计与实现

| 要求 | 当前实现与证据 |
|---|---|
| 运行前固定问题、原文版本、模型、限额、规则 | [预登记](ce8-preregistration.md)、evals/ce8/fixtures.json；每个实际运行保存夹具、登记、执行器与全部源码 SHA |
| 完整输入 / 仅投影 / 投影加摘要 | evals/ce8/run.py 的评测专用替换；产品无策略开关、无旧后端；相同 ControlledEngine 与窗口预检 |
| 中文同义、长历史中段、纠错、旧问题 | equipment 的青鹭/北斗、7 台与收入、v1/v2、历史完成事项与当前核对 |
| 跨报告、不可比年份、算术与业务区别 | cashflow 的 100−20=80、120−30=90、12.5%，分别保留年份/币种/主体、拒绝可比集团增长 |
| 订单与实际交付、缺失不等于不存在 | orders 的 105/90/80/10、白鹭验收门槛 85、收入与市场分母未知、预测不冒充实绩 |
| 同一账本、权限、恢复与代码冲突 | 重新运行四入口、撤销、Git worktree/集成重测、真实 PG 与 SIGKILL 契约；真实模型恢复另记 |
| 删除旧生产旁路 | 移除 agent-core；records/dispatch/resources 只保留历史查询及资料操作；全部现行研究、任务、方法、演化、评测 CLI 共享 PG 受控服务配置 |
| 删除全历史拼接、脚本回退 | 安装包去掉 ConversationService.send/ExecutionService.send 与演示构造；旧记录种子放 tests/legacy_*；生产缺 engine 失败，不生成脚本报告 |
| 历史保留 | 28 个旧执行脚本保存为只读 .txt；[归档清单](ce8-evidence/historical-entrypoints/removed-executables.json)含原路径与 hash；不删除数据库、旧运行或资料，不迁移旧协议 |
| 沿用成熟组件 | 原 SDK 0.22.2、DBOS 3.0.0、SQLAlchemy、PostgreSQL、Git、React/Playwright；使用现有公开 ModelProvider 懒加载客户端，只读命令无需 API key；无新增依赖 |

## 质量判定

固定三题，每题四项证据、四项约束；以完整报告、真实工具响应、实际私有笔记和固定源逐项核对。程序只汇总成本与调用状态，人工语义评审单独保存。模型自检、pending_review、关键词命中均不等于质量通过。

首轮未触发投影，不能作为有效比较；后续实测缺口与修复见[失败记录](ce8-evidence/failures.md)。每轮全部输出保留，不选取各轮最佳格子拼成通过结果。完整输入仍经过相同保守 UTF-8 窗口估计；预检失败是本地策略限制，不谎称供应商返回窗口错误。

第七轮完成三题×三臂的固定对照，全部原始结果保留。产品默认 compaction 三题均交付：**必需证据 12/12、关键约束 12/12、私有笔记 3/3**；均实际读取固定资料、保持 pending_review，未请求其他固定源。详见 [逐项评审与全部成本](ce8-evidence/round7-review.md)、[机器可读明细](ce8-evidence/round7-metrics.json)。

| 策略 | 正文交付 | 满足全部固定任务 | 主要限制 |
|---|---:|---:|---|
| full | 0/3 | 0/3 | 三题后续请求均被相同窗口预检拒绝 |
| projection | 2/3 | 0/3 | 设备分页参数越界；财务遗漏非净利润边界；订单门槛口径丢失且笔记未保存 |
| compaction | 3/3 | 3/3 | 摘要成本纳入总量；设备4次摘要中2次校验失败，保留旧head后完成 |

三个 compaction 的 input/output token 分别为设备 61599/12424、财务 32837/6245、订单 33305/6683；模型/工具为 8/7、5/3、5/3；延迟 72.607、37.324、39.438 秒，usage.complete 均 true。缓存计在 input 内。第七轮所有模型请求均确认，但四个失败运行的结果 usage.complete 保守为 false；第六轮两个取消运行各有一次未确认模型调用，费用未知。不能用未交付基线的部分用量声称完整任务更省。

私有草稿仍存在来源转录风险：订单草稿写错/猜测了一处销售资料 UUID，而实际工具结构化 ref 与正式报告 sources 的完整 ref 正确，未错用版本。错误保留在原始证据，不人工修饰输出；私有笔记不能替代正式结构化证据。财务报告的额外会计准则概括不属于固定夹具的独立验证范围。上述局限不抹去，但也不等同于越权读源或错用固定版本。

直接执行入口另修复了外层 DispatchPolicy 默认为120秒、提前取消冻结360秒任务的问题；外层现沿用冻结任务时限，未提高原任务预算。30/360参数修复前均失败、修复后通过。最终广泛回归在该修复后重跑。

最终代码完整回归：**608 passed / 1 skipped，196.26 秒**，六个新建测试数据库（20261008_103617）。时限修复前为604 passed / 1 skipped，失败与修复记录均保留。唯一跳过为 tests/test_evolution.py:172 的 memory 参数下 PostgreSQL-only 检查；对应 PostgreSQL 参数已运行，不是缺少依赖。前次原始输出见 [regression-before-deadline-fix.txt](ce8-evidence/regression-before-deadline-fix.txt)，最新结果见 [regression.txt](ce8-evidence/regression.txt)。

## 最终代码的控制契约覆盖

| 领域 | 证据来源与实际检查 |
|---|---|
| 授权与世代 | test_controlled_collaboration：撤销等待中的模型、旧世代隔离、跨专家私有笔记拒绝、固定报告授权；test_controlled_external：真实 PG fencing、伪造/重放/晚到拒绝 |
| 代码交付 | test_coding / test_coding_controlled：明确代码合约、独立 worktree、路径拒绝、可信测试、冻结差异、集成冲突与重测、保存重试 |
| 输入与摘要 | test_context_projection / test_compaction / test_compaction_controlled：用户原文、工具配对、分页授权、引用校验、失败保留、原预算、可信摘要窗口压力保护 |
| 持久化与故障 | test_checkpoints / test_checkpoint_sdk / test_checkpoint_process：公开 RunState、原子回执、真实 SIGKILL、已确认调用重放、未知模型与工具的不同处置 |
| 产品路径 | test_product_cli / test_architecture：移除旧命令与执行拼接、PG 新进程读取、无模型凭据的只读查询、模块依赖边界 |

上述测试提供确定性控制契约；模型输出质量另由固定真实样本逐项评审，不相互替代。

## 真实外部验收与历史阻塞

- 首次四入口新库样本：23 次 Responses HTTP 200，随后5次 HTTP 402。主助手初始派发、两个专家、两次咨询已完成；两份专家报告保留 marker/7/市场未知/年份未提供，实际写读笔记，回执待审未采用，保存重试零模型调用。恢复主助手和普通讨论未完成，该次整体 **失败**，不计为通过样本。[原始记录](ce8-evidence/entrances-attempt1.json)
- 首次样本第一条咨询首句把“未提供目标年份”写成“本任务不存在应适用年份”，后文和专家报告均回到“未提供/未知”。这类绝对化措辞保留在历史失败样本；恢复后新样本明确限定“本次资料与主助手未提供”，未把首次样本标为语义通过。
- 额度恢复后新库四入口完整重验：**35次 Responses HTTP200**，10项控制检查通过；主助手派发、双专家读源/写读笔记、两次咨询、主助手续接汇总及普通讨论全部完成。完整人工复核保留marker、7、市场未知、目标年份未提供，结果待审未采用，保存重试零模型调用。[原始记录](ce8-evidence/entrances-restored.json)、[语义复核](ce8-evidence/entrances-restored-semantic-review.json)。主助手坦陈只读到一份专家报告的预览，未谎称完整审阅；固定原文已足以核对本样本事实，这不构成完整报告审计或复杂分歧裁定的通过证明。
- Chromium 浏览器4项通过、零页面错误：新进程打开持久项目、专家对话、包含准确 marker 的完整报告、待审结果无正式采用入口。恢复后新样本已再次通过相同浏览器检查。正式 CLI 启动，未提供 API 密钥，临时8796服务已关闭。[浏览器记录](ce8-evidence/browser.json)、[截图](ce8-evidence/browser-saved-report.png)
- 额度恢复后，当前代码真实 Responses + SIGKILL + DBOS 验收通过：在HTTP响应之后、回执保存前强杀（exit=-9），新进程恢复同一run，3次HTTP200、1次笔记副作用；报告保留v1/7台/收入未知/未审未采用。原请求未确认，usage.complete=false，已知input=6862/output=1005，未知费用未当零；仍用原8次模型预算。[恢复检查](ce8-evidence/live-crash/acceptance.json)、[语义复核](ce8-evidence/live-crash/semantic-review.json)。
- 新建测试数据库、各轮失败样本均保留；第七轮九个库、首次四入口、恢复后四入口及强杀恢复共12个 pg_dump、源码及本轮证据/私有记录归档及 SHA 见 [备份清单](ce8-evidence/backup-manifest.json)。未修改历史服务或原有数据库、未提交推送。

额度恢复后缺失的四入口与真实模型强杀验收已补齐。生产源码和依赖哈希与第七轮评测、608项通过的完整回归一致，没有为了本次外部重验修改生产代码；无需重复相同回归。CE5→CE6→CE7→CE8 当前固定范围已按序完成，原HTTP402、各轮失败及非阻断局限仍保留。

## 保留边界

- 合成固定材料上的小样本，不能证明开放领域普遍质量，不能宣布历史 C4/C5 所有开放问题关闭。
- 投影和摘要依然可能有来源简写、措辞或摘要生成失败；失败摘要不发布新 head，原文保留。交付必须独立复核，不自动采用。
- 不新增向量检索：本轮发现的是输入选取、回读规划和缺失信息推断问题，没有据此证明现有词法检索必须替换。
- 不提供 Docker/OS 沙箱、跨主机接管、硬配额、供应商调用恰好一次或未知工具副作用盲重试。
- 用户数据与资料原地保留；旧执行说明仅作历史记录，现行命令见[README](../README.md)。
