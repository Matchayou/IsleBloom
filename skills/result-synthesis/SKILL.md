---
name: result-synthesis
description: 汇总报告、比较专家结果、综合已有结论及分歧。Use for report synthesis, comparing saved expert results and resolving conflicting conclusions.
allowed-tools: search_context read_context read_source search_source
---

根据 related_results 的目标与交付状态选择报告，通过 read_context 读取选定 result_ids 的正文。目录预览只用于选取报告，不当作完整结论。

整合时保留各报告的证据边界、假设和分歧。已校验的来源引用可复用，新增结论再补所需资料。专家交付通过复核不等于用户已采用。
