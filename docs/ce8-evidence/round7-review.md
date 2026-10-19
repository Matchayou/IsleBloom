# CE8 第七轮逐项语义评审

本表判定固定任务完成情况；私有草稿和历史摘要不是独立审核证据。评分明细见 [JSON](round7-semantic-review.json)，原始响应不做后期修订。

| 题目 | 策略 | 证据 /4 | 约束 /4 | 笔记落盘 | 任务通过 |
|---|---|---:|---:|---|---|
| cashflow | compaction | 4 | 4 | True | True |
| cashflow | full | 未交付 | 未交付 | False | False |
| cashflow | projection | 3 | 4 | True | False |
| equipment | compaction | 4 | 4 | True | True |
| equipment | full | 未交付 | 未交付 | True | False |
| equipment | projection | 未交付 | 未交付 | False | False |
| orders | compaction | 4 | 4 | True | True |
| orders | full | 未交付 | 未交付 | False | False |
| orders | projection | 3 | 4 | False | False |

## cashflow / compaction

- 两个主体/年度/币种/单位、80与90及12.5%原始算式、FCF非净利润、缺汇率与重述/抵销、v2未批准、不排名均保留。
- 额外会计准则概括不是夹具证据，不据此扩展本次验收结论；没有再断言FCF排除营运资本。

## cashflow / full

- 未生成报告：相同窗口预检 context_budget 失败；不评分未交付正文。

## cashflow / projection

- 未保留 FCF 不等于会计净利润；将年份不同单列为不可比原因不充分，主要不可比原因应是主体与币种。

## equipment / compaction

- 7台/2024、收入未知、青鹭/北斗撤回、v2不适用、已核对/待补资料均正确；正文、固定源读回与实际笔记逐项一致。
- 4次摘要调用中2次发布成功，2次失败保留旧head，已计入总成本。

## equipment / full

- 未生成报告：相同窗口预检 context_budget 失败；不评分未交付正文。

## equipment / projection

- read_context_page limit=12352 超过 12000，上层 SDK 拒绝；未交付。

## orders / compaction

- 105/90/80/10、白鹭85台验收门槛与差5未达成、固定截止日、未知收入/市占率、150预测和撤回口号均保留。
- 私有草稿销售资料UUID出现错误转录和问号；真实read_source结构化ref及正式报告sources完整ref正确，未读取外部版本。该草稿不作为正式证据或可直接执行引用；错误原样保存，未人工修饰成通过样本。

## orders / full

- 未生成报告：相同窗口预检 context_budget 失败；不评分未交付正文。

## orders / projection

- 找到白鹭与80+5，但没有取回明确85台验收门槛原文，报告把口径写成未知；12次工具耗尽且笔记未落盘。

## 成本与完整性

| 题目 / 策略 | 输入 token | 输出 token | 缓存输入¹ | 请求字节² | 模型/工具 | 摘要调用/成功 | 重复读 | 秒 | usage.complete |
|---|---:|---:|---:|---:|---|---|---:|---:|---|
| cashflow / compaction | 32837 | 6245 | 21120 | 130142 | 5/3 | 2/2 | 0 | 37.324 | True |
| cashflow / full | 19339 | 622 | 12288 | 103672 | 2/3 | 0/0 | 0 | 7.494 | False |
| cashflow / projection | 49295 | 4245 | 38912 | 224112 | 6/7 | 0/0 | 0 | 32.573 | True |
| equipment / compaction | 61599 | 12424 | 37632 | 252925 | 8/7 | 4/2 | 0 | 72.607 | True |
| equipment / full | 18629 | 1103 | 12416 | 101587 | 2/2 | 0/0 | 0 | 12.323 | False |
| equipment / projection | 22889 | 1038 | 16512 | 109172 | 3/3 | 0/0 | 0 | 13.34 | False |
| orders / compaction | 33305 | 6683 | 21632 | 131209 | 5/3 | 2/2 | 0 | 39.438 | True |
| orders / full | 19286 | 616 | 12672 | 103327 | 2/3 | 0/0 | 0 | 7.53 | False |
| orders / projection | 96273 | 8435 | 62848 | 456784 | 10/12 | 0/0 | 3 | 57.32 | True |

¹ 缓存是输入的子集，不相加。² 实际模型请求 wire UTF-8 字节累计，包含摘要；不是压缩后的资料正文大小。

第七轮各模型请求均有确认用量（账本 complete=true、unconfirmed=0）；四个失败运行的结果 usage.complete 保守记 false，不能把失败运行的部分成本当作完整任务成本。第六轮两个取消任务各有一个 unconfirmed 请求，未知费用不按零处理。

full 0/3 正文交付；projection 2/3 正文交付但 0/3 满足全部固定任务要求；compaction 3/3 正文交付、12/12 必需证据、12/12 约束、3/3 笔记。九格均实际读回全部必需固定资料，未请求外部固定源。设备 compaction 的额外摘要成本明显；财务 compaction 输入少于 projection，但延迟更长，不能宣称所有场景更快或更省。
