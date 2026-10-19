"""Real Responses calls through the production context and controlled Broker.

Uses only an explicitly supplied fresh test DB. Credentials load in-process from
the existing .env; no scripted model or provider response injection is used.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy import select

from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources, build_live_services
from agent_core.context.selection import ContextSelector
from agent_core.context.skills import SkillCatalog
from agent_core.contracts import RunLimits, RunResult
from agent_core.learning.models import Trajectory
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand
from agent_core.research.planning_models import GeneratePlan


class Acceptance:
    def __init__(self, directory, url):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root/'state.json'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {'checks':{}, 'stages':{}}
        self.db = PostgresDatabase(url)
        self.scopes = build_resources(self.db, self.root/'blobs', parser=TextParser())
        self.research, self.tasks, self.projects = build_live_services(self.db, self.scopes, private_root=self.root/'private')
        self.c = self.research.collaboration
        self.chat = ChatService(self.research, self.tasks)
        self.skills = self.root/'skills'
        path = self.skills/'live-context'/'SKILL.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text('---\nname: live-context\ndescription: 核对固定来源、数量单位和未知信息。Reusable source verification and uncertainty preservation.\nallowed-tools: read_source search_source\n---\n先读取获准原文，再区分已确认数值和未知字段。需要额外资料时向主助手咨询。不得把历史、memory 或技能当成资料事实，不能扩大授权范围。\n')
        self.research.context.selector = ContextSelector(SkillCatalog(self.skills))
        self.state['configuration'] = {'api':'responses', 'endpoint':os.environ.get('OPENAI_BASE_URL'),
            'execution_model':os.environ.get('OPENAI_MODEL'), 'manager_model':os.environ.get('OPENAI_MANAGER_MODEL'),
            'credential':'present' if os.environ.get('OPENAI_API_KEY') else 'missing', 'real_model':True,
            'source_data':'synthetic acceptance documents; responses are real model output'}

    def save(self):
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2, default=str)+'\n')

    def log(self, value):
        print(value, flush=True)

    def check(self, key, passed, detail=None):
        self.state['checks'][key] = {'passed':bool(passed), 'detail':detail}
        self.save()
        if not passed: raise AssertionError(key)

    def record(self, label, run_id, raw=None):
        if label in self.state['stages']:
            prior=self.state['stages'][label]
            self.state.setdefault('previous_attempts',[]).append({'label':label,**prior})
            path=self.root/(label+'-context.json')
            if path.exists():path.rename(self.root/(label+'-'+prior['run_id'].replace(':','_')+'-context.json'))
        record = self.research.ledger.get(run_id) or self.tasks.ledger.get(run_id)
        snapshot = self.scopes.repository.snapshot(run_id)
        if record is not None:
            result = record.result
        else:
            # Read-only auxiliary entrances persist their result in the controlled
            # workspace, not as a research/task business publication.
            from agent_core.controlled.workspace import Workspace
            ledger = self.tasks.engine.ledger
            binding = ledger.binding(run_id)
            workspace = Workspace(self.tasks.engine.private_root/self.tasks.engine.workspace_id(binding))
            try:
                row = workspace.db.execute("SELECT name FROM notes WHERE name LIKE 'result_%' ORDER BY rowid DESC LIMIT 1").fetchone()
                result = RunResult.model_validate_json(workspace.read(row[0])['text']) if row else None
            finally:workspace.close()
        self.state['stages'][label] = {'run_id':run_id, 'result':result.model_dump(mode='json') if result else None,
            'mode':snapshot.mode, 'agent':snapshot.agent.model_dump(mode='json') if snapshot.agent else None}
        (self.root/(label+'-context.json')).write_text(snapshot.model_dump_json(indent=2))
        self.save()
        if raw is not None: self.check(label+':raw_user', snapshot.input.messages[-1].content == raw)
        self.check(label+':succeeded', result is not None and result.status == 'succeeded',
            result.error.model_dump(mode='json') if result and result.error else None)
        self.check(label+':confirmed_usage', result.usage.complete and result.usage.requests > 0, result.usage.model_dump())
        return result, snapshot

    async def preflight(self):
        root = self.research.create('Real model preflight '+uuid4().hex)
        thread = await self.chat.start(root.id, 'Real model preflight')
        raw = '  请只在 report 返回 LIVE_PREFLIGHT_MAIN。不调用工具，不派发专家，不编造其他信息。\n  '
        thread = self.chat.send(thread.node_id, raw, thread.revision)
        command = await self.research.submit(NodeCommand(command_id=uuid4().hex, research_id=root.id,
            node_id=thread.node_id, kind='chat_reply', chat_revision=thread.revision,
            limits=RunLimits(max_turns=3,max_calls=0,timeout_seconds=150)))
        self.record('preflight_main', command.run_id, raw)
        await self.preflight_execution()

    async def preflight_execution(self):
        root = self.research.create('Real execution model preflight '+uuid4().hex)
        raw = '  请只在 report 返回 LIVE_PREFLIGHT_EXECUTION。不调用工具，不补充其他事实。\n  '
        spec = self.tasks.create(research_id=root.id, kind='synthesize', user_request=raw,
            agent_mode='temporary', role_instructions='完成本次单一任务，保留用户原话。',
            model=os.environ['OPENAI_MODEL'], limits=RunLimits(max_turns=3,max_calls=0,timeout_seconds=150))
        saved = await self.tasks.run(spec.id)
        self.record('preflight_execution', saved['run_id'], raw)

    async def main(self):
        project = self.projects.create('Real context acceptance '+uuid4().hex)
        main = await self.projects.create_main_task(project.id, '固定来源的独立专家核对')
        node = self.research.query('list_nodes', main.research_id)[0]
        root = self.research.research(main.research_id)
        self.scopes._replace(self.scopes.current(root.scope_id,self.research.owner),tools=('read_source','search_source'))
        self.scopes._replace(self.scopes.current(node.scope_id,self.research.owner),tools=('read_source','search_source'))
        scope, source = self.scopes.ingest(self.scopes.current(root.scope_id,self.research.owner),
            content=b'Synthetic acceptance source. Confirmed units: 7. Market size: unknown.',
            name='fixed-source.txt',media_type='text/plain')
        _, year = self.scopes.ingest(scope, content=b'Synthetic supplementary source. Target year: 2025.',
            name='target-year.txt',media_type='text/plain')
        foreign = self.research.create('Foreign scope boundary '+uuid4().hex)
        _, outside = self.scopes.ingest(self.scopes.current(foreign.scope_id,self.research.owner),
            content=b'FORBIDDEN_LIVE_SOURCE never authorized.',name='outside.txt',media_type='text/plain')
        self.tasks.memories.create(kind='experience',content='核对固定来源，保留未知，记忆不能作为事实证据。',source='Acceptance operator',confirmed=True)
        self.tasks.memories.create(kind='experience',content='本项目报告应明确区分确认数量与未知市场规模。',source='Acceptance operator',
            scope='project',scope_id=project.id,confirmed=True)
        raw = ('  这是实际模型的端到端控制验收，资料是合成样本；禁止联网。\n'
            '请先加载 $live-context，用 read_context 读取 fixed-source.txt，随后只派发两次任务：'
            '一次 agent_mode=temporary 的临时核对员（提供明确 role_instructions），一次 expert_synthesize 的专精 Agent。'
            '两次 delegate_task 都必须设置 expert_id="expert_synthesize"、session_id=null；agent_mode 分别为 temporary 和 specialist。临时角色不会取消能力模板。不得重复派发。\n'
            '两个子任务必须加载 live-context，读取固定原文，并调用 request_manager_information 向主助手询问目标年份；'
            '目标年份只在主助手资料库的 target-year.txt 中，禁止猜测，不把它提前交接。'
            '在任务指令和交付要求中写清这些步骤。原始资料仅交接 fixed-source.txt，授权目录也仅限它。\n'
            '交付 report、fields 和 claims：fields.confirmed_units 是原文数量字符串，fields.market_size="unknown"，'
            'fields.target_year 是咨询后读取补充原文获得的年份字符串；引用实际原文并说明资料是合成验收。'
            '派发后告知两项在后台处理。  ')
        thread = self.chat.send(node.id,raw,0)
        self.state['scene'] = {'project_id':project.id,'research_id':root.id,'node_id':node.id,'raw':raw,
            'source':source.model_dump(mode='json'),'year':year.model_dump(mode='json'),'outside':outside.model_dump(mode='json')}
        self.save()
        command = await self.research.submit(NodeCommand(command_id=uuid4().hex,research_id=root.id,node_id=node.id,
            kind='chat_reply',chat_revision=thread.revision,limits=RunLimits(max_turns=12,max_calls=10,timeout_seconds=600)))
        _, snapshot = self.record('main',command.run_id,raw)
        intents = self.c.query('intents_for',root.id)
        self.state['intents'] = [i.command_id for i in intents];self.save()
        self.check('main:two_agents',len(intents)==2 and {self.tasks.spec(i.task_id).agent.mode for i in intents}=={'temporary','specialist'})
        self.check('main:outside_hidden','FORBIDDEN_LIVE_SOURCE' not in snapshot.input.model_dump_json())

    def called(self, run_id):
        binding = self.tasks.engine.ledger.binding(run_id)
        from agent_core.controlled.workspace import Workspace
        workspace = Workspace(self.tasks.engine.private_root/self.tasks.engine.workspace_id(binding))
        try:
            values=[json.loads(workspace.read(row[0])['text']) for row in workspace.db.execute(
                "SELECT name FROM notes WHERE name LIKE 'transcript_%_request' ORDER BY rowid")]
            return [v['payload']['name'] for v in values if v['operation'] in ('tool','private')]
        finally: workspace.close()

    def tool_results(self, run_id):
        from agent_core.controlled.workspace import Workspace
        binding=self.tasks.engine.ledger.binding(run_id)
        workspace=Workspace(self.tasks.engine.private_root/self.tasks.engine.workspace_id(binding))
        try:
            results=[]
            for (name,) in workspace.db.execute("SELECT name FROM notes WHERE name LIKE 'transcript_%_request' ORDER BY rowid").fetchall():
                request=json.loads(workspace.read(name)['text'])
                if request['operation'] not in ('tool','private'):continue
                response_name=name.removesuffix('_request')+'_response'
                if workspace.db.execute('SELECT 1 FROM notes WHERE name=?',(response_name,)).fetchone():
                    results.append({'name':request['payload']['name'],'arguments':request['payload']['arguments'],
                        'response':json.loads(workspace.read(response_name)['text'])})
            return results
        finally:workspace.close()

    async def experts(self):
        from agent_core.resources.snapshots import ResourceRef
        source = ResourceRef.model_validate(self.state['scene']['source'])
        year = ResourceRef.model_validate(self.state['scene']['year'])
        for command_id in self.state['intents']:
            intent = self.c.query('intent',command_id)
            spec = self.tasks.spec(intent.task_id)
            label = spec.agent.mode
            self.log('Running '+label)
            status = await self.c.execute(command_id)
            runs = self.tasks.query('list_runs',spec.id)
            self.check(label+':one_run',len(runs)==1)
            result,snapshot = self.record(label,runs[0].id,self.state['scene']['raw'])
            receipt = self.tasks.receipt('task-result:'+runs[0].id)
            self.state[label] = {'task_id':spec.id,'run_id':runs[0].id,'result_id':receipt.result_id,
                'called_tools':self.called(runs[0].id)};self.save()
            self.check(label+':handoff_raw',self.c.packet(spec.context_packet_id).user_instruction==self.state['scene']['raw'])
            self.check(label+':fixed_scope',spec.authorized_refs==(source,) and year not in snapshot.scope.allowed)
            self.check(label+':consultation', 'request_manager_information' in self.called(runs[0].id))
            self.check(label+':skill_load','load_skill' in self.called(runs[0].id))
            self.check(label+':fields',result.output.get('fields',{})=={'confirmed_units':'7','market_size':'unknown','target_year':'2025'},result.output.get('fields'))
            self.check(label+':actual_year_grant',year in receipt.resource_refs)
            for info in self.c.query('information_for',spec.id):
                for attempt in range(1,info.attempts+1):
                    info_run=info.id+f':manager:{attempt}'
                    if self.tasks.engine.ledger.binding(info_run):
                        self.record(label+'_consultation',info_run,self.state['scene']['raw'])
            if label=='temporary':self.check('temporary:no_trajectory',self.tasks.learning.get(Trajectory,runs[0].id) is None)
            else:self.check('specialist:trajectory',self.tasks.learning.get(Trajectory,runs[0].id) is not None)

    async def learning_source(self):
        project=self.projects.create('Independent domain acceptance '+uuid4().hex)
        main=await self.projects.create_main_task(project.id,'独立领域发布验收')
        node=self.research.query('list_nodes',main.research_id)[0]
        root=self.research.research(main.research_id)
        for scope_id in (root.scope_id,node.scope_id):
            self.scopes._replace(self.scopes.current(scope_id,self.research.owner),tools=('read_source','search_source'))
        scope,source=self.scopes.ingest(self.scopes.current(root.scope_id,self.research.owner),
            content=b'Synthetic acceptance source. Confirmed units: 7. Market size: unknown.',name='fixed-source.txt',media_type='text/plain')
        _,year=self.scopes.ingest(scope,content=b'Synthetic supplementary source. Target year: 2025.',name='target-year.txt',media_type='text/plain')
        raw='  这是独立的真实模型领域学习验收，资料为合成样本。两份获准原文已完整提供；请专精核对员读取它们，区分确认数量、未知市场规模和目标年份，以原文字符串交付字段并给出逐字引用。\n说明可重复的核对步骤和判断规则；不联网、不咨询、不派发其他任务。  '
        scene={'project_id':project.id,'research_id':root.id,'node_id':node.id,'raw':raw,
            'source':source.model_dump(mode='json'),'year':year.model_dump(mode='json')}
        self.chat.send(node.id,scene['raw'],0)
        self.state['learning_scene']=scene;self.save()
        refs=(source,year)
        intent=self.c.delegate_task(command_id=uuid4().hex,node_id=scene['node_id'],expert_id='expert_synthesize',
            task_instruction='核对合成验收资料：实际读取两个获准来源。报告确认数量、未知市场规模与目标年份，字段保持原文字符串。明确说明你如何区分事实和未知，如何依据获准原文交付逐字引用。此子任务资料已完整，不咨询、不联网、不派其他任务。',
            delivery_requirements='report、fields.confirmed_units、fields.market_size、fields.target_year 和真实 claims；简述可重复核对流程。',resource_refs=refs)
        spec=self.tasks.spec(intent.task_id)
        await self.c.execute(intent.command_id)
        run=self.tasks.query('list_runs',spec.id)[0]
        self.record('learning_source',run.id,scene['raw'])
        receipt=self.tasks.receipt('task-result:'+run.id)
        self.state['learning_source']={'task_id':spec.id,'run_id':run.id,'result_id':receipt.result_id};self.save()

    async def learning(self):
        row=self.state.get('learning_source',self.state['specialist']);learning=self.tasks.learning
        self.log('Generate quarantined domain candidate')
        status=await learning.process(row['run_id'])
        self.state['learning_before_review']={'state':status['state'],'candidate':status['candidate'].model_dump(mode='json') if status['candidate'] else None};self.save()
        self.check('learning:unpublished_before_review',status['state']=='candidate' and status['publication'] is None,status['state'])
        self.check('learning:reusable_candidate',status['candidate'].draft.memory is not None or status['candidate'].draft.skill is not None)
        spec=self.tasks.spec(row['task_id']);result=self.tasks.ledger.get(row['run_id']).result
        self.check('learning:unverified_hidden',not any(e.get('publication_id')==row['run_id'] for e in learning.skills(spec.agent))
            and not any(m.publication_id==row['run_id'] for m in self.tasks.memories.list(spec.research_id,agent=spec.agent)))
        self.check('learning:independent_source_fields',result.output['fields']=={'confirmed_units':'7','market_size':'unknown','target_year':'2025'})
        bundle=self.research.evidence.read(row['result_id'])
        from agent_core.resources.snapshots import ResourceRef
        scene=self.state.get('learning_scene',self.state['scene'])
        texts={ResourceRef.model_validate(scene['source']):'Synthetic acceptance source. Confirmed units: 7. Market size: unknown.',
            ResourceRef.model_validate(scene['year']):'Synthetic supplementary source. Target year: 2025.'}
        self.check('learning:source_evidence',bool(bundle.claims) and bool(bundle.evidence)
            and all(e.ref in texts and e.quote.strip() and e.quote in texts[e.ref] for e in bundle.evidence)
            and all(any(token in e.quote for e in bundle.evidence) for token in ('7','unknown','2025')))
        self.tasks.review(row['result_id'],{item.number:('passed',
            'Independent acceptance code verified literal fixed-source count, unknown size, supplemental year and validated citations.') for item in spec.acceptance},
            'live-acceptance-independent-code-review')
        self.log('Validate and publish domain candidate')
        status=await learning.process(row['run_id'])
        self.state['learning_after_review']={k:(v.model_dump(mode='json') if hasattr(v,'model_dump') else v)
            for k,v in status.items()};self.save()
        for stage in ('generate','verify'):
            from agent_core.learning.models import DomainCall
            value=learning.get(DomainCall,row['run_id']+':'+stage)
            if value:self.record('domain_'+stage,value.request.run_id,scene['raw'])
        self.check('learning:published',status['state']=='published',status['state'])
        self.check('learning:validation',status['validation'].passed)

    async def planning(self):
        scene=self.state['scene'];node=self.research.node(scene['node_id']);thread=self.chat.read(node.id)
        raw='  只生成一份两步计划：先核对固定来源，再总结确认数量和未知项；禁止联网，先不要执行。\n保留所有未知。  '
        proposal=self.research.planning.create(GeneratePlan(research_id=node.research_id,origin_node_id=node.id,
            expected_chat_revision=thread.revision,request_id=uuid4().hex,request=raw))
        proposal=await self.research.planning.generate(proposal.id)
        self.record('planning',proposal.planning_request.run_id,raw)
        self.check('planning:two_steps',len(proposal.nodes)==2)
        self.check('planning:awaiting_approval',proposal.status=='awaiting_confirmation',proposal.reason)
        self.check('planning:no_mutation_tools',not {'delegate_task','search_web','write_private_note'} & set(self.called(proposal.planning_request.run_id)))

    async def discussion(self):
        row=self.state['specialist'];thread=self.projects.expert_thread(row['task_id'])
        raw='  用固定报告解释确认数量、年份来自哪里，哪些结论仍未知。只讨论，不重新执行或修改任务。\n保留原话。  '
        before=len(self.tasks.query('list_runs',row['task_id']))
        thread=await self.projects.discuss(row['task_id'],raw,thread.revision)
        self.record('discussion',thread.messages[-1].run_id,raw)
        self.check('discussion:no_rerun',len(self.tasks.query('list_runs',row['task_id']))==before)

    async def retrieval(self):
        scene=self.state.get('learning_scene',self.state['scene']);row=self.state.get('learning_source',self.state['specialist'])
        from agent_core.resources.snapshots import ResourceRef
        source=ResourceRef.model_validate(scene['source'])
        candidate,publication=self.tasks.learning.available(row['run_id'])
        self.check('retrieval:both_published_forms',publication.memory_id is not None and publication.skill_name is not None)
        raw='  前一轮两份资料的完整核对已经完成。本轮仅复核 fixed-source.txt 的确认数量和未知市场规模，并复用已经验证发布的领域经验。\n只交付 confirmed_units 和 market_size 两个字段及真实引用；无需年份，不读取补充年份资料，不咨询、不联网、不派其他任务。  '
        instruction=(f'本轮只核对固定原文的确认数量与未知字段。这是领域经验复用验收：先 load_skill name={publication.skill_name}，'
            f'再 search_memory scope=specialist query={json.dumps(candidate.draft.memory[:100],ensure_ascii=False)}，应用相关流程，但 memory/skill 不是事实证据；'
            '读取获准来源后仅交付 report、fields.confirmed_units、fields.market_size 和真实引用，字段保持原文字符串。不交付年份。禁止联网，不咨询，不派其他任务。')
        existing=[i for i in self.c.query('intents_for',scene['research_id']) if self.tasks.spec(i.task_id).user_request==raw]
        if not existing:
            thread=self.chat.read(scene['node_id'])
            self.chat.send(scene['node_id'],raw,thread.revision)
        self.state['retrieval_raw']=raw;self.save()
        intent=existing[0] if existing else self.c.delegate_task(command_id=uuid4().hex,node_id=scene['node_id'],expert_id='expert_synthesize',
            task_instruction=instruction,delivery_requirements='使用已验证发布的领域经验核对固定来源，保留未知。',resource_refs=(source,))
        spec=self.tasks.spec(intent.task_id)
        if existing:
            run=self.tasks.query('list_runs',spec.id)[0]
            frozen=self.scopes.repository.snapshot(run.id)
            await self.tasks.run_durable(spec.id,spec.version,run.id)
            self.check('retrieval:frozen_resume',self.scopes.repository.snapshot(run.id)==frozen)
        else:
            await self.c.execute(intent.command_id)
            run=self.tasks.query('list_runs',spec.id)[0]
        result,snapshot=self.record('retrieval',run.id,raw)
        self.check('retrieval:published_skill_candidate',any(e['name']==publication.skill_name for e in snapshot.skill_catalog))
        self.check('retrieval:published_memory_catalog',publication.memory_id in {m.id for m in snapshot.memory_catalog})
        calls=self.called(run.id);self.check('retrieval:actual_load',{'load_skill','search_memory','read_source'}<=set(calls),calls)
        responses=self.tool_results(run.id)
        (self.root/'retrieval-tools.json').write_text(json.dumps(responses,ensure_ascii=False,indent=2))
        self.check('retrieval:memory_returned',any(t['name']=='search_memory' and any(
            m['id']==publication.memory_id for m in t['response'].get('value',{}).get('memories',[])) for t in responses))
        self.check('retrieval:skill_body_returned',any(t['name']=='load_skill' and
            t['response'].get('value',{}).get('skill',{}).get('name')==publication.skill_name and
            bool(t['response']['value']['skill'].get('instructions')) for t in responses))
        self.check('retrieval:source_fields',result.output.get('fields',{})=={'confirmed_units':'7','market_size':'unknown'})
        self.check('retrieval:no_extra_information',not {'request_manager_information','search_web','fetch_web'} & set(calls))

    def audit(self):
        ledger=self.tasks.engine.ledger
        with self.db.transaction():
            runs=self.db.connection.execute(select(ledger.runs)).mappings().all()
            manifests=self.db.connection.execute(select(ledger.manifests)).mappings().all()
            operations=self.db.connection.execute(select(ledger.operations)).mappings().all()
        evidence={'runs':[dict(v) for v in runs],'manifests':[dict(v) for v in manifests],'operations':[dict(v) for v in operations]}
        (self.root/'audit.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str))
        self.state['audit']={'controlled_runs':len(runs),'operations':len(operations),
            'responded_models':sum(m['state']=='responded' for m in manifests),
            'http_statuses':[m['manifest'].get('response_metadata',{}).get('http_status') for m in manifests if m['state']=='responded']}
        self.save()


async def main(args):
    load_dotenv('.env',override=False)
    if not os.environ.get('OPENAI_API_KEY'):raise RuntimeError('Configured model credential is missing')
    if not args.database.startswith('postgresql+psycopg://') or '/ce2_live_' not in args.database:
        raise ValueError('Use a dedicated ce2_live_ PostgreSQL database')
    acceptance=Acceptance(args.output,args.database)
    try:
        stages=('preflight','main','experts','learning_source','learning','planning','discussion','retrieval') if args.stage=='all' else (args.stage,)
        for stage in stages:
            acceptance.log('START '+stage)
            try:
                await getattr(acceptance,stage)()
                acceptance.log('PASS '+stage)
            except Exception as exc:
                failure={'type':type(exc).__name__,'message':str(exc)[:500]}
                acceptance.state.setdefault('failures',[]).append({'stage':stage,**failure})
                acceptance.state['stages'][stage+'_failure']=failure
                acceptance.save();acceptance.log('FAIL '+stage+': '+type(exc).__name__+': '+str(exc)[:200])
                raise
    finally:
        acceptance.audit()
        acceptance.db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--stage',choices=('all','preflight','preflight_execution','main','experts','learning_source','learning','planning','discussion','retrieval'),default='all')
    asyncio.run(main(parser.parse_args()))
