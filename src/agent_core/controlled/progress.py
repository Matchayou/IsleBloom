"""Bounded investigation within the existing SDK/broker loop, not another runner."""
from datetime import datetime, timezone
from .ledger import digest

WAIT_TOOLS = frozenset(('request_manager_information','ask_collaborator','get_task_status'))


class ProgressPolicy:
    def __init__(self, ledger, request, *, plan_budget=None):
        self.ledger, self.request = ledger, request
        self.plan_budget = plan_budget
        self.reserve_turns = 2 if request.limits.max_turns is None or request.limits.max_turns >= 4 else 1
        # Two delivery turns (answer + bounded correction) own half the deadline.
        # This scales with long research runs instead of a fixed 30-second cap.

    @property
    def reserve_seconds(self):
        # Observations survive checkpoints and exclude summary-model latency.
        durations = self.ledger.task_model_durations(self.request.run_id)
        total = self.request.limits.timeout_seconds
        return min(total * .75, max(total / 2, max(durations, default=0) * self.reserve_turns))

    def remaining_seconds(self):
        row = self.ledger.run_budget(self.request.run_id)
        return max(0, (row['deadline']-datetime.now(timezone.utc)).total_seconds())

    def prepare_model(self):
        row=self.ledger.run_budget(self.request.run_id)
        state=self.ledger.progress(self.request.run_id)
        if state['phase']=='researching':
            token_pressure = False
            if self.plan_budget:
                observation = self.ledger.input_observation(self.request.run_id)
                if observation:
                    from .model_projection import OUTPUT_TOKENS,MARGIN_TOKENS
                    used = self.ledger.plan_usage(self.plan_budget['node_ids'])
                    remaining_tokens = self.plan_budget['max_tokens']-used.input_tokens-used.output_tokens
                    token_pressure = remaining_tokens <= self.reserve_turns * (
                        observation['input_tokens']+OUTPUT_TOKENS+MARGIN_TOKENS)
            reason = ('reserved_turns' if self.request.limits.max_turns is not None and self.request.limits.max_turns-row['models']<=self.reserve_turns else
                'tool_budget' if row['tools']>=self.request.limits.max_calls else
                'reserved_tokens' if token_pressure else
                'reserved_time' if self.remaining_seconds()<=self.reserve_seconds else None)
            if reason:self.ledger.set_phase(self.request.run_id,'finalizing',reason)
        return self.ledger.progress(self.request.run_id)

    def after_tool(self, name, arguments, result):
        if isinstance(arguments,str):
            import json
            try: arguments=json.loads(arguments)
            except json.JSONDecodeError:
                # Core has returned invalid_input without executing the tool.
                # Fingerprint the failed attempt as-is; progress accounting must
                # not turn correctable argument feedback into a terminal error.
                if not isinstance(result,dict) or result.get('status') != 'invalid_input': raise
        value={k:v for k,v in result.items() if k!='remaining_tool_calls'} if isinstance(result,dict) else result
        fingerprint=digest({'tool':name,'arguments':arguments,'result':value})
        return self.ledger.record_progress(self.request.run_id,fingerprint,waiting=name in WAIT_TOOLS)

    def finish(self,result):
        if result.status=='succeeded' and self.ledger.progress(self.request.run_id)['phase']=='researching':
            self.ledger.set_phase(self.request.run_id,'finalizing','model_completed')
        self.ledger.set_phase(self.request.run_id,'delivered' if result.status=='succeeded' else 'incomplete',
            'validated_output' if result.status=='succeeded' else result.error.code if result.error else result.status)


FINAL_INSTRUCTION = ('\n执行阶段：finalizing。调查已结束；当前任务是依据已保存证据交付最终结果与明确缺口。'
    '不再计划下一次调查，不请求或描述新的工具调用。工具调用标签（包括DSML）不是交付。'
    '严格按当前输出schema提交JSON，无代码围栏；按字段说明区分阻断缺项与允许的局限。blocking_missing只表示必需交付未完成，不包含允许的未知实测值；允许的研究局限列入limitations（若schema有此字段）。')


class FinalizeAfterTimeout(Exception):
    """Continue only the known SDK input, with no tools and the original budget."""
    def __init__(self, input, remaining_turns):
        self.input, self.remaining_turns = input, remaining_turns
        super().__init__('Investigation timed out; use reserved finalization budget')
