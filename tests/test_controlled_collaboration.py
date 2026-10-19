"""CE2 real PostgreSQL + real SDK; deterministic Model fixtures, NOT live LLM evidence."""
import asyncio
import json
import os
from collections import deque
from uuid import uuid4

import pytest
from agents import ModelResponse
from agents.usage import Usage as SDKUsage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
from sqlalchemy import select
from sqlalchemy.engine import make_url

from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_controlled_services
from agent_core.context.scopes import ScopeDenied
from agent_core.controlled.workspace import Workspace
from agent_core.contracts import RunLimits
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand


def answer(value):
    return [ResponseOutputMessage(id=uuid4().hex, type='message', role='assistant', status='completed',
        content=[ResponseOutputText(type='output_text', text=value if isinstance(value, str)
            else json.dumps(value), annotations=[])])]


def call(tool_name, **args):
    return [ResponseFunctionToolCall(type='function_call', name=tool_name, call_id=uuid4().hex,
        arguments=json.dumps(args))]


class Provider:
    """Supplies SDK Model responses, never bypasses the controlled engine or broker."""
    def __init__(self):
        self.plans = deque()
        self.requests = []

    def get_model(self, name):
        plan = deque(self.plans.popleft())
        provider = self
        class Model:
            async def get_response(self, **kwargs):
                provider.requests.append(kwargs)
                step = plan.popleft()
                if callable(step):
                    step = await step(kwargs)
                return ModelResponse(output=step, usage=SDKUsage(requests=1, input_tokens=3, output_tokens=4),
                    response_id=None)
        return Model()


@pytest.fixture
def controlled(tmp_path):
    url = os.environ.get('CE2_TEST_DATABASE_URL')
    if not url:
        pytest.skip('CE2 dedicated PostgreSQL required; no persistence substitute')
    assert make_url(url).database.startswith('ce2_')
    db = PostgresDatabase(url)
    provider = Provider()
    scopes = build_resources(db, tmp_path / 'blobs')
    research, tasks, projects = build_controlled_services(db, scopes,
        private_root=tmp_path / 'private', model_provider=provider)
    yield db, scopes, research, tasks, projects, research.collaboration, provider
    db.close()


async def seed(controlled):
    _, scopes, research, tasks, projects, _, _ = controlled
    project = projects.create('CE2 ' + uuid4().hex)
    main = await projects.create_main_task(project.id, 'CE2 synthetic source review')
    node = research.query('list_nodes', main.research_id)[0]
    root = scopes.current(research.research(node.research_id).scope_id, research.owner)
    _, ref = scopes.ingest(root, content=b'CE2 synthetic marker; confirmed count 7; unknown market size.',
        name='ce2.txt', media_type='text/plain')
    chat = ChatService(research, tasks).send(node.id, 'Review the synthetic source; preserve unknowns.', 0)
    return node, ref, chat


def rows(controlled):
    db, _, _, tasks, *_ = controlled
    with db.transaction():
        return db.connection.execute(select(tasks.engine.ledger.runs)).mappings().all()


