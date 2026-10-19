# 真实模型动态上下文验收：2026-10-12

状态：**余额阻断，未通过完整验收**。本次不是合成模型测试：两次请求实际发送到 DeepSeek Responses API，均经过生产 ContextAssembler、CoreRunner、ControlledEngine、SDK、Channel/Broker 和 HTTP 审计入口。

## 配置与凭据检查

启动环境中没有 OPENAI_API_KEY，项目已有 `.env` 配置了密钥。验收脚本在进程内加载该文件，不将密钥置于命令行、日志或报告。

| 项目 | 检查结果 |
|---|---|
| 端点 | https://api.deepseek.com |
| 主助手 / 领域生成验证模型 | deepseek-v4-pro |
| 专家执行模型 | deepseek-flash |
| 凭据 | 项目 .env 中存在；真实请求已到达提供方 |
| 数据库 | 新建隔离 ce2_live_context_20261012，端口 55473 |
| 响应 | 两个模型均返回 HTTP 402 |

DeepSeek 官方将 HTTP 402 定义为账户余额不足：<https://api-docs.deepseek.com/zh-cn/quick_start/error_codes/>。该响应不能证明模型输出、工具调用或多 Agent 行为正确，也不意味着已验证全部模型参数兼容性。此次成功模型回答为零。

## 已执行与未执行

| 场景 | 实际执行 | 结果 |
|---|---|---|
| 主助手真实模型预检 | deepseek-v4-pro，真实受控调用 | HTTP 402，失败 |
| 临时 Agent 执行模型预检 | deepseek-flash，真实受控调用 | HTTP 402，失败 |
| 两个预检的用户原话 | 检查冻结 ContextSnapshot 的最后 user 消息 | 字节内容原样保留 |
| 主助手派发临时 / 专精 Agent | 未执行 | 余额阻断，不计通过 |
| 专家加载 skill、读取原文及咨询主助手 | 未执行 | 余额阻断，不计通过 |
| 规划 | 未执行 | 余额阻断，不计通过 |
| 专家讨论 | 未执行 | 余额阻断，不计通过 |
| 信息咨询 | 未执行 | 余额阻断，不计通过 |
| 领域轨迹 → 候选 → 独立验证 → 发布 | 未执行 | 余额阻断，不计通过 |
| 已发布领域 memory/skill 的动态检索 | 未执行 | 余额阻断，不计通过 |

现有失败预检的固定输入没有资料权限；工具预算为零。临时 Agent 没有积累领域轨迹。没有发布任何未验证候选，也没有以先前合成模型回归替代真实验收。

## 已准备的完整验收

`scripts/verify_live_context.py` 使用生产 LiveModelProvider，没有 ScriptedEngine、QueueModel、响应替换或模拟 HTTP 成功：

1. 分别预检主助手与执行模型，要求真实成功输出和确认用量。
2. 主助手加载项目 skill，读取固定合成来源，再实际派发一个临时核对员与一个专精 Agent；交接仅授予固定来源。额外年份资料留在主助手库，另一个研究的资料始终不授权。
3. 专家实际加载 skill、读取获准原文，并通过受控信息咨询获取年份原文；核对回执中实际授予的来源、工具调用、fields 与用户原话。临时 Agent 不产生领域轨迹。
4. 专精轨迹先生成隔离候选，确认源报告尚未复核时不发布。独立验收代码检查固定数量、未知项、年份和已校验引用后提交源报告复核，再运行真实候选验证器；只有验证通过才能发布。
5. 使用独立只读入口生成待确认计划、讨论固定专家任务，验证没有重执行或扩大权限。
6. 新专精任务检索并实际加载发布的领域 skill/memory，保留原交接原话；检查模型/工具操作与发布目录，冻结快照和审计均保存到隔离证据目录。

这里的文档事实是明确标注的合成验收来源，但模型必须是实际提供方输出。该设计验证真实模型与控制链配合，不冒充真实业务数据质量审核。

脚本已经保存并进行语法检查；被阻断的后续阶段尚未运行，不能声称该脚本的完整运行已经通过。

## 充值后继续

用户已选择为当前账户充值；尚未收到到账确认。充值完成后，使用新的隔离数据库和新的证据目录执行完整验收，保留本次失败记录。

```sh
pg_ctl -D /private/tmp/agent-node-pg-20261012 -l /private/tmp/agent-node-pg-20261012.log -o '-h 127.0.0.1 -p 55473 -k /private/tmp' start
createdb -h 127.0.0.1 -p 55473 ce2_live_context_next
.venv/bin/python scripts/verify_live_context.py --database postgresql+psycopg://chenyouran@127.0.0.1:55473/ce2_live_context_next --output docs/live-context-evidence/next --stage all
```

密钥只从本机 .env/进程环境加载，不要在聊天里发送。失败证据见 `live-context-evidence/20261012/state.json`、`audit.json` 和两个预检 ContextSnapshot。临时测试集群在本次检查结束后关闭，数据和证据保留。
