"""Real PostgreSQL and SDK contract; deterministic responses, not a live LLM."""
import json
import os
from uuid import uuid4
import pytest
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_controlled_services
from agent_core.coding.models import CODING_TOOLS
from agent_core.contracts import RunLimits
from test_coding import coding
from test_controlled_collaboration import Provider, answer, call

async def test_coding_through_broker_receipt_and_save_retry(coding,tmp_path):
    url=os.environ.get('CE5_TEST_DATABASE_URL')
    if not url:pytest.skip('Fresh CE5 PostgreSQL required')
    service,admit,_=coding
    provider=Provider();db=PostgresDatabase(url)
    try:
        scopes=build_resources(db,tmp_path/'blobs')
        research,tasks,_=build_controlled_services(db,scopes,private_root=tmp_path/'private',model_provider=provider,coding=service)
        root=research.create('CE5 '+uuid4().hex);scope=scopes.current(root.scope_id,'local')
        scopes._replace(scope,tools=tuple(sorted(CODING_TOOLS)))
        contract=admit().contract
        spec=tasks.create(research_id=root.id,kind='coding',user_request='Change a and add new file; test and freeze.',
            coding=contract,limits=RunLimits(max_turns=10,max_calls=12,timeout_seconds=60))
        async def finish(kwargs):
            records=[json.loads(v['output'])['value'] for v in kwargs['input'] if v.get('type')=='function_call_output']
            candidate_ref=next(v['candidate_ref'] for v in reversed(records) if isinstance(v,dict) and v.get('candidate_ref'))
            return answer({'report':'Fixed code, approved syntax validation only; awaiting independent review.', 'candidate_ref':candidate_ref})
        provider.plans.append([call('search_tools',query='code_status write_code test_code freeze_code',limit=10),
            call('code_status'),call('write_code',path='a.py',text='ANSWER = 3\n'),
            call('write_code',path='new.py',text='NEW = True\n'),call('test_code',profile='syntax'),call('freeze_code'),finish])
        receipt=(await tasks.run(spec.id))['receipt']
        record=tasks.ledger.get(receipt.run_id)
        assert record.result.status=='succeeded',record.result
        assert receipt.delivery=='pending_review' and receipt.code_candidate
        assert 'new.py' in receipt.code_candidate.files
        assert tasks.read_artifact(receipt.result_id)['output']['candidate']==receipt.code_candidate.model_dump(mode='json')
        count=len(provider.requests);tasks.executors.clear();tasks.retry_save(receipt.run_id)
        assert len(provider.requests)==count
        assert tasks.engine.ledger.run_budget(receipt.run_id)['tools']==6
    finally:db.close()

async def test_manager_selects_only_granted_contract_and_reads_saved_diff(coding,tmp_path):
    from agent_core.research.chat import ChatService
    from agent_core.context.scopes import ScopeDenied
    url=os.environ.get('CE5_TEST_DATABASE_URL')
    if not url:pytest.skip('Fresh CE5 PostgreSQL required')
    service,admit,_=coding;provider=Provider();db=PostgresDatabase(url)
    try:
        scopes=build_resources(db,tmp_path/'blobs')
        research,tasks,projects=build_controlled_services(db,scopes,private_root=tmp_path/'private',model_provider=provider,coding=service)
        project=projects.create('Coding manager '+uuid4().hex)
        main=await projects.create_main_task(project.id,'Controlled coding')
        node=research.query('list_nodes',main.research_id)[0];c=research.collaboration
        assert not any(e['kind']=='coding' for e in c.find_experts(main.research_id))
        tasks.grant_coding(main.research_id,'fix-a',admit().contract)
        assert next(e for e in c.find_experts(main.research_id) if e['kind']=='coding')['coding_contracts']['fix-a']
        ChatService(research,tasks).send(node.id,'Fix the authorized a.py.',0)
        args=dict(command_id=uuid4().hex,node_id=node.id,expert_id='expert_coding',
            task_instruction='Change a.py then test and freeze',delivery_requirements='Fixed code reference',coding_contract_id='fix-a')
        intent=c.delegate_task(**args);assert c.delegate_task(**args)==intent
        with pytest.raises(ScopeDenied):c.delegate_task(**{**args,'command_id':uuid4().hex,'coding_contract_id':'foreign'})
        async def finish(kwargs):
            values=[json.loads(i['output'])['value'] for i in kwargs['input'] if i.get('type')=='function_call_output']
            ref=next(v['candidate_ref'] for v in reversed(values) if v.get('candidate_ref'))
            return answer({'report':'Syntax checked, no runtime execution; awaiting review.','candidate_ref':ref})
        provider.plans.append([call('search_tools',query='write_code test_code freeze_code',limit=10),
            call('write_code',path='a.py',text='ANSWER = 7\n'),
            call('test_code',profile='syntax'),call('freeze_code'),finish])
        await c.execute(intent.command_id)
        run=tasks.query('list_runs',intent.task_id)[0];receipt=tasks.receipt('task-result:'+run.id)
        assert receipt.delivery=='pending_review'
        artifact=tasks.read_artifact(receipt.result_id)
        assert '+ANSWER = 7' in artifact['output']['code_patch']
        read=c.read_context(node.id,result_ids=(receipt.result_id,))
        assert 'ANSWER = 7' in json.dumps(read)
        scope=scopes.current(research.research(main.research_id).scope_id,'local')
        scopes._replace(scope,tools=())
        assert not tasks.coding_grants(main.research_id)
        with pytest.raises(ScopeDenied):tasks.read_artifact(receipt.result_id)
    finally:db.close()