async def test_four_entrances_consultation_and_followup_use_same_broker(controlled):
    _, scopes, research, tasks, projects, c, provider = controlled
    node, ref, chat = await seed(controlled)
    provider.plans.append([answer({'report': 'I will review the synthetic source.', 'claims': []})])
    command = await research.submit(NodeCommand(command_id=uuid4().hex, research_id=node.research_id,
        node_id=node.id, kind='chat_reply', chat_revision=chat.revision))
    assert research.ledger.get(command.run_id).result.status == 'succeeded'
    intent = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_synthesize',
        task_instruction='Read the source then ask for missing year.', delivery_requirements='State unknowns',
        resource_refs=(ref,))
    provider.plans.extend([
        [call('search_tools', query='read_source write_private_note read_private_note request_manager_information', limit=10),
         call('read_source', ref=ref.model_dump(mode='json')),
         call('write_private_note', name='draft', text='expert-only marker'),
         call('read_private_note', name='draft'),
         call('request_manager_information', question='Which year?', progress='Read synthetic count 7.'),
         answer({'report': 'Count 7; year unknown.', 'missing': ['year']})],
        [answer({'available': False, 'answer': 'Year was not provided.', 'missing': ['year']})],
    ])
    status = await c.execute(intent.command_id)
    assert status['intent'].status == 'completed', status
    assert status['receipts'][0].delivery == 'pending_review'
    assert c.query('information_for', intent.task_id)[0].status == 'resolved'
    provider.plans.append([answer('This is a draft discussion, not an adopted conclusion.')])
    thread = projects.expert_thread(intent.task_id)
    discussion = await projects.discuss(intent.task_id, 'Explain the uncertainty.', thread.revision)
    assert discussion.messages[-1].content.startswith('This is a draft')
    assert not {'write_private_note', 'delegate_task', 'search_web'} & {t.name for t in provider.requests[-1]['tools']}
    assert provider.requests[-1]['output_schema'] is None
    provider.plans.append([answer({'report': 'Expert draft is saved and pending review; year unknown.'})])
    chat = ChatService(research, tasks).read(node.id)
    followup = await research.submit(NodeCommand(command_id='resume:' + uuid4().hex,
        research_id=node.research_id, node_id=node.id, kind='chat_followup', chat_revision=chat.revision))
    assert research.ledger.get(followup.run_id).result.status == 'succeeded'
    bindings = [r['binding'] for r in rows(controlled) if r['binding']['research_id'] == node.research_id]
    assert sorted(b['entrance'] for b in bindings) == ['consultation', 'discussion', 'expert', 'manager', 'manager']
    assert all(b['execution'] == 'controlled_tools' for b in bindings)
    assert sum(r['models'] for r in rows(controlled) if r['binding']['research_id'] == node.research_id) == 10


async def test_two_experts_overlap_and_have_distinct_private_workspaces(controlled):
    _, _, _, tasks, _, c, provider = controlled
    node, ref, _ = await seed(controlled)
    intents = [c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id=expert,
        task_instruction='Review independently', delivery_requirements='State uncertainty', resource_refs=(ref,))
        for expert in ('expert_search', 'expert_synthesize')]
    entered, release = asyncio.Event(), asyncio.Event()
    active = 0
    async def pause(_):
        nonlocal active
        active += 1
        if active == 2: entered.set()
        await release.wait()
        return answer({'report': 'Independent synthetic draft.'})
    for index in range(2):
        provider.plans.append([call('search_tools', query='write_private_note'),
            call('write_private_note', name='only_' + str(index), text='secret ' + str(index)), pause])
    running = [asyncio.create_task(c.execute(i.command_id)) for i in intents]
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert len(tasks.engine.active) == 2
        release.set()
        outcomes = await asyncio.gather(*running)
        assert all(o['intent'].status == 'completed' for o in outcomes), outcomes
    finally:
        release.set()
        await asyncio.gather(*running, return_exceptions=True)
    bindings = [r['binding'] for r in rows(controlled) if r['binding']['research_id'] == node.research_id]
    directories = [tasks.engine.private_root / tasks.engine.workspace_id(b) for b in bindings]
    assert len(set(directories)) == 2
    for index, directory in enumerate(directories):
        work = Workspace(directory)
        try:
            names = {r[0] for r in work.db.execute('SELECT name FROM notes')}
            own = next(n for n in names if n.startswith('only_'))
            foreign = 'only_1' if own == 'only_0' else 'only_0'
            with pytest.raises(ValueError, match='not found'): work.read(foreign)
        finally: work.close()


