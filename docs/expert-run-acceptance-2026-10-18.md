# 执行 Agent 报告版本与运行详情验收

日期：2026-10-18（Asia/Shanghai）。服务：http://127.0.0.1:8744/ 。

## 入口与实现

从 Flow 点击执行 Agent，再点击“报告与运行”。选择报告对应任务版本、该版本下的具体运行。查看开轮输入、完整报告入口、运行状态/耗时/token，以及按调用 ID 归并后的工具记录；展开工具按需读取输入输出。

新增 GET /api/expert_run?task_id=...&run_id=...，可加 call_id 读取一次工具的输入输出。复用 expert_context 的访问校验、现有运行账本、受控操作、只读 transcript 索引及 checkpoint 回执。不添加数据库表，不回填旧数据，不重跑历史任务来补记录。工具输入需匹配受控账本中的请求哈希；输出需绑定该次运行的确认回执及内容哈希。私人笔记和模型供应商请求正文不通过此接口返回。

缺失输入/回执和未确认操作明确展示；无时间事件的调用标为未记录时间。分页工具输出展示当时返回内容，不声称已还原完整来源正文。

## 页面实测

项目：专家派遣验证。主任务：子 Agent 上下文实测（合成数据）。
执行任务：963e972f-4239-4235-b7a3-c778f9dff35c。

| 报告 | 对应运行 | 工具调用 |
| --- | --- | --- |
| v1 | 5877d8f1-af2d-45b0-a229-d857808955e3 | load_skill，共 1 次 |
| v2 | 0d385875-3257-43ea-837f-6238745f821f | read_context、search_memory、write_memory、read_context，共 4 次 |

- 从实际 Flow 的执行节点打开详情成功。
- v1/v2 来回切换，运行 ID、输入、报告和工具列表对应切换。
- v1 保持原始 5+7+9=21 报告；v2 新增平均值 21÷3=7。
- 两次同名 read_context 具有不同调用 ID，分别展示。
- 实际页面展开 load_skill 和 read_context，均显示输入和输出。
- v2 四次调用均通过接口确认 input_recorded=true、output_recorded=true。
- 本次 v2 由“补充任务要求 → 保存并执行”生成，属于人工发起的版本更新，不是自动评审修订；旧评审仍标注为 v1 的记录。

## 验证

20 项测试通过：test_automatic_review.py、test_expert_run.py、test_expert_context.py、test_run_analysis.py。
覆盖交错同名调用归并、未结束调用、请求哈希匹配、跨运行回执拒绝、私人笔记排除、历史版本冻结输入、跨任务拒绝、撤权拒绝，以及查看不触发执行。

前端 npm run build 通过。保留已有依赖的 use-client 指令和大包提示。
