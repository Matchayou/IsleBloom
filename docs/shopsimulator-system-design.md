# ShopSimulator 选品 Agent：系统改造与实验设计

日期：2026-10-18。状态：设计，未实施、未跑分。固定模型，不进行 SFT、GRPO 或其他参数训练。

## 1. 目标与边界

在现有 Research Island 上增加消费者购物应用，以 ShopSimulator 为主环境。研究问题：相同模型与预算条件下，结构化任务状态、证据检索、适用记忆和受限专家协作能否提高购物成功率、降低错误规格选择与重复操作？

当前底座继续承担受控执行、权限、快照、账本、checkpoint、任务与专家身份。新增购物领域模块，不把它强塞进现有研究报告模型，不引入第二套 Agent 框架。

参考 shopping-grpo-longhorizon 的动作合同、当前观察守卫、轨迹规范化、评分隔离和固定分母；不复制其训练、Reward v3 或修改后的环境。论文原版协议、应用系统协议、跨任务记忆扩展分别报告。

### 已核验的接口事实

ShopSimulator multi_eval 通过环境 HTTP 接口 reset/interact/release，并将与环境操作和询问 shopper 区分。环境页面包含当前搜索可用性及可点击项；persona 模式可提供用户文档。多轮 reset 还涉及仅供 shopper 使用的完整目标字段，因此 HTTP 原始返回值不能直接送给助手。

参考 harness 将 function call 映射为环境动作，并根据最新观察检查合法性。其工具还包含修改环境支持的不购买终止路径；官方环境未核验同等支持前，不将其搬入主评测。

这些是静态代码核验，最终实现仍须固定 commit、运行契约测试并核实任务文件、依赖与实际响应。

## 2. 总体结构

```text
用户 / 官方 shopper simulator
             |
购物负责人：维护需求、选择下一步、唯一外部执行权限
             |
      受控 Broker + EpisodeCoordinator
          /                    \
    固定证据快照             唯一环境会话
      /      \                  |
调查顾问   约束复核员       ShopSimulator API
      \      /
   有证据引用的结构化建议

独立离线 scorer：官方指标 + 补充约束/轨迹/成本分析
开发轨迹 → 审查 → 方法/检索版本候选 → 验证 → 发布
```

关键原则：一次 episode 一个页面状态写入者。专家只分析已合法读取的冻结证据，不能独立搜索/点击相同 env_idx。环境导航、询问 shopper、最终购买均由负责人执行；不把 shopper、核验 Agent、离线 judge 混为同一角色。

## 3. 最小领域合同

所有模型输出用现有 Pydantic Value 定义、禁止额外字段；以下为拟新增模型，不是当前 API。

| 模型 | 必需信息 | 更新规则 |
| --- | --- | --- |
| ShoppingEpisode | episode_id、trial_id、protocol、task_key、env handle、状态、revision、预算账本 | 环境 handle 服务端保管，外部动作串行 |
| Requirement | field、operator、value、hard/soft/unspecified、subject、source_event、quote、status | 当前用户修订生成新需求版本；不静默删减 |
| ShoppingState | 需求版本、未解问题、已问项、候选引用、排除原因、当前选择、重复动作摘要 | 以事件增量更新，不靠滚动摘要作为唯一真相 |
| Observation | event_id、action_id、page_revision、actor 可见正文、当前按钮与搜索状态、hash | 原文不可变；新观察创建新版本 |
| CandidateEvidence | product_id、已见属性、选项轴、已见价格及对应组合、支持/反证 event_id | 每字段 true/false/unknown，并保留矛盾 |
| Decision | 需求版本、候选、当前规格、逐项满足/违反/未知、待补证项 | 不能把未知自动视作满足 |
| ShoppingAdvice | snapshot_id、目标缺口、建议问题/查询/检查、证据引用、局限 | 专家只能提建议，不能提交外部动作 |

产品基础价、当前选项价格与完整变体价格明确区分。页面无完整选项信息时不能伪造 variant_id；用已观测的选项组合及其来源表示。

