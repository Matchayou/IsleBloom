"""Frozen batches and isolated trials through the production controlled engine."""
import asyncio
from contextlib import contextmanager
import fcntl
from importlib.metadata import version
import json
import os
from pathlib import Path
import random
import sqlite3
import time
from uuid import uuid4
from urllib.parse import urlsplit

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url

from .cases import load_cases, public_request
from .storage import digest, implementation, now, read, write


def endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use a model endpoint without credentials, query or fragment')
    return value.rstrip('/')


def plan(output, case_ids, repeats, model, base_url, seed=17, candidate=None):
    from agent_core.methods.defaults import METHODS
    if repeats < 1 or not model.strip() or len(set(case_ids)) != len(case_ids) or not case_ids:
        raise ValueError('Unique cases, a model and positive repetitions required')
    catalog = load_cases()
    cases = [catalog[key] for key in case_ids]
    prompt = Path(candidate).read_text() if candidate else None
    if prompt is not None and not prompt.strip():
        raise ValueError('Candidate instructions cannot be empty')
    if prompt == METHODS['synthesize'][1]:
        raise ValueError('Candidate instructions are identical to baseline')
    arms = ['baseline', 'candidate'] if prompt else ['baseline']
    manifest = dict(protocol='research-quality-v1', created_at=now(), batch_id=uuid4().hex,
        scope='expert_component', model=model, base_url=endpoint(base_url),
        baseline_method_version=METHODS['synthesize'][0],
        limits=dict(max_turns=14, max_calls=12, timeout_seconds=360),
        order_seed=seed, repeats=repeats, arms=arms, cases=cases,
        candidate_instructions=prompt, implementation=implementation(),
        packages={name: version(name) for name in ('research-agent-core', 'openai-agents', 'openai', 'pydantic', 'sqlalchemy')})
    rng = random.Random(seed)
    pairs = [(case['id'], repeat) for case in cases for repeat in range(repeats)]
    rng.shuffle(pairs)
    trials = []
    for index, (case_id, repeat) in enumerate(pairs):
        # Alternate order within randomized pairs to balance temporal effects.
        order = arms if (index + seed) % 2 == 0 else list(reversed(arms))
        for arm in order:
            trials.append(dict(trial_id=uuid4().hex, case_id=case_id, arm=arm, repeat=repeat))
    manifest['trials'] = trials
    manifest['protocol_sha256'] = digest(manifest)
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    write(root/'manifest.json', manifest)
    for trial in trials:
        write(root/'trials'/trial['trial_id']/'trial.json', dict(**trial,
            protocol_sha256=manifest['protocol_sha256'], execution_status='planned'))
    return manifest


def validate_manifest(manifest):
    contents = {k: v for k, v in manifest.items() if k != 'protocol_sha256'}
    if digest(contents) != manifest['protocol_sha256']:
        raise ValueError('Batch manifest changed')
    if implementation() != manifest['implementation']:
        raise ValueError('Implementation changed since planning; create a new batch')
    if any(version(name) != pinned for name, pinned in manifest['packages'].items()):
        raise ValueError('Dependencies changed since planning')


@contextmanager
def batch_lock(root):
    with (Path(root)/'.run.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Batch already running') from None
        yield


def create_database(admin_url, trial_id):
    """Only create a uniquely named database; never clear or reuse an existing one."""
    url = make_url(admin_url)
    if url.drivername != 'postgresql+psycopg':
        raise ValueError('EVAL_DATABASE_ADMIN_URL must use postgresql+psycopg')
    name = 'evalq_' + trial_id
    if len(trial_id) != 32 or any(c not in '0123456789abcdef' for c in trial_id):
        raise ValueError('Invalid trial ID')
    engine = create_engine(url, isolation_level='AUTOCOMMIT', hide_parameters=True,
                           connect_args={'connect_timeout': 5})
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        engine.dispose()
    return url.set(database=name).render_as_string(hide_password=False)


