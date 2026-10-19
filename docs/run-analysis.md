# Flow 运行分析

2026-10-13：完成运行分析第一层。在项目总览展开研究主任务，Flow 下方可查看“运行分析”。数据来自保存的主对话和专家运行账本，读取与回放不执行模型或工具。进行中的运行通过“刷新运行记录”读取最新快照。

支持历史运行选择、按事件顺序播放/暂停/拖动进度、跳到首个失败事件；Flow 的失败节点标红。可选择另一条运行作为基线，比较状态、总 token、总耗时和各工具调用次数。按 Agent 汇总输入/输出 token 并排序；模型请求表含任务、摘要和记忆等辅助请求的已记录耗时与用量。缓存 token 已包含在输入 token 中，不再次相加。

设计参考 [Langfuse 的 trace/observation 模型](https://langfuse.com/docs/observability/data-model)和 [session 回放](https://langfuse.com/docs/observability/features/sessions)：任务关系图、执行事件、模型请求测量分别呈现。本层复用项目数据库，不引入第二套追踪服务。React 请求按 [官方 Effect 清理模式](https://react.dev/reference/react/useEffect)中止过期请求；进度控件使用有 label 的原生 range。

## 证据边界

- 回放按账本事件 sequence；播放间隔是显示节奏，不是对真实时间的模拟。工具耗时按 run/phase/call/child/name 配对开始和结束，可处理交错调用。仅有开始事件时耗时未知。
- 总耗时只有根运行结束事件时才能计算。缺失终态、模型用量或未确认操作均保留未知；不根据当前时间猜测历史耗时。
- 首个失败事件是排查线索，不是根因结论；工具失败后恢复的成功运行不会被标为运行失败。检索质量、计划正确性和工具输入正确性需要进一步证据与语义评测。
- 不提供供应商账单金额或估算金额，当前没有冻结的计费单价。token 的减少不等同于节省金额，也不等同于质量提升。
- 对比给出状态、token、耗时和工具调用次数；尚未实现模型输入、输出正文、prompt/skill 版本及逐步内容差异。
- 当前覆盖主对话与专家任务历史，不包括未绑定到这些任务的规划、学习等独立辅助运行。子运行显示在其保存的根轨迹内。

## 权限与实现

新增 GET `/api/run_analysis?research_id=…`。主对话复用 `/api/run` 的当前授权检查；专家检查受控授权代次、冻结快照 scope、当前根资料权限和关联聊天权限。授权不可读的运行不返回任何轨迹或指标，仅计入不可读数量。响应设置 `Cache-Control: no-store`，不返回运行请求、结果正文、错误 message、工具参数、供应商 wire body 或私有上下文。

实现：`src/agent_core/execution/analysis.py` 负责纯指标投影；`ControlledLedger.model_measurements` 查询已记录测量；API 负责授权；`frontend/src/workspace/RunAnalysis.jsx` 负责回放与对比。没有新增依赖、数据库 schema、收费请求或执行权限。

## 下一层

回归测试复用 `evals/research_quality` 现有八道固定题和基线/候选 A/B，下一步应冻结 prompt/skill 版本并增加明确的比较门槛与退出码，再接入运行分析。统计执行差异不能替代评测。

沙箱必须作为独立执行边界实施：现有仓库路径权限、可信测试和受控运行账本不构成 OS 级代码隔离。未来应先确定实际运行平台，再接入平台支持的进程/容器隔离、默认关闭网络、按任务授予目录与资源限制；不能用危险命令字符串过滤替代隔离。本轮未改动执行器权限。

## 验证

- `tests/test_run_analysis.py`、`tests/test_workspace_api.py`、`tests/test_delivery_files.py`：12 passed。验证交错耗时、未知用量、恢复成功、只读内容投影、主对话/专家撤权和既有文件交付边界。
- `frontend/tests/run-analysis.spec.js`：Chromium 通过，验证回放、首个失败定位、基线切换和刷新后撤权数据清除。使用标记合成数据，不是模型质量或持久化验收。
- `npm run build --prefix frontend`：通过。现有 bundle 大小警告仍存在。
- 本轮未运行真实 PostgreSQL/模型验收；新增指标查询未声称通过真实数据库验收。