需求抽取可以用固定 LLM，但必须引用已合法收到的用户/ persona 原文。代码校验引用存在、字段类型正确。hard/soft 不由相似商品或 gold 决定；有歧义先标 unspecified 并询问。某类目常用检查项只是补问提示，不自动成为用户硬要求。

## 4. Harness 如何接入现有系统

### 4.1 环境边界

拟新增 ShopSimulatorClient 管理 reset/interact/release。使用已安装 httpx，不新增 HTTP 框架。将模型选择的动作结构映射为官方支持的动作语法，映射只改变表示，不增加动作能力。

模型可见工具首版只保留 `shop_search(query)`、`shop_click(value)`、`ask_shopper(message)`，加只读 `read_shopping_evidence`。保留当前可点击值，而不是凭空开放全商品 ID 查询。后续细分 select_option 等工具时需保证动作等价，并作为 schema 变量单独验证。

控制器只向模型开放 protocol 允许的 instruction、persona、观察和对话。完整目标、goal_options、gold、reward、评分 rubric、未公开商品字段进入 evaluator 存储，绝不进入资源目录、工具错误、摘要、专家交接或记忆提炼。

### 4.2 动作守卫

每次提交外部动作前比较 expected_page_revision；检查 search_available、当前按钮、episode 未结束及权限。未知值返回带原因的非法动作结果，不执行“最接近按钮”修复。记录尝试、拒绝和实际执行，重试耗费调用/时间预算。

所有系统实验臂使用相同基础合法性守卫。购买前的需求/证据检查是显式系统变量：先增加确定性约束表，再实验是否需要 LLM 复核。不能靠隐藏 gold 阻止错误购买。软偏好不构成硬拦截。

用户确认要求取决于所固定的协议；应用模式购买需有效的用户确认，官方复现不能偷偷新增或删除协议行为。负责人收到用户拒绝或新要求后，旧确认与旧候选结论失效。

### 4.3 观察与状态更新

保存完整、允许 Actor 读取的原始观察，再生成有引用的目录/摘要。当前页面操作字段、当前选项与价格、用户否定及纠错受保护。大正文通过现有 artifacts 分页；如果增加读取分页的内部调用，单列并计费，不伪装为额外免费环境证据。

每次环境动作或新用户消息完成后，提交一次 ShoppingState 新版本，再为下一轮冻结 ContextSnapshot。既有 snapshot 的版本失效检查保留；不能在同一个冻结目录里无限塞入新观察。专家回复携带 state_revision，需求或证据变化后要重验适用性。

### 4.4 恢复限制

稳定 action_id、请求 hash、响应 hash 和状态均进入受控账本。checkpoint 能避免重复已确认动作，但原始模拟器不能假定支持幂等动作或任意环境快照恢复。

发生动作发送成功但回执未知、服务重启导致页面状态丢失时，不继续点击旧页面。标记 infrastructure_unknown；若试验协议允许重跑，则全 episode 新 attempt 重置，保存旧 attempt 与额外成本，不拼成一次成功轨迹。购买超时不盲目重试。

结束时跟踪本 episode 的 session lease；服务端可能回收 env_idx，不能按已回收的旧数字继续 release，更不能调用 release_all 影响其他任务。首版不提供跨进程恢复环境会话的虚假保证。

## 5. 记忆：四类信息，各自不同生命周期

### 5.1 单任务工作状态

ShoppingState 保存当前需求、已问项、候选与失败搜索，是任务真相。现有 compaction 只负责压缩旧正文，不能取代这份状态；关键用户原话、否定、当前动作界面必须保留。

例：用户把“预算 500”改为“最多 800”，旧约束标 superseded；相关候选排除原因重新校验。某次失败搜索只属于当前 episode，不写成永久经验。

### 5.2 用户长期偏好

复用 MemoryEntry、MemoryWrite 的来源、版本、确认和撤销。新增购物适用关系 ShoppingMemoryBinding：memory_id、category、subject、condition、source_kind。不拓宽全局 user/project/specialist namespace，不把 task_id 当真实用户。