async def test_revocation_cancels_waiting_model_and_quarantines_old_generation(controlled):
    _, scopes, research, tasks, _, c, provider = controlled
    node, ref, _ = await seed(controlled)
    intent = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_synthesize',
        task_instruction='Review synthetic source', delivery_requirements='State uncertainty', resource_refs=(ref,))
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def pause(_):
        entered.set()
        try: await asyncio.Event().wait()
        finally: stopped.set()
    provider.plans.append([call('search_tools', query='write_private_note'),
        call('write_private_note', name='secret', text='revoked private derivation'), pause])
    running = asyncio.create_task(c.execute(intent.command_id))
    await asyncio.wait_for(entered.wait(), 10)
    admitted = tasks.query('list_runs', intent.task_id)[0]
    root = scopes.current(research.research(node.research_id).scope_id, research.owner)
    scopes.revoke(root, ref)
    await asyncio.wait_for(running, 10)
    assert stopped.is_set() and not tasks.engine.active
    record = tasks.ledger.get(admitted.id)
    assert record.result.status != 'succeeded' and record.result.output is None
    with pytest.raises(ScopeDenied): tasks.engine.validate_run(admitted.id)
    assert not tasks.engine.ledger.usage(admitted.id).complete
    provider.plans.append([answer({'report': 'New clean task has no old source or notes.'})])
    fresh = c.delegate_task(command_id=uuid4().hex, node_id=node.id, session_id=intent.session_id,
        task_instruction='Discuss remaining gaps without revoked source', delivery_requirements='No invented facts')
    outcome = await c.execute(fresh.command_id)
    assert outcome['intent'].status == 'completed', outcome
    b = tasks.engine.ledger.binding(outcome['runs'][0].id)
    work = Workspace(tasks.engine.private_root / tasks.engine.workspace_id(b))
    try:
        with pytest.raises(ValueError, match='not found'): work.read('secret')
    finally: work.close()


@pytest.mark.parametrize('grant', [True, False])
async def test_report_handoff_requires_manager_read_and_fixed_authorization(controlled, grant):
    _, _, _, tasks, _, c, provider = controlled
    node, ref, _ = await seed(controlled)
    upstream = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_search',
        task_instruction='Initial report', delivery_requirements='Preserve uncertainty', resource_refs=(ref,))
    provider.plans.append([answer({'report': 'CE2_PEER_REPORT_MARKER; count 7, unknown market size.'})])
    source = (await c.execute(upstream.command_id))['receipts'][0]
    downstream = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_synthesize',
        task_instruction='Ask for the peer report', delivery_requirements='Keep its review status', resource_refs=(ref,))
    observed = []
    async def ask_after_denial(kwargs):
        text = json.dumps(kwargs['input'])
        assert 'authorization_required' in text and 'CE2_PEER_REPORT_MARKER' not in text
        return call('request_manager_information', question='May I read the peer draft?', progress='No authorized peer body yet.',
                    requested_result_ids=[source.result_id])
    async def inspect_grant(kwargs):
        text = json.dumps(kwargs['input'])
        assert 'CE2_PEER_REPORT_MARKER' in text and 'pending_review' in text
        observed.append(True)
        return answer({'report': 'Compared authorized draft; still pending independent review.'})
    provider.plans.append([
        call('read_context', result_ids=[source.result_id]), ask_after_denial,
        call('read_context', result_ids=[source.result_id]), inspect_grant])
    manager_plan = [answer({'available': True, 'answer': 'Read this fixed draft, pending review.',
        'result_ids': [source.result_id]})]
    if grant:
        manager_plan.insert(0, call('read_information', result_ids=[source.result_id]))
    provider.plans.append(manager_plan)
    result = await c.execute(downstream.command_id)
    assert result['intent'].status == ('completed' if grant else 'failed'), result['intent']
    assert bool(observed) == grant
    request = c.query('information_for', downstream.task_id)[0]
    if grant:
        assert request.report_grants[0].run_id == source.run_id
        assert request.report_grants[0].spec_version == source.spec_version
    else:
        assert request.status == 'waiting' and not request.report_grants
    assert tasks.receipt(source.result_id) == source


