"""Reopen CE1 live evidence against real PostgreSQL and files; no model is called."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources, build_research, build_tasks
from agent_core.controlled.ledger import ControlledLedger
from agent_core.controlled.workspace import Workspace
from agent_core.execution.ports import Conflict


def verify(complete, cancelled, output):
    url = os.environ['CE1_TEST_DATABASE_URL']
    if not (make_url(url).database or '').startswith('ce1_'):
        raise ValueError('Dedicated CE1 database required')
    evidence = json.loads((complete/'live.json').read_text())
    cancel_evidence = json.loads((cancelled/'live.json').read_text())
    db = PostgresDatabase(url)
    try:
        scopes = build_resources(db, complete/'blobs', parser=TextParser())
        research = build_research(db, scopes)
        tasks = build_tasks(db, scopes, research)
        ledger = ControlledLedger(db)
        record = tasks.ledger.get(evidence['run_id'])
        receipt = tasks.receipt(evidence['receipt']['result_id'])
        spec = tasks.spec(receipt.task_id, receipt.spec_version)
        root = research.research(spec.research_id)
        scope = scopes.current(root.scope_id, research.owner)
        source = scopes.original(scope, spec.required_refs[0]).decode()
        marker = re.search(r'Marker: (CE1-[a-f0-9]+)\.', source).group(1)
        fields = record.result.output['fields']
        checks = {
            'result_survived_restart':record.result.model_dump(mode='json') == evidence['result'],
            'receipt_survived_restart':receipt.model_dump(mode='json') == evidence['receipt'],
            'confirmed_usage_survived':ledger.usage(record.run_id) == record.result.usage and record.result.usage.complete,
            'source_authorized_and_original':source == f'SYNTHETIC acceptance data only. Marker: {marker}. Confirmed units: 7. Unconfirmed units: unknown.',
            'marker_matches_source':fields.get('marker') == marker,
            'confirmed_count_matches_source':str(fields.get('confirmed_units')) == '7',
            'unknown_not_fabricated':fields.get('unconfirmed_units') == 'unknown',
            'synthetic_distinguished': 'synthetic' in fields.get('data_nature','').lower(),
            'not_automatically_adopted':receipt.delivery == 'pending_review' and receipt.adoption == 'for_discussion',
        }
        paths = list((complete/'private').glob('*/index.sqlite'))
        if len(paths) != 1: raise ValueError('Expected one private workspace')
        workspace = Workspace(paths[0].parent)
        try:
            checks['private_hash_survived_restart'] = workspace.read('ce1_draft')['sha256'] == evidence['private_rebuild']['note']['sha256']
        finally: workspace.close()
        checks['writer_released'] = ledger.writer(paths[0].parent.name)['writer'] is None
        cancelled_record = tasks.ledger.get(cancel_evidence['run_id'])
        cancelled_receipt = tasks.receipt(cancel_evidence['receipt']['result_id'])
        checks['cancelled_result_survived'] = cancelled_record.result.model_dump(mode='json') == cancel_evidence['result']
        checks['cancelled_receipt_survived'] = cancelled_receipt.model_dump(mode='json') == cancel_evidence['receipt']
        checks['unknown_usage_survived'] = ledger.usage(cancelled_record.run_id).requests == 1 and not ledger.usage(cancelled_record.run_id).complete
        try:
            ledger.check_cancelled(cancelled_receipt.task_id, cancelled_receipt.spec_version)
        except Conflict: checks['persistent_cancel_denies_late_work'] = True
        else: checks['persistent_cancel_denies_late_work'] = False
        with db.transaction():
            count = db.connection.scalar(select(func.count()).select_from(ledger.operations).where(
                ledger.operations.c.run_id == record.run_id, ledger.operations.c.kind == 'model'))
        checks['reopen_did_not_call_model'] = count == evidence['result']['usage']['requests']
        result = {'status':'passed' if all(checks.values()) else 'failed', 'checks':checks,
            'run_id':record.run_id,'cancelled_run_id':cancelled_record.run_id,
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'usage':ledger.usage(record.run_id).model_dump(mode='json'),
            'receipt_delivery':receipt.delivery,'model_calls_during_this_verification':0,
            'semantic_validation':'Independent comparison against the original synthetic fixture; model self_checks are not used.'}
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        if not all(checks.values()): raise RuntimeError('Persistence or fixture validation failed')
        print(json.dumps({'status':'passed','checks':len(checks),'evidence':str(output)},ensure_ascii=False))
    finally: db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--complete', type=Path, required=True)
    parser.add_argument('--cancelled', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.complete, args.cancelled, args.output)