def tool_transcripts(root):
    """Read content-addressed transcripts after execution; verify every blob read."""
    import hashlib
    records = []
    for index in sorted(root.glob('private/*/index.sqlite')):
        connection = sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)
        try:
            blobs = dict(connection.execute('SELECT name,digest FROM notes'))
            def payload(name):
                sha = blobs[name]
                raw = (index.parent/sha).read_bytes()
                if hashlib.sha256(raw).hexdigest() != sha:
                    raise ValueError('Transcript hash mismatch')
                return json.loads(raw)
            for name in sorted(blobs):
                if not name.startswith('transcript_') or not name.endswith('_request'):
                    continue
                frame = payload(name)
                if frame['operation'] not in ('tool', 'private'):
                    continue
                response_name = name.removesuffix('_request') + '_response'
                raw_arguments = frame['payload']['arguments']
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = None
                records.append(dict(operation_id=frame['operation_id'],
                    name=frame['payload']['name'], arguments=arguments, raw_arguments=raw_arguments,
                    response=payload(response_name) if response_name in blobs else None))
        finally:
            connection.close()
    return records


def collect(tasks, spec, root):
    """Collect even without a receipt; failed/uncertain calls remain in the ledger."""
    ledger = tasks.engine.ledger
    runs = tasks.query('list_runs', spec.id)
    collected = []
    for binding in runs:
        receipt = tasks.query('receipt', f'task-result:{binding.id}')
        record = tasks.ledger.get(binding.id)
        with tasks.database.transaction():
            def rows(table):
                return [dict(row) for row in tasks.database.connection.execute(
                    select(table).where(table.c.run_id == binding.id)).mappings()]
            budgets = [dict(row) for row in tasks.database.connection.execute(
                select(ledger.runs).where(ledger.runs.c.id == binding.id)).mappings()]
            collected.append(dict(run_id=binding.id,
                receipt=receipt.model_dump(mode='json') if receipt else None,
                result=record.result.model_dump(mode='json') if record and record.result else None,
                budget=budgets[0] if budgets else None, operations=rows(ledger.operations),
                manifests=rows(ledger.manifests), reads=rows(ledger.reads)))
        collected[-1]['progress'] = ledger.progress(binding.id)
    result = dict(runs=collected, tool_requests=[], collection_complete=False)
    try:
        result.update(tool_requests=tool_transcripts(root), collection_complete=True)
    except Exception as exc:
        result['collection_error'] = type(exc).__name__
    return result


