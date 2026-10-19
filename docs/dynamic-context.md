# 动态上下文组装

本实现采用 [Agent Skills 的渐进披露](https://agentskills.io/specification)、[Anthropic 的按需工具发现](https://www.anthropic.com/engineering/advanced-tool-use)与 [LangChain 的调用前上下文选择](https://docs.langchain.com/oss/python/langchain/context-engineering)。长期记忆通过独立轻量 LLM side query 语义选择；历史、资料、结果、技能和工具仍使用 [SQLite FTS5 BM25](https://www.sqlite.org/fts5.html#the_bm25_function)。不使用向量服务。

## 本轮输入

聊天以最后一条用户原话为检索查询；后台续接也保留该原话，并在业务 goal 中加入本轮事件。专家以冻结任务目标加用户原话检索，独立 user 消息保留交接包中的用户原话。原话没有经过改写。

1. `ContextSelector` 保留当前分支全部用户陈述、最近三条消息和最多六条相关历史。其他回答仍在原始聊天记录中，按需通过 `read_context` 读取。不会从兄弟分支检索历史。
2. 仅检索当前 owner 的已确认且未过期记忆。用户记忆跨项目共享；项目记忆绑定真实 Project ID，允许同项目不同主任务复用；领域记忆绑定固定 specialist ID，仅该专精 Agent 可读。三个范围分别由轻量模型按语义选择最多三条；显式选择另行保留。主助手与临时 Agent 没有领域范围。同步组装先冻结目录，语义选择在受控 Runner 首次发送主模型输入前完成。
3. 主聊天在当前研究获准资料中按名称、元数据与已解析全文检索，初始注入最多六条相关目录。正文由资料工具读取，阅读与引用权限沿用现有 scope。
4. 检索当前研究可访问的专家结果正文，初始注入最多四条目录与短预览，保留交付状态；结论须用 `read_context` 读取完整报告。显式上游结果仍是必需输入，不被排名丢弃。
5. 扫描项目技能元数据，先提供最多三个匹配技能的名称、描述和匹配理由，Agent 判断后调用 `load_skill` 加载正文。候选不会激活工具。`$skill-name` 优先，显式点名的技能全部保留。正文和参考文件均记录哈希；本轮读取文件发生改变会拒绝读取，下一轮重新组装。
6. 第一条 SDK user 消息是结构化业务包，最后一条 user 消息是原话。所有用户陈述均受预算投影保护；技能候选和选择记录也保留，预算不足时明确失败。运行阶段继续使用已有分页、累计摘要和模型窗口检查。

## 技能与工具

默认读取进程工作目录下的 `skills/<name>/SKILL.md`。可用 `AGENT_SKILLS_DIR` 指定一个运营方管理的目录；不会自动导入宿主 Codex 的个人技能。格式使用标准 YAML frontmatter（必需 `name`、`description`），由 PyYAML `safe_load` 解析。

`allowed-tools` 使用本项目工具名称作为选择提示，仅与实际获准工具求交集，不增加授权。支持 `search_skills`、`load_skill`、`read_skill_resource` 分页读取附属文本。不会执行技能脚本，二进制资源不能作为文本读取。目录穿越、目录外符号链接和本轮未登记文件不可读取。

模型首次调用前对获准工具名称与描述进行 BM25 排序，，先加载最多五个匹配工具 schema；技能候选不参与这一步。`load_skill` 成功后才加载该技能提及的获准工具。`search_tools` 在运行中检索并加载更多工具；下一次模型调用只发送已加载的 schema。`select_task_tools` 仍用于显式选择完整工作流工具组，两者都保存在 SDK checkpoint。所有发现与读取操作经过原有工具审计和调用预算。

初始工具检索查询通过 `PreparedInput.tool_query` 传递，不从用户 JSON 文本推断加载状态。技能激活只根据真实 `load_skill` 工具返回值进行，并与实际获准工具目录求交集。旧的 `preferred_tools` 开轮激活路径已删除。

技能是任务流程，当前用户指令、执行权限和交付合同优先。资料与记忆不能通过伪造 skill 字段成为指令。

## 检索边界

历史、资料、结果、技能和工具采用词法检索：英文按词，中文按双字词，查询最多使用前 128 个不同词项；同义词、跨语言表达可能不匹配。长期记忆使用 LLM 语义选择，允许无匹配返回空列表；没有旧 BM25 记忆检索路径。历史用户约束全部保留，长约束可能使本轮超过预算，系统不会静默丢弃。

`ContextSnapshot.selection` 保存算法、查询哈希、选择的消息、显式记忆版本、资料/结果命中与技能哈希，`memory_algorithm` 为 `llm-side-query`。`skill_catalog` 保存本轮可读取文件清单。`memory_refs` 记录显式必需记忆版本；`memory_catalog` 冻结本轮允许检索的已确认记忆 ID/版本，不包含正文。自动选择保存在受控 `memory:initial` checkpoint，包含 ID、版本、理由和原文。`search_memory` 用相同 side query 按显式范围搜索这个目录，不能跨范围、读取本轮新增记忆或写入记忆。执行 Agent 另有 `write_memory`，规则见 [写入说明](agent-memory-writing.md)。它先保存待提交请求，业务提交前不修改当前目录。冻结目录在调用和发布前重新验证：目录内任何记忆被修改、删除、暂停、过期或移出范围，本轮停止，下一轮重新组装。

轻量模型由 `OPENAI_MEMORY_MODEL` 指定，默认 `deepseek-flash`，使用现有 provider 和 Responses 接口。它没有工具、联网或写入权限，只返回结构化的条目 ID、版本和选择理由；程序拒绝越界、错误版本、重复或超过 limit 的选择，并加载数据库原文。候选最多每批 20 条、每次业务输入 24,000 UTF-8 字节，先遍历全部完整正文，再按选择理由合并；过长的单条记忆或查询明确失败，不静默截断或回退。开轮查询包含检索目标、用户原话及最近三条已选历史；运行中查询使用 `search_memory` 的显式 query。

每次 side query 消耗原运行的模型次数、时间和计划 token 预算，预留交付预算，输出上限 2,048 tokens，单次超时最多 30 秒。输入、实际模型名、响应及用量由 Broker 审计；已确认响应和自动选择通过 checkpoint 恢复，未知完成状态停止等待核查，不自动重发收费查询。新的受控 checkpoint 合同为 `ce7-v4-memory-capture-budget`，不接管旧合同的已开始运行。

受控绑定还冻结记忆查询提示、输出 schema 与分批上限的合同哈希，修改这些内容不能继续旧运行。完全相同的查询、上下文和候选可复用同一已确认响应，包括开轮选择与运行中 `search_memory`；不同查询产生新的受控操作。真实模型和 PostgreSQL 验收见 [2026-10-13 记忆检索验收](memory-side-query-evidence-2026-10-13/README.md)。

## 代码入口

- `context/retrieval.py`：中文分词与 SQLite 排序。
- `context/selection.py`：上下文选择、资料和结果权限过滤。
- `context/skills.py`：技能发现、冻结、读取。
- `context/assembler.py`：主助手/临时/专精执行的统一业务组装。
- `context/builder.py`：组合输入与原话预算预留。
- `context/directory_tools.py`：子 Agent 的授权资料目录检索。
- `context/capabilities.py`、`tool_routing.py`：工具渐进加载。
- `context/memory_tools.py`、`memories/service.py`：按范围检索记忆和版本重验。
- `context/memory_retrieval.py`、`controlled/runner.py`、`controlled/broker.py`：分批语义选择、开轮注入、轻量模型预算与恢复。
- `research/service.py`、`tasks/service.py`：聊天和专家接入、记忆版本验证。
- `adapters/openai.py`：每次 SDK 调用的动态工具 schema 与恢复状态。

规划、讨论、咨询的入口模式、检索范围及预算见 [第三层说明](agent-context-layer-3.md)。它们与主助手及执行专家共用组装器，权限按各自只读业务合同收紧。

经过验证并发布的领域 memory/skill 从 [第四层发布目录](agent-context-layer-4.md) 进入同一检索管道；生成中的候选不参与正式检索，撤销和来源变化会触发重验失效。

开轮注入与单次 search_memory 返回使用完整条目预算，显式必需条目不能被静默排除。主助手完成后的后台草稿提炼复用受控审查与恢复，具体规则见 [长期记忆写入](agent-memory-writing.md)。
