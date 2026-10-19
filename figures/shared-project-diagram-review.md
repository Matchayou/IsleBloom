# 多人协作架构图验证记录

日期：2026-10-13。仅验证设计图与文档，不代表产品功能已实现。

验证工具：临时 Mermaid CLI 12.0.0；本机 Chromium 渲染。未改动项目依赖。

## 特点与解法

可见内容：六行从协作问题到具体机制再到现有模块，每行三列，箭头从左指向右。

### 文件检查

- [x] .mmd 仅包含 Mermaid 源码。
- [x] .md 的 Mermaid 内容与 .mmd 完全一致。
- [x] Mermaid CLI 渲染成功，PNG 已用图像工具逐图查看。

### 节点检查

- [x] accessProblem：① 多人共享，权限不同；谁能读、执行、审核、发布？。标签和设计职责一致。
- [x] accessDesign：项目成员角色 + 服务端 actor；个人记忆隔离 + 运行授权持续验证。标签和设计职责一致。
- [x] accessBase：扩展现有 owner / scope；新增真实成员身份与授权。标签和设计职责一致。
- [x] conflictProblem：② 同时修改与发指令；谁的修改生效，会否重复收费？。标签和设计职责一致。
- [x] conflictDesign：版本比较更新 + 命令幂等；一对话一运行，独立分支并行。标签和设计职责一致。
- [x] conflictBase：复用 revision / command_id；补齐触发消息和冲突交互。标签和设计职责一致。
- [x] executionProblem：③ 人离线，任务仍运行；谁能接手、取消，如何恢复？。标签和设计职责一致。
- [x] executionDesign：平台持久化任务 + 负责人；唯一协调器 + 项目与成员额度。标签和设计职责一致。
- [x] executionBase：扩展 DBOS 与受控账本；移除回复依赖 Web 内存的路径。标签和设计职责一致。
- [x] reviewProblem：④ 结果需要多人交接；审核的是哪版，旧批准还有效吗？。标签和设计职责一致。
- [x] reviewDesign：固定产物哈希 + 独立审核人；发布前验证目标与资料版本。标签和设计职责一致。
- [x] reviewBase：复用固定结果、review / adopt；补齐真实身份与发布冲突规则。标签和设计职责一致。
- [x] syncProblem：⑤ 每个人看到不同进度；刷新、断线后是否遗漏更新？。标签和设计职责一致。
- [x] syncDesign：事务事件 + 授权查询 + SSE 游标；断线补读，不广播私有正文。标签和设计职责一致。
- [x] syncBase：扩展 projection 与现有 SSE；新增项目级事件及未读记录。标签和设计职责一致。
- [x] contextProblem：⑥ 共享资料与成果不断变化；谁改了输入，旧任务怎么处理？。标签和设计职责一致。
- [x] contextDesign：项目资料版本 + 冻结输入引用；变更标过期，撤权停止新调用与发布。标签和设计职责一致。
- [x] contextBase：复用 ResourceRef / ContextSnapshot；把项目共享与个人上下文分开。标签和设计职责一致。

### 箭头检查

- [x] accessProblem → accessDesign：方向及目标正确。
- [x] accessDesign → accessBase：方向及目标正确。
- [x] conflictProblem → conflictDesign：方向及目标正确。
- [x] conflictDesign → conflictBase：方向及目标正确。
- [x] executionProblem → executionDesign：方向及目标正确。
- [x] executionDesign → executionBase：方向及目标正确。
- [x] reviewProblem → reviewDesign：方向及目标正确。
- [x] reviewDesign → reviewBase：方向及目标正确。
- [x] syncProblem → syncDesign：方向及目标正确。
- [x] syncDesign → syncBase：方向及目标正确。
- [x] contextProblem → contextDesign：方向及目标正确。
- [x] contextDesign → contextBase：方向及目标正确。

### 完整性与视觉检查

- [x] 涵盖用户、共享项目、权限、并发、后台执行、审核/发布、更新同步及现有模块映射。
- [x] 标签无裁切，正文按明确换行可读，颜色与边界一致。
- [x] 图的新增/扩展/复用说明与设计稿一致。

