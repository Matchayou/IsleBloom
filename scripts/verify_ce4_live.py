"""Explicit paid CE4 acceptance: real model must finalize under frozen investigation limits."""
import argparse,asyncio,json,os
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.engine import make_url
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources,build_controlled_services
from agent_core.context.audit import audit_http_request
from agent_core.context.projection import sha,dumps
from agent_core.contracts import RunLimits

async def run(args):
    if not args.allow_paid_model or not os.environ.get('OPENAI_API_KEY'):raise ValueError('Paid model authorization and credential required')
    url=os.environ['CE4_TEST_DATABASE_URL']
    if not make_url(url).database.startswith('ce4_'):raise ValueError('Fresh CE4 database required')
    root=Path(args.output).resolve();root.mkdir(parents=True,exist_ok=False)
    os.environ['OPENAI_MODEL']=os.environ['OPENAI_MANAGER_MODEL']=args.model
    evidence={'stage':'CE4','status':'running','model':args.model,'synthetic':True,'http_statuses':[]}
    from openai import AsyncOpenAI,DefaultAsyncHttpxClient
    from agents.models.openai_provider import OpenAIProvider
    async def response_hook(response):evidence['http_statuses'].append(response.status_code)
    client=AsyncOpenAI(base_url='https://api.deepseek.com',max_retries=0,http_client=DefaultAsyncHttpxClient(
        timeout=120,event_hooks={'request':[audit_http_request],'response':[response_hook]}))
    db=PostgresDatabase(url)
    try:
        scopes=build_resources(db,root/'blobs',parser=TextParser())
        research,tasks,projects=build_controlled_services(db,scopes,private_root=root/'private',model_provider=OpenAIProvider(openai_client=client,use_responses=True))
        if research.query('list_researches',research.owner):raise ValueError('Database must be empty')
        project=projects.create('CE4 真实收尾验收');main=await projects.create_main_task(project.id,'固定资料与预算内交付')
        scope=scopes.current(research.research(main.research_id).scope_id,research.owner)
        marker='CE4-'+uuid4().hex
        source=f'SYNTHETIC ACCEPTANCE ONLY. Marker: {marker}. Confirmed units: 7. Market size: unknown. Year: not supplied. Count is not revenue.'
        _,ref=scopes.ingest(scope,content=source.encode(),name='ce4-source.txt',media_type='text/plain')
        spec=tasks.create(research_id=main.research_id,kind='synthesize',required_refs=(ref,),
            user_request='这是合成验收。依次调查：1. read_source 读取固定资料；2. write_private_note 保存带 marker、数量与未知项的草稿；3. read_private_note 回读；4. request_manager_information 核对年份。每次模型回复最多发起一个工具调用。不得联网，不得派发其他专家。如果系统提前进入收尾，则立即用已有证据写中文最终报告，逐项列出实际未完成的核验、市场规模未知及年份缺失，不得声称做过未执行的步骤。不要把保存说成独立复核通过或正式采用。',
            limits=RunLimits(max_turns=4,max_calls=2,timeout_seconds=180))
        receipt=(await tasks.run(spec.id))['receipt'];run_id=receipt.run_id;record=tasks.ledger.get(run_id)
        ledger=tasks.engine.ledger
        artifact=tasks.read_artifact(receipt.result_id) if receipt.artifact else None
        with db.transaction():
            manifests=[dict(r) for r in db.connection.execute(select(ledger.manifests).where(ledger.manifests.c.run_id==run_id)).mappings()]
            ops=[dict(r) for r in db.connection.execute(select(ledger.operations).where(ledger.operations.c.run_id==run_id)).mappings()]
        state=ledger.progress(run_id);usage=ledger.usage(run_id).model_dump(mode='json')
        before=len(evidence['http_statuses']);tasks.executors.clear();saved=tasks.retry_save(run_id)
        checks={'succeeded':record.result.status=='succeeded','pending_not_adopted':receipt.delivery=='pending_review' and receipt.adoption=='for_discussion',
            'reserved_finalization':state.get('finalization_reason') in ('reserved_turns','tool_budget','investigation_budget'),
            'tools_closed_at_wire':any(m['manifest']['wire_body'] is not None and not m['manifest']['wire_body'].get('tools') and 'finalizing' in m['manifest']['wire_body'].get('instructions','') for m in manifests),
            'wire_hashes':bool(manifests) and all(m['state']=='responded' and sha(m['manifest']['wire_text'])==m['manifest']['wire_sha256'] for m in manifests),
            'within_original_budget':ledger.run_budget(run_id)['models']<=4 and ledger.run_budget(run_id)['tools']<=2,
            'save_retry_no_generation':saved.status=='completed' and len(evidence['http_statuses'])==before,
            'real_http':bool(evidence['http_statuses']) and all(s==200 for s in evidence['http_statuses'])}
        evidence.update(run_id=run_id,task_id=spec.id,source=source,source_ref=ref.model_dump(mode='json'),
            receipt=receipt.model_dump(mode='json'),result=record.result.model_dump(mode='json'),
            artifact=artifact['artifact'].model_dump(mode='json') if artifact else None,phase=state,usage=usage,manifests=manifests,
            operations=[{k:v for k,v in op.items() if k not in ('created_at',)} for op in ops],checks=checks)
        if not all(checks.values()):raise RuntimeError('CE4 control acceptance failed')
        evidence['status']='control_passed_semantic_review_required'
    except BaseException as exc:
        evidence.update(status='failed',failure_type=type(exc).__name__);raise
    finally:
        (root/'live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str))
        db.close();await client.close()
    print(json.dumps({'status':evidence['status'],'evidence':str(root/'live.json')}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--allow-paid-model',action='store_true');p.add_argument('--output',required=True)
    p.add_argument('--model',default='deepseek-flash');args=p.parse_args()
    try:asyncio.run(run(args))
    except Exception as exc:print(json.dumps({'status':'failed','error_type':type(exc).__name__}));raise SystemExit(1)
