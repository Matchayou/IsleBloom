# 恢复方案真实执行验收

日期：2026-10-12。模型 deepseek-v4-pro。

用户确认恢复提案 7ebde8db-a948-4758-9551-c885958c927c 的版本 1；通过前端“确认并执行”提交，记录批准版本 1，后台进入 running。原提案 abc08601-5ea6-45c2-bd22-25ab4da4b71c 和结果 result:887ad94a-c028-4ba3-96da-46993988a306 保留。

## 实际执行结果

第一步运行 0493e159-04c6-438f-ad26-73dd962de3d6：10:44:12 启动，10:44:40 调用 search_web，10:44:44 完成；10:45:46 调用 fetch_web，10:45:57 完成；10:47:12 达到批准的 180 秒截止时间，保存 failed / timeout。受控账本保存 10 条 discovery 和 1 条 web ingestion；最终研究报告尚未生成。

已确认输入 15967、输出 8057，共 24024 tokens。5 个模型 operation 中 3 个 confirmed、2 个 unconfirmed，usage.complete=false；不能将 24024 当作最终总量，也不能盲目重发未确认请求。后两步为 waiting，没有执行。恢复提案终态 blocked，原批准时限和预算未被扩大。

## 保存与前端证明

服务在无活跃 writer 时重启，重新读取恢复提案、错误、用量、联网来源记录；全部保存。原第一步结果的完整 version 和 output 与首次保存的 JSON 逐项相同。原提案批准版本和 blocked 状态保持。

前端刷新后展示两个提案各自的状态；本轮第一步明确显示失败、步骤超过批准执行时限（timeout）、用量尚未完全确认，依赖步骤仍等待。截图 live-recovery-timeout.jpg。

新增回归 test_timed_out_step_exposes_error_and_blocks_dependents：失败错误可供前端读取，无成功结果ID，下游不运行。本轮规划测试 16 passed、18 skipped（未启用独立 PostgreSQL 测试实例，跳过其参数）；前端生产构建通过。此前阶段修复的 PostgreSQL 回归 86 passed、1 skipped。

## 验收结论

确认后启动、联网工具链、失败保存、保留上轮记录、重启与前端错误展示已验证。三步成功执行和最终报告保存尚未通过。继续研究需要更适合真实模型耗时的时限/预算方案，或更快的模型；修改后应绑定新版本重新确认。未确认模型用量需要先检查，不能自动创建完整预算恢复方案。
