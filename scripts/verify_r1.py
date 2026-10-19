"""R1 scripted control and independent-process PostgreSQL query.

This checks persistence and handoff mechanics. It is not a real-model semantic evaluation.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from agent_core.composition import build_resources, build_research, build_tasks
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine


def script(request):
    if request.agent_id == 'expert_extract':
        return [], {'report':'青松公司 2025 年经营现金流 120 百万元，资本开支 40 百万元。',
            'fields':{'company':'青松公司','period':'2025年度','operating_cash_flow':'120',
                'capital_expenditure':'40','unit':'百万元'},
            'sources':{'operating_cash_flow':'company-1.txt:operating_cash_flow',
                'capital_expenditure':'company-1.txt:capex','unit':'company-1.txt:unit'},
            'missing':[],'limitations':[],'self_checks':{n:'passed' for n in range(1,6)}}
    return [], {'report':'经营现金流减资本开支为 80 百万元。', 'fields':{}, 'sources':{},
        'missing':[],'limitations':[],'self_checks':{1:'passed',2:'passed'}}


async def main():
    if not os.environ.get('DATABASE_URL'):
        print('DATABASE_URL for a dedicated R1 database is required',file=sys.stderr)
        return 2
    blobs = Path('.r1/blobs').resolve()
    db = PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        scopes = build_resources(db, blobs)
        research = build_research(db, scopes)
        root = research.create('R1 scripted acceptance: 青松公司现金流')
        scope = scopes.current(root.scope_id, research.owner)
        source = Path('tests/fixtures/stage4/company-1.txt')
        scope, ref = scopes.ingest(scope, content=source.read_bytes(), name=source.name, media_type='text/plain')
        engine = ScriptedEngine(script)
        tasks = build_tasks(db, scopes, research, engine)
        extract = tasks.create(research_id=root.id, kind='extract',
            user_request='提取青松公司 2025 年经营现金流、资本开支和单位', required_refs=(ref,))
        first = (await tasks.run(extract.id))['receipt']
        assert first.delivery == 'pending_review'
        assert len(engine.requests) == 1
        review = tasks.review(first.result_id, {n:('passed',f'Fixture JSON key checked for item {n}')
                                                for n in range(1,6)}, reviewer='script-fixture')
        assert review.delivery == 'passed'
        analysis = tasks.create(research_id=root.id, kind='analyze',
            user_request='分析现金流扣除资本开支', required_results=(first.result_id,))
        second = (await tasks.run(analysis.id))['receipt']
        assert second.delivery == 'pending_review'
        assert len(engine.requests) == 2
        expected = {'research_id':root.id,'extract_task_id':extract.id,'analysis_task_id':analysis.id,
            'extract_result_id':first.result_id,'analysis_result_id':second.result_id,
            'extract_receipt':review.model_dump(mode='json'),
            'analysis_receipt':second.model_dump(mode='json'),
            'model_calls':len(engine.requests)}
    finally:
        db.close()
    query = subprocess.run([sys.executable,'-m','agent_core.tasks_cli','--blobs',str(blobs),
        'show',extract.id],text=True,capture_output=True,check=True)
    reopened = json.loads(query.stdout)
    assert reopened['spec']['id'] == extract.id
    assert reopened['runs'][0]['receipt']['result_id'] == first.result_id
    assert reopened['runs'][0]['receipt']['delivery'] == 'passed'
    assert reopened['runs'][0]['execution']['commit_id'] is not None
    expected['cross_process_query'] = {'exit_code':query.returncode,
        'receipt_matches':True,'commit_id':reopened['runs'][0]['execution']['commit_id'],
        'program_checks':len(reopened['runs'][0]['receipt']['program_checks']),
        'semantic_checks':len(reopened['runs'][0]['receipt']['semantic_checks'])}
    print(json.dumps(expected,ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__': raise SystemExit(asyncio.run(main()))
