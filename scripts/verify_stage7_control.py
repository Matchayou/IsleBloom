"""Trace the browser feedback through a scripted, explicitly provisional comparison."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.composition import build_resources, build_research, build_tasks, build_evaluation
from agent_core.app.api import serial


async def main():
    browser = json.loads(Path('docs/stage7-browser-feedback.json').read_text())
    db = PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        scopes = build_resources(db,os.environ['STAGE7_BLOBS'])
        engine = ScriptedEngine(lambda _: ([],{
            'report':'A 公司 2025 年经营现金流为 100 千美元。',
            'fields':{'company':'A','period':'2025','operating_cash_flow':'100',
                      'capital_expenditure':'20','unit':'thousand USD'},
            'sources':{'operating_cash_flow':'source.txt line 1',
                       'capital_expenditure':'source.txt line 1','unit':'source.txt line 1'}}))
        research = build_research(db,scopes,engine)
        tasks = build_tasks(db,scopes,research,engine)
        evaluation = build_evaluation(db,tasks)
        feedback = evaluation.feedback(browser['feedback_id'])
        before = tasks.receipt(feedback.result_id)
        spec = tasks.spec(before.task_id,before.spec_version)
        root = research.research(spec.research_id)
        scope = scopes.current(root.scope_id,research.owner)
        source = scopes.original(scope,before.resource_refs[0]).decode()
        original = tasks.ledger.get(before.run_id).result.output
        assert 'thousand USD' in source and original['fields']['unit'] == 'million USD'
        assert next(c for c in before.program_checks if c.number == 4).status == 'passed'
        checked = evaluation.adjudicate(feedback.id,status='verified',attribution='checker',
            finding='Control fixture source says thousand USD; output says million USD; program item 4 passed presence only',
            reviewer='scripted-control-audit',acceptance_number=4)
        case = evaluation.freeze(checked.id,split='development',dimension='checker')
        after_spec = tasks.create(research_id=spec.research_id,node_id=spec.node_id,
            kind=spec.kind,user_request=spec.user_request,goal=spec.goal,
            required_refs=spec.required_refs,required_results=spec.required_results,
            model='scripted-corrected-control')
        after = (await tasks.run(after_spec.id))['receipt']
        assert tasks.ledger.get(after.run_id).result.output['fields']['unit'] == 'thousand USD'
        experiment = evaluation.create_experiment(experiment_id=str(uuid4()),
            case_ids=(case.id,),arms=('before','after'),quality_floor=1,
            max_total_cost_usd=.01,min_quality_gain=1)
        for arm,receipt,score in (('before',before,'failed'),('after',after,'passed')):
            evaluation.record_trial(trial_id=str(uuid4()),experiment_id=experiment.id,
                case_id=case.id,result_id=receipt.result_id,arm=arm,score=score,
                reviewer='scripted-control-audit',evidence='Compared fixed source line 1 to frozen output unit',
                cost_usd=0,cost_basis='estimated')
        comparison = evaluation.compare(experiment.id)
        assert comparison['verdict'] == 'provisional_estimated_cost'
        report={'control':'scripted, not real model or human semantic signoff',
            'source':source,'before_unit':original['fields']['unit'],
            'after_unit':tasks.ledger.get(after.run_id).result.output['fields']['unit'],
            'feedback':checked,'case':case,'comparison':comparison}
        Path('docs/stage7-control-case.json').write_text(json.dumps(serial(report),ensure_ascii=False,indent=2))
        print(json.dumps({'feedback_id':feedback.id,'case_id':case.id,
            'experiment_id':experiment.id,'verdict':comparison['verdict']},ensure_ascii=False))
    finally: db.close()


if __name__ == '__main__': asyncio.run(main())