具体修订：第一版标签过窄、换行过多；增大 wrappingWidth 并使用 classic 风格，六行三列可顺序阅读。

评分：9/10。特点图可直接阅读；目标架构图完整但纵向较长，适合在文档中放大查看。

结论：ACCEPT；语法、文件一致性、节点及关键箭头检查通过。

## 目标架构

可见内容：顶部多人请求经过身份授权，分为命令与读取；命令事务落盘，底部执行保存候选并回写，查询支路展示给各成员。

### 文件检查

- [x] .mmd 仅包含 Mermaid 源码。
- [x] .md 的 Mermaid 内容与 .mmd 完全一致。
- [x] Mermaid CLI 渲染成功，PNG 已用图像工具逐图查看。

### 节点检查

- [x] people：成员 A / 成员 B / 审核人；各自登录，共享同一项目。标签和设计职责一致。
- [x] identity：身份与项目授权（新增）；服务端 actor / 项目角色 / 撤权。标签和设计职责一致。
- [x] commands：协作命令（新增）；操作归属 / 幂等 / 目标版本 / 配额。标签和设计职责一致。
- [x] tx：原子事务（扩展）；业务变更 + 审计 + 项目事件。标签和设计职责一致。
- [x] state：PostgreSQL 项目事实（扩展）；成员 / 任务 / 固定结果 / 审批 / 事件。标签和设计职责一致。
- [x] admission：持久化任务接纳（扩展）；会话串行 / 独立任务并发 / 排队可见。标签和设计职责一致。
- [x] runner：单一执行协调器（复用）；DBOS + 受控 broker + SDK Runner；每步及提交前复核权限。标签和设计职责一致。
- [x] scope：冻结输入与授权（扩展）；项目资料版本 / 触发消息 / 成员授权代次。标签和设计职责一致。
- [x] candidate：不可变候选结果（扩展）；旧输入、旧目标不覆盖当前成果。标签和设计职责一致。
- [x] approval：版本化审核与发布（扩展）；真实审核人 / 固定哈希 / 发布复核。标签和设计职责一致。
- [x] projection：授权查询与事件订阅（扩展）；游标补读 / 撤权断流 / 版本失效提示。标签和设计职责一致。
- [x] flow：每位成员的协作 Flow（扩展）；发起人 / 负责人 / Agent / 审核人；执行、审核、采用状态分别展示。标签和设计职责一致。

### 箭头检查

- [x] people → identity（请求）：方向及目标正确。
- [x] identity → commands（操作授权通过）：方向及目标正确。
- [x] identity → projection（读取与订阅授权）：方向及目标正确。
- [x] commands → tx（校验版本与重复提交）：方向及目标正确。
- [x] tx → state（提交事实与待派发意图）：方向及目标正确。
- [x] state → admission（未派发的固定命令）：方向及目标正确。
- [x] admission → scope（接纳固定执行请求）：方向及目标正确。
- [x] scope → runner（稳定 workflow ID 与授权快照）：方向及目标正确。
- [x] runner → candidate（保存产物与证据）：方向及目标正确。
- [x] candidate → state（原子保存候选、终态及事件）：方向及目标正确。
- [x] commands → approval（审核或发布命令）：方向及目标正确。
- [x] approval → tx（原子校验并更新固定结果引用）：方向及目标正确。
- [x] state → projection（快照与持久化事件）：方向及目标正确。
- [x] projection → flow（相同事实，按各自权限展示）：方向及目标正确。

### 完整性与视觉检查

- [x] 涵盖用户、共享项目、权限、并发、后台执行、审核/发布、更新同步及现有模块映射。
- [x] 标签无裁切，正文按明确换行可读，颜色与边界一致。
- [x] 图的新增/扩展/复用说明与设计稿一致。

具体修订：第一版过窄，修订标签宽度；最终将冻结输入/授权放在 Runner 之前，产物由 Runner 产生并原子回写，避免让授权校验模块承担产物保存。

评分：9/10。特点图可直接阅读；目标架构图完整但纵向较长，适合在文档中放大查看。

结论：ACCEPT；语法、文件一致性、节点及关键箭头检查通过。
