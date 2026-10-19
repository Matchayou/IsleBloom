# Stage 0 设计符合性与验收记录

日期：2026-09-19。设计来源：`architecture-analysis/增量开发架构与阶段验收.md`，第一节 Core 契约与第三节 Stage 0。范围止于 Stage 0；**实现、设计符合性、本地测试及真实模型语义验收均通过，Stage 0 完成。Stage 0 没有前序 Stage，未推进 Stage 1。**

## 复用决策

源码索引固定参考提交 `fbf59a40e9da5adb88d370fefaeaae0478376d4a`。阅读了本地 SDK `docs/context.md`、`agent.py` 的 as_tool、`run.py`、`result.py`、`memory/session.py`，并核对官方该提交的 pyproject：版本为 0.22.2。安装完整 PyPI `openai-agents==0.22.2`，全部传递依赖记录于 `uv.lock`（含 openai 3.16.2、pydantic 2.13.5）；未复制缺失依赖的源码摘选。

进一步检查已安装版本 FunctionTool、Model 接口、工具执行与错误包装实现。产品代码只使用 SDK 公开 API，不导入 `run_internal`、下划线 API，不重写模型循环。使用 `FunctionTool` + `set_function_tool_failure_error_function(..., None)` 保留失败语义。SDK Session 协议用作会话职责参考，但其原始 item/自动追加不满足当前简化文本历史与 revision 规则，因此独立 ConversationService/Store。`Agent.as_tool()` 会共享底层 context，改为显式 delegate 工具调用同一 Core 入口。

