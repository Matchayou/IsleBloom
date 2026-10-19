"""Feedback adjudication and frozen, paired offline evaluations."""
import hashlib
import json
from ..execution.ports import Conflict
from ..context.scopes import ScopeDenied
from ..resources.snapshots import identity
from ..research.chat import ChatService
from .models import Feedback, FrozenCase, Experiment, Trial


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


class EvaluationService:
    def __init__(self, database, tasks):
        self.database, self.tasks = database, tasks

    def query(self, name, *args):
        with self.database.transaction() as tx: return getattr(tx.evaluation, name)(*args)

    def _access(self, research_id, refs, spec=None):
        research = self.tasks.research.research(research_id)
        scope = self.tasks.scopes.current(research.scope_id, self.tasks.research.owner)
        for ref in refs: self.tasks.scopes.check(scope, ref)
        if spec:
            for message in spec.chat_context:
                ChatService(self.tasks.research, self.tasks)._validate_message(
                    research_id, message, spec.node_id)

    def _bound_result(self, result_id):
        receipt = self.tasks.receipt(result_id)
        spec = self.tasks.spec(receipt.task_id, receipt.spec_version)
        if result_id != f'task-result:{receipt.run_id}': raise Conflict('Result/run mismatch')
        binding = self.tasks.query('run', receipt.run_id)
        if binding is None or (binding.task_id, binding.spec_version, binding.model,
            binding.method_digest, binding.route_id) != (spec.id, spec.version, receipt.model,
            receipt.method_digest, receipt.route_id):
            raise Conflict('Frozen task binding mismatch')
        self._access(spec.research_id, receipt.resource_refs, spec)
        execution = self.tasks.ledger.get(receipt.run_id)
        if execution is None or execution.result is None: raise Conflict('Result not committed')
        return spec, receipt, execution.result

    def submit(self, *, feedback_id, result_id, kind, description, proposed_correction=None):
        spec, receipt, _ = self._bound_result(result_id)
        value = Feedback(id=feedback_id, result_id=result_id, research_id=spec.research_id,
            task_id=spec.id, spec_version=spec.version, run_id=receipt.run_id,
            expert_id=spec.expert_id, method_version=receipt.method_version,
            method_digest=receipt.method_digest, model=receipt.model, route_id=receipt.route_id,
            resource_refs=receipt.resource_refs, kind=kind,
            description=description.strip(), proposed_correction=proposed_correction)
        with self.database.transaction() as tx:
            old = tx.evaluation.feedback(feedback_id)
            if old:
                if old.model_copy(update={'status':'pending','attribution':None,'finding':None,
                    'reviewer':None,'acceptance_number':None}) != value:
                    raise Conflict('Feedback ID reused with different content')
                return old
            tx.evaluation.add_feedback(value)
        return value

    def feedback(self, feedback_id):
        value = self.query('feedback', feedback_id)
        if value is None: raise KeyError(feedback_id)
        self._access(value.research_id, value.resource_refs,
            self.tasks.spec(value.task_id, value.spec_version))
        return value

    def list_feedback(self, research_id):
        self._access(research_id, ())
        values = self.query('list_feedback', research_id)
        visible = []
        for value in values:
            try: self.feedback(value.id)
            except (ScopeDenied, KeyError, Conflict): continue
            visible.append(value)
        return visible

    def adjudicate(self, feedback_id, *, status, attribution, finding, reviewer,
                   acceptance_number=None):
        old = self.feedback(feedback_id)
        if old.status != 'pending': raise Conflict('Feedback already adjudicated')
        if status not in ('verified','rejected') or not finding.strip() or not reviewer.strip():
            raise ValueError('Decision, finding and reviewer required')
        if status == 'verified' and old.kind != 'preference' and not attribution:
            raise ValueError('Verified factual assessment needs attribution')
        if old.kind == 'verified_example' and attribution not in ('reference','checker'):
            raise ValueError('Verified correct example uses reference or checker assessment')
        if old.kind == 'fact_error' and attribution == 'reference':
            raise ValueError('A factual error needs a failure attribution')
        if old.kind == 'preference' and (attribution is not None or acceptance_number is not None):
            raise ValueError('Preference cannot be attributed as a factual failure')
        if acceptance_number is not None:
            spec = self.tasks.spec(old.task_id, old.spec_version)
            if acceptance_number not in {item.number for item in spec.acceptance}:
                raise ValueError('Unknown acceptance item')
        if attribution == 'checker' and acceptance_number is None:
            raise ValueError('Checker attribution needs an acceptance item')
        if status == 'verified' and attribution == 'checker':
            receipt = self.tasks.receipt(old.result_id)
            check = next(c for c in receipt.program_checks if c.number == acceptance_number)
            expected = 'passed' if old.kind == 'fact_error' else 'failed'
            if check.status != expected:
                raise Conflict('Checker attribution requires a program/verified-label disagreement')
        value = old.model_copy(update=dict(status=status, attribution=attribution,
            finding=finding.strip(), reviewer=reviewer.strip(), acceptance_number=acceptance_number))
        with self.database.transaction() as tx: tx.evaluation.save_feedback(value, old)
        return value

    def freeze(self, feedback_id, *, split, dimension):
        feedback = self.feedback(feedback_id)
        if feedback.status != 'verified' or feedback.kind == 'preference':
            raise Conflict('Only verified factual assessments can become cases')
        if dimension == 'checker' and feedback.acceptance_number is None:
            raise Conflict('Checker case needs a numbered check')
        spec, receipt, result = self._bound_result(feedback.result_id)
        snapshot = self.tasks.scopes.repository.snapshot(receipt.run_id)
        if snapshot is None: raise Conflict('Frozen input missing')
        value = FrozenCase(id=feedback_id, feedback_id=feedback_id,
            result_id=feedback.result_id, research_id=feedback.research_id,
            task_id=feedback.task_id, spec_version=feedback.spec_version,
            run_id=feedback.run_id, expert_id=feedback.expert_id,
            method_version=feedback.method_version, method_digest=feedback.method_digest,
            model=feedback.model, route_id=feedback.route_id,
            resource_refs=feedback.resource_refs, split=split, dimension=dimension,
            input_digest=digest(snapshot.input.model_dump(mode='json')),
            output_digest=digest(result.model_dump(mode='json')),
            label=feedback.finding, original_assessment='incorrect' if feedback.kind == 'fact_error' else 'correct',
            attribution=feedback.attribution,
            acceptance_number=feedback.acceptance_number)
        def source_hashes(refs):
            return {self.tasks.scopes.resources.describe(ref)[1].blob_hash for ref in refs}
        hashes = source_hashes(value.resource_refs)
        for existing in self.query('list_cases',feedback.research_id):
            if existing.id == value.id: continue
            if (existing.result_id == value.result_id and
                    existing.original_assessment != value.original_assessment):
                raise Conflict('One result cannot have contradictory frozen factual labels')
            if existing.split != split and (existing.task_id == value.task_id or
                    hashes & source_hashes(existing.resource_refs)):
                raise Conflict('Development and holdout cannot share a task or source content')
        with self.database.transaction() as tx:
            for existing in tx.evaluation.list_cases(feedback.research_id):
                if existing.id == value.id: continue
                if existing.split != split and (existing.task_id == value.task_id or
                        set(existing.resource_refs) & set(value.resource_refs)):
                    raise Conflict('Development and holdout cannot share a task or source version')
            tx.evaluation.add_case(value)
        return value

    def case(self, case_id):
        value = self.query('case', case_id)
        if value is None: raise KeyError(case_id)
        self._access(value.research_id, value.resource_refs,
            self.tasks.spec(value.task_id, value.spec_version))
        return value

    def create_experiment(self, *, experiment_id, case_ids, arms, quality_floor,
                          max_total_cost_usd, min_quality_gain=0, candidate_pool=(),
                          task_split_digest=None):
        cases = tuple(self.case(key) for key in case_ids)
        if not cases: raise ValueError('At least one labeled case required')
        if len({c.research_id for c in cases}) != 1 or len({c.split for c in cases}) != 1 or len({c.dimension for c in cases}) != 1:
            raise Conflict('Experiment cases must share research, split and dimension')
        def task_shape(case):
            spec = self.tasks.spec(case.task_id, case.spec_version)
            return (spec.kind, spec.goal,
                [ref.model_dump(mode='json') for ref in spec.required_refs], spec.required_results)
        actual_split = digest(tuple(task_shape(case) for case in cases))
        if task_split_digest is not None and task_split_digest != actual_split:
            raise Conflict('Task split digest differs from frozen cases')
        task_split_digest = actual_split
        canonical = dict(research_id=cases[0].research_id, split=cases[0].split,
            dimension=cases[0].dimension, case_ids=tuple(case_ids), arms=tuple(arms),
            candidate_pool=tuple(candidate_pool), task_split_digest=task_split_digest,
            quality_floor=quality_floor, max_total_cost_usd=max_total_cost_usd,
            min_quality_gain=min_quality_gain)
        value = Experiment(id=experiment_id, **canonical, frozen_digest=digest(canonical))
        with self.database.transaction() as tx: tx.evaluation.add_experiment(value)
        return value

    def experiment(self, experiment_id):
        value = self.query('experiment',experiment_id)
        if value is None: raise KeyError(experiment_id)
        for key in value.case_ids: self.case(key)
        return value

    def history_context(self, case_ids, candidate_pool, dimension):
        """Share aggregate, permission-checked development labels without source text."""
        history = [self.case(key) for key in case_ids]
        if not history or len({c.id for c in history}) != len(history):
            raise Conflict('History needs distinct frozen cases')
        counts = {candidate:[0,0] for candidate in candidate_pool}
        for case in history:
            if case.split != 'development' or case.dimension != dimension:
                raise Conflict('History must be verified development cases of the same dimension')
            candidate = case.expert_id if dimension == 'expert_choice' else case.model
            if candidate not in counts:
                raise Conflict('Historical candidate outside frozen pool')
            counts[candidate][1] += 1
            counts[candidate][0] += case.original_assessment == 'correct'
        if any(total == 0 for _,total in counts.values()):
            raise Conflict('History lacks a verified label for a candidate')
        summary = '; '.join(f'{key}: {good}/{total} correct' for key,(good,total) in counts.items())
        return f'[verified-development-history {digest(tuple(sorted(case_ids)))}] {summary}'

    def history_choice(self, case_ids, candidate_pool, dimension):
        self.history_context(case_ids,candidate_pool,dimension)
        counts = {candidate:[0,0] for candidate in candidate_pool}
        for key in case_ids:
            case = self.case(key)
            candidate = case.expert_id if dimension == 'expert_choice' else case.model
            counts[candidate][1] += 1
            counts[candidate][0] += case.original_assessment == 'correct'
        return max(candidate_pool,key=lambda key: counts[key][0]/counts[key][1])

    def record_trial(self, *, trial_id, experiment_id, case_id, result_id, arm,
                     score, reviewer, evidence, cost_usd=None, cost_basis='unknown', latency_ms=None,
                     history_case_ids=(), cost_evidence=None):
        experiment = self.experiment(experiment_id)
        if case_id not in experiment.case_ids or arm not in experiment.arms:
            raise Conflict('Trial outside frozen experiment')
        case = self.case(case_id)
        spec, receipt, result = self._bound_result(result_id)
        if spec.research_id != experiment.research_id:
            raise ScopeDenied('Foreign evaluation result')
        original = self.tasks.spec(case.task_id, case.spec_version)
        if (spec.kind, spec.goal, spec.required_refs, spec.required_results, spec.acceptance) != (
            original.kind, original.goal, original.required_refs, original.required_results, original.acceptance):
            raise Conflict('Compared run changed the frozen task or inputs')
        if arm == 'before' and result_id != case.result_id:
            raise Conflict('Before arm must use original result')
        if result_id == case.result_id and score != ('passed' if case.original_assessment == 'correct' else 'failed'):
            raise Conflict('Trial contradicts the frozen original label')
        if set(experiment.arms) == {'fixed','rule','routellm','history','routellm_history'}:
            route = self.tasks.query('route', receipt.route_id) if receipt.route_id else None
            chosen = (route.selected_expert if experiment.dimension == 'expert_choice' else
                route.selected_model) if route else None
            if route is None or route.status != 'selected' or chosen not in experiment.candidate_pool:
                raise Conflict('Route trial needs a selected candidate in the frozen pool')
            selection_step = next((step for step in route.steps if step.stage == (
                'expert' if experiment.dimension == 'expert_choice' else 'model')), None)
            if selection_step is None or set(selection_step.eligible) != set(experiment.candidate_pool):
                raise Conflict('Route candidate pool differs across experimental arms')
            for prior in self.query('list_trials', experiment_id):
                if prior.case_id != case_id: continue
                prior_receipt = self.tasks.receipt(prior.result_id)
                prior_route = self.tasks.query('route', prior_receipt.route_id)
                fixed = 'selected_model' if experiment.dimension == 'expert_choice' else 'selected_expert'
                if getattr(prior_route, fixed) != getattr(route, fixed):
                    raise Conflict('Route experiment changed the non-tested selection stage')
            if arm in ('routellm','routellm_history') and not any(step.stage == 'model' and
                    step.source == 'routellm' for step in route.steps):
                raise Conflict('RouteLLM trial needs an actual RouteLLM model decision')
            if arm in ('history','routellm_history'):
                if not history_case_ids: raise Conflict('History arm needs frozen development cases')
                for historical_id in history_case_ids:
                    historical = self.case(historical_id)
                    if (historical.split != 'development' or historical.research_id != case.research_id or
                            historical_id in experiment.case_ids):
                        raise Conflict('History evidence leaks evaluation cases or uses foreign data')
                context = self.history_context(history_case_ids,experiment.candidate_pool,
                    experiment.dimension)
                if context not in route.summary:
                    raise Conflict('Route decision did not receive the frozen history context')
                if arm == 'history' and chosen != self.history_choice(history_case_ids,
                        experiment.candidate_pool,experiment.dimension):
                    raise Conflict('History-only choice disagrees with verified development labels')
            elif history_case_ids:
                raise Conflict('Non-history arm cannot use historical cases')
            elif '[verified-development-history ' in route.summary:
                raise Conflict('Non-history arm received historical evidence')
        elif history_case_ids:
            raise Conflict('Paired before/after comparison has no history arm')
        if experiment.dimension == 'checker':
            if case.acceptance_number not in {c.number for c in receipt.program_checks}:
                raise Conflict('Compared checker item is absent')
        for prior in self.query('list_trials', experiment_id):
            if (prior.case_id,prior.arm) == (case_id,arm) and prior.id != trial_id:
                raise Conflict('Case and arm already scored')
        value = Trial(id=trial_id, experiment_id=experiment_id, case_id=case_id,
            result_id=result_id, arm=arm, score=score, reviewer=reviewer,
            evidence=evidence, cost_usd=cost_usd, cost_basis=cost_basis,
            cost_evidence=cost_evidence, latency_ms=latency_ms,
            history_case_ids=tuple(history_case_ids))
        with self.database.transaction() as tx: tx.evaluation.add_trial(value)
        return value

    def compare(self, experiment_id):
        experiment = self.experiment(experiment_id)
        trials = self.query('list_trials', experiment_id)
        for trial in trials: self._bound_result(trial.result_id)
        by_pair = {(v.case_id,v.arm):v for v in trials}
        complete = all((case,arm) in by_pair for case in experiment.case_ids for arm in experiment.arms)
        metrics = {}
        for arm in experiment.arms:
            values = [by_pair[(case,arm)] for case in experiment.case_ids if (case,arm) in by_pair]
            known = bool(values) and all(v.cost_usd is not None for v in values)
            metrics[arm] = dict(scored=len(values), quality=sum(v.score == 'passed' for v in values)/len(values) if values else None,
                total_cost_usd=sum(v.cost_usd for v in values) if known else None,
                cost_basis='measured' if known and all(v.cost_basis == 'measured' for v in values) else
                    ('estimated' if known else 'unknown'),
                total_latency_ms=sum(v.latency_ms for v in values) if values and all(v.latency_ms is not None for v in values) else None)
        verdict = 'incomplete'
        history_gain = None
        if complete:
            if all(v['total_cost_usd'] is not None for v in metrics.values()):
                if 'after' in experiment.arms:
                    baseline = metrics['before']['quality']
                    improved_arm = 'after'
                else:
                    baseline = metrics['routellm']['quality']
                    improved_arm = 'routellm_history'
                    history_gain = {'history_vs_rule':metrics['history']['quality']-metrics['rule']['quality'],
                        'routellm_history_vs_routellm':metrics['routellm_history']['quality']-baseline}
                improved = metrics[improved_arm]['quality']
                passed = (improved >= experiment.quality_floor and
                    improved - baseline >= experiment.min_quality_gain and
                    metrics[improved_arm]['total_cost_usd'] <= experiment.max_total_cost_usd)
                if not passed: verdict = 'failed'
                elif all(v['cost_basis'] == 'measured' for v in metrics.values()): verdict = 'passed'
                else: verdict = 'provisional_estimated_cost'
            else: verdict = 'cost_unknown'
        return {'experiment':experiment,'complete':complete,'metrics':metrics,'verdict':verdict,
            'history_gain':history_gain,'online_history_enabled':False}