明确长期偏好才可 confirmed；临时预算和“给妈妈买”进任务状态。推断保持 draft，不参加正式推荐；显式纠正沿用版本检查。真实用户和模拟用户来源严格区分，模拟消息不冒充真人同意，也不能流入生产记忆。

已有 MemoryService 的独立轻量 LLM 选择可先复用：先按用户、category、subject、condition 预筛，再语义选择。显式适用的禁忌与当前用户纠错作为必需项保留，不能只因为 top-3 名额被丢掉；超预算显式报错，不静默丢规则。

若跨任务实验要允许“由用户历史表达推断偏好”，必须建立独立 shopping_profile 候选层和公开的推断使用策略，不降低现有用户确认合同。主实验先使用明确证据，推断策略单独成臂；persona 提供的信息标明 provided_profile，不伪造为系统学到的记忆。

### 5.3 商品证据缓存

只索引 Agent 已看到的原始观察，命名为 episode evidence。详情、评论和规格分开，正文带 event_id/位置；候选的排除原因能回指需求版本。

官方评测 episode 间清空，防止前一题提前泄露商品信息。应用模式可缓存有来源的稳定规格，报价和当前按钮始终重新读取；缓存事实没有当前页面的点击权限。

### 5.4 可复用流程经验

复用 learning/ 的 Trajectory → DomainCandidate → DomainValidation → DomainPublication，但发布门槛补充行为回归。候选独立审查通过只表示有依据，不能证明改善任务成功率。

仅提炼可迁移规则，如“完整规格选择后再次核对价格”；不存 benchmark task_id、目标商品、具体答案、可识别用户事实。经验/skill 版本在每个评测批次前冻结，正式评测禁止后台自动发布新经验。

## 6. 检索：三个入口，不越过环境边界

| 入口 | 检索对象 | 首版实现 |
| --- | --- | --- |
| 用户记忆 | 获准且适用于当前购买对象的偏好 | 结构过滤 + 现有 LLM side-query |
| 商品发现 | 官方环境当前允许的 search | 需求驱动 query 规划，仍调用原搜索入口 |
| 证据/经验 | 本 episode 已见页面、已发布通用流程 | 当前 FTS5 BM25 + 引用读取，结构字段精确优先 |

### 6.1 查询规划

QueryPlan 包含目标缺口、必要词项、软偏好词项、已尝试 query 摘要、下一动作理由。先品类和强区分属性；按观察反馈改写，不机械拼接整段历史。无结果时允许放宽软词，不能偷偷放宽硬约束；缺关键条件向 shopper 问。

不能照隐藏目标生成完美 query。相同 query、相同页面与需求版本下，无新增信息的重试应提示重复；这是应用策略变量，不借此修改环境 reward。

### 6.2 候选比较与证据选择

先检查已知硬冲突，再比较未核验项；未知候选保留待证，不当成不满足直接过滤。排序用可解释的分层比较：硬冲突→关键未知数→软偏好匹配→需要的补证动作；不预设任意多指标权重。

每条 claim 指向观察中的真实 span，核验字段值、否定和作用对象。标题不能替代当前规格选择，评论不能替代可查规格。无法可靠结构化时保留原文并标 unknown。

### 6.3 何时加向量

先用当前依赖和 BM25 完成端到端。有开发集证据显示同义表述导致证据遗漏，再加入固定 embedding 的混合召回与 RRF；冻结 embedding、索引和融合参数，不训练 reranker。仍只检索可见证据。

对全商品库建索引属于独立检索增强环境扩展，会给出额外观察与随机访问能力，不能混入官方条件的主成绩。主评测的证据重排只改变已获得信息的使用方式。

## 7. 多 Agent：主从分工，单写入者

### 7.1 三种职责

