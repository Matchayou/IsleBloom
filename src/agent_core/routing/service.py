"""Deterministic expert admission and RouteLLM model selection."""
import math
import time
from ..resources.snapshots import identity
from .models import RouteRecord, RouteStep


class RouteBlocked(Exception):
    def __init__(self, record):
        self.record = record
        super().__init__(record.reason)


class Router:
    def __init__(self, experts, models, model_router, *, catalog_version,
                 policy_version='routellm-bert-v1'):
        self.experts = {e.id: e for e in experts}
        self.models = {m.id: m for m in models}
        if len(self.experts) != len(experts) or len(self.models) != len(models):
            raise ValueError('Catalog IDs must be unique')
        self.model_router = model_router
        if model_router is not None and model_router.strong_model == model_router.weak_model:
            raise ValueError('RouteLLM model pair must be distinct')
        self.catalog_version, self.policy_version = catalog_version, policy_version

    async def route(self, *, research_id, task_id=None, spec_version=1, approved_summary,
                    kind, input_chars, required_tools=(),
                    preferred_expert=None, preferred_model=None, budget_usd=None,
                    expected_output_tokens=1000, spent_usd=0, resource_refs=(),
                    minimum_rank=0, trigger=None, previous=()):
        task_id = task_id or identity()
        steps = []
        selected_expert = selected_model = None
        if any(record.task_id != task_id for record in previous):
            raise ValueError('Foreign route history')
        if previous and any(record.catalog_version != self.catalog_version or
                            record.policy_version != self.policy_version or
                            record.router_checkpoint != (self.model_router.checkpoint_path
                                if self.model_router else None) or
                            record.router_threshold != (self.model_router.threshold
                                if self.model_router else None)
                            for record in previous):
            raise ValueError('Task route policy or catalog changed')
        for past in previous:
            if past.expert_config and self.experts.get(past.expert_config.id) != past.expert_config:
                raise ValueError('Task expert catalog changed under the same version')
            if (past.selected_model_config and
                    self.models.get(past.selected_model_config.id) != past.selected_model_config):
                raise ValueError('Task model catalog changed under the same version')

        def record(reason=None, cost=None):
            return RouteRecord(task_id=task_id, spec_version=spec_version, research_id=research_id,
                status='blocked' if reason else 'selected', reason=reason, trigger=trigger,
                summary=approved_summary, policy_version=self.policy_version,
                catalog_version=self.catalog_version,
                router_version='routellm-bert-0.2.0' if self.model_router else 'single-model',
                router_checkpoint=self.model_router.checkpoint_path if self.model_router else None,
                router_threshold=self.model_router.threshold if self.model_router else None,
                input_chars=input_chars, resource_refs=tuple(resource_refs),
                required_tools=tuple(sorted(required_tools)),
                budget_usd=budget_usd, spent_usd=spent_usd,
                estimated_execution_usd=cost, steps=tuple(steps),
                selected_expert=selected_expert, selected_model=selected_model,
                expert_config=self.experts.get(selected_expert),
                selected_model_config=self.models.get(selected_model))

        def block(stage, ids, excluded, state, criteria, reason):
            steps.append(RouteStep(stage=stage, eligible=ids, excluded=excluded,
                state=state, criteria=criteria, source='blocked'))
            raise RouteBlocked(record(reason))

        tools = frozenset(required_tools)
        if budget_usd is None or not math.isfinite(budget_usd) or budget_usd <= 0:
            raise ValueError('Routed task requires a positive execution budget')
        if previous and any(r.budget_usd != budget_usd for r in previous):
            raise ValueError('Task route budget cannot change across attempts')
        remaining_usd = max(0, budget_usd - spent_usd)

        def estimated_cost(model):
            return (input_chars / 4 * model.input_usd_per_million_tokens +
                    expected_output_tokens * model.output_usd_per_million_tokens) / 1_000_000

        expert_eligible, expert_excluded = {}, {}
        for expert in self.experts.values():
            reason = None
            if expert.kind != kind: reason = 'task kind mismatch'
            elif not tools <= expert.tools: reason = 'required tools unavailable'
            elif not any(m.available and kind in m.supported_kinds and
                         (preferred_model is None or m.id == preferred_model) and
                         tools <= m.tools and input_chars <= m.max_input_chars and
                         estimated_cost(m) <= remaining_usd
                         for m in self.models.values()):
                reason = 'no compatible model within capacity and budget'
            if reason: expert_excluded[expert.id] = reason
            else: expert_eligible[expert.id] = expert
        expert_ids = tuple(expert_eligible)
        expert_state = {'task': approved_summary, 'kind': kind, 'required_tools': sorted(tools)}
        expert_criteria = {key: value.description for key, value in expert_eligible.items()}
        if preferred_expert is not None:
            if preferred_expert not in expert_eligible:
                block('expert', expert_ids, expert_excluded, expert_state, expert_criteria,
                      f'User specified expert is ineligible: {preferred_expert}')
            selected_expert, expert_source = preferred_expert, 'user'
        elif not expert_ids:
            block('expert', expert_ids, expert_excluded, expert_state, expert_criteria,
                  'No eligible expert candidates')
        elif len(expert_ids) == 1:
            selected_expert, expert_source = expert_ids[0], 'only_candidate'
        else:
            default_expert = f'expert_{kind}'
            if default_expert not in expert_eligible:
                block('expert', expert_ids, expert_excluded, expert_state, expert_criteria,
                      'Default expert is ineligible; select an expert explicitly')
            selected_expert, expert_source = default_expert, 'policy'
        steps.append(RouteStep(stage='expert', eligible=expert_ids, excluded=expert_excluded,
            state=expert_state, criteria=expert_criteria, source=expert_source,
            selected=selected_expert))

        expert = expert_eligible[selected_expert]
        model_eligible, model_excluded = {}, {}
        for model in self.models.values():
            reason = None
            if not model.available: reason = 'unavailable'
            elif kind not in model.supported_kinds: reason = 'task kind unsupported'
            elif model.capability_rank < minimum_rank: reason = 'continuation downgrade forbidden'
            elif not tools <= model.tools: reason = 'required tools unsupported'
            elif input_chars > model.max_input_chars: reason = 'input exceeds capacity'
            elif estimated_cost(model) > remaining_usd:
                reason = 'estimated cost exceeds remaining budget'
            if reason: model_excluded[model.id] = reason
            else: model_eligible[model.id] = model
        ids = tuple(model_eligible)
        state = {'task': approved_summary, 'expert': selected_expert,
                 'method_version': expert.method_version, 'input_chars': input_chars}
        criteria = {key: value.description for key, value in model_eligible.items()}
        if preferred_model is not None:
            if preferred_model not in model_eligible:
                block('model', ids, model_excluded, state, criteria,
                      f'User specified model is ineligible: {preferred_model}')
            selected_model, source = preferred_model, 'user'
        elif not ids:
            block('model', ids, model_excluded, state, criteria,
                  'No eligible model candidates')
        elif len(ids) == 1:
            selected_model, source = ids[0], 'only_candidate'
        else:
            if self.model_router is None:
                block('model', ids, model_excluded, state, criteria,
                      'Multiple eligible models require RouteLLM configuration')
            pair = {self.model_router.strong_model, self.model_router.weak_model}
            if set(ids) != pair:
                block('model', ids, model_excluded, state, criteria,
                      'Eligible models do not match the RouteLLM pair')
            began = time.monotonic()
            try:
                selected_model = await self.model_router.choose(approved_summary)
                if selected_model not in model_eligible:
                    raise ValueError('RouteLLM returned an ineligible model')
            except Exception as exc:
                steps.append(RouteStep(stage='model', eligible=ids, excluded=model_excluded,
                    state=state, criteria=criteria, source='routellm',
                    elapsed_ms=round((time.monotonic() - began) * 1000)))
                raise RouteBlocked(record(f'RouteLLM model decision failed: {type(exc).__name__}')) from None
            source = 'routellm'
            steps.append(RouteStep(stage='model', eligible=ids, excluded=model_excluded,
                state=state, criteria=criteria, source=source, selected=selected_model,
                raw={'router': 'bert', 'threshold': self.model_router.threshold,
                     'checkpoint_path': self.model_router.checkpoint_path},
                elapsed_ms=round((time.monotonic() - began) * 1000)))
            return record(cost=estimated_cost(model_eligible[selected_model]))
        steps.append(RouteStep(stage='model', eligible=ids, excluded=model_excluded,
            state=state, criteria=criteria, source=source, selected=selected_model))
        return record(cost=estimated_cost(model_eligible[selected_model]))
