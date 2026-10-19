# CE3：上下文投影、分页外存与请求清单

2026-10-07。状态：当前 CE3 范围验收通过；允许按顺序开始 CE4。

CE2 验收通过后实施。保留可信本机服务、原 OS 执行锁、PostgreSQL、DBOS 和 Agents SDK；不实现容器、任意代码工具、摘要生成或 CE5–CE8。

## 实现与证据

| 要求 | 实现 | 当前证据 |
| --- | --- | --- |
| 字符限额前投影 | ContextBuilder 及讨论/咨询的 project_payload；主助手和专家历史改为结构化字段 | 14 万字符旧助手历史在旧 3 万字符限额前投影；保留中段用户约束和最新要求 |
| 不丢用户约束 | 全部用户原话保守保留；不推断约束已失效；必需内容不适配时显式拒绝 | 中段否定约束测试、必需内容超限拒绝测试 |
| 完整外存与固定版本分页 | PreparedInput 外存全文，运行工作区按 SHA-256 保存 context artifact；read_context_page 使用 JSON pointer 和字符范围 | 巨大单条、目录多页、尾页不足、版本不混淆、跨工作区拒绝；4.5 MB 工具结果落盘后返回预览 |
| 原始资料分页 | read_source 固定三元引用 + location/offset/limit；大返回有范围、余量、完整性和续页参数 | 保留小片段结构；大读取不再空页伪完成 |
| SDK 每轮投影 | RunConfig.call_model_input_filter；旧输出按引用外存，工具调用/返回配对保留 | 精确重复按工具、参数和结果识别；不同版本不去重 |
| 实际发送清单 | broker 保存 assembled；公开 HTTP 请求钩子保存 exact wire_text/body/hash 与 sent 状态；响应/异常更新 responded/unknown | 实际 SDK + HTTP 模拟传输捕获的 body 字节与清单一致；审计不可用时零调用 |
| 阅读记录独立 | context_reads 与 context_manifests 分表；每页范围独立记录 | 阅读全文完成才触发信息授权；部分页不能授权未读全文 |
| 整体输入预算 | 系统指令、工具定义、输出 schema、输入、8192 输出预留与4096误差空间统一核算 | 明确区分字符、UTF-8 字节保守 token 上界与 provider 实测用量 |
| 撤权 | 每次存储/分页/模型发送前继续原准入验证和授权世代检查 | 保存后撤权不能读取，CE2 原四入口/撤权回归继续通过 |

外存只存在当前准入工作区，模型不能给文件路径或搜索全库。全文文件先 fsync，再发布 SQLite 索引；没有摘要生成。目录不再使用 sources[:50]、results[:30] 或最近消息数量硬截断；大目录通过同一固定 artifact 分页。业务完整对象不再以 100k 超限空响应作为读取结果。

## 已执行验证

- [首轮回归](ce3-evidence/initial-regression.txt)：72 passed / 21 skipped / 9 failed。除受限环境禁止本地测试 HTTP server 外，主要为旧协议桩未包含新 instructions/审计字段及旧空结果断言。保留失败证据。
- [第二轮单元](ce3-evidence/second-unit-regression.txt)：37 passed / 1 failed；最后失败为旧测试只期望 frozen 系统文本，现实现正确传递 SDK 增补的参数纠错说明，断言已更新。
- [综合首轮](ce3-evidence/combined-attempt1.txt)：219 passed / 1 failed，无跳过。失败是报告阅读回执与 Core 附加的 remaining_tool_calls 导致的哈希不匹配。
- [交接诊断](ce3-evidence/handoff-diagnostic2.txt)：真实原因是 Manager information cites unread or foreign context。修复为将业务回执关联到最终带预算字段的 artifact，不提前授权。
- [HTTP/投影专项](ce3-evidence/projection-attempt2.txt)：8 passed。
- [修复后专项与四入口](ce3-evidence/projection-handoff-fixed.txt)：24 passed，无跳过。
- 新库 ce3_controlled_acceptance；真实模型使用另一个全新 ce3_live_20261007_attempt1，blob/private/evidence 独立。

## 运行与依据

```sh
# 凭据只经受保护的进程环境传入。
export CE3_TEST_DATABASE_URL='postgresql+psycopg://USER@127.0.0.1:55462/ce3_live_NEW'
.venv/bin/python scripts/verify_ce3_live.py --allow-paid-model --output docs/ce3-evidence/live-NEW
```