async def test_tool_cannot_read_peer_private_note(controlled):
    _, _, _, _, _, c, provider = controlled
    node, ref, _ = await seed(controlled)
    first = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_search',
        task_instruction='A', delivery_requirements='Draft', resource_refs=(ref,))
    provider.plans.append([call('write_private_note', name='private_a', text='NEVER_SHARE_PRIVATE_A'),
        answer({'report': 'A public draft'})])
    assert (await c.execute(first.command_id))['intent'].status == 'completed'
    second = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_synthesize',
        task_instruction='B', delivery_requirements='Draft', resource_refs=(ref,))
    provider.plans.append([call('read_private_note', name='private_a')])
    before = len(provider.requests)
    result = await c.execute(second.command_id)
    assert result['intent'].status == 'failed'
    assert 'NEVER_SHARE_PRIVATE_A' not in json.dumps([v['input'] for v in provider.requests[before:]])


async def test_duplicate_event_delivered_then_consumed_survives_new_process(controlled, tmp_path):
    import subprocess
    import sys
    db, _, _, tasks, projects, c, provider = controlled
    node, ref, _ = await seed(controlled)
    intent = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_synthesize',
        task_instruction='A saved task', delivery_requirements='Draft', resource_refs=(ref,))
    provider.plans.append([answer({'report': 'Saved draft'})])
    await c.execute(intent.command_id)
    event = c.send_agent_message(command_id=uuid4().hex, task_id=intent.task_id, session_id=intent.session_id,
        kind='information', content='Persistent supplemental event', requires_reply=False)
    before = len(provider.requests)
    delivered = await c.execute_message(event.id)
    assert delivered.status == 'delivered' and delivered.consumer_turn_id is None
    assert await c.execute_message(event.id) == delivered
    assert len(provider.requests) == before
    provider.plans.append([answer('I consumed the supplemental event in this discussion.')])
    thread = projects.expert_thread(intent.task_id)
    await projects.discuss(intent.task_id, 'Explain the note', thread.revision)
    consumed = c.query('message', event.id)
    assert consumed.status == 'consumed' and consumed.consumer_turn_id
    assert await c.execute_message(event.id) == consumed
    assert len(provider.requests) == before + 1
    db.close()
    script = '''
import asyncio,json,os,sys
from types import SimpleNamespace
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources,build_controlled_services
v=json.load(sys.stdin)
db=PostgresDatabase(os.environ['CE2_TEST_DATABASE_URL'])
try:
 scopes=build_resources(db,v['blobs'])
 research,tasks,projects=build_controlled_services(db,scopes,private_root=v['private'],model_provider=SimpleNamespace())
 c=research.collaboration
 saved=c.query('message',v['event'])
 assert saved.model_dump(mode='json')==v['saved']
 assert asyncio.run(c.execute_message(v['event']))==saved
 session=c.query('session',saved.session_id)
 assert sum(m.id==saved.id for m in session.messages)==1
 print('CE2 new process: event and consumption receipt unchanged; no model provider available or called')
finally: db.close()
'''
    result = subprocess.run([sys.executable, '-c', script], input=json.dumps({
        'blobs': str(tmp_path / 'blobs'), 'private': str(tasks.engine.private_root),
        'event': event.id, 'saved': consumed.model_dump(mode='json')}), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'new process' in result.stdout


async def test_dbos_waiting_information_wakes_without_expert_model_polling(controlled):
    from agent_core.durable import DurableResearch
    _, _, _, tasks, _, c, provider = controlled
    node, ref, _ = await seed(controlled)
    intent = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_synthesize',
        task_instruction='Ask for the year', delivery_requirements='State gaps', resource_refs=(ref,))
    entered, release = asyncio.Event(), asyncio.Event()
    async def consult(_):
        entered.set()
        await release.wait()
        return answer({'available': False, 'answer': 'No year supplied.', 'missing': ['year']})
    provider.plans.extend([
        [call('request_manager_information', question='Which year?', progress='Synthetic count read.'),
         answer({'report': 'Draft with unknown year.'})],
        [consult]])
    durable = DurableResearch(tasks, os.environ['CE2_TEST_DATABASE_URL'])
    running = None
    try:
        await durable.start()
        c.durable = durable
        running = asyncio.create_task(c.execute(intent.command_id))
        await asyncio.wait_for(entered.wait(), 15)
        saved = c.query('information_for', intent.task_id)[0]
        assert saved.status == 'waiting' and saved.answer is None
        assert len(provider.requests) == 2
        await asyncio.sleep(.15)
        assert len(provider.requests) == 2 and not running.done()
        release.set()
        result = await asyncio.wait_for(running, 15)
        assert result['intent'].status == 'completed', result['intent']
        assert c.query('information_for', intent.task_id)[0].status == 'resolved'
        before = len(provider.requests)
        assert (await c.execute(intent.command_id))['intent'].status == 'completed'
        assert len(provider.requests) == before == 3
    finally:
        release.set()
        if running: await asyncio.gather(running, return_exceptions=True)
        c.durable = None
        durable.close()


