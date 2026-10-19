# CE8 失败与修复记录

1. 首次新增架构检查：8 passed / 1 failed。context/compaction.py 和 model_projection.py 原先直接依赖 SDK，违反通用上下文模块边界。模型投影移入 controlled；Compactor 使用 dataclasses.replace 保留传入数据类型，去掉 SDK 导入。随后相关 36 passed / 4 skipped（未配置 PG 的本地检查，跳过项不算验收）；另有 CLI/SDK/架构 21 passed。
2. 第一轮夹具长度未触发 30000 字节业务投影阈值，equipment 的 full/projection 首个实际请求同为 31836 字节且无业务排除。该轮不是有效比较。equipment 三臂保留；cashflow/full 被操作员中止，结果 cancelled、已知 input=14581/output=1202、usage.complete=false；不记作模型语义失败，不重置账本。第二轮只增加无事实填充长度，仍用原模型、事实和预算。
3. 第二轮设备 full：报告多出无来源的“1998 之外的年份”，不能因其余事实正确算质量通过。设备 projection：未找到青鹭/北斗撤回，12 次工具耗尽且笔记未写。设备 compaction：找回代号，但同样耗尽 12 次工具、笔记未写；根工件与 /history 重叠读回，未完成任务。一次运行时摘要逐字引用校验失败，原记录与旧摘要保留，未冒充摘要成功。
4. 第二轮财务 full 与订单 full：后续模型请求触发原有 context_budget 预检，未交付，未提高窗口或删改失败。财务 projection：主要算式正确，但漏掉 FCF≠净利润和 v2 未批准的历史说明，并自行补充了没有原文支持的 FCF 排除项（包括营运资本等），语义不通过。订单 projection：算对数量及差值，却在明确历史门槛上给出未能确认的矛盾表述，笔记未完成。财务/订单 compaction 的必要事实和约束均正确，笔记落盘。
5. 针对第二轮实际原因：受控执行公共指令补充最小引用读取、避免重复遍历、为明确要求的动作留预算、区分历史事项与当前执行、核对事实性年份/单位/版本。无题目答案注入，没有增加检索库或提升预算。第三轮完整三题×三臂重跑，原轮记录不替换。

最终分数、样本与当前仍存在的基线限制以验收报告和逐项评审为准。任何 pending_review 只表示报告已交付，不代表语义通过。

6. 第三轮 orders/compaction 虽然必要数字与里程碑正确且写了笔记，正文却把“未包含退货”推为“未来不受退货因素调整”，与 missing 冲突，故不通过。第四轮新增通用缺失/否定边界说明，不填题目答案。
7. 第四轮 orders/compaction 再次漏写笔记。实际 manifests 证实 model:3 起 index=0 的有效业务摘要被 request_budget 外置，后续运行时摘要也遭外置，模型反而保留长原文页。这是输入投影实现缺口，不能仅靠更长提示词解决。第五轮保护 Compactor 实际发布的精确摘要文本，其他内容仍按原预检外置；伪造的相似模型输出不受保护。新增窗口压力测试通过。
8. 旧全历史构造移入测试夹具后首次收集出现 ConversationStore 注解未导入，测试夹具补上公开类型导入；63 passed / 1 skipped（未配置 PG 的 CLI 测试）。内存阶段另有 60 passed / 58 skipped / 1 failed，唯一失败为沙箱禁止监听 localhost，未作为产品失败修补。
9. 首次广泛 PG 回归为 599 passed / 4 failed / 1 skipped。两个双侧调查测试仍把结构化咨询输入当旧纯文本，诊断确认 current_instruction 已正确存在；改为解析该字段并显式配置同一测试 engine。两个旧“每个专家均可联网”测试不符合 CE5 编码单独授权边界，改验无代码合约时拒绝，不恢复编码联网。对应 17 项通过。不适用的跳过项是 PostgreSQL-only 检查的 memory 参数分支；真实 PostgreSQL 参数已运行。

提示词修复前后、结构性修复前后的原始输出均完整保留。回归前后目录和记录不可用通过样本覆盖失败样本。

10. 第五轮已确认摘要保护生效，但 equipment/compaction 仍漏写笔记；cashflow/compaction 再次自行补充 FCF 排除营运资本等无依据解释。orders/compaction 必要事实及笔记正确。本轮整体不通过；第六轮修正通用必需动作顺序和公式解释边界，完整保留前轮。

11. 第六轮 equipment/projection 与 compaction 分别 120.206/120.198 秒取消，各保留一个 unconfirmed 模型请求。根因是 tasks.run 的单项 DispatchPolicy 默认 120 秒提前取消冻结 360 秒任务。已使外层使用冻结时限；30/360 两个参数在修复前失败、修复后通过，完整回归与第七轮重新执行。第六轮全九格及费用未知项保留。

12. 第七轮三题 compaction 必需项目与交付通过；full 三题均为 context_budget 失败；equipment/projection 生成 limit=12352 越过分页12000上限，SDK拒绝。其余projection遗漏及私有草稿UUID转录问题详见 round7-review.md；保留原文，不择优替换。
13. 最终四入口真实样本先得到23次HTTP200、后出现5次HTTP402，主助手恢复及普通讨论未闭合。停止后续真实模型强杀验收；不以两个已保存专家结果冒充四入口通过。正式CLI浏览器读取已保存结果仍通过。

14. 用户确认额度恢复后，原生产代码不变，新库四入口35次HTTP200且控制/窄语义通过；真实响应后SIGKILL、DBOS同run恢复、一次笔记副作用通过。原请求未知用量继续保留。原HTTP402样本未覆盖，浏览器重验通过。
