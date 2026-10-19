# RouteLLM 模型路由

当前代码使用 [RouteLLM 0.2.0](https://pypi.org/project/routellm/) 的 `Controller.route(prompt, router="bert", threshold=...)`。RouteLLM 只在强、弱两个模型间选择；专家由任务类型对应的发布方法确定，或由用户显式指定。目录预筛会先检查工具、输入容量、续接能力等级与剩余执行预算。若只有一个合格模型，直接选择该模型。两个都合格时，才将任务摘要输入本地 RouteLLM。路由失败或返回目录外模型会阻断任务，不启动执行。

## 当前可运行目录

[DeepSeek Flash 单模型目录](../config/deepseek-flash.json)已按官方模型能力和 2026-10-02 的峰时、缓存未命中价格填写，并以账户的 `/models` 响应和一次真实生成请求验证。启动时在进程环境设置 `OPENAI_API_KEY` 为获授权的 DeepSeek 密钥、`OPENAI_BASE_URL=https://api.deepseek.com`，然后传入 `--catalog /absolute/path/config/deepseek-flash.json`。密钥不写入目录。该目录的路由来源为 `only_candidate`；它提供已验证的实际执行入口，尚不声称取得双模型 RouteLLM 的质量或成本收益。

2026-10-02 使用获授权凭据完成一次内存数据库端到端试跑：目录选中 `deepseek-flash`，专家任务执行成功，结果进入 `pending_review`；执行回执记录 2 次请求、4,596 输入 token 和 1,005 输出 token。该试跑验证调用链路，研究结论仍需独立语义复核。`deepseek-v4-pro` 也完成一次最小生成请求，但未被赋予未经验证的强弱等级。凭据只在运行时注入，未保存到仓库。

## 固定权重和目录

RouteLLM 官方本地 BERT 权重为 [`routellm/bert_gpt4_augmented`](https://huggingface.co/routellm/bert_gpt4_augmented/tree/main)。先在部署机下载固定提交；推断权重约 1.11 GB，不下载训练优化器：

本轮下载该提交并用官方权重完成一次 `RouteLLMClient.choose` 本地调用；返回值为测试模型对中的一个 ID。此检查只确认权重和依赖可加载，不评价 DeepSeek 两模型的路由质量。检查后已清理占用约 1.1 GB 的临时缓存；启用双模型目录时按上面的命令重新下载并保留检查点。

```sh
uv sync --frozen
HF_HOME=/absolute/path/hf-cache uv run python - <<'PY'
from huggingface_hub import snapshot_download
print(snapshot_download('routellm/bert_gpt4_augmented',
    revision='86237e3df400762178ea98379477b8296e66d5e4',
    allow_patterns=['LICENSE','config.json','model.safetensors',
                    'sentencepiece.bpe.model','special_tokens_map.json',
                    'tokenizer.json','tokenizer_config.json']))
PY
```

经任务样本验证强弱配对后，将输出的本地快照目录写进双模型目录 JSON 的 `checkpoint_path`。同一个目录还需填写：

- `version`：运营目录版本；改动模型、价格、阈值或权重时递增。
- `strong_model`、`weak_model`：恰好对应 `models` 数组里的两个不同 ID。强模型 `capability_rank` 必须更高。
- `threshold`：0 到 1 的 BERT 阈值；只在开发样本上校准并冻结，不能把默认 0.5 当成已验证门槛。
- `models`：每项按 `ModelCandidate` 填写 `id`、`description`、`available`、`tools`、`supported_kinds`、`capability_rank`、`max_input_chars`、输入/输出每百万 token 美元价格、`capability_source` 和 `price_source`。目录不保存密钥。

示例结构（模型 ID、价格、容量和来源必须换成经核实的值）：

```json
{
  "version": "my-pair-v1",
  "strong_model": "strong-model-id",
  "weak_model": "weak-model-id",
  "checkpoint_path": "/absolute/path/hf-cache/hub/models--routellm--bert_gpt4_augmented/snapshots/86237e3df400762178ea98379477b8296e66d5e4",
  "threshold": 0.5,
  "models": []
}
```

上例 `models` 留空是有意的：它不能通过目录校验，避免将示意价格当作真实价格运行。BERT 模型在本地选择模型，不调用远端嵌入接口。RouteLLM 0.2.0 在导入时仍会初始化 OpenAI 客户端，因此进程必须提供 `OPENAI_API_KEY`；密钥仅从环境读取，不进入目录。RouteLLM 自身不会生成执行答案，本项目仍负责运行和记录选中的模型。

## 验收边界

RouteLLM 官方说明其路由训练基于首轮问题，并建议按实际查询校准阈值。当前权重源于 GPT-4/Mixtral 偏好数据；中文研究、短回复和财务提取任务的效果不能由公开基准推定。Stage 7 的五组模型选择评测现为固定模型、规则、RouteLLM、历史证据、RouteLLM 加历史证据。专家选择只做独立的前后配对，不再冒称 RouteLLM 在选择专家。真正的质量、端到端费用、阻断率仍需用已授权的双模型目录及留出案例测量。

路由记录保存目录与策略版本、阈值、候选和排除理由、已选模型、路由耗时及任务范围。RouteLLM 的 `route` API 只返回模型 ID，因此记录不会伪造置信度、概率或 token 用量。

DeepSeek 当前官方模型与峰谷价格整理在 [DeepSeek 模型参考目录](deepseek-model-reference.json)。V4.1 Flash 在官方发布材料中多项能力评测超过现有 V4 Pro，且价格更低；不能只凭价格或名称把 Pro 定为“强模型”。因此该参考目录不直接生成可运行的强弱路由目录。先用本项目开发样本验证两者在目标任务上的质量排序和阈值，再冻结 `strong_model`、`weak_model` 和 `capability_rank`。
