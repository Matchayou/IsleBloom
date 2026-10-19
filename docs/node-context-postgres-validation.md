# 普通节点 PostgreSQL 验证与原 52 项跳过清单

原结果为 60 passed / 52 skipped，跳过没有证明持久化或受控执行通过。下面逐项保留原跳过用例的身份。

## 所需环境与补齐方式

- 本机 PostgreSQL 17.11（Homebrew 已安装），Python 3.12、项目 .venv；psycopg、SQLAlchemy、openai-agents、DBOS 均已安装，无新增包。
- 使用独立集群 `/private/tmp/agent-node-pg-20261012`，只监听 `127.0.0.1:55473`。初始化/启动需要操作系统共享内存和本机监听权限；沙箱下 initdb 被共享内存权限阻止，授权后的执行成功。
- `TEST_DATABASE_URL`：研究节点与规划持久化；`CE2_TEST_DATABASE_URL`：独立的 ce2_ 前缀库，真实 ControlledLedger、Broker、SDK 和子进程恢复；`SECOND_TEST_DATABASE_URL`：验证不同数据库的进程所有权锁相互独立。
- 资料 blob 和受控 private workspace 也必须保留才能跨进程恢复，数据库本身不包含这些文件的全部内容。
- 本轮模型使用 SDK Model 测试提供器，无需在线模型密钥；验证真实工具、Broker 和 PostgreSQL 控制链，不证明在线模型输出质量。

## 可复现运行

集群已初始化，若停止可运行：

```sh
pg_ctl -D /private/tmp/agent-node-pg-20261012 -l /private/tmp/agent-node-pg-20261012.log -o '-h 127.0.0.1 -p 55473 -k /private/tmp' start
sh scripts/verify_node_context_postgres.sh
```

脚本每次创建三个全新数据库，不复用已有业务库；输出 JUnit 到 `docs/node-context-evidence/results.xml`，并断言 failures/errors/skipped 均为零。测试数据库保留用于审计。

## 发现并修复的问题

1. 专精回执提交事务内再次读取 ContextSnapshot，PostgreSQL 拒绝嵌套 begin。现在在事务外读取不可变快照，事务内仍一起保存回执、轨迹和证据，不增加嵌套事务兼容层。
2. 受控 search_tools 原目录只包含业务工具，没有包含运行时获准的私有笔记工具。Broker 现在用真实业务工具与获准私有工具的合并目录执行发现；Runner 去除重复绑定，发现调用仍经过 Channel/Broker、权限校验、持久预算与操作审计。
3. 部分旧 CE2 测试直接调用尚未加载的工具，已改为先 search_tools 再调用，并保留原并发、取消、私有隔离和公开报告断言。
4. 规划重开测试原先参数化出一个无法证明持久化的 memory 用例，现在仅参数化 PostgreSQL，直接执行真实重开验证，未将 memory 用例计为通过。

## 新增验证

`tests/test_node_controlled_postgres.py` 验证普通节点无聊天输入，经真实 ControlledEngine、SDK、Channel/Broker 加载 skill、搜索项目 memory、拒绝目录外读取；确认四次模型、三次工具和一次结果操作写入 PostgreSQL。随后关闭连接，在独立 Python 进程中恢复完全相同的输入、选择记录、skill/memory 目录与已发布报告。

同文件对专精回执和轨迹注入事务内写入后失败，确认回执、轨迹和 commit 一起回滚；恢复保存时不重放模型。

## 原跳过清单

### 研究节点：16 项；原缺 TEST_DATABASE_URL

- `tests/test_research.py::test_branches_versions_compare_layout_idempotency[postgres]`
- `tests/test_research.py::test_resource_revocation_mixed_excluded_inputs[postgres]`
- `tests/test_research.py::test_cancel_swallowed_late_result_cannot_publish[postgres]`
- `tests/test_research.py::test_atomic_publication_failure_and_save_retry[postgres]`
- `tests/test_research.py::test_plan_waves_and_partial[postgres]`
- `tests/test_research.py::test_retry_fixed_input_and_queue[postgres]`
- `tests/test_research.py::test_scope_switch_retry_and_publish_permission[postgres]`
- `tests/test_research.py::test_company_period_mismatch_rejected_before_model[postgres]`
- `tests/test_research.py::test_http_product_loop[postgres]`
- `tests/test_research.py::test_partial_plan_does_not_consume_old_success[postgres]`
- `tests/test_research.py::test_two_product_scenarios[postgres]`
- `tests/test_research.py::test_result_only_branch_has_discovery_without_source_access[postgres]`
- `tests/test_research.py::test_sdk_read_tool_research_atomic_boundary[postgres]`
- `tests/test_research.py::test_after_commit_response_loss_is_not_second_execution[postgres]`
- `tests/test_research.py::test_plan_identity_budget_and_resource_replay[postgres]`
- `tests/test_research.py::test_model_missing_is_partial_and_propagates[postgres]`

