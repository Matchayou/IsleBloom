"""CE6 real Responses + PostgreSQL incremental memory; synthetic facts, real semantics."""
import argparse,asyncio,getpass,json,os
from pathlib import Path
from sqlalchemy import select
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources,build_controlled_services
from agent_core.context.audit import audit_http_request,audit_http_response
from agent_core.context.artifacts import ContextArtifacts
from agent_core.controlled.workspace import Workspace
from agent_core.contracts import RunLimits
from agent_core.research.models import ChatMessage

async def run(args):
    root=Path(args.output).resolve();root.mkdir(parents=True,exist_ok=False)
    evidence={'stage':'CE6','fixture':'synthetic fixed facts; real model, SDK and PostgreSQL','http_statuses':[]}
    from openai import AsyncOpenAI,DefaultAsyncHttpxClient
    from agents.models.openai_provider import OpenAIProvider
    async def status(response):evidence['http_statuses'].append(response.status_code)
    client=AsyncOpenAI(base_url='https://api.deepseek.com',max_retries=0,http_client=DefaultAsyncHttpxClient(timeout=120,
        event_hooks={'request':[audit_http_request],'response':[audit_http_response,status]}))
    db=PostgresDatabase(os.environ['CE6_TEST_DATABASE_URL'])
    try:
        scopes=build_resources(db,root/'blobs',parser=TextParser())
        research,tasks,_=build_controlled_services(db,scopes,private_root=root/'private',model_provider=OpenAIProvider(openai_client=client,use_responses=True))
        if research.query('list_researches',research.owner):raise ValueError('Fresh database required')
        record=research.create('CE6 incremental memory live');scope=scopes.current(record.scope_id,'local')
        _,ref=scopes.ingest(scope,content='固定证据 v1：2024 年登记设备为 7 台，不是收入。市场规模、2025 年可比口径均未提供。不得据此计算同比增长。'.encode(),name='fixed-v1.txt',media_type='text/plain')
        facts=['会议暂定名称为北斗项目，尚未立项。','计数固定版本为 v1，2024 年设备 7 台。',
            '早期把 7 写成收入是错误判断，不能引用。','用户纠错：7 是设备数量，不是收入；原先的收入解释撤回。',
            '固定中段事实：交付代号为青鹭，不是北斗；青鹭只是内部代号。','v2 草案的 11 台未经确认，不可替换本任务固定 v1 的 7 台。',
            '已完成：登记口径核对。未完成：索取市场规模与 2025 年可比数据。','没有市场规模资料，市场占有率未知；不允许从数量推出增长。',
            '最终报告保持中文；只报告有证据的结论。','旧追问是选择配色，当前任务是事实核对，不应回答配色。']
        history=[ChatMessage(role='assistant',content=fact+'\n'+('背景记录仅用于长度压力，不增加事实。 '*120)) for fact in facts]
        history.insert(4,ChatMessage(role='user',content='明确纠正：7 是设备数量而非收入，交付代号按后续确认的青鹭；保留未知，不做同比。'))
        spec=tasks.create(research_id=record.id,kind='synthesize',required_refs=(ref,),selected_messages=history,model='deepseek-flash',
            user_request='整理固定 v1 的中文事实核对报告。先 read_source 读取提供的固定证据，再 write_private_note 记录核对所得，最后提交报告。需要核对设备数/计数口径/代号/旧解释纠正/v2 草案适用性/已做与待做/市场规模及跨年可比性。历史摘要只是工作记忆，可回查原文。报告须保留未知和否定，不谈配色，不声称已审或采用。',
            limits=RunLimits(max_turns=14,max_calls=10,timeout_seconds=360))
        receipt=(await tasks.run(spec.id))['receipt'];ledger=tasks.engine.ledger
        output=tasks.ledger.get(receipt.run_id).result
        with db.transaction():
            attempts=list(db.connection.execute(select(ledger.compactions.c.attempt).where(ledger.compactions.c.run_id==receipt.run_id)).scalars())
            manifests=[dict(r) for r in db.connection.execute(select(ledger.manifests).where(ledger.manifests.c.run_id==receipt.run_id)).mappings()]
        workspace=ledger.binding(receipt.run_id);workspace_id=tasks.engine.workspace_id(workspace)
        work=Workspace(root/'private'/workspace_id)
        try:
            artifacts=ContextArtifacts(work,lambda:None,lambda _:None)
            summaries=[{'attempt':a,'memory':json.loads(artifacts.load_text(a['summary_artifact']))} for a in attempts if a['state']=='succeeded']
        finally:work.close()
        evidence.update(receipt=receipt.model_dump(mode='json'),result=output.model_dump(mode='json'),spec=spec.model_dump(mode='json'),
            budget=ledger.run_budget(receipt.run_id),attempts=attempts,summaries=summaries,workspace_id=workspace_id,
            manifests=manifests)
        checks={'real_http':bool(evidence['http_statuses']) and all(s==200 for s in evidence['http_statuses']),
            'delivered_pending':output.status=='succeeded' and receipt.delivery=='pending_review',
            'incremental_summaries':len(summaries)>=2,
            'summary_counted':sum(m['manifest']['purpose']=='summary' for m in manifests)>=2,
            'same_original_budget':ledger.run_budget(receipt.run_id)['models']==len(manifests)<=spec.limits.max_turns,
            'raw_provider_cache_recorded':all('usage' in m['manifest'].get('response_metadata',{}) for m in manifests)}
        evidence['checks']=checks;evidence['status']='control_passed_semantic_review_required' if all(checks.values()) else 'failed'
        if not all(checks.values()):raise ValueError('CE6 live acceptance failed')
    except BaseException as exc:evidence.update(status='failed',failure_type=type(exc).__name__);raise
    finally:
        (root/'live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str));db.close();await client.close()
    print(json.dumps({'status':evidence['status'],'output':str(root/'live.json')}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--hidden-key',action='store_true');a=p.parse_args()
    if a.hidden_key:os.environ['OPENAI_API_KEY']=getpass.getpass('DeepSeek credential (hidden): ')
    try:asyncio.run(run(a))
    except Exception as exc:print(type(exc).__name__);raise SystemExit(1)
