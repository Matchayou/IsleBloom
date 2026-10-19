"""Isolated method evolution: verified development lessons, frozen previews, gated release."""
import json
from ..contracts import AgentSpec, Message, PreparedInput, RunRequest, RunLimits
from ..controlled.admission import admit
from ..core import AgentCatalog, CoreRunner
from ..context.execution import ScopeExecutionGate
from ..context.tool_guard import ScopedTool
from ..execution.ports import Conflict
from ..methods.models import AssetVersion, MethodBody
from ..evaluation.service import digest
from ..resources.snapshots import identity
from ..tasks.models import ExpertAnswer
from ..tasks.service import checks
from .models import Candidate, CandidateDraft, Preview, Assessment


class EvolutionService:
    def __init__(self, database, tasks, evaluation):
        self.database, self.tasks, self.evaluation = database, tasks, evaluation
        self.methods = tasks.methods

    def _get(self, key, model):
        with self.database.transaction() as tx: row = tx.evolution.get(key)
        if row is None or row['type'] != model.__name__: raise KeyError(key)
        value = model.model_validate(row['value'])
        if model is Candidate: self.evaluation._access(value.research_id, ())
        elif model is Preview: self.candidate(value.candidate_id); self.evaluation.case(value.case_id)
        elif model is Assessment: self.preview(value.preview_id)
        return value

    def _add(self, value):
        with self.database.transaction() as tx:
            tx.evolution.add(f'{type(value).__name__}:{value.id if hasattr(value,"id") else value.preview_id}',
                {'type':type(value).__name__,'value':value.model_dump(mode='json')})
        return value

    def candidate(self, key): return self._get('Candidate:'+key,Candidate)
    def preview(self, key): return self._get('Preview:'+key,Preview)
    def assessment(self, key): return self._get('Assessment:'+key,Assessment)

    def list_candidates(self, research_id):
        self.evaluation._access(research_id, ())
        with self.database.transaction() as tx: rows=tx.evolution.list()
        return [self.candidate(r['value']['id']) for r in rows if r['type']=='Candidate' and r['value']['research_id']==research_id]

    async def propose(self, *, candidate_id, expert_id, development_case_ids, holdout_case_ids,
                      approved_lesson, lesson_reviewer, generator_model,
                      quality_floor, min_quality_gain, max_total_cost_usd):
        if not approved_lesson.strip() or not lesson_reviewer.strip(): raise ValueError('Reviewed reusable lesson required')
        if not candidate_id.strip() or not generator_model.strip() or not (0 <= quality_floor <= 1) or not (0 <= min_quality_gain <= 1) or max_total_cost_usd <= 0:
            raise ValueError('Candidate identity, model and frozen gates required')
        with self.database.transaction() as tx:
            if tx.evolution.get('Candidate:'+candidate_id): raise Conflict('Candidate ID reused')
        cases = tuple(self.evaluation.case(key) for key in development_case_ids)
        holdouts = tuple(self.evaluation.case(key) for key in holdout_case_ids)
        if not cases or len({c.id for c in cases}) != len(cases): raise Conflict('Distinct development cases required')
        if not holdouts or len({c.id for c in holdouts}) != len(holdouts): raise Conflict('Distinct holdout cases required')
        if any(c.split != 'development' or c.expert_id != expert_id or c.attribution != 'method'
               for c in cases): raise Conflict('Only verified method-attributed development cases may generate a candidate')
        if len({c.research_id for c in cases}) != 1: raise Conflict('Candidate cases need one research')
        if any(c.split != 'holdout' or c.expert_id != expert_id or c.research_id != cases[0].research_id for c in holdouts):
            raise Conflict('Frozen holdout cases must belong to the same expert and research')
        base = self.methods.resolve('method',expert_id)
        self.methods.require_published(base)
        if any(c.method_version != base.version or c.method_digest != MethodBody.model_validate(base.body).method_digest for c in (*cases,*holdouts)):
            raise Conflict('Frozen cases must use the active base method')
        # The model sees the reviewed abstraction only. Frozen inputs, labels and holdout IDs stay outside this prompt.
        prompt = json.dumps({'expert':expert_id,'kind':base.body['kind'],
            'current_instructions':base.body['instructions'],'current_examples':base.body['examples'],
            'approved_reusable_lesson':approved_lesson.strip(),
            'allowed_change':'Return JSON with instructions and examples only. Preserve task, tools, permissions and output contract.'},ensure_ascii=False)
        if self.tasks.engine is None: raise ValueError('Evolution execution engine is not configured')
        engine = self.tasks.engine
        from ..agent_identity import AgentIdentity
        run_id = identity()
        generation_scope = self.tasks.scopes.create(self.tasks.research.owner, tools=())
        snapshot = self.tasks.research.context.assemble(self.tasks.scopes, generation_scope, run_id,
            'Improve reviewed method lesson', agent=AgentIdentity(mode='assistant', id='method_generator'), mode='learning',
            original_message=Message(role='user', content=approved_lesson),
            project={'research_id':cases[0].research_id}, refs=(),
            business={'generator_context':json.loads(prompt)})
        request = RunRequest(run_id=run_id, agent_id='method_generator', input=snapshot.input,
            limits=RunLimits(max_calls=0))
        runner = CoreRunner(engine,AgentCatalog([AgentSpec('method_generator',
            'Improve only the reusable method instructions and examples. Return the requested JSON; do not include case-specific facts.',
            output_type=CandidateDraft,model=generator_model)]))
        def validate_generation():
            if self.methods.resolve('method', expert_id).digest != base.digest:
                raise Conflict('Generator base method changed')
            for case in (*cases, *holdouts): self.evaluation.case(case.id)
        with admit(request, research_id=cases[0].research_id, session_id='candidate:'+candidate_id,
                   task_id='candidate:'+candidate_id, spec_version=1, kind='learning',
                   validate=validate_generation):
            result = await runner.execute(request)
        if result.status != 'succeeded': raise Conflict(f'Candidate generation failed: {result.error.code if result.error else result.status}')
        draft = CandidateDraft.model_validate(result.output)
        body = MethodBody.model_validate({**base.body,'instructions':draft.instructions,'examples':draft.examples})
        if body == MethodBody.model_validate(base.body): raise Conflict('Candidate must change the method')
        version = f'evolution-{candidate_id}'
        asset = AssetVersion(category='method',asset_id=expert_id,version=version,
            description=f'Candidate improvement for {expert_id}',body=body.model_dump(mode='json'),
            author='method-generator',evidence=f'Verified development cases: {", ".join(development_case_ids)}; reviewed lesson by {lesson_reviewer}',sanitized=True)
        value = Candidate(id=candidate_id,research_id=cases[0].research_id,expert_id=expert_id,
            base_version=base.version,version=version,development_case_ids=tuple(development_case_ids),
            holdout_case_ids=tuple(holdout_case_ids),
            lesson=approved_lesson.strip(),lesson_reviewer=lesson_reviewer.strip(),
            generator_prompt=prompt,generator_model=generator_model,
            generator_usage=result.usage.model_dump(mode='json'),quality_floor=quality_floor,
            min_quality_gain=min_quality_gain,max_total_cost_usd=max_total_cost_usd,
            asset_digest=asset.digest)
        with self.database.transaction() as tx:
            if tx.evolution.get('Candidate:'+candidate_id): raise Conflict('Candidate ID reused')
            tx.methods.add(asset)
            tx.evolution.add('Candidate:'+candidate_id,{'type':'Candidate','value':value.model_dump(mode='json')})
        return value

    async def run_preview(self, *, preview_id, candidate_id, case_id, arm, upstream_preview_id=None):
        candidate = self.candidate(candidate_id)
        case = self.evaluation.case(case_id)
        if case.research_id != candidate.research_id: raise Conflict('Foreign preview case')
        if arm in ('before','after'):
            if case_id not in (*candidate.development_case_ids,*candidate.holdout_case_ids):
                raise Conflict('Case is outside the frozen candidate split')
            if upstream_preview_id or case.expert_id != candidate.expert_id or case.method_version != candidate.base_version:
                raise Conflict('Paired preview must use the candidate expert and fixed base')
        elif arm == 'downstream':
            upstream = self.preview(upstream_preview_id)
            if upstream.candidate_id != candidate_id or upstream.arm != 'after': raise Conflict('Downstream needs this candidate output')
            if case.split != 'holdout': raise Conflict('Downstream must be a holdout case')
            spec = self.tasks.spec(case.task_id,case.spec_version)
            up_case = self.evaluation.case(upstream.case_id)
            if up_case.split != 'holdout': raise Conflict('Downstream preview must consume a holdout preview')
            if up_case.result_id not in spec.required_results:
                raise Conflict('Downstream case does not consume the candidate upstream result')
        else: raise ValueError('Unknown preview arm')
        version = candidate.version if arm == 'after' else (case.method_version if arm == 'before' else case.method_version)
        asset = self.methods.resolve('method',case.expert_id,version)
        if arm == 'after' and asset.digest != candidate.asset_digest: raise Conflict('Candidate asset changed')
        body = MethodBody.model_validate(asset.body)
        spec = self.tasks.spec(case.task_id,case.spec_version)
        binding = self.tasks.query('run',case.run_id)
        snapshot = self.tasks.scopes.repository.snapshot(case.run_id)
        if binding is None or snapshot is None or digest(snapshot.input.model_dump(mode='json')) != case.input_digest:
            raise Conflict('Frozen input changed')
        self.evaluation._access(case.research_id,case.resource_refs,spec)
        scope = self.tasks.scopes.repository.scope(snapshot.scope_id)
        self.tasks.scopes.valid(scope)
        for ref in binding.resource_refs: self.tasks.scopes.check(scope,ref)
        if set(scope.tools) - set(body.tools): raise Conflict('Method tool contract narrowed')
        prepared = snapshot.input
        if arm == 'downstream':
            payload = json.loads(prepared.messages[0].content)
            changed = 0
            for result in payload.get('results',[]):
                if result['mount']['result_id'] == up_case.result_id:
                    result['output'] = upstream.output
                    changed += 1
            if changed != 1: raise Conflict('Frozen downstream handoff missing')
            prepared = PreparedInput(messages=(Message(role='user',content=json.dumps(payload,ensure_ascii=False)),))
        run_id = identity()
        clone = snapshot.model_copy(update={'id':identity(),'run_id':run_id,'input':prepared})
        self.tasks.scopes.repository.save_snapshot(clone)
        tools = []
        scoped = ScopedTool(self.tasks.scopes,scope)
        if 'read_source' in scope.tools and binding.resource_refs: tools.append(scoped.read_binding())
        if 'search_source' in scope.tools and binding.resource_refs and spec.evidence_required: tools.append(scoped.search_binding())
        agent = AgentSpec(case.expert_id,body.prompt + ' 输出 JSON: report、fields、sources、assumptions、conflicts、missing、limitations、follow_up、self_checks、claims。自检只供参考，系统独立检查。仅使用本任务输入。',
            tools=tuple(tools),output_type=ExpertAnswer,model=case.model)
        if self.tasks.engine is None: raise ValueError('Evolution execution engine is not configured')
        runner = ScopeExecutionGate(CoreRunner(self.tasks.engine,
            AgentCatalog([agent])),self.tasks.scopes)
        request = RunRequest(run_id=run_id,agent_id=case.expert_id,input=prepared,limits=spec.limits)
        def validate_preview():
            self.candidate(candidate_id)
            self.evaluation.case(case_id)
            self.evaluation._access(case.research_id, case.resource_refs, spec)
            self.tasks.scopes.valid(scope)
            self.tasks.memories.validate(case.research_id, snapshot.memory_catalog, agent=snapshot.agent)
            self.tasks.learning.validate_skills(snapshot.skill_catalog, snapshot.agent)
            for ref in binding.resource_refs: self.tasks.scopes.check(scope, ref)
        with admit(request, research_id=case.research_id, session_id='preview:'+preview_id,
                   task_id=spec.id, spec_version=spec.version, kind='preview', validate=validate_preview):
            result = await runner.execute(request)
        if result.status != 'succeeded': raise Conflict(f'Preview run failed: {result.error.code if result.error else result.status}')
        answer = ExpertAnswer.model_validate(result.output)
        output = answer.model_dump(mode='json')
        program,_,_ = checks(spec,result)
        preview = Preview(id=preview_id,candidate_id=candidate_id,case_id=case_id,arm=arm,
            method_version=version,method_digest=body.method_digest,
            input_digest=digest(prepared.model_dump(mode='json')),upstream_preview_id=upstream_preview_id,
            output=output,output_digest=digest(output),usage=result.usage.model_dump(mode='json'),
            program_passed=all(c.status=='passed' for c in program),contract_passed=True)
        return self._add(preview)

    def assess(self, *, preview_id, passed, scope_passed, reviewer, evidence, cost_usd, cost_evidence, cost_basis):
        preview = self.preview(preview_id)
        if not reviewer.strip() or not evidence.strip() or not cost_evidence.strip(): raise ValueError('Independent review and cost evidence required')
        value = Assessment(preview_id=preview_id,passed=passed,scope_passed=scope_passed,
            reviewer=reviewer,cost_usd=cost_usd,cost_basis=cost_basis,evidence=evidence,cost_evidence=cost_evidence)
        return self._add(value)

    def compare(self, candidate_id):
        candidate = self.candidate(candidate_id)
        with self.database.transaction() as tx: rows=tx.evolution.list()
        previews = [Preview.model_validate(r['value']) for r in rows if r['type']=='Preview' and r['value']['candidate_id']==candidate_id]
        by_case = {}
        duplicated=False
        for p in previews:
            if p.arm in ('before','after'):
                arms=by_case.setdefault(p.case_id,{})
                if p.arm in arms: duplicated=True
                arms[p.arm]=p
        splits = {'development':[],'holdout':[]}
        complete = True
        for case_id,arms in by_case.items():
            case=self.evaluation.case(case_id)
            if case.expert_id != candidate.expert_id or case.method_version != candidate.base_version:
                raise Conflict('Compared case changed expert or method')
            if set(arms) != {'before','after'}: complete=False;continue
            splits[case.split].append(arms)
        if duplicated or set(by_case) != set((*candidate.development_case_ids,*candidate.holdout_case_ids)) or not splits['holdout']:
            complete=False
        assessments={}
        for p in previews:
            try: assessments[p.id]=self.assessment(p.id)
            except KeyError: complete=False
        downstream=[p for p in previews if p.arm=='downstream']
        if not downstream: complete=False
        verdict='incomplete'
        metrics={}
        if complete:
            for split,pairs in splits.items():
                before=sum(assessments[p['before'].id].passed for p in pairs)/len(pairs)
                after=sum(assessments[p['after'].id].passed for p in pairs)/len(pairs)
                metrics[split]={'before_quality':before,'after_quality':after,'gain':after-before}
            cost=sum(assessments[p.id].cost_usd for p in previews)
            hard_scope=all(p.contract_passed and p.program_passed and assessments[p.id].scope_passed for p in previews if p.arm!='before')
            hard_quality=all(m['after_quality']>=candidate.quality_floor and m['gain']>=candidate.min_quality_gain for m in metrics.values())
            hard_integration=all(assessments[p.id].passed and assessments[p.id].scope_passed for p in downstream)
            hard_cost=all(assessments[p.id].cost_basis=='measured' for p in previews) and cost<=candidate.max_total_cost_usd
            verdict='passed' if hard_scope and hard_quality and hard_integration and hard_cost else 'failed'
            metrics['total_cost_usd']=cost
            metrics['scope_passed']=hard_scope
            metrics['integration_passed']=hard_integration
            metrics['cost_passed']=hard_cost
        return {'candidate':candidate,'complete':complete,'metrics':metrics,'verdict':verdict,
            'previews':previews,'assessments':assessments}

    def publish(self, candidate_id, *, expected, user_actor, confirmation):
        candidate=self.candidate(candidate_id)
        if not user_actor.strip() or confirmation != f'Publish {candidate.expert_id}/{candidate.version}':
            raise Conflict('Explicit user confirmation of the exact version required')
        report=self.compare(candidate_id)
        if report['verdict']!='passed': raise Conflict('Complete passing development, holdout and integration evidence required')
        if expected != candidate.base_version: raise Conflict('Candidate expected base changed')
        return self.methods.publish('method',candidate.expert_id,candidate.version,expected,
            user_actor,f'Stage 10 confirmed candidate {candidate_id}',reviewed_candidate_id=candidate_id)

    def rollback(self, candidate_id, *, expected, user_actor, reason):
        candidate=self.candidate(candidate_id)
        if expected != candidate.version or not user_actor.strip() or not reason.strip():
            raise Conflict('Explicit rollback of current candidate version required')
        return self.methods.publish('method',candidate.expert_id,candidate.base_version,expected,user_actor,reason)