### 规划：20 项；原缺 TEST_DATABASE_URL

- `tests/test_planning.py::test_generate_confirm_execute_and_replay[postgres]`
- `tests/test_planning.py::test_questions_revision_and_old_confirm[postgres]`
- `tests/test_planning.py::test_stale_conversation_and_protected_step[postgres]`
- `tests/test_planning.py::test_planner_cannot_dispatch[postgres]`
- `tests/test_planning.py::test_partial_upstream_blocks_downstream[postgres]`
- `tests/test_planning.py::test_limitations_allow_three_steps_and_preserve_blocked_history[postgres]`
- `tests/test_planning.py::test_recovery_carries_completed_report_and_validated_evidence[postgres]`
- `tests/test_planning.py::test_cancel_generation_fences_late_output[postgres]`
- `tests/test_planning.py::test_api_old_entry_removed_and_explicit_confirmation[postgres]`
- `tests/test_planning.py::test_confirm_transaction_rolls_back_and_snapshot_is_fixed[postgres]`
- `tests/test_planning.py::test_restart_after_confirmation_does_not_lose_queue[postgres]`
- `tests/test_planning.py::test_restart_between_waves_reuses_saved_result[postgres]`
- `tests/test_planning.py::test_revoked_input_blocks_confirmation_and_generation[postgres]`
- `tests/test_planning.py::test_main_planner_sees_authorized_library_without_mutating_origin[postgres]`
- `tests/test_planning.py::test_real_database_reopen_preserves_approval_queue[postgres]`
- `tests/test_planning.py::test_unconfirmed_step_recovery_stops_without_model_replay[postgres]`
- `tests/test_planning.py::test_approved_execution_explicitly_supersedes_historical_planning_stage[postgres]`
- `tests/test_planning.py::test_recovery_preserves_failure_and_requires_fresh_confirmation[postgres]`
- `tests/test_planning.py::test_timed_out_step_exposes_error_and_blocks_dependents[postgres]`
- `tests/test_planning.py::test_planning_reuses_fetched_body_without_mounting_discovery_snippets[postgres]`

### CE2 受控链路：15 项；原缺 CE2_TEST_DATABASE_URL

- `tests/test_controlled_collaboration.py::test_four_entrances_consultation_and_followup_use_same_broker`
- `tests/test_controlled_collaboration.py::test_two_experts_overlap_and_have_distinct_private_workspaces`
- `tests/test_controlled_collaboration.py::test_revocation_cancels_waiting_model_and_quarantines_old_generation`
- `tests/test_controlled_collaboration.py::test_report_handoff_requires_manager_read_and_fixed_authorization[True]`
- `tests/test_controlled_collaboration.py::test_report_handoff_requires_manager_read_and_fixed_authorization[False]`
- `tests/test_controlled_collaboration.py::test_tool_cannot_read_peer_private_note`
- `tests/test_controlled_collaboration.py::test_duplicate_event_delivered_then_consumed_survives_new_process`
- `tests/test_controlled_collaboration.py::test_dbos_waiting_information_wakes_without_expert_model_polling`
- `tests/test_controlled_collaboration.py::test_automatic_source_acquisition_does_not_revoke_generation`
- `tests/test_controlled_collaboration.py::test_sigkill_keeps_information_event_and_resumes_at_saved_boundary[False]`
- `tests/test_controlled_collaboration.py::test_sigkill_keeps_information_event_and_resumes_at_saved_boundary[True]`
- `tests/test_controlled_collaboration.py::test_manager_delegates_two_experts_and_resumes_with_public_reports_only`
- `tests/test_controlled_collaboration.py::test_revoked_generation_is_hidden_from_api_and_discussion`
- `tests/test_controlled_collaboration.py::test_existing_method_generation_and_preview_use_broker`
- `tests/test_controlled_collaboration.py::test_product_live_composition_has_no_uncontrolled_model_path`

### 内存重开：1 项；后端能力不适用

- `tests/test_planning.py::test_real_database_reopen_preserves_approval_queue[memory]`

原原因为 `Reopen proof requires PostgreSQL`。该内存参数已移除，相应 `[postgres]` 用例仍执行，验证审批队列重开恢复。

## 最终结果

完整回归：**133 passed，0 failures，0 errors，0 skipped**（105.97 秒）。原先缺配置的 51 个数据库用例均实际执行；1 个不适用的 memory 重开参数移除，真实 PostgreSQL 重开用例执行通过。

普通节点跨进程读取、专精回执/轨迹事务回滚与保存重试均通过。PostgreSQL 服务真实停止/重启前后，快照数量和内容哈希、受控运行/操作数量、领域记录数量完全相同。重启证据见 `node-context-evidence/before-postgres-restart.json` 与 `after-postgres-restart.json`；完整逐项结果见 `results.xml`。

本次只使用隔离测试库，未连接业务库。完成验证后停止隔离集群，保留数据库文件和证据；复现时按前述命令启动。
