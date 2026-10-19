"""Ordinary nodes: real PostgreSQL, SDK, Broker and a separate-process reopen."""
import json
import os
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import select

from agent_core.context.selection import ContextSelector
from agent_core.context.skills import SkillCatalog
from agent_core.research.models import NodeCommand
from agent_core.learning.models import Trajectory
from test_controlled_collaboration import controlled, answer, call, seed
from test_dynamic_context import skill


async def test_plain_node_broker_discovery_scope_audit_and_process_reopen(controlled, tmp_path):
    db, scopes, research, tasks, projects, _, provider = controlled
    project = projects.create('Ordinary node persistence')
    main = await projects.create_main_task(project.id, 'cash flow')
    node = research.query('list_nodes', main.research_id)[0]
    root = research.research(main.research_id)
    scope, ref = scopes.ingest(scopes.current(root.scope_id, research.owner),
        content=b'Cash flow synthetic source.', name='cash-flow.txt', media_type='text/plain')
    _, outside = scopes.ingest(scope, content=b'UNAUTHORIZED_LIBRARY_SECRET cash flow',
        name='outside.txt', media_type='text/plain')
    skill(tmp_path/'skills')
    research.context.selector = ContextSelector(SkillCatalog(tmp_path/'skills'))
    user = research.memories.create(kind='experience', content='cash flow USER_RULE', source='User', confirmed=True)
    project_memory = research.memories.create(kind='experience', content='cash flow PROJECT_RULE',
        source='User', confirmed=True, scope='project', scope_id=project.id)
    tasks.memories.create(kind='experience', content='cash flow DOMAIN_SECRET', source='User',
        confirmed=True, scope='specialist', scope_id='expert_synthesize')
    raw = '  $cash-flow\n保留未知，不捏造数字。  '
    # A run command may carry its own raw instruction without a chat message.
    provider.plans.append([call('load_skill', name='cash-flow'),
        call('search_memory', query='cash flow', scope='project'),
        call('read_source', ref=outside.model_dump(mode='json')),
        answer({'report':'Unknown figures remain unknown.', 'claims':[],
            'blocking_missing':[], 'limitations':[]})])
    command = await research.submit(NodeCommand(command_id=uuid4().hex, research_id=root.id,
        node_id=node.id, kind='continue', goal='cash flow review', instructions=raw, resource_refs=(ref,)))
    result = research.ledger.get(command.run_id).result
    assert result.status == 'succeeded', result
    snap = scopes.repository.snapshot(command.run_id)
    assert snap.input.messages[-1].content == raw and snap.selection
    assert snap.scope.allowed == (ref,)
    admitted_memories = {m.id for m in snap.memory_catalog}
    assert {user.id, project_memory.id} <= admitted_memories
    assert all(m.scope in ('user', 'project') for m in research.memories.validate(
        root.id, snap.memory_catalog, agent=snap.agent))
    inputs = json.dumps([r['input'] for r in provider.requests], ensure_ascii=False)
    assert 'UNAUTHORIZED_LIBRARY_SECRET' not in inputs and 'DOMAIN_SECRET' not in inputs
    assert '保留现金流原文与来源。' not in json.dumps(provider.requests[0]['input'], ensure_ascii=False)
    assert '保留现金流原文与来源。' in json.dumps(provider.requests[1]['input'], ensure_ascii=False)
    assert 'unavailable' in json.dumps(provider.requests[-1]['input'], ensure_ascii=False)
    assert all('forbidden_tool' not in {t.name for t in r['tools']} for r in provider.requests)
    ledger = tasks.engine.ledger
    with db.transaction():
        binding = db.connection.execute(select(ledger.runs).where(ledger.runs.c.id == command.run_id)).mappings().one()
        operations = db.connection.execute(select(ledger.operations).where(
            ledger.operations.c.run_id == command.run_id)).mappings().all()
    assert binding['binding']['entrance'] == 'manager'
    assert binding['binding']['execution'] == 'controlled_tools'
    assert binding['models'] == 4 and binding['tools'] == 3
    assert sorted(o['kind'] for o in operations) == ['model'] * 4 + ['result'] + ['tool'] * 3
    assert all(o['status'] == 'confirmed' for o in operations)
    result_id = research.node(node.id).current_result_id
    expected = {'input':snap.input.model_dump(mode='json'), 'selection':snap.selection,
        'memory_catalog':[m.model_dump(mode='json') for m in snap.memory_catalog],
        'skill_catalog':list(snap.skill_catalog)}
    db.close()
    # A fresh interpreter/connection must recover the same input and publication.
    script = '''
import json, os, sys
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_controlled_services
from types import SimpleNamespace
db = PostgresDatabase(os.environ['CE2_TEST_DATABASE_URL'])
try:
    scopes = build_resources(db, sys.argv[1])
    research, tasks, projects = build_controlled_services(db, scopes,
        private_root=sys.argv[2], model_provider=SimpleNamespace())
    snap = scopes.repository.snapshot(sys.argv[3])
    value, report = research.read_result(sys.argv[4])
    print(json.dumps({'input':snap.input.model_dump(mode='json'), 'selection':snap.selection,
        'memory_catalog':[m.model_dump(mode='json') for m in snap.memory_catalog],
        'skill_catalog':list(snap.skill_catalog), 'report':report}, ensure_ascii=False))
finally:
    db.close()
'''
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path/'blobs'),
        str(tmp_path/'private'), command.run_id, result_id], capture_output=True, text=True,
        timeout=30, env=dict(os.environ))
    assert child.returncode == 0, child.stdout + child.stderr
    restored = json.loads(child.stdout)
    assert restored.pop('report') == 'Unknown figures remain unknown.'
    assert restored == expected


async def test_specialist_receipt_and_trajectory_rollback_and_save_without_model_replay(controlled, monkeypatch):
    _, _, _, tasks, _, collaboration, provider = controlled
    node, ref, _ = await seed(controlled)
    intent = collaboration.delegate_task(command_id=uuid4().hex, node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Read the fixed sample',
        delivery_requirements='State limits', resource_refs=(ref,))
    provider.plans.append([answer({'report':'Synthetic result, unknown market size.'})])
    original = tasks.learning.record
    def fail_after_write(*args):
        original(*args)
        raise RuntimeError('Injected failure after trajectory write')
    monkeypatch.setattr(tasks.learning, 'record', fail_after_write)
    status = await collaboration.execute(intent.command_id)
    assert status['intent'].status == 'failed' and status['intent'].error == 'save_failed'
    binding = tasks.query('list_runs', intent.task_id)[0]
    assert tasks.query('receipt', 'task-result:' + binding.id) is None
    assert tasks.learning.get(Trajectory, binding.id) is None
    assert tasks.ledger.committed('run:' + binding.id) is None
    model_count = len(provider.requests)
    monkeypatch.setattr(tasks.learning, 'record', original)
    saved = tasks.retry_save(binding.id)
    assert saved.status == 'completed'
    receipt = tasks.receipt('task-result:' + binding.id)
    trajectory = tasks.learning.get(Trajectory, binding.id)
    assert trajectory.snapshot_id == receipt.context_snapshot_id
    assert trajectory.original_user == tasks.spec(intent.task_id).user_request
    assert len(provider.requests) == model_count
