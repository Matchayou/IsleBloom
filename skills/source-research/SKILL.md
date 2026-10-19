---
name: source-research
description: 查找资料、检索论文、搜索网页并读取相关正文，为研究问题提供可定位的证据。Use for source research, paper search, web discovery and evidence extraction.
allowed-tools: search_context read_context search_source read_source search_web fetch_web
---

先检查本轮 sources 中相关资料，再用 search_context 查找获准目录。已有正文用 search_source 聚焦定位；全文抓取不是全文已读。

确需新资料时用 search_web 找候选，再用 fetch_web 获取正文；长文提供聚焦 query。来源摘要与发现目录只能用于选择正文。

引用仅使用正文工具实际返回的完整 ref、location 和原文。资料缺口影响结论时明确指出。任务范围和当前用户约束优先。