| 角色 | 输入 | 可调用能力 | 输出 |
| --- | --- | --- | --- |
| 负责人 | 当前用户原话、需求状态、适用记忆、当前页面 | 环境操作、shopper 对话、证据读取、派发 | 下一步动作或最终候选 |
| 调查顾问 | 当前版本需求、候选与已见证据、具体缺口 | 只读已授权证据 | 候选差异、建议 query、建议补问/补证 |
| 复核员 | 固定候选与规格、当前价格证据、逐项需求 | 只读当前决策证据 | satisfied/violated/unknown 与引用 |

负责人与专家使用同一个固定模型版本和推理设置，第一轮实验不加入大小模型路由。Role instructions 不代表授权，工具权限在 Broker 处限制。

### 7.2 调用条件

首版采用明确控制流：所有外部操作前做代码合法性检查；负责人可在已见候选难以区分时调用一次调查顾问；购买前按实验臂调用一次复核员。新需求版本或新候选产生后才允许重新复核，首版每 episode 最多两次顾问调用和两次复核，均从全局预算扣除。具体阈值只在开发集选择并冻结。

不自动复核每个调查子任务，也不将现有报告评审循环原样叠加到每次搜索。购物决策有专用复核产物；独立离线评分不进入在线修订。

### 7.3 状态机

`need_clarification → discover → inspect → compare → review → confirm_if_required → purchase → terminal`

状态可以退回；用户修订需求时从当前证据重算待办。复核发现缺证据返回 inspect，不强制换一个候选。预算用尽按原协议结束并记失败/未完成；不增加“强制随便买一件”的业务兜底。

顾问建议在需求版本或候选证据版本变化后失效；复核 verdict 绑定候选/选项组合/需求/证据 hash。旧建议不会因为来自“专家”而自动取得权威。

### 7.4 并行与预算

两个只读专家可分析同一冻结快照，负责人等待建议期间不修改它。环境读写仍串行；不克隆环境给专家免费搜索。预算同时限制环境动作、询问次数、全部模型调用/token 和墙钟时间。官方动作计数按 pin 后协议精确实现，不能把专家调用重置为一套新预算。

## 8. 无训练的数据飞轮

### 8.1 三类反馈去向

| 反馈 | 保存位置 | 可改变什么 |
| --- | --- | --- |
| 用户明确偏好或纠正 | 个人记忆/当前需求 | 下次适用信息和过滤条件 |
| 商品事实纠正 | 有来源的证据记录 | 当前候选判断，应用模式下缓存失效 |
| 流程失败 | 开发案例与经验候选 | 查询策略、选择与分页、交接、复核触发 |

点击/购买只作为事件，不直接标“满意”。模拟器奖励是合成任务结果，不是真实用户满意度。无使用反馈时保持未知。

### 8.2 每轮改进过程

1. 从获准开发/产品轨迹收集动作、观察、状态差异、检索命中、交接、反馈与成本。
2. 按 need_missing、query_miss、candidate_miss、evidence_miss、variant_error、memory_overuse、memory_ignored、stale_advice、loop、infra 分类；标注由人工抽检，模型只能提案。
3. 选择一个主要失败簇，形成一个具体版本候选，例如“规格选择后刷新价格检查”，不一次改全部组件。
4. 在已知开发案例回归，再在独立验证集评估；固定未打开的测试集只用于最终版本验收。
5. 通过后发布策略/skill/检索配置版本，保留来源、效果、成本和撤销入口。

线上立即学习仅限有明确来源的用户记忆更新；共享流程、查询模板、同义词与检索参数按批次审查发布。更换 skill、索引或 prompt 都算系统版本变化，不能在正式 batch 中隐式漂移。

### 8.3 明确改善例子

观察到多例“列表价满足预算，选大容量后超预算”→失败簇 variant_error→增加真实规格价回读与硬预算检查→相同模型重跑验证→看该错误下降且总体成功/成本未恶化→发布。全过程没有更新模型参数，也没有把题目答案存成经验。

### 8.4 数据隔离

