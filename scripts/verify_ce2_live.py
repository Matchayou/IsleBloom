"""CE2 live Responses acceptance. Requires a NEW database and output directory."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import make_url

from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources, build_controlled_services
from agent_core.contracts import RunLimits
from agent_core.controlled.workspace import Workspace
from agent_core.durable import DurableResearch
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand


async def run(args):
    if not args.allow_paid_model:
        raise ValueError('Explicit --allow-paid-model is required')
    if not os.environ.get('OPENAI_API_KEY'):
        raise ValueError('OPENAI_API_KEY is missing; no live acceptance performed')
    url = os.environ['CE2_TEST_DATABASE_URL']
    if not (make_url(url).database or '').startswith('ce2_'):
        raise ValueError('Use a fresh ce2_ database; historical databases are forbidden')
    os.environ['OPENAI_MODEL'] = os.environ['OPENAI_MANAGER_MODEL'] = args.model
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = {'stage': 'CE2', 'status': 'running', 'model': args.model, 'api': 'responses',
        'provider_http_statuses': [], 'synthetic_source': True, 'checks': {}}
    db = client = durable = None
    try:
        from agents.models.openai_provider import OpenAIProvider
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient
        from httpx2 import Timeout
        async def observe(response):
            evidence['provider_http_statuses'].append(response.status_code)
        client = AsyncOpenAI(base_url=args.base_url, max_retries=0,
            http_client=DefaultAsyncHttpxClient(timeout=Timeout(120, connect=20),
                event_hooks={'response': [observe]}))
        db = PostgresDatabase(url)  # Original OS lock and advisory lock, without overrides.
        scopes = build_resources(db, output / 'blobs', parser=TextParser())
        research, tasks, projects = build_controlled_services(db, scopes,
            private_root=output / 'private',
            model_provider=OpenAIProvider(openai_client=client, use_responses=True))
        if research.query('list_researches', research.owner):
            raise ValueError('Acceptance requires an empty fresh database')
        c = research.collaboration
        project = projects.create('CE2 live ' + uuid4().hex)
        main = await projects.create_main_task(project.id, '合成资料的独立双专家核对')
        node = research.query('list_nodes', main.research_id)[0]
        marker = 'CE2-' + uuid4().hex
        original = f'SYNTHETIC acceptance only. Marker: {marker}. Confirmed units: 7. Market size: unknown.'
        root = scopes.current(research.research(node.research_id).scope_id, research.owner)
        _, ref = scopes.ingest(root, content=original.encode(), name='ce2-source.txt', media_type='text/plain')
        evidence.update(research_id=node.research_id, node_id=node.id, source_ref=ref.model_dump(mode='json'),
            original_source=original)
        chat = ChatService(research, tasks).send(node.id,
            '这是合成验收，禁止联网。请用 find_experts 和 read_context 找到合成资料，'
            '向 expert_search 和 expert_synthesize 各派发一次独立正式任务。'
            '两份任务均要求：用 read_source 读正文，write_private_note 保存名为 ce2_draft 的私有笔记，'
            '用 read_private_note 重读；必须调用 request_manager_information 问主助手资料未注明的目标年份，'
            '然后据原文报告 Marker、已确认数量和市场规模未知，不编造。'
            '当前没有提供目标年份，咨询时应明确无信息。将上述工具步骤写入任务指令；派发后告知处理中。', 0)
        command = await research.submit(NodeCommand(command_id='live-main:' + uuid4().hex,
            research_id=node.research_id, node_id=node.id, kind='chat_reply', chat_revision=chat.revision,
            limits=RunLimits(max_turns=10, max_calls=9, timeout_seconds=300)))
        first = research.ledger.get(command.run_id)
        evidence['main'] = first.result.model_dump(mode='json')
        if first.status != 'succeeded': raise RuntimeError('Main conversation failed')
        intents = c.query('intents_for', node.research_id)
        if len(intents) != 2 or {tasks.spec(i.task_id).expert_id for i in intents} != {'expert_search', 'expert_synthesize'}:
            raise RuntimeError('Manager did not admit exactly the two required expert tasks')
        durable = DurableResearch(tasks, url)
        await durable.start()
        c.durable = durable
        await c.recover_pending()
        experts = []
        for intent in intents:
            current = c.query('intent', intent.command_id)
            admitted = tasks.query('list_runs', intent.task_id)
            if len(admitted) != 1: raise RuntimeError('Expected one fixed run per expert')
            record = tasks.ledger.get(admitted[0].id)
            receipt = tasks.query('receipt', 'task-result:' + record.run_id)
            binding = tasks.engine.ledger.binding(record.run_id)
            work = Workspace(tasks.engine.private_root / tasks.engine.workspace_id(binding))
            try:
                frames = [json.loads(work.read(row[0])['text']) for row in work.db.execute(
                    "SELECT name FROM notes WHERE name LIKE 'transcript_%_request' ORDER BY rowid")]
                called = [f['payload']['name'] for f in frames if f['operation'] in ('tool', 'private')]
                note = work.read('ce2_draft')
            finally:
                work.close()
            before = tasks.engine.ledger.usage(record.run_id)
            replay = tasks.retry_save(record.run_id)
            information = c.query('information_for', intent.task_id)
            row = {'task_id': intent.task_id, 'session_id': intent.session_id, 'run_id': record.run_id,
                'intent_status': current.status, 'result': record.result.model_dump(mode='json'),
                'receipt': receipt.model_dump(mode='json') if receipt else None, 'called_tools': called,
                'note': note, 'information': [v.model_dump(mode='json') for v in information],
                'save_retry_same_result': replay.result == record.result,
                'save_retry_no_model': tasks.engine.ledger.usage(record.run_id) == before}
            experts.append(row)
        evidence['experts'] = experts
        await c.resume_manager()
        chat = ChatService(research, tasks).read(node.id)
        evidence['resumed_chat'] = chat.model_dump(mode='json')
        target = intents[0]
        thread = projects.expert_thread(target.task_id)
        discussion = await projects.discuss(target.task_id, '简短解释为什么市场规模仍然未知；不重做任务。', thread.revision)
        evidence['discussion'] = discussion.messages[-1].model_dump(mode='json')
        with db.transaction():
            bindings = list(db.connection.scalars(select(tasks.engine.ledger.runs.c.binding)))
        evidence['bindings'] = bindings
        required = {'read_source', 'write_private_note', 'read_private_note', 'request_manager_information'}
        evidence['checks'] = {
            'all_four_entrances': {'manager', 'expert', 'consultation', 'discussion'} <= {b['entrance'] for b in bindings},
            'manager_resumed': len(research.query('list_runs', node.id)) >= 2,
            'two_expert_sessions': len({e['session_id'] for e in experts}) == 2,
            'expert_success': all(e['intent_status'] == 'completed' and e['result']['status'] == 'succeeded' for e in experts),
            'required_tools': all(required <= set(e['called_tools']) for e in experts),
            'source_marker_and_note': all(marker in json.dumps(e['result']['output']) and marker in e['note']['text'] for e in experts),
            'consultations_resolved': all(e['information'] and e['information'][0]['status'] == 'resolved' for e in experts),
            'save_retry_without_model': all(e['save_retry_same_result'] and e['save_retry_no_model'] for e in experts),
            'pending_independent_review': all(e['receipt'] and e['receipt']['delivery'] == 'pending_review'
                and e['receipt']['adoption'] == 'for_discussion' for e in experts),
            'real_http_success': bool(evidence['provider_http_statuses']) and all(s == 200 for s in evidence['provider_http_statuses']),
        }
        evidence['status'] = 'control_checks_passed_semantic_review_required' if all(evidence['checks'].values()) else 'failed'
        evidence['semantic_review'] = 'Compare both full reports, consultation and synthesis to original_source. Regex/tool success is not semantic acceptance.'
        if not all(evidence['checks'].values()): raise RuntimeError('Live CE2 acceptance checks failed')
    except BaseException as exc:
        evidence.update(status='failed', failure_type=type(exc).__name__)
        raise
    finally:
        (output / 'live.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        if durable: durable.close()
        if client: await client.close()
        if db: db.close()
    print(json.dumps({'status': evidence['status'], 'evidence': str(output / 'live.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-paid-model', action='store_true')
    parser.add_argument('--model', default='deepseek-flash')
    parser.add_argument('--base-url', default='https://api.deepseek.com')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception as exc:
        # Provider exception text can include request data; emit class only.
        print(json.dumps({'status': 'not_passed', 'error_type': type(exc).__name__}))
        raise SystemExit(1)
