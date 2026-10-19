# Stage 10 — 专家独立进化验收

日期：2026-10-03。阶段依据：`architecture-analysis/当前产品-增量开发与技术选型.md` 第 13 节及 `architecture-analysis/产品设计-当前版本.md` 第 8 节。Stage 10 工程链路已实现；本文件区分合成控制夹具、真实模型与真人确认。

## 可靠参考代码与已验证边界

实际阅读了 BearCode 的 `agents/online_skill_evolution.py`、`agents/online_skill_eval.py` 和 `agents/skill_evolution.py`。参考了其中“从反馈抽取可复用经验、排除一次性事实/秘密”的候选边界，以及先在 `mutate_dev` 比较、再在独立 `promotion_test` 验证选定候选的次序。其 `evolve_skill_file` 会保存历史快照并直接改写生效文件；这里采用仓库 Stage 8 已验证的不可变 `AssetVersion`、active 指针、CAS 发布与审计记录，避免预览污染正在运行的专家方法。Stage 7 `Feedback` 核验和 `FrozenCase` 的同源/同任务跨分割阻断提供反馈与数据隔离。候选生成、预览仍走项目现有 `CoreRunner` 和模型适配器；没有添加训练系统、模型权重变更或新依赖。

## 最小链路与设计核对

| 设计要求 | 实现和验收证据 |
|---|---|
| 核验反馈与专家归因 | `propose` 仅接受同一专家、同一研究、原 active 方法版本、`attribution=method` 的已核验开发案例。通用教训由审阅者显式给出并记录。 |
| 生成边界 | 生成器只接收基线方法和获准通用教训，输出严格限于 `instructions`、`examples`；`MethodBody` 的 kind 与 tools 继承基线。留出案例 ID、标签和原始资料不进入生成提示。 |
| 固定比较与留出 | 候选创建时冻结开发/留出案例 ID、质量地板、最小质量收益与总成本上限。每个案例必须有 before/after 两次独立预览，使用相同冻结 `ContextSnapshot`、专家、模型和输入；候选不写正式任务回执。重复/遗漏比较保持 incomplete。 |
| 跨专家端到端 | 下游预览必须使用留出案例中的已冻结任务，并消费该候选 after 输出替换的上游挂载；检查输出 schema、程序验收及独立评定。 |
| 硬门槛优先 | 发布前要求开发和留出质量均过线、after 及下游程序/契约检查、范围审阅、下游评定通过、全部成本为已测量且总额不超上限。偏好收益没有绕过硬门槛的入口。 |
| 发布与回退 | 精确版本确认文本、用户操作者、完整比较证据缺一不可。方法服务阻止常规资产 API/CLI 直接发布候选；发布沿 Stage 8 数据库 CAS。回退也要求当前 active 是该候选版本，复用审计和 CAS。 |
| 在途旧版本 | Stage 8 既有任务绑定和本阶段测试均确认，active 切换不改变旧 `TaskSpec.method_version`；回退不删除失败或旧候选。 |

控制夹具中的单位案例：基线把源材料的 `thousand USD` 错写为 `million USD`；候选加入逐项定位单位的指令，开发和留出预览均改正，下游分析消费正确单位。该候选在合成夹具里模拟确认发布，随后回退到基线。另一候选仍输出错误单位，开发与留出比较及下游检查给出 `failed`，资产和评测记录仍保留，active 保持基线。夹具的 reviewer 名称和成本值只验证工程门槛，**不是真人审批、真实账单或真实模型质量证据**。

## 工程验证

- `TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55448/stage10_acceptance .venv/bin/python -m pytest -q tests/test_evolution.py`：11 passed，1 skipped（内存分支跳过仅适用 PostgreSQL 的跨进程测试）。单独覆盖估算成本阻断、失败候选保留、API 绕过阻断和生成器越权字段拒绝。
- 同一隔离库执行 Stage 10、Stage 8 方法、Stage 7 评价集成：45 passed（该次运行在增加后续 API/跨进程用例之前）。
- 全量前序集成回归：356 passed，1 skipped；唯一跳过是内存参数化分支不执行 PostgreSQL 专属跨进程检查。运行时长 98.25 秒。此后只新增了两项 Stage 10 负例测试，均已单独通过，生产代码未变。
- `npm run build --prefix frontend`：通过。Vite 仅报告已有第三方模块 directive 与大 chunk 警告。
- 本机浏览器烟测：在真实 PostgreSQL 服务中打开方法管理，加载保留的失败候选并显示 `failed` 比较及开发/留出指标；无页面异常。截图：[stage10-browser.png](stage10-browser.png)。浏览器夹具仍为合成数据。
- PostgreSQL 隔离目录 `/private/tmp/stage10-pg-20261003`，端口 55448，库 `stage10_acceptance`；未清理 Stage 8/9 数据或共享内存。Stage 9 原实例因遗留共享内存未能启动，因此新建本阶段隔离实例。

## 真实限制与待用户确认

用户随后提供了 DeepSeek 凭据。七份虚构资料上的真实基线均一次成功，经独立来源核对单位和数值均正确；真实结构化生成器协议也完成一次成功调用。完整运行、用量和审批核对见 [Stage 10 真实模型复核](stage10-live-review.md)。由于没有真实方法归因错误，不能制造反馈来产生候选；探索草稿未保存为方法资产。真实候选质量增益、账单成本、跨专家候选检查及真人确认发布仍无证据，本阶段没有实际发布。工程夹具不能代替这些步骤。