训练不是唯一泄漏渠道：prompt、经验库、同义词表、缓存也能记住测试答案。dev 的复盘可进入案例和规则；holdout 的内容禁止进入诊断和候选生成。若打开某题详细轨迹用于改进，该题转 dev，另建未暴露 holdout，不再声称它仍是盲测。

按目标商品/模板近重复分组和时间切分；仅 task_id 不重叠不够。真实用户偏好不跨用户共享；一般化流程删除个人标识和具体商品答案。测试集完整目标只供离线 scorer。

## 9. 对照实验：先回答可归因的问题

### 9.1 三种协议

| 协议 | 数据与环境 | 能支持的结论 |
| --- | --- | --- |
| P0 官方复现 | 固定官方 commit、任务、四种设置、prompt/解析与评分 | 复现参照；与论文仍需核对模型与配置 |
| P1 系统实验 | 相同官方环境/任务/动作能力/评分，统一工具 schema 与合法性守卫 | 在该统一 harness 下系统组件的配对增益；不自动视为论文榜单直比 |
| P2 连续用户扩展 | 独立构造的历史→购买任务→反馈→后续任务 | 长期个性化与飞轮效果；明确标自建扩展 |

P0 不用于证明某个单组件有效。P1 不增设后台全库索引，不引入原版没有的 finish 动作。P2 用户与来源由测试作者建立，不能把互不相关的官方任务拼成“同一真实用户”。

### 9.2 P1 组件对照

共同固定：相同模型版本、推理设置、Agent SDK、动作合同、观察内容、基础守卫、任务、模拟器设置与预算上限。H0 已使用现有受控底座，但关闭自动经验发布和无关业务工具。

| 实验臂 | 工作状态与记忆 | 检索 | 编排 |
| --- | --- | --- | --- |
| H0 | 当前原始对话，无购物结构状态 | 无新增证据检索 | 单 Agent |
| H1 | +结构化需求与候选状态 | 同 H0 | 单 Agent |
| H2 | 同 H1 | +查询规划、已见证据 BM25/结构检索 | 单 Agent |
| H3 | 同 H2 | 同 H2 | +负责人自检，给出相当的复核预算 |
| H4 | 同 H2 | 同 H2 | +独立复核员，替代自检 |
| H5 | 同 H2 | 同 H2 | +条件调查顾问与复核员 |

H1−H0 测显式状态；H2−H1 测检索；H4−H3 测分离身份与上下文是否超过同等计算的自检；H5−H4 测调查顾问是否必要。若新增结构状态需要 LLM 抽取，其调用也计入该臂，不可免费预处理。

进一步对最终系统做 remove-one 消融；怀疑状态与检索存在交互时再做 2×2，不先跑巨大组合。向量检索仅在 BM25 缺口明确后单独加一臂。

### 9.3 记忆实验

官方 persona 设置内只比较提供信息的选择与应用，所有臂获得完全相同 persona；不能只给实验组额外画像。普通设置无 persona 时不凭空构造。

P2 固定一组用户历史与序列，比较 M0 无历史、M1 完整历史、M2 结构偏好+适用检索、M3 M2+纠正/撤销。除无历史诊断臂外，其余臂获得相同合法历史，区别只在管理方式。M0 与 M2 的差值包含历史可用性收益，M2 与 M1 才更接近记忆管理收益。

任务包括预算变化、偏好纠正、给他人购买、临时例外、遗忘、无关历史与类目切换。跨任务只保留个人历史；不同用户彻底隔离。可以后续使用 VitaBench 2.0 做独立外部验证，但不替代 ShopSimulator 主线。

### 9.4 飞轮版本实验

冻结系统 v1；只用截止时间 T 前的开发反馈构建 v2；v1/v2 在 T 后的独立验证/最终留出任务上配对运行。报告更新了哪些规则和失败簇，不把“增加记忆条数”当作收益。

再用同一 v1/v2 对新用户、未见目标商品和未见模板分别报告。冻结测试期间不自动写共享经验；用户序列任务中允许的个人记忆更新按协议一致进行。

## 10. 样本、预算和统计

