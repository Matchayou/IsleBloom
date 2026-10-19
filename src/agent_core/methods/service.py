"""Manual version publication and bounded compilation into the existing ResearchPlan."""
import asyncio
from ..execution.ports import Conflict
from ..resources.snapshots import ResourceRef
from ..research.models import NodeCommand, ResearchPlan, PlanStep
from ..routing.models import ExpertCandidate
from .models import AssetVersion, MethodBody, WorkflowBody, digest


class MethodService:
    def __init__(self, database, research):
        self.database,self.research = database,research
        self.locks = {}

    def query(self, name, *args):
        with self.database.transaction() as tx: return getattr(tx.methods,name)(*args)

    def resolve(self, category, asset_id, version=None):
        with self.database.transaction() as tx:
            version = version or tx.methods.head(category,asset_id)
            value = tx.methods.get(category,asset_id,version)
        if value is None: raise KeyError(f'{category}/{asset_id}/{version}')
        return value

    def save(self, value):
        value = AssetVersion.model_validate(value)
        if value.category == 'workflow':
            template = WorkflowBody.model_validate(value.body)
            if template.policy.max_runs < len(template.steps):
                raise ValueError('Workflow run budget is smaller than the step count')
            for step in template.steps:
                self.resolve('method',step.expert_id,step.method_version)
        with self.database.transaction() as tx: tx.methods.add(value)
        return value

    def publish(self, category, asset_id, version, expected, actor, reason, reviewed_candidate_id=None):
        if not actor.strip() or not reason.strip(): raise ValueError('Publisher and review evidence required')
        self.resolve(category,asset_id,version)
        with self.database.transaction() as tx:
            if 'evolution' in self.database.repositories:
                records = tx.evolution.list()
                candidate = next((r['value'] for r in records if r['type']=='Candidate' and
                    r['value']['expert_id']==asset_id and r['value']['version']==version),None)
                if candidate and reviewed_candidate_id != candidate['id']:
                    raise Conflict('Evolution candidate requires reviewed publication')
            tx.methods.activate(category,asset_id,version,expected,actor,reason)
        return self.resolve(category,asset_id,version)

    def catalog(self):
        values = self.query('list')
        return {'versions':[dict(**v.model_dump(mode='json'),digest=v.digest,
            active=self.query('head',v.category,v.asset_id)==v.version) for v in values],
            'releases':self.query('history')}

    def experts(self):
        values = self.query('list')
        return tuple(self.candidate(v) for v in values if v.category=='method' and
            self.query('head','method',v.asset_id)==v.version)

    def candidate(self, value):
        body = MethodBody.model_validate(value.body)
        return ExpertCandidate(id=value.asset_id,kind=body.kind,description=value.description,
            tools=body.tools,method_version=value.version,method_digest=body.method_digest)

    def require_published(self, value):
        if not any(r['category']==value.category and r['asset_id']==value.asset_id and r['version']==value.version for r in self.query('history')):
            raise Conflict('Version has not been published')

    def method(self, expert_id, version=None):
        value = self.resolve('method',expert_id,version)
        self.require_published(value)
        return value,MethodBody.model_validate(value.body)

    async def instantiate(self, *, instance_id, asset_id, version, research_id, parameters):
        async with self.locks.setdefault(instance_id,asyncio.Lock()):
            asset = self.resolve('workflow',asset_id,version)
            self.require_published(asset)
            template = WorkflowBody.model_validate(asset.body)
            if template.policy.max_runs < len(template.steps):
                raise ValueError('Workflow run budget is smaller than the step count')
            if set(parameters) != set(template.parameters): raise ValueError('Exact template parameters required')
            root = self.research.research(research_id)
            scope = self.research.scopes.current(root.scope_id,self.research.owner)
            refs = {}
            text = {}
            for name,kind in template.parameters.items():
                value = parameters[name]
                if kind == 'text':
                    if not isinstance(value,str) or not value.strip(): raise ValueError(f'{name} needs nonempty text')
                    text[name] = value
                else:
                    ref = ResourceRef.model_validate(value)
                    self.research.scopes.check(scope,ref)
                    self.research.scopes.mount(scope,ref)
                    refs[name] = ref
            signature = dict(asset_id=asset_id,version=version,research_id=research_id,parameters=parameters)
            fingerprint = digest(signature)
            old = self.query('instance',instance_id)
            if old:
                if old['fingerprint'] != fingerprint: raise Conflict('Instance ID reused with different parameters')
                return old
            # Validate all inputs before creating any nodes. Each create is idempotent, so
            # interrupted compilation can finish from the same ID without duplicate nodes.
            prepared = []
            for step in template.steps:
                method,body = self.method(step.expert_id,step.method_version)
                tools = body.tools & template.tools
                prepared.append((step,method,body,tools))
            nodes,bindings = {},{}
            for index,(step,method,body,tools) in enumerate(prepared):
                record = await self.research.submit(NodeCommand(command_id=f'template:{instance_id}:{step.key}',
                    research_id=research_id,kind='create',goal=step.goal.format_map(text),
                    instructions=body.prompt,resource_refs=tuple(refs[p] for p in step.source_parameters),
                    allowed_tools=tuple(sorted(tools)),position=(index*240,0)))
                nodes[step.key] = record.node_id
                bindings[record.node_id] = dict(expert_id=method.asset_id,method_version=method.version,
                    method_digest=body.method_digest,asset_digest=method.digest)
            plan = ResearchPlan(id=f'template:{instance_id}',research_id=research_id,policy=template.policy,
                steps=tuple(PlanStep(node_id=nodes[s.key],depends_on=tuple(nodes[d] for d in s.depends_on),limits=s.limits) for s in template.steps))
            result = dict(id=instance_id,**signature,fingerprint=fingerprint,template_digest=asset.digest,
                bindings=bindings,plan=plan.model_dump(mode='json'))
            with self.database.transaction() as tx: tx.methods.add_instance(result)
            return result

    def instance(self, key):
        value = self.query('instance',key)
        if value is None: raise KeyError(key)
        root = self.research.research(value['research_id'])
        scope = self.research.scopes.current(root.scope_id,self.research.owner)
        template = WorkflowBody.model_validate(self.resolve('workflow',value['asset_id'],value['version']).body)
        for name,kind in template.parameters.items():
            if kind == 'resource': self.research.scopes.check(scope,ResourceRef.model_validate(value['parameters'][name]))
        return value

    async def propose(self,key,origin_node_id=None):
        value = self.instance(key)
        root_scope=self.research.scopes.current(self.research.research(value['research_id']).scope_id,self.research.owner)
        allowed=set(root_scope.tools) | ({'search_source'} if 'read_source' in root_scope.tools else set())
        # Detect manual node edits; a compiled instance always executes the previewed inputs.
        template = WorkflowBody.model_validate(self.resolve('workflow',value['asset_id'],value['version']).body)
        for step,planned in zip(template.steps,value['plan']['steps']):
            node=self.research.node(planned['node_id'])
            _,body=self.method(step.expert_id,step.method_version)
            scope=self.research.scopes.current(node.scope_id,self.research.owner)
            if not set(scope.tools) <= body.tools & template.tools & allowed:
                raise PermissionError('Workflow tool permission changed; instantiate a new plan')
            if node.instructions != body.prompt or node.goal != step.goal.format_map(value['parameters']):
                raise Conflict('Compiled node was edited; instantiate a new workflow')
            expected=tuple(ResourceRef.model_validate(value['parameters'][p]) for p in step.source_parameters)
            if node.resource_refs != expected: raise Conflict('Compiled source bindings changed')
        return await self.research.planning.import_plan(ResearchPlan.model_validate(value['plan']), 'workflow:' + key, origin_node_id)
