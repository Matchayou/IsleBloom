"""Frozen three-arm CE8 benchmark. Arm overrides exist only in this evaluator."""
import argparse
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from sqlalchemy import select
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources, build_controlled_services
from agent_core.context.audit import audit_http_request, audit_http_response
from agent_core.context.compaction import Compactor
from agent_core.context.projection import dumps
from agent_core.contracts import Message, PreparedInput, RunLimits
from agent_core.research.models import ChatMessage

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures.json')


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def full_input(payload, maximum):
    return PreparedInput(messages=(Message(role='user', content=dumps(payload)),))


async def without_compaction(self, data, projection):
    return projection(data)


async def run(args):
    fixture = json.loads(FIXTURE.read_text())
    case = next(c for c in fixture['cases'] if c['id'] == args.case)
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    evidence = {'protocol': fixture['protocol'], 'case': args.case, 'arm': args.arm,
        'fixture_sha256': file_hash(FIXTURE), 'preregistration_sha256': file_hash(ROOT/'docs/ce8-preregistration.md'),
        'harness_sha256': file_hash(Path(__file__)), 'http_statuses': [],
        'source_sha256': {s['name']: hashlib.sha256(s['text'].encode()).hexdigest() for s in case['sources']},
        'implementation': {str(p.relative_to(ROOT)): file_hash(p) for p in sorted((ROOT/'src').rglob('*.py'))}}
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient
    from agents.models.openai_provider import OpenAIProvider
    async def status(response):
        evidence['http_statuses'].append(response.status_code)
    client = AsyncOpenAI(base_url='https://api.deepseek.com', max_retries=0,
        http_client=DefaultAsyncHttpxClient(timeout=120,
            event_hooks={'request': [audit_http_request], 'response': [audit_http_response, status]}))
    db = PostgresDatabase(os.environ['CE8_TEST_DATABASE_URL'])
    started = time.monotonic()
    try:
        scopes = build_resources(db, root/'blobs', parser=TextParser())
        research, tasks, _ = build_controlled_services(db, scopes, private_root=root/'private',
            model_provider=OpenAIProvider(openai_client=client, use_responses=True))
        if research.query('list_researches', research.owner):
            raise ValueError('Fresh database required')
        record = research.create('CE8 ' + case['id'])
        refs = []
        for source in case['sources']:
            scope = scopes.current(record.scope_id, research.owner)
            _, ref = scopes.ingest(scope, content=source['text'].encode(), name=source['name'], media_type='text/plain')
            refs.append(ref)
        filler = fixture['padding']['text'] * fixture['padding']['repeats']
        history = [ChatMessage(role='assistant', content=f'{fact}\n{filler}') for fact in case['history']]
        history.insert(5, ChatMessage(role='user', content=case['correction']))
        spec = tasks.create(research_id=record.id, kind='synthesize', required_refs=tuple(refs),
            selected_messages=history, model=fixture['model'], limits=RunLimits(**fixture['limits']),
            user_request=case['request'] + ' 先用 read_source 读取每份固定资料；历史或其摘要中也有必要信息，缺失时回查固定原文。'
                '用 write_private_note 保存核对所得，再提交报告。历史摘要只是工作记忆，保留未知、纠错与否定，不声称已审或采用。')
        evidence['spec'] = spec.model_dump(mode='json')
        # Patches are scoped to this evaluator and never install a product backend.
        from contextlib import ExitStack
        with ExitStack() as stack:
            if args.arm == 'full':
                stack.enter_context(patch('agent_core.context.builder.project_payload', full_input))
            if args.arm != 'compaction':
                stack.enter_context(patch.object(Compactor, 'prepare', without_compaction))
            outcome = await tasks.run(spec.id)
        receipt = outcome['receipt']
        ledger = tasks.engine.ledger
        result = tasks.ledger.get(receipt.run_id).result
        with db.transaction():
            def rows(table):
                return [dict(r) for r in db.connection.execute(select(table).where(table.c.run_id == receipt.run_id)).mappings()]
            manifests = rows(ledger.manifests)
            reads = rows(ledger.reads)
            operations = rows(ledger.operations)
            summaries = rows(ledger.compactions)
        events = [e.model_dump(mode='json') for e in tasks.ledger.events(receipt.run_id)]
        evidence.update(receipt=receipt.model_dump(mode='json'), result=result.model_dump(mode='json'),
            budget=ledger.run_budget(receipt.run_id), manifests=manifests, reads=reads,
            operations=operations, compactions=summaries, events=events)
        evidence['status'] = 'semantic_review_required' if result.status == 'succeeded' else 'failed'
    except BaseException as exc:
        evidence.update(status='failed', failure_type=type(exc).__name__, failure=str(exc)[:500])
        raise
    finally:
        evidence['latency_seconds'] = round(time.monotonic() - started, 3)
        (root/'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
        db.close()
        await client.close()
    print(json.dumps({'case': args.case, 'arm': args.arm, 'status': evidence['status'], 'output': str(root/'result.json')}))
    if evidence['status'] == 'failed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=('equipment', 'cashflow', 'orders'), required=True)
    parser.add_argument('--arm', choices=('full', 'projection', 'compaction'), required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--hidden-key', action='store_true')
    args = parser.parse_args()
    if args.hidden_key:
        os.environ['OPENAI_API_KEY'] = getpass.getpass('DeepSeek credential (hidden): ')
    asyncio.run(run(args))