async def test_automatic_source_acquisition_does_not_revoke_generation(controlled):
    _, scopes, research, _, _, c, provider = controlled
    node, ref, _ = await seed(controlled)
    intent = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_synthesize',
        task_instruction='Review source', delivery_requirements='Draft', resource_refs=(ref,))
    async def acquired(_):
        root = scopes.current(research.research(node.research_id).scope_id, research.owner)
        changed, _ = scopes.ingest(root, content=b'New automatic ingestion, not a revocation.',
            name='additional.txt', media_type='text/plain', automatic=True)
        assert changed.version == root.version
        return answer({'report': 'Original authorized draft remains valid.'})
    provider.plans.append([acquired])
    result = await c.execute(intent.command_id)
    assert result['intent'].status == 'completed', result['intent']


@pytest.mark.parametrize('resolved', [False, True])
async def test_sigkill_keeps_information_event_and_resumes_at_saved_boundary(controlled, tmp_path, resolved):
    """Kill at persisted handoff boundaries, not arbitrary in-flight model replay (CE7)."""
    import signal
    import subprocess
    import sys
    controlled[0].close()
    marker = tmp_path / 'killed-handoff.json'
    script = '''
import asyncio,json,os,signal,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_controlled_collaboration import Provider,seed,answer
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources,build_controlled_services
from agent_core.collaboration.information import RequestInformation
v=json.load(sys.stdin)
async def main():
 db=PostgresDatabase(os.environ['CE2_TEST_DATABASE_URL'])
 scopes=build_resources(db,v['blobs'])
 provider=Provider()
 research,tasks,projects=build_controlled_services(db,scopes,private_root=v['private'],model_provider=provider)
 c=research.collaboration
 node,ref,_=await seed((db,scopes,research,tasks,projects,c,provider))
 intent=c.delegate_task(command_id=v['command'],node_id=node.id,expert_id='expert_synthesize',task_instruction='Resume saved progress',delivery_requirements='Keep missing year',resource_refs=(ref,))
 run_id=tasks.prepare_durable(intent.task_id,1)
 binding=tasks.query('run',run_id)
 spec=tasks.spec(intent.task_id)
 scopes.running.set(run_id)
 with scopes.track(run_id,scopes.repository.scope(binding.scope_id)):
  args=RequestInformation(question='Which year?',progress='CE2_PERSISTED_PROGRESS')
  if v['resolved']:
   provider.plans.append([answer({'available':False,'answer':'No year supplied.','missing':['year']})])
   await c.information.request(spec,binding,args)
  else: c.information.begin(spec,binding,args)
 saved=c.information.get(run_id)
 Path(v['marker']).write_text(json.dumps({'intent':intent.command_id,'task':intent.task_id,'run':run_id,'event':saved.model_dump(mode='json')}))
 os.kill(os.getpid(),signal.SIGKILL)
asyncio.run(main())
'''
    values = dict(blobs=str(tmp_path / 'restart-blobs'), private=str(tmp_path / 'restart-private'),
        command=uuid4().hex, resolved=resolved, marker=str(marker))
    killed = subprocess.run([sys.executable, '-c', script], input=json.dumps(values),
        capture_output=True, text=True, timeout=30)
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    saved = json.loads(marker.read_text())
    db = PostgresDatabase(os.environ['CE2_TEST_DATABASE_URL'])
    try:
        provider = Provider()
        scopes = build_resources(db, values['blobs'])
        research, tasks, _ = build_controlled_services(db, scopes,
            private_root=values['private'], model_provider=provider)
        c = research.collaboration
        assert c.information.get(saved['run']).model_dump(mode='json') == saved['event']
        if not resolved:
            provider.plans.append([answer({'available': False, 'answer': 'No year supplied.', 'missing': ['year']})])
        async def continuation(kwargs):
            assert 'CE2_PERSISTED_PROGRESS' in kwargs['system_instructions']
            return answer({'report': 'Completed from saved progress; year unknown.'})
        provider.plans.append([continuation])
        result = await c.execute(saved['intent'])
        assert result['intent'].status == 'completed', result['intent']
        assert len(tasks.query('list_runs', saved['task'])) == 1
        event = c.information.get(saved['run'])
        assert event.status == 'resolved' and event.attempts == 1
        assert len(provider.requests) == (1 if resolved else 2)
        before = len(provider.requests)
        await c.execute(saved['intent'])
        assert len(provider.requests) == before
    finally:
        db.close()


