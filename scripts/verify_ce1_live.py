"""Real CE1 acceptance entrypoint; never auto-loads host credentials or starts historical services."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4
from sqlalchemy.engine import make_url

from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_research, build_ce1_tasks
from agent_core.contracts import RunLimits


async def run(args):
    url = os.environ['CE1_TEST_DATABASE_URL']
    if not (make_url(url).database or '').startswith('ce1_'):
        raise ValueError('A dedicated ce1_ PostgreSQL database is required')
    if not args.allow_paid_model:
        raise ValueError('Real model calls require explicit --allow-paid-model authorization')
    # New private output/blob directory for every acceptance attempt.
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    db = None
    client = None
    try:
        db = PostgresDatabase(url)  # Original host/process locking remains intact.
        from agent_core.adapters.parsing.text import TextParser
        scopes = build_resources(db, output / 'blobs', parser=TextParser())
        research = build_research(db, scopes)
        root = research.create('CE1 synthetic source / controlled tools and real model acceptance')
        scope = scopes.current(root.scope_id, research.owner)
        marker = 'CE1-' + uuid4().hex
        _, ref = scopes.ingest(scope, name='ce1-source.txt', media_type='text/plain',
            content=f'SYNTHETIC acceptance data only. Marker: {marker}. Confirmed units: 7. Unconfirmed units: unknown.'.encode())
        from agents.models.openai_provider import OpenAIProvider
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient
        from httpx2 import Timeout
        response_started = asyncio.Event()
        response_statuses = []
        async def observe_response(response):
            # Observe a real server response without retaining headers, credentials or body.
            response_statuses.append(response.status_code)
            if args.scenario == 'cancel' and response.status_code == 200:
                response_started.set()
                # Deterministic cancellation boundary after the real provider accepted the call,
                # before the SDK consumes it. This hook never fabricates a response.
                await asyncio.Event().wait()
        client = AsyncOpenAI(max_retries=0, http_client=DefaultAsyncHttpxClient(
            event_hooks={'response': [observe_response]}, timeout=Timeout(90, connect=20)))
        provider = OpenAIProvider(openai_client=client, use_responses=args.api == 'responses')
        tasks = build_ce1_tasks(db, scopes, research, private_root=output / 'private', model_provider=provider)
        spec = tasks.create(research_id=root.id, kind='synthesize', model=args.model,
            required_refs=(ref,), limits=RunLimits(max_turns=8,max_calls=8,timeout_seconds=120),
            user_request='使用 read_source 读取固定资料，保存名为 ce1_draft 的私有笔记并用 read_private_note 重读。'
                '报告原文 Marker、已确认数量与未知数量，区分合成验收资料与真实研究结论。不联网，不编造。')
        if args.scenario == 'cancel':
            execution = asyncio.create_task(tasks.run(spec.id))
            admitted = None
            try:
                async with asyncio.timeout(60):
                    while not execution.done():
                        runs = tasks.query('list_runs', spec.id)
                        if runs and response_started.is_set():
                            admitted = tasks.ledger.get(runs[-1].id)
                            break
                        await asyncio.sleep(0.05)
                    if admitted is None:
                        await execution
                        raise RuntimeError('No in-flight model request observed for cancellation')
                    _, _, workspace = tasks.engine.binding(admitted.request)
                    tasks.stop(spec.id)
                    outcome = await execution
                record = tasks.ledger.get(outcome['run_id'])
                receipt = outcome['receipt']
                from agent_core.execution.ports import Conflict
                persisted = False
                try: tasks.engine.ledger.check_cancelled(spec.id, spec.version)
                except Conflict: persisted = True
                late_denied = False
                try: tasks.engine.binding(admitted.request)
                except Conflict: late_denied = True
                evidence = {'scenario':'cancel','execution':'controlled_tools','run_id':record.run_id,
                    'provider_http_statuses':response_statuses,
                    'cancellation_boundary':'HTTP 200 received; SDK response consumption paused by test hook',
                    'result':record.result.model_dump(mode='json'),
                    'receipt':receipt.model_dump(mode='json') if receipt else None,
                    'checks':{'real_response_observed':response_started.is_set(),
                        'unknown_usage_retained':record.result.usage.requests >= 1 and not record.result.usage.complete,
                        'cancel_persisted':persisted,'late_binding_denied':late_denied,
                        'no_success_output':record.result.status != 'succeeded' and record.result.output is None,
                        'not_publishable':receipt is not None and receipt.delivery=='rework',
                        'execution_stopped':workspace not in tasks.engine.active and execution.done(),
                        'writer_released':tasks.engine.ledger.writer(workspace)['writer'] is None},
                    'usage_note':'Cancellation can leave a sent request with unknown provider usage; reservations remain.'}
                (output/'live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
                if not all(evidence['checks'].values()): raise RuntimeError('Cancellation acceptance failed')
                print(json.dumps({'status':'cancellation_case_passed','evidence':str(output/'live.json')}))
                return
            finally:
                if not execution.done():
                    tasks.stop(spec.id)
                    await asyncio.gather(execution,return_exceptions=True)
        outcome = await tasks.run(spec.id)
        record = tasks.ledger.get(outcome['run_id'])
        receipt = outcome['receipt']
        (output/'live.json').write_text(json.dumps({'status':record.status,
            'run_id':record.run_id, 'result':record.result.model_dump(mode='json'),
            'private_rebuild':'not yet verified', 'provider_http_statuses':response_statuses},ensure_ascii=False,indent=2))
        if record.status != 'succeeded':
            raise RuntimeError('CE1 task failed; original result retained')
        _, _, workspace = tasks.engine.binding(record.request)
        # Reopen the actual SQLite/files in another process; do not emulate storage.
        import subprocess
        import sys
        probe = """