先 8–12 个 dev episode 验证协议和账本；再约 40 个分层 dev 任务定位问题。正式任务数量在核验官方 splits 和可用预算后决定，建议从约 200 个未暴露任务的配对试验起步，并明确这是子集。最终有条件运行完整官方测试集，不把自选子集当官方全量成绩。

单轮/多轮、普通/persona 分层汇报。每个正式任务建议至少 3 次独立重复；多轮模型模拟器固定版本与采样设置，支持 seed 则记录，但不声称 LLM 调用完全确定。不同系统询问路径不同，不能强制重放另一臂的 shopper 回复。

实验臂按任务配对，随机交错运行；相同最大环境步数和总预算只是上限公平，实际 token 消耗仍可能不同。除共同上限结果外，再报告成功率—实际成本曲线，检查多 Agent 是否只是多花计算。全局计入抽取、检索 side-query、摘要、自检、专家与失败重试；离线 judge 成本另列。预算在 dev 按当前固定模型的实际能力选择，遵守官方环境动作限制后冻结。

官方动作如何计数以固定代码为准；同时单列 env actions、shopper asks、internal tools、model calls、guard rejects，避免“40步”掩盖上百次内部调用。

主指标：官方 full success / strict / loose（以所 pin 官方实现为准），分别报告，不用自定义总分替换。辅助指标：明确硬约束违反、变体错误、重复提问/搜索、无依据结论、复核有效纠错和误阻断、实际 token/费用、端到端延迟及预算失败。

置信区间按 task 聚类 bootstrap；连续任务按 user 聚类，不把多次重复或同一用户子任务当独立样本。报告配对成功/失败迁移；二分类配对检验与多重比较方案在正式跑前确定。没有足够样本和区间支持时只报告观察差值。

所有计划 trial 保留，基础设施失败比例单列；端到端成功率用全分母，条件有效率作为辅助。允许重跑的基础设施错误先定义，按规则重跑完整配对/重复，保留旧产物与成本，不挑成功 attempt。judge 未完成是 unknown，不填 0 或伪造通过。

## 11. 评分与审计

分四份产物：官方结果、补充约束判断、行为诊断、资源成本。参考 harness 的分栏和证据 ID 方式，不复制其自定义 reward 或所有参数。

官方 scorer 只能离线运行，可访问 gold；补充 rubric 由测试作者/离线 scorer 冻结，当前 Actor 不能读取。在线复核员只能看到当时用户与观察。补充 judge 看匿名 arm 的 actor-visible trajectory 与固定评分规则，标注支持 event_id；不看运行 reward 来倒推过程质量。用于错误诊断的 hidden-truth scorer 与 blind trajectory judge 区分存储。

评分器需做反例校准：正确商品错误规格、标题匹配但功能无证据、价格过期、伪造引用、拒绝后仍购买、合法但无效循环。抽检 30–50 条分层轨迹，关注分歧；冻结后才正式比较。

manifest 保存实际任务列表与 hash；N 必须等于计划 task×repeat×arm，聚合器对缺失/重复/多余 trial 直接报错。参考项目评估文档存在“200”和另一处“183”分母文字不一致，因此采用这种自动约束，不抄写固定数字。

## 12. 对现有代码的具体修改

以下目录是拟新增设计；当前文件复用程度由实现时契约测试确认。

```text
src/agent_core/shopping/
  models.py          # Episode、State、Requirement、Evidence、Advice
  service.py         # episode 生命周期、唯一执行者、状态版本与全局预算
  tools.py           # 模型可见工具与角色权限
  retrieval.py       # query plan、已见证据检索、适用记忆过滤
  memory.py          # shopping binding、来源类型、序列实验个人状态
  orchestration.py   # 调用条件、顾问/复核交接与版本有效性
src/agent_core/adapters/
  shopsimulator.py   # HTTP、官方观察白名单、动作映射
src/agent_core/adapters/postgres/
  shopping.py        # episode/state/binding，证据正文复用资源存储
evals/shopping/
  protocol.py        # P0/P1/P2 数据边界和协议 manifest
  run.py             # 配对执行、隔离 DB/private、trial 身份
  grade.py           # 官方评分适配、补充评审与审计
  report.py          # 分层结果、配对差值与成本
```