async def test_manager_delegates_two_experts_and_resumes_with_public_reports_only(controlled):
    _, _, research, tasks, _, c, provider = controlled
    node, ref, chat = await seed(controlled)
    manager_plan = [call('select_task_tools', profile='experts'),
        call('find_experts', required_tools=['search_web']), call('list_context', category='sources')]
    for expert in ('expert_search', 'expert_synthesize'):
        manager_plan.append(call('delegate_task', expert_id=expert,
            task_instruction='Read synthetic material independently', delivery_requirements='State gaps',
            resource_refs=[ref.model_dump(mode='json')]))
    manager_plan.append(answer({'report': 'Two experts are working.'}))
    provider.plans.append(manager_plan)
    command = await research.submit(NodeCommand(command_id=uuid4().hex, research_id=node.research_id,
        node_id=node.id, kind='chat_reply', chat_revision=chat.revision))
    assert research.ledger.get(command.run_id).status == 'succeeded'
    intents = c.query('intents_for', node.research_id)
    assert len(intents) == 2 and all(i.manager_run_id == command.run_id for i in intents)
    for _ in intents:
        provider.plans.append([call('search_tools', query='write_private_note'),
            call('write_private_note', name='private', text='EXPERT_NOTE_MUST_NOT_REACH_MANAGER'),
            answer({'report': 'PUBLIC_REPORT; confirmed count 7, unknown market size.'})])
    await c.recover_pending()
    receipts = [c.status(i.command_id)['receipts'][0] for i in intents]
    before = len(provider.requests)
    provider.plans.append([call('search_tools', query='read_context'),
        call('read_context', result_ids=[r.result_id for r in receipts]),
        answer({'report': 'Both public drafts agree, pending independent review.'})])
    await c.resume_manager()
    resumed = provider.requests[before:]
    assert len(resumed) == 3
    assert 'PUBLIC_REPORT' in json.dumps(resumed[-1]['input'])
    assert 'EXPERT_NOTE_MUST_NOT_REACH_MANAGER' not in json.dumps([v['input'] for v in resumed])
    assert all(c.query('intent', i.command_id).resume_status == 'complete' for i in intents)
    before = len(provider.requests)
    await c.resume_manager()
    assert len(provider.requests) == before