async def execute_trial(root, manifest, trial, admin_url, provider=None):
    from agent_core.adapters.postgres import PostgresDatabase
    from agent_core.adapters.parsing.text import TextParser
    from agent_core.composition import build_resources, build_controlled_services
    from agent_core.contracts import RunLimits
    from agent_core.research.models import ChatMessage
    from agent_core.methods.models import AssetVersion

    root = Path(root).resolve()
    path = root/'trials'/trial['trial_id']
    state = read(path/'trial.json')
    if state['execution_status'] != 'planned':
        raise ValueError('A trial cannot be run twice; create a new batch')
    state.update(execution_status='running', started_at=now(), database='evalq_' + trial['trial_id'])
    write(path/'trial.json', state)
    started = time.monotonic()
    db = client = tasks = spec = None
    task_started = None
    evidence = dict(protocol_sha256=manifest['protocol_sha256'], runs=[], tool_requests=[],
                    collection_complete=False, sources=[], forbidden_refs=[])
    case = next(c for c in manifest['cases'] if c['id'] == trial['case_id'])
    try:
        db = PostgresDatabase(create_database(admin_url, trial['trial_id']))
        if provider is None:
            from openai import AsyncOpenAI, DefaultAsyncHttpxClient
            from agents.models.openai_provider import OpenAIProvider
            from agent_core.context.audit import audit_http_request, audit_http_response
            client = AsyncOpenAI(base_url=manifest['base_url'], max_retries=0,
                http_client=DefaultAsyncHttpxClient(timeout=120,
                    event_hooks={'request': [audit_http_request], 'response': [audit_http_response]}))
            provider = OpenAIProvider(openai_client=client, use_responses=True)
        scopes = build_resources(db, path/'blobs', parser=TextParser())
        research, tasks, _ = build_controlled_services(db, scopes,
            private_root=path/'private', model_provider=provider)
        record = research.create('Quality ' + case['id'])
        forbidden = None
        if case['id'] == 'Q08':
            foreign = research.create('Unrelated private B')
            scope = scopes.current(foreign.scope_id, research.owner)
            _, forbidden = scopes.ingest(scope, content=b'PRIVATE_B_CANARY_7421; secret count 66.',
                                        name='private-B.txt', media_type='text/plain')
            evidence['forbidden_refs'] = [forbidden.model_dump(mode='json')]
        refs = []
        for source in case['sources']:
            content = source['text'].replace('{{FORBIDDEN_REF}}',
                json.dumps(forbidden.model_dump(mode='json')) if forbidden else '')
            scope = scopes.current(record.scope_id, research.owner)
            _, ref = scopes.ingest(scope, content=content.encode(), name=source['name'], media_type='text/plain')
            refs.append(ref)
            evidence['sources'].append(dict(name=source['name'], text=content, ref=ref.model_dump(mode='json')))
        if trial['arm'] == 'candidate':
            original = tasks.methods.resolve('method', 'expert_synthesize')
            body = {**original.body, 'instructions': manifest['candidate_instructions']}
            if body == original.body:
                raise ValueError('Candidate instructions are identical to baseline')
            candidate = AssetVersion(category='method', asset_id=original.asset_id, version='eval-candidate',
                description='Frozen evaluation candidate', body=body, author='evaluation',
                evidence='Isolated paired evaluation only', sanitized=True)
            tasks.methods.save(candidate)
            tasks.methods.publish('method', original.asset_id, candidate.version, original.version,
                                  'evaluation', 'Isolated evaluation database')
        filler = 'Archived background without additional business facts. ' * case['padding_repeats']
        history = [ChatMessage(role='assistant', content=fact + '\n' + filler) for fact in case['history']]
        history.insert(min(5, len(history)), ChatMessage(role='user', content=case['correction']))
        spec = tasks.create(research_id=record.id, kind='synthesize', model=manifest['model'],
            user_request=public_request(case), required_refs=tuple(refs), selected_messages=history,
            limits=RunLimits(**manifest['limits']))
        evidence['spec'] = spec.model_dump(mode='json')
        write(path/'evidence.json', evidence)
        state['task_id'] = spec.id
        write(path/'trial.json', state)
        task_started = time.monotonic()
        await tasks.run(spec.id)
        state['execution_status'] = 'completed'
    except BaseException as exc:
        state.update(execution_status='interrupted' if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else 'failed',
                     failure_type=type(exc).__name__)
        # Raw exception messages may contain credentials or requests; retain type only.
        if not isinstance(exc, Exception):
            raise
    finally:
        state['execution_seconds'] = round(time.monotonic()-task_started, 3) if task_started is not None else None
        if tasks is not None and spec is not None:
            try:
                evidence.update(collect(tasks, spec, path))
                outcomes = [r.get('result') for r in evidence['runs']]
                if state['execution_status'] == 'completed' and (not outcomes or any(
                        not r or r['status'] != 'succeeded' for r in outcomes)):
                    state['execution_status'] = 'failed'
            except Exception as exc:
                evidence['collection_error'] = type(exc).__name__
        evidence = json.loads(json.dumps(evidence, default=str))
        evidence['evidence_sha256'] = digest(evidence)
        write(path/'evidence.json', evidence)
        state.update(finished_at=now(), latency_seconds=round(time.monotonic()-started, 3),
                     evidence_sha256=evidence['evidence_sha256'])
        write(path/'trial.json', state)
        if db is not None:
            db.close()
        if client is not None:
            await client.close()
    return state


async def run_batch(root, admin_url):
    root = Path(root).resolve()
    with batch_lock(root):
        manifest = read(root/'manifest.json')
        validate_manifest(manifest)
        if any(read(root/'trials'/t['trial_id']/'trial.json')['execution_status'] != 'planned' for t in manifest['trials']):
            raise ValueError('Batch already started; report it as-is and plan a new batch')
        if not os.environ.get('OPENAI_API_KEY'):
            raise ValueError('OPENAI_API_KEY is required for live execution')
        states = []
        for trial in manifest['trials']:
            validate_manifest(manifest)
            state = await execute_trial(root, manifest, trial, admin_url)
            states.append(state)
            print(json.dumps({k: state[k] for k in ('trial_id', 'case_id', 'arm', 'repeat', 'execution_status')}, ensure_ascii=False), flush=True)
        return states