| 现有位置 | 复用与必要调整 |
| --- | --- |
| contracts.py、controlled/ | 保留 AgentSpec/ToolBinding/Broker/checkpoint；增加 root episode 全局预算预留/扣减，防并行超额 |
| context/assembler.py、builder.py | 通过已有 business 参数注入经验证的 shopping state；按下一轮重新冻结目录 |
| context/selection.py | 显式传入购物 retrieval goal，不依赖研究主助手默认空查询；在购物 selector 处理证据，不全局替换研究路径 |
| context/projection.py、compaction.py | 当前按钮/约束/规格价受保护，旧观察按引用分页；不把模拟观察误当用户原话 |
| memories/ | 保留确认与版本合同；适用关系存独立绑定，模拟与真人写入走不同来源，禁止假冒 user_statement |
| collaboration/ | 复用身份、ContextPacket、任务与只读讨论入口；购物 Advice 专用，不叠加报告自动评审 |
| learning/ | 复用候选和发布 seal；加入行为验证引用，正式测试禁自动发布 |
| methods/models.py | 现有 kind/tools 枚举没有 shopping；实施时增加显式 shopping 方法和工具合同，不把购物冒充 synthesize |
| evaluation/ | 当前 Experiment 限定 before/after 等 arms，不能直接承载 H0–H5；购物多臂 manifest 放 evals/shopping，复用冻结/成本原则而非复制整套领域存储 |
| evals/research_quality/ | 复用 plan/run/report 模式及隔离实践，不把现有八道研究题当购物质量证据 |
| app/、frontend/ | 增加购物入口与比较产物，沿用 Flow 和运行分析；真实购买工具不开放 |

不将新工具默认开放给研究助手；现有研究业务保持独立。替换同一职责的旧路径时直接移除，避免新旧两套行为。

## 13. 分层交付与验收

1. **P0/P1 接通**：单 Agent、官方模拟器、动作合同、过滤敏感字段、官方评分、完整轨迹；一个任务端到端可运行。
2. **状态与检索**：需求版本、候选证据、query plan、BM25，完成 H0/H1/H2 的 dev 对照。
3. **受限专家**：先自检对独立复核，再加调查顾问；只有收益足够时保留复杂度。
4. **长期记忆与飞轮**：单独建立 P2，支持来源/纠正/撤销；跑 M0–M3 和 v1/v2 留出对照。
5. **产品展示与正式报告**：选择三个典型成功与失败样例，展示需求变化、证据、交接和成本；冻结系统后正式评测。

实现验证优先覆盖实际风险：hidden 字段不泄漏、陈旧按钮拒绝、专家无环境权限、原始反馈来源不冒充、全局预算并发扣减、未知动作不重发、测试数据不进入经验、分母完整性。协议测试通过与真实模型购物成功必须分别报告。

## 来源与核验范围

本设计基于本地相关模块静态阅读，以及下列官方/原仓库源码与文档；没有部署 ShopSimulator，也没有调用付费模型。

- ShopSimulator 官方多轮执行：https://github.com/ShopAgent-Team/ShopSimulator/blob/main/multi_eval/agent.py
- 环境客户端：https://github.com/ShopAgent-Team/ShopSimulator/blob/main/multi_eval/env.py
- 环境 API：https://github.com/ShopAgent-Team/ShopSimulator/blob/main/shop_env/shop_env/pack_api.py
- 动作守卫参考：https://github.com/YYHDBL/shopping-grpo-longhorizon/blob/main/src/shopping_grpo/environment/actions.py
- 动作合同参考：https://github.com/YYHDBL/shopping-grpo-longhorizon/blob/main/src/shopping_grpo/environment/tools.py
- 评估审计参考：https://github.com/YYHDBL/shopping-grpo-longhorizon/blob/main/docs/evaluation.md
