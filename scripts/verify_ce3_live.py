"""Paid CE3 fixed synthetic long-history and paginated-source acceptance."""
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
from agent_core.context.audit import audit_http_request
from agent_core.context.projection import dumps, sha
from agent_core.contracts import RunLimits
from agent_core.research.chat import ChatService
from agent_core.research.models import ChatMessage, NodeCommand


async def run(args):
    if not args.allow_paid_model or not os.environ.get('OPENAI_API_KEY'):
        raise ValueError('Explicit paid-model permission and credential required')
    url=os.environ['CE3_TEST_DATABASE_URL']
    if not (make_url(url).database or '').startswith('ce3_'):raise ValueError('Fresh CE3 database required')
    root=Path(args.output).resolve();root.mkdir(parents=True,exist_ok=False)
    os.environ['OPENAI_MODEL']=os.environ['OPENAI_MANAGER_MODEL']=args.model
    evidence={'stage':'CE3','status':'running','model':args.model,'http_statuses':[],'synthetic':True}
    from openai import AsyncOpenAI,DefaultAsyncHttpxClient
    from agents.models.openai_provider import OpenAIProvider
    async def response_hook(response): evidence['http_statuses'].append(response.status_code)
    client=AsyncOpenAI(base_url='https://api.deepseek.com',max_retries=0,
        http_client=DefaultAsyncHttpxClient(timeout=120,event_hooks={
            'request':[audit_http_request],'response':[response_hook]}))
    db=PostgresDatabase(url)
    try:
        scopes=build_resources(db,root/'blobs',parser=TextParser())
        research,tasks,projects=build_controlled_services(db,scopes,private_root=root/'private',
            model_provider=OpenAIProvider(openai_client=client,use_responses=True))
        if research.query('list_researches',research.owner):raise ValueError('Database must be empty')
        project=projects.create('CE3 live');main=await projects.create_main_task(project.id,'CE3 fixed synthetic source')
        node=research.query('list_nodes',main.research_id)[0]
        scope=scopes.current(research.research(node.research_id).scope_id,research.owner)
        marker='CE3-'+uuid4().hex
        source='SYNTHETIC ONLY. Start marker: '+marker+'. '+('Neutral filler with no additional facts. '*260)+'\nConfirmed units: 7. Market size: unknown. End marker: '+marker+'.'
        _,ref=scopes.ingest(scope,content=source.encode(),name='ce3-source.txt',media_type='text/plain')
        chat=ChatService(research,tasks).read(node.id)
        history=[]
        for n in range(35):
            history.append(ChatMessage(role='assistant',content='Old irrelevant draft '+str(n)+': '+('x'*4000)))
            if n==17:history.append(ChatMessage(role='user',content='中段有效约束：已确认数量不是金额；市场规模未知时不得估算。最终必须带标签「保留未知」。'))
        history.append(ChatMessage(role='user',content='这是合成验收，禁止联网、禁止派发专家。请用 read_context 列目录，读取该次返回的 /sources 目录，找到 ce3-source.txt 的 ref，再用 read_context(resource_refs=[该ref]) 读取全文。若工具返回分页，必须跟随 next_cursor，用 read_context_page 读到 complete=true，不得把预览当全文。核对开头和结尾 marker，然后报告 marker、已确认数量和市场规模。保留中段用户约束，最终只给简短结论，未知不编造。'))
        with db.transaction() as tx:
            tx.research.save_chat(chat.model_copy(update={'revision':1,'messages':tuple(history)}),0)
        result=await research.submit(NodeCommand(command_id=uuid4().hex,research_id=node.research_id,
            node_id=node.id,kind='chat_reply',chat_revision=1,evidence_required=True,
            limits=RunLimits(max_turns=12,max_calls=11,timeout_seconds=300)))
        run=research.ledger.get(result.run_id)
        snapshot=scopes.repository.snapshot(result.run_id)
        ledger=tasks.engine.ledger
        with db.transaction():
            manifests=[dict(r) for r in db.connection.execute(select(ledger.manifests).where(ledger.manifests.c.run_id==result.run_id)).mappings()]
            reads=[dict(r) for r in db.connection.execute(select(ledger.reads).where(ledger.reads.c.run_id==result.run_id)).mappings()]
        evidence.update(run_id=result.run_id,source_ref=ref.model_dump(mode='json'),original_source=source,
            source_sha256=sha(source),original_history_chars=len(dumps([m.model_dump(mode='json') for m in history])),
            projected_chars=len(snapshot.input.messages[0].content),result=run.result.model_dump(mode='json'),
            manifests=manifests,reads=reads)
        checks={'history_exceeds_old_limit':evidence['original_history_chars']>30000,
            'business_projection_fits':evidence['projected_chars']<=30000,
            'succeeded':run.result.status=='succeeded','real_http':bool(evidence['http_statuses']) and all(s==200 for s in evidence['http_statuses']),
            'wire_audited':bool(manifests) and all(m['state']=='responded' and m['manifest']['wire_body'] is not None and
                sha(m['manifest']['wire_text'])==m['manifest']['wire_sha256'] for m in manifests),
            'middle_constraint_in_every_request':all('保留未知' in dumps(m['manifest']['wire_body']['input']) for m in manifests),
            'actual_page_continuation':any(r['page']['start']>0 for r in reads)}
        evidence['checks']=checks
        if not all(checks.values()):raise RuntimeError('CE3 control acceptance failed')
        evidence['status']='control_passed_semantic_review_required'
    except BaseException as e:
        evidence.update(status='failed',failure_type=type(e).__name__)
        raise
    finally:
        (root/'live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
        db.close();await client.close()
    print(json.dumps({'status':evidence['status'],'evidence':str(root/'live.json')}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--allow-paid-model',action='store_true')
    p.add_argument('--output',required=True);p.add_argument('--model',default='deepseek-flash');args=p.parse_args()
    try:asyncio.run(run(args))
    except Exception as e:
        print(json.dumps({'status':'failed','error_type':type(e).__name__}));raise SystemExit(1)