import json,sys
from agent_core.controlled.workspace import Workspace
w=Workspace(sys.argv[1])
try:
 frames=[json.loads(w.read(row[0])['text']) for row in w.db.execute("SELECT name FROM notes WHERE name LIKE 'transcript_%_request'")]
 values=[json.loads(w.read(row[0])['text'])['value'] for row in w.db.execute("SELECT name FROM notes WHERE name LIKE 'tool_%' ORDER BY rowid")]
 print(json.dumps({'note':w.read('ce1_draft'), 'original_note_hashes':[v['sha256'] for v in values if isinstance(v,dict) and v.get('name')=='ce1_draft'], 'private_tools':[f['payload']['name'] for f in frames if f['operation']=='private'], 'source_tools':[f['payload']['name'] for f in frames if f['operation']=='tool']}))
finally: w.close()
"""
        rebuilt = subprocess.run([sys.executable, '-c', probe, str(output/'private'/workspace)],
                                 capture_output=True, text=True, timeout=15, check=True)
        notebook = json.loads(rebuilt.stdout)
        before_usage = tasks.engine.ledger.usage(record.run_id)
        replay = tasks.retry_save(record.run_id)
        evidence = {'execution':'controlled_tools','task_id':spec.id,'run_id':record.run_id,
            'private_rebuild':notebook,
            'provider_http_statuses':response_statuses,
            'status':record.status,'result':record.result.model_dump(mode='json'),
            'receipt':receipt.model_dump(mode='json') if receipt else None,
            'checks': {'succeeded':record.status=='succeeded',
                'save_retry_same_result':receipt is not None and replay.result == record.result and tasks.receipt(receipt.result_id)==receipt,
                'save_retry_no_model_call':tasks.engine.ledger.usage(record.run_id)==before_usage,
                'source_read':'read_source' in notebook['source_tools'],
                'model_called':record.result.usage.requests > 0,
                'marker_preserved':marker in json.dumps(record.result.output,ensure_ascii=False),
                'note_survived_rebuild':notebook is not None and marker in notebook['note']['text'],
                'note_hash_preserved':notebook is not None and len(notebook['original_note_hashes'])>=2 and notebook['original_note_hashes'][-1]==notebook['note']['sha256'],
                'note_tools_called':notebook is not None and {'write_private_note','read_private_note'} <= set(notebook['private_tools']),
                'not_adopted':receipt is not None and receipt.adoption=='for_discussion'},
            'limitations':['Controlled tools provide logical separation, not an OS security boundary.']}
        (output/'live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
        if not all(evidence['checks'].values()): raise RuntimeError('CE1 live checks failed; evidence retained')
        print(json.dumps({'status':'live_case_passed','evidence':str(output/'live.json')},ensure_ascii=False))
    except BaseException as exc:
        (output/'failure.json').write_text(json.dumps({'status':'failed','exception_type':type(exc).__name__,
            'note':'No success claimed; inspect retained run/result and restricted runtime diagnostics.'},indent=2))
        raise
    finally:
        if client is not None: await client.close()
        if db is not None: db.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--allow-paid-model',action='store_true')
    parser.add_argument('--model',required=True)
    parser.add_argument('--api',choices=('responses','chat_completions'),required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--scenario',choices=('complete','cancel'),default='complete')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__': main()