async def test_revoked_generation_is_hidden_from_api_and_discussion(controlled):
    import httpx
    from agent_core.app.api import create_app
    _, scopes, research, tasks, projects, c, provider = controlled
    node, ref, _ = await seed(controlled)
    # A result can derive from private state even without declaring a source ref.
    # Root generation revocation must block it independently of the ref checks.
    intent = c.delegate_task(command_id=uuid4().hex, node_id=node.id, expert_id='expert_synthesize',
        task_instruction='Describe uncertainty', delivery_requirements='Draft')
    provider.plans.append([answer({'report': 'OLD_GENERATION_DERIVATION'})])
    result = await c.execute(intent.command_id)
    receipt = result['receipts'][0]
    root = scopes.current(research.research(node.research_id).scope_id, research.owner)
    scopes.revoke(root, ref)
    with pytest.raises(ScopeDenied): tasks.retry_save(receipt.run_id)
    with pytest.raises(ScopeDenied): projects.expert_thread(intent.task_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)),
                                base_url='http://ce2-test') as client:
        denied = await client.get('/api/task_receipt', params={'id': receipt.result_id})
        assert denied.status_code == 403
        activity = await client.get('/api/task_activity', params={'research_id': node.research_id})
        assert activity.status_code == 200
        run = next(v for v in activity.json() if v['spec']['id'] == intent.task_id)['runs'][0]
        assert run['access'] == 'revoked' and run['artifact'] is None and 'output' not in run and run['receipt'] is None


async def test_existing_method_generation_and_preview_use_broker(controlled):
    from agent_core.composition import build_evaluation, build_evolution
    from agent_core.methods.defaults import METHODS
    from test_evolution import frozen_error
    db, scopes, research, tasks, projects, c, provider = controlled
    node, ref, chat = await seed(controlled)
    evaluation = build_evaluation(db, tasks)
    evolution = build_evolution(db, tasks, evaluation)
    fixture = (db, scopes, research, research.research(node.research_id), provider, tasks, evaluation, evolution)
    output = {'report': 'CFO 100 million USD', 'fields': {'company': 'A', 'period': '2025',
        'operating_cash_flow': '100', 'capital_expenditure': '20', 'unit': 'million USD'},
        'sources': {'operating_cash_flow': 'line 1', 'capital_expenditure': 'line 1', 'unit': 'line 1'}}
    provider.plans.extend([[answer(output)], [answer(output)]])
    dev, _ = await frozen_error(fixture, 'Development', 'development')
    holdout, _ = await frozen_error(fixture, 'Holdout', 'holdout')
    provider.plans.append([answer({'instructions': METHODS['extract'][1] + ' Verify source units.', 'examples': []})])
    candidate = await evolution.propose(candidate_id=uuid4().hex, expert_id='expert_extract',
        development_case_ids=(dev.id,), holdout_case_ids=(holdout.id,), approved_lesson='Check source unit.',
        lesson_reviewer='synthetic fixture', generator_model='fixture', quality_floor=1,
        min_quality_gain=1, max_total_cost_usd=.1)
    provider.plans.append([answer(output)])
    await evolution.run_preview(preview_id=uuid4().hex, candidate_id=candidate.id, case_id=dev.id, arm='after')
    assert {'consultation', 'preview'} <= {r['binding']['entrance'] for r in rows(controlled)}
    assert provider.requests[-2]['tools'] == []
    assert {t.name for t in provider.requests[-1]['tools']} <= {
        'read_source', 'search_source', 'write_private_note', 'read_private_note', 'read_context_page'}


def test_product_live_composition_has_no_uncontrolled_model_path(controlled, tmp_path, monkeypatch):
    from agent_core.composition import build_live_services
    from agent_core.controlled.engine import ControlledEngine
    from agents.models.openai_responses import OpenAIResponsesModel
    db, scopes, *_ = controlled
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-construction-only')
    research, tasks, projects = build_live_services(db, scopes, private_root=tmp_path/'product-private')
    assert isinstance(research.engine, ControlledEngine)
    assert research.engine is tasks.engine
    assert isinstance(tasks.engine.model_provider.get_model('fixture-model'), OpenAIResponsesModel)


def test_live_provider_is_lazy_without_credentials(monkeypatch):
    from agent_core.controlled.provider import LiveModelProvider
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    provider = LiveModelProvider()
    assert provider.provider is None
    with pytest.raises(Exception, match='api_key'):
        provider.get_model('fixture-model')