Context7 技能已读取，但本会话无对应工具；文档核对采用本地固定源码与[官方固定提交](https://github.com/openai/openai-agents-python/blob/fbf59a40e9da5adb88d370fefaeaae0478376d4a/pyproject.toml)。未引入其他阶段的参考依赖。

## 符合性矩阵

| 当前设计要求 | 实现证据 | 测试/交互证据 | 状态 |
|---|---|---|---|
| CoreRunner、EnginePort、ExecutePort；SDK 拥有循环 | contracts/core/adapters.openai | SDK Model 替身驱动实际 Runner，两轮工具→结果映射 | 通过 |
| AgentCatalog、普通 ToolBinding、输出契约 | catalog 校验稳定 ID/工具名/批准目标；Pydantic 输入输出 | unknown_agent、invalid input、structured output/invalid output | 通过 |
| 标准结果、错误、用量、事件 | RunResult/RunError/CoreEvent；父子和 call ID | C0 时间线、SDK 用量含子一次、失败 origin/code | 通过 |
| 串行工具，A/B 不执行成 A/A/B | run 内锁、call ID 缓存、SDK parallel_tool_calls=False | SDK 一轮返回 A/B，精确处理器序列 [12,3]，SDK 重放同 ID 只执行一次 | 通过 |
| 工具异常不当成成功文本 | CoreFailure 与 SDK UserError 映射；关闭默认工具错误文本处理 | SDK 工具失败不进入下轮模型；兄弟工具未执行 | 通过 |
| 轮数、超时、调用数、取消 | RunLimits、asyncio.timeout、标准取消结果 | turn/call/depth limit，普通运行超时与取消，SDK handler 清理 | 通过 |
| 显式独立专家输入、深度 1、串行 | 批准列表、独立 PreparedInput、同一 Core 入口 | 脚本与 SDK 检查子 input 无父标记；递归/未批准目标拒绝 | 通过 |
| 父子取消/超时与因果关联 | 子取消继续传播，cause_id 与 delegate call ID | 父取消/父截止时间时子清理完成、delegate cancelled | 通过 |
| 独立会话服务、文本历史、失败不写入 | ConversationService/Store、in_memory adapter | C0 追问、失败后 revision=0、独立会话、store failure | 通过 |
| 单写入者、revision 冲突、run_id 幂等追加 | active conversation 集合、store append 校验 | 并发 send 拒绝；重复 append 不增版本；旧版本/改内容冲突 | 通过 |
| 可运行 CLI | cli/composition | scripted demo 及真实 DeepSeek CLI 两轮结果均含 96；完整事件文件 | 通过 |
| 真实模型语义与实际交互 | evals/core_semantics/run.py / openai CLI | DeepSeek-V4.1-Flash 真实 multiply→delegate→中文回答→历史追问；语义评测与 CLI 均退出 0 | 通过 |
| 不提前进入 Stage 1+ | 仅当前所需目录，无数据库/研究/资料/记忆模块 | 依赖方向检查测试 | 通过 |

## Gap 修复记录

1. SDK 对 handler 的 CoreFailure 包装为 UserError，初测 18 通过 / 2 失败，错误被误映射为 engine_failed。adapter 在本次执行闭包中保留标准工具错误，捕获公开 UserError 时还原；未读取私有 SDK 状态。对应测试现通过。
2. 子 Core 返回 cancelled 后，若直接转 CoreFailure，会破坏父超时/取消语义。delegate 在发出子终态事件后继续抛 CancelledError；增加父取消与截止时间测试，确认子任务无残留。
3. delegate 事件初版缺少工具 call ID。增加同一串行调用内关联，使 run→tool_call→child_run 可追踪，并以测试校验。
4. 失败时 SDK 不保证完整用量；显式标记 usage.complete=false，避免零值被解释为无消耗。成功用量包含子运行且只累计一次。
5. 首次 editable 构建早于包目录写入，CLI 暂时无法导入。创建源码后重建本地包；CLI 可直接执行，后续从完成目录 `uv sync --frozen` 可正常安装。

## 实际命令与证据

```sh
UV_CACHE_DIR=/tmp/stage0-uv uv sync --python /opt/homebrew/bin/python3.12
.venv/bin/pytest -q
.venv/bin/agent-core --engine scripted --demo
.venv/bin/agent-core --engine openai --demo
.venv/bin/python evals/core_semantics/run.py
```

安装初次受网络沙箱阻止，获自动审批后固定依赖安装成功。最终测试 **30 passed in 0.71s，退出码 0**，输出见 `stage-0-tests.txt`；`uv sync --frozen --offline` 再次核对 45 个已安装包成功。脚本 CLI 退出码 0，完整两轮事件及输出见 `stage-0-scripted-demo.txt`。这些证据不代表真实模型。

首次因凭据缺失，真实验收未运行（退出码 2）。用户随后提供了 DeepSeek 配置与授权凭据，现已补齐：

- 地址 `https://api.deepseek.com`；模型 `deepseek-flash`。依据[DeepSeek 官方说明](https://api-docs.deepseek.com/)，此 API 标识对应用户指定的 DeepSeek-V4.1-Flash。实际 `/models` 返回 `deepseek-flash`、`deepseek-v4-pro`。
- 使用现有 OpenAI Agents SDK 默认 Responses 接口，未修改 Core、adapter、模型循环或依赖，未增加协议回退。
- 语义评测实际以 `runpy.run_path("evals/core_semantics/run.py", run_name="__main__")` 执行，退出码 0。标准输出保存为 `stage-0-real-eval.json`。首轮得到“12乘以8的结果是96。”，追问得到“刚才的结果是 96”。工具开始事件精确为 multiply、delegate，各一次。首轮 4 次请求（含专家），追问 1 次；共输入 2317 / 输出 478 tokens。
- 真实 CLI 演示通过 `asyncio.run(cli.run(Namespace(engine="openai", demo=True)))` 调用同一 CLI 执行入口，退出码 0；完整 stdout 直接保存为 `stage-0-real-cli.txt`。专家句子“结果为96。”，追问正确回答 96；共输入 2298 / 输出 420 tokens。
- 两次真实运行共 10 次模型请求，输入 4615 / 输出 898 tokens（以 SDK 报告为准）。这是固定小样本验收，不代表广泛语义评测。
- 密钥通过关闭回显的终端输入进入测试进程环境，未写入仓库、报告或持久配置，进程已退出。复现时需自行安全提供 `OPENAI_API_KEY`，再设置 README 中的非敏感地址与模型变量。未使用 Codex/ChatGPT 登录令牌。
- 随后重跑完整 C0：30 项测试通过；没有新增待修复 gap。本阶段无前序 Stage，回归范围为 Stage 0 基线。

## 已知边界

仅内存会话/事件，无重启留存，无跨进程锁，无持久执行。异步取消依赖 handler 协作，不支持强制终止阻塞本机代码。失败后的外部副作用不回滚，去重限定单 run/call ID。相同 conversation 的多 service 实例可在执行后提交冲突，本阶段默认单 service 单进程。全任务累计预算、并发调度、资料权限与研究树均未实施，符合当前边界。事件回调必须快速且不抛异常。当前阶段无未解决验收项；上述已知边界不在本阶段承诺内。
