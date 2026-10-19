"""Real model memory writes, PostgreSQL atomicity and SIGKILL recovery.

Fault hooks instrument persistence boundaries only; no provider response is mocked.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4
from dotenv import load_dotenv
from sqlalchemy import select
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from agents.models.openai_provider import OpenAIProvider
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources, build_controlled_services
from agent_core.context.audit import audit_http_request, audit_http_response
from agent_core.context.selection import ContextSelector
from agent_core.context.skills import SkillCatalog
from agent_core.controlled.checkpoints import Checkpoints
from agent_core.memories.writing import MemoryWriter
from agent_core.memories.models import MemoryEntry
from agent_core.tasks.service import TaskLedger
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand
from agent_core.contracts import RunLimits

CASES = ('main_preference', 'temporary_project', 'specialist_candidate', 'temporary_domain_denied',
    'foreign_project_denied', 'correction', 'forget', 'temporary_request', 'inferred_draft',
    'project_to_user_denied', 'draft_confirmation', 'conflicting_create_denied',
    'foreign_owner_denied', 'handoff_user_preference')
FAULTS = ('after_intent', 'after_tool_receipt', 'inside_business_commit', 'after_business_commit')


def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def append(path, value):
    with path.open('a') as f:
        f.write(json.dumps(value, ensure_ascii=False) + '\n'); f.flush(); os.fsync(f.fileno())


async def worker(args):
    load_dotenv('.env', override=False)
    if not os.environ.get('OPENAI_API_KEY'): raise RuntimeError('Configured credential missing')
    root = Path(args.directory).resolve(); root.mkdir(parents=True, exist_ok=True)
    os.environ['OPENAI_MANAGER_MODEL'] = 'deepseek-flash'
    def pause(point):
        if args.fault == point:
            (root/'paused').write_text(point)
            while True: time.sleep(.1)
    async def sent(request):
        await audit_http_request(request)
        body = json.loads(request.content); text = json.dumps(body)
        purpose = ('memory_write_review' if 'Review a proposed long-term memory write.' in text else
            'memory' if 'Select historical memories relevant to the query' in text else 'task')
        append(root/'http.jsonl', {'event': 'sent', 'purpose': purpose, 'model': body.get('model')})
    async def received(response):
        await audit_http_response(response)
        append(root/'http.jsonl', {'event': 'received', 'status': response.status_code})
    client = AsyncOpenAI(max_retries=0, http_client=DefaultAsyncHttpxClient(event_hooks={'request': [sent], 'response': [received]}))
    db = PostgresDatabase(args.database)
    owner = 'write-probe:' + hashlib.sha256(str(root).encode()).hexdigest()
    try:
        scopes = build_resources(db, root/'blobs', parser=TextParser())
        research, tasks, projects = build_controlled_services(db, scopes, private_root=root/'private',
            model_provider=OpenAIProvider(openai_client=client, use_responses=True), owner=owner)
        research.context.selector = ContextSelector(SkillCatalog(root/'empty-skills'))
        seed_path = root/'seed.json'
        if not seed_path.exists():
            record = research.create('Memory writing acceptance ' + uuid4().hex)
            project = projects.create('Current synthetic project')
            await projects.create_main_task(project.id, record.goal, record.id)
            case = args.case; old = None; foreign = None
            if case in ('correction', 'forget', 'draft_confirmation', 'conflicting_create_denied'):
                old = research.memories.create(kind='preference', content='先给结论，再解释原因' if case == 'draft_confirmation' else '只给简短答案',
                    source='Synthetic user statement', confirmed=case != 'draft_confirmation')
            if case == 'foreign_project_denied':
                other = projects.create('Foreign synthetic project')
                foreign = research.memories.create(kind='preference', content='Foreign project convention', source='Synthetic user',
                    confirmed=True, scope='project', scope_id=other.id)
            if case == 'foreign_owner_denied':
                foreign = MemoryEntry(owner=owner + ':foreign', kind='preference', content='Foreign private preference', source='Synthetic other user')
                with db.transaction() as tx: tx.memories.save(foreign, None)
            statements = {
                'main_preference': '请记住：以后先给结论，再解释原因。',
                'temporary_project': '这个项目以后统一使用人民币万元口径，请记住这个项目约定。',
                'specialist_candidate': '保存一个可复用的领域经验候选：比较来源结论时先检查来源之间的矛盾，再标明未解决的分歧。',
                'temporary_domain_denied': '请把比较来源分歧的经验保存到专家领域记忆。',
                'foreign_project_denied': '请把另一个项目的约定改为人民币万元口径。',
                'correction': '纠正以前的偏好：以后先给结论，再解释原因，不再只给简短答案。',
                'forget': '请忘记以前只给简短答案的偏好，以后不再使用它。',
                'temporary_request': '本次回答简短一点，只针对这一次。',
                'inferred_draft': '我还没有确认任何长期偏好，你可以提出一个简短回答的偏好候选，但不能当成已确认。',
                'project_to_user_denied': '这个项目以后统一人民币万元口径，其他项目不适用。',
                'draft_confirmation': '请确认以前的偏好候选：以后先给结论，再解释原因。',
                'conflicting_create_denied': '纠正以前只给简短答案的偏好：以后先给结论，再详细解释原因。',
                'foreign_owner_denied': '请把另一个用户的偏好候选改为先给结论，再解释原因。',
                'handoff_user_preference': '请记住：以后先给结论，再解释原因。',
            }
            action = 'correct' if case in ('correction', 'foreign_project_denied', 'foreign_owner_denied', 'draft_confirmation') else 'forget' if case == 'forget' else 'create'
            scope = 'project' if case in ('temporary_project', 'foreign_project_denied') else 'specialist' if case in ('specialist_candidate', 'temporary_domain_denied') else 'user'
            content = ('比较来源结论时先检查来源之间的矛盾，再标明未解决的分歧' if scope == 'specialist' else
                '统一使用人民币万元口径' if scope == 'project' else '简短回答' if case in ('temporary_request', 'inferred_draft') else
                '' if case == 'forget' else '统一人民币万元口径' if case == 'project_to_user_denied' else '先给结论，再解释原因')
            params = {'action': action, 'scope': scope, 'kind': 'experience' if scope == 'specialist' else 'preference',
                'content': content, 'applicability': '',
                'basis': 'experience' if scope == 'specialist' else 'inferred_preference' if case == 'inferred_draft' else 'user_statement',
                'quote': '' if scope == 'specialist' or case == 'inferred_draft' else statements[case],
                'reason': 'Memory acceptance request', 'target_id': (old or foreign).id if action != 'create' and (old or foreign) else None,
                'expected_version': 1 if action != 'create' and (old or foreign) else None, 'expires_at': None}
            original = statements[case] + '\n验收执行：只调用一次 write_memory，参数严格使用下列 JSON；随后提交简短中文报告。'
            original += '\n' + json.dumps(params, ensure_ascii=False)
            original += '\n若工具拒绝或不保存，只报告该结果，不换范围、不重试、不询问用户。不得自动确认候选，不调用其他业务工具。'
            seed = {'case': case, 'research_id': record.id, 'project_id': project.id, 'original': original,
                'params': params, 'old_id': old.id if old else None, 'foreign_id': foreign.id if foreign else None}
            if case == 'main_preference':
                chat_service = ChatService(research, tasks)
                chat = await chat_service.start(record.id, record.goal)
                chat = chat_service.send(chat.node_id, original, chat.revision)
                command = NodeCommand(command_id=uuid4().hex, research_id=record.id, node_id=chat.node_id,
                    kind='chat_reply', chat_revision=chat.revision, limits=RunLimits(max_turns=10, max_calls=3, timeout_seconds=240))
                seed['command'] = command.model_dump(mode='json')
            elif case == 'handoff_user_preference':
                chat_service = ChatService(research, tasks)
                chat = await chat_service.start(record.id, record.goal)
                chat_service.send(chat.node_id, original, chat.revision)
                intent = research.collaboration.delegate_task(command_id=uuid4().hex, node_id=chat.node_id,
                    expert_id='expert_synthesize', task_instruction='Perform the exact memory write requested in the original user message.',
                    delivery_requirements='Report the queued write status; do not confirm candidates or use other business tools.')
                spec = tasks.spec(intent.task_id)
                run_id = tasks.prepare_durable(spec.id, spec.version)
                seed.update(task_id=spec.id, spec_version=spec.version, run_id=run_id)
            else:
                temporary = case in ('temporary_project', 'temporary_domain_denied', 'foreign_project_denied', 'inferred_draft')
                spec = tasks.create(research_id=record.id, kind='synthesize', model='deepseek-flash', user_request=original,
                    agent_mode='temporary' if temporary else 'specialist',
                    role_instructions='Handle only the specified memory write and report its actual status.' if temporary else '',
                    memory_ids=(old.id,) if old and old.available() else (), limits=RunLimits(max_turns=10, max_calls=3, timeout_seconds=240))
                run_id = tasks.prepare_durable(spec.id, spec.version)
                seed.update(task_id=spec.id, spec_version=spec.version, run_id=run_id)
            write(seed_path, seed)
        seed = json.loads(seed_path.read_text())
        original_submit = MemoryWriter.submit
        async def submit(self, snapshot, research_id, proposal):
            result = await original_submit(self, snapshot, research_id, proposal)
            if result.get('status') == 'queued':
                values = self.memories.validate(research_id, snapshot.memory_catalog, agent=snapshot.agent)
                append(root/'intents.jsonl', {'write_id': result['write_id'], 'frozen_validation_passed': True,
                    'memory_versions_during_run': {v.id: v.version for v in values}})
                pause('after_intent')
            return result
        MemoryWriter.submit = submit
        original_complete = Checkpoints.complete
        def complete(self, frame, value, usage):
            original_complete(self, frame, value, usage)
            if frame.operation == 'tool' and frame.payload.get('name') == 'write_memory': pause('after_tool_receipt')
        Checkpoints.complete = complete
        original_publish = MemoryWriter.publish_in
        def publish(self, tx, run_id, result):
            original_publish(self, tx, run_id, result)
            pause('inside_business_commit')
        MemoryWriter.publish_in = publish
        original_commit = TaskLedger.commit
        def commit(self, value):
            result = original_commit(self, value)
            pause('after_business_commit')
            return result
        TaskLedger.commit = commit
        if 'command' in seed:
            command = await research.submit(NodeCommand.model_validate(seed['command']))
            run_id = command.run_id
        else:
            run_id = seed['run_id']
            await tasks.run_durable(seed['task_id'], seed['spec_version'], run_id)
            # A second terminal replay must neither generate nor revise memory again.
            await tasks.run_durable(seed['task_id'], seed['spec_version'], run_id)
        record = research.ledger.get(run_id) or tasks.ledger.get(run_id)
        writes = research.memories.writes.records(run_id)
        ledger = tasks.engine.ledger
        with db.transaction() as tx:
            operations = [dict(r) for r in db.connection.execute(select(ledger.operations).where(ledger.operations.c.run_id == run_id)).mappings()]
            manifests = [dict(r) for r in db.connection.execute(select(ledger.manifests).where(ledger.manifests.c.run_id == run_id)).mappings()]
            memories = [v.model_dump(mode='json') for v in tx.memories.list(owner)]
            protected_target = tx.memories.get(seed['foreign_id']).model_dump(mode='json') if seed['foreign_id'] else None
            rows = [dict(r) for r in db.connection.execute(select(ledger.checkpoints).where(ledger.checkpoints.c.run_id == run_id)).mappings()]
        http = [json.loads(v) for v in (root/'http.jsonl').read_text().splitlines()]
        intents = [json.loads(v) for v in (root/'intents.jsonl').read_text().splitlines()] if (root/'intents.jsonl').exists() else []
        evidence = {'case': seed['case'], 'run_id': run_id, 'result': record.result.model_dump(mode='json'),
            'writes': [v.model_dump(mode='json') for v in writes], 'memories': memories, 'protected_target': protected_target, 'operations': operations,
            'manifests': manifests, 'checkpoints': rows, 'http': http, 'intents': intents}
        write(root/'evidence.json', evidence)
        print(json.dumps({'case': seed['case'], 'status': record.result.status, 'writes': len(writes),
            'error': record.result.error.model_dump() if record.result.error else None}), flush=True)
    finally:
        db.close(); await client.close()


def grade(directory):
    e = json.loads((directory/'evidence.json').read_text()); s = json.loads((directory/'seed.json').read_text())
    records, memories = e['writes'], e['memories']; case = s['case']
    checks = {'run_succeeded': e['result']['status'] == 'succeeded', 'usage_confirmed': e['result']['usage']['complete'],
        'no_retry_operations': all(':retry:' not in v['id'] for v in e['operations'])}
    if case in ('temporary_domain_denied', 'foreign_project_denied', 'foreign_owner_denied', 'temporary_request', 'project_to_user_denied', 'conflicting_create_denied'):
        checks['no_write_request'] = not records
        checks['no_agent_memory'] = not any(m['id'].startswith('agent-memory:') for m in memories)
        if s['foreign_id']: checks['foreign_unchanged'] = e['protected_target']['version'] == 1
    else:
        checks['one_request'] = len(records) == 1
        checks['one_intent_execution'] = len(e['intents']) == 1
        checks['frozen_valid_during_write'] = bool(e['intents']) and all(v['frozen_validation_passed'] for v in e['intents'])
        checks['published_atomically'] = len(records) == 1 and records[0]['state'] == 'published'
        if records:
            value = next(m for m in memories if m['id'] == records[0]['resolution']['memory_id'])
            checks['namespace_bound'] = value['scope'] == s['params']['scope'] and value['scope_id'] == (
                s['project_id'] if value['scope'] == 'project' else 'expert_synthesize' if value['scope'] == 'specialist' else None)
            checks['correct_state'] = value['status'] == ('draft' if case in ('specialist_candidate', 'inferred_draft', 'forget') else 'confirmed')
            checks['correct_content'] = value['content'] == ('只给简短答案' if case == 'forget' else s['params']['content'])
            if case in ('correction', 'forget', 'draft_confirmation'):
                checks['one_revision'] = value['id'] == s['old_id'] and value['version'] == 2
                checks['old_version_preserved'] = records[0]['old_memory']['version'] == 1
                checks['current_round_not_invalidated'] = e['result']['status'] == 'succeeded'
            if case == 'specialist_candidate': checks['independent_validation_required'] = records[0]['resolution']['status'] == 'awaiting_independent_validation'
    sent = [r for r in e['http'] if r['event'] == 'sent']
    models = [r for r in e['operations'] if r['kind'] == 'model']
    checks['http_matches_model_operations'] = len(sent) == len(models)
    checks['operations_confirmed'] = all(r['status'] == 'confirmed' for r in e['operations'])
    if (directory/'kill.json').exists():
        kill = json.loads((directory/'kill.json').read_text())
        checks['real_sigkill'] = kill['exit_code'] == -signal.SIGKILL
        checks['review_not_repeated'] = sum(r['purpose'] == 'memory_write_review' for r in sent) == 1
        # Recovery probes allow only write_memory, so exactly one admitted tool
        # operation must exist, rather than merely bounding the number of calls.
        checks['one_write_tool_operation'] = len([r for r in e['operations'] if r['kind'] == 'tool']) == 1
    return {'case': case, 'passed': all(checks.values()), 'checks': checks, 'run_id': e['run_id'],
        'evidence': str(directory/'evidence.json'), 'http_sends': sent, 'usage': e['result']['usage']}


def orchestrate(args):
    root = Path(args.directory).resolve(); root.mkdir(parents=True, exist_ok=False)
    report = {'real_model': True, 'model': 'deepseek-flash', 'cases': [], 'recovery': []}
    def execute(name, case, fault=None):
        directory = root/name; directory.mkdir()
        cmd = [sys.executable, __file__, '--worker', '--database', args.database, '--directory', str(directory), '--case', case]
        with (directory/'worker.log').open('w') as log:
            if fault:
                proc = subprocess.Popen(cmd + ['--fault', fault], stdout=log, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 260
                    while not (directory/'paused').exists() and proc.poll() is None and time.monotonic() < deadline: time.sleep(.1)
                    if not (directory/'paused').exists(): raise RuntimeError('Fault marker missing: ' + name)
                    before = [json.loads(r) for r in (directory/'http.jsonl').read_text().splitlines()]
                    proc.send_signal(signal.SIGKILL)
                    exit_code = proc.wait(timeout=5)
                    if exit_code != -signal.SIGKILL: raise RuntimeError('SIGKILL not confirmed')
                    write(directory/'kill.json', {'point': fault, 'exit_code': exit_code, 'http_before': before})
                finally:
                    if proc.poll() is None: proc.kill(); proc.wait()
            completed = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=260)
            if completed.returncode: raise RuntimeError('Worker failed: ' + name)
        result = grade(directory)
        print(json.dumps({'stage': name, 'passed': result['passed'], 'checks': result['checks']}), flush=True)
        return result
    try:
        for case in (CASES if not args.only_case else (args.only_case,)):
            report['cases'].append(execute(case, case)); write(root/'summary.json', report)
        if not args.quality_only:
            for fault in FAULTS:
                report['recovery'].append(execute(fault, 'correction', fault)); write(root/'summary.json', report)
        report['passed'] = all(r['passed'] for r in report['cases'] + report['recovery'])
        if not report['passed']: raise SystemExit(1)
    except Exception as exc:
        report['failure'] = {'type': type(exc).__name__, 'message': str(exc)}; raise
    finally: write(root/'summary.json', report)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--database', required=True); p.add_argument('--directory', required=True)
    p.add_argument('--worker', action='store_true'); p.add_argument('--case', choices=CASES, default='correction')
    p.add_argument('--fault', choices=FAULTS); p.add_argument('--only-case', choices=CASES); p.add_argument('--quality-only', action='store_true')
    a = p.parse_args()
    if not a.database.rsplit('/', 1)[-1].startswith('memory_write_'): raise ValueError('Independent memory_write_ test database required')
    if a.worker: asyncio.run(worker(a))
    else: orchestrate(a)