脚本拒绝非 ce3_ 库、非空数据库和已存在输出目录。实际 Responses API、max_retries=0；保留全部 wire body、哈希、阅读范围、合成原文和输出供独立审查，不保存鉴权头或密钥。

采用 SDK 公开 [call_model_input_filter](https://openai.github.io/openai-agents-python/ref/run_config/) 和 HTTP 客户端公开[请求事件钩子](https://www.python-httpx.org/advanced/event-hooks/)。本地 SDK 0.22.2 的对应类型已核对；没有调用 SDK 私有循环。

## 最终验收

- [第四轮真实结果](ce3-evidence/live-attempt4/live.json)：5 次 Responses HTTP 200；151,370 字符历史投影为 6,403 字符；保留中段用户否定约束和当前指令。模型真正读取目录，随后读完资料 artifact 的 [0,8000) 与 [8000,11235) 两页。
- [独立审查](ce3-evidence/live-attempt4/independent-review.json)：原始 blob 哈希一致，首尾 Marker 一致，数量为 7 且不解释为金额，市场规模保持未知、未估算，保留“保留未知”标签；引文来自固定版本的 line:1 / line:2。只证明本合成长历史案例，不外推广泛研究质量。
- [最终综合回归](ce3-evidence/combined-final.txt)：264 passed / 1 failed，无跳过。唯一失败是旧讨论夹具期待把 expert 历史转换成独立 assistant 消息；当前结构保留真实 role=expert 于 history。更新该断言后[报告信息整套复测](ce3-evidence/report-information-final2.txt) 6 passed，关闭该失败；没有修改权限断言或放宽成果要求。另 [SDK/受控单元](ce3-evidence/unit-final.txt) 38 passed。
- 新增 HTTP 审计提交失败测试也在最终综合回归中：wire 清单写入失败时 MockTransport 零发送，状态为 dispatch_failed；真实 wire 字节审计与 provider 模拟传输一致。
- 本轮集群备份至 `.ce3/20261007/accepted-cluster.sql`；仅重启本轮 55462 集群。`scripts/verify_ce3_saved.py` 在重启前后新进程执行，均退出 0。[前](ce3-evidence/live-attempt4/before-restart.json) / [后](ce3-evidence/live-attempt4/after-restart.json)一致：5 份精确 wire 清单、4 条阅读范围、3 份完整外存 artifact、成果与累计预算均保留，零新增模型调用。

当前范围无未关闭的阻断项。用户消息过多、全部必需原文自身超过预算时明确拒绝，不会偷偷生成摘要；摘要仍属 CE6。此前三轮真实失败与全部诊断保留。可以进入 CE4，CE5–CE8 未推进。

## 真实模型发现的问题（保留原始失败样本）

1. [第一轮](ce3-evidence/live-attempt1/live.json)：2 次 HTTP 200，模型猜测 `/sources/0/resource_refs`，路径不存在导致 SDK 工具异常。修复为明确的 invalid_input 与可用 JSON pointer 提示；未返回任何伪内容。
2. [第二轮](ce3-evidence/live-attempt2/live.json)：6 次 HTTP 200。旧输出裁剪错误地存储分页包装，模型沿包装引用重复读取，未完成。修复为分页结果保留底层 artifact/range/next_cursor；新增“不递归包装”回归，并减少近期历史占用。
3. [第三轮](ce3-evidence/live-attempt3/live.json)：3 次 HTTP 200；第四次预占后、发送前被完整预算检查拦下。SDK 投影未包含完整 ModelSettings，最终输入估算 53,252，加输出/误差预留后超出 65,536。修复为投影与 broker 使用同一 ModelSettings 和模型标识。未发送的预占不伪称已计费或释放未知状态。
4. 重试完整输入回归发现旧节点状态不能作为重试准入条件；改为复用固定 snapshot 并核验原授权世代/固定 scope，避免丢失已外存的历史与约束。[修复回归](ce3-evidence/fixes-attempt5.txt) 58 passed。
5. [完整预算与分页专项](ce3-evidence/projection-attempt6.txt)：11 passed，包括上述真实问题的固定回归。

第四轮在新空库 ce3_live_20261007_attempt4 完成并通过独立复核；前三轮均不算 CE3 通过。
