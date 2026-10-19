"""CE7 fault worker. Fault hooks live only in this acceptance harness."""
import argparse, asyncio, json, os, time
from pathlib import Path
from sqlalchemy import event, select
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_controlled_services
from agent_core.adapters.parsing.text import TextParser
from agent_core.contracts import RunLimits
from agent_core.controlled.checkpoints import Checkpoints
from agent_core.controlled.ledger import digest


def append(path, value):
    with path.open('a') as f:
        f.write(json.dumps(value, ensure_ascii=False) + '\n'); f.flush(); os.fsync(f.fileno())

async def main(a):
    root=Path(a.directory); root.mkdir(parents=True,exist_ok=True)
    def pause(point):
        if a.fault==point:
            (root/'paused').write_text(point)
            while True: time.sleep(.1)
    class Provider:
        def get_model(self, name):
            class Model:
                async def get_response(self, **kw):
                    from agents import ModelResponse
                    from agents.usage import Usage
                    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
                    pause('before_request')
                    append(root/'requests.jsonl', {'model':name})
                    has_result=any(x.get('type')=='function_call_output' for x in kw['input'])
                    output=([ResponseOutputMessage(type='message',id='final',role='assistant',status='completed',
                        content=[ResponseOutputText(type='output_text',text=json.dumps({'report':'固定记录：7 台设备；收入未知。笔记已保存，尚待审核。'}),annotations=[])])]
                        if has_result else [ResponseFunctionToolCall(type='function_call',name='write_private_note',call_id='ce7-write',
                            arguments=json.dumps({'name':'ce7_note','text':'固定 v1：7 台设备；收入未知。'}))])
                    response=ModelResponse(output=output,usage=Usage(requests=1,input_tokens=3,output_tokens=4),response_id=None)
                    pause('after_response')
                    return response
            return Model()
    client=None
    if a.live:
        from openai import AsyncOpenAI,DefaultAsyncHttpxClient
        from agents.models.openai_provider import OpenAIProvider
        from agent_core.context.audit import audit_http_request,audit_http_response
        async def sent(request):
            await audit_http_request(request)
            append(root/'requests.jsonl', {'sent':True})
        async def received(response):
            await audit_http_response(response)
            append(root/'http.jsonl', {'status':response.status_code})
            pause('after_response')
        client=AsyncOpenAI(base_url='https://api.deepseek.com',max_retries=0,http_client=DefaultAsyncHttpxClient(
            timeout=120,event_hooks={'request':[sent],'response':[received]}))
        provider=OpenAIProvider(openai_client=client,use_responses=True)
    else: provider=Provider()
    db=PostgresDatabase(os.environ['CE7_TEST_DATABASE_URL'])
    durable=None
    try:
        scopes=build_resources(db,root/'blobs',parser=TextParser())
        research,tasks,_=build_controlled_services(db,scopes,private_root=root/'private',model_provider=provider,
            owner='ce7-recovery:' + digest(str(root.resolve())))
        seed=root/'seed.json'
        if not seed.exists():
            record=research.create('CE7 recovery acceptance')
            spec=tasks.create(research_id=record.id,kind='synthesize',model='deepseek-flash' if a.live else 'fixture',
                user_request='已授权固定事实：v1 是 7 台设备，收入未知。只调用一次 write_private_note，name=ce7_note，text=固定 v1：7 台设备；收入未知。然后交付简短中文报告，保留未知，不声称已审或采用。',
                limits=RunLimits(max_turns=8,max_calls=5,timeout_seconds=600))
            run_id=tasks.prepare_durable(spec.id,spec.version)
            seed.write_text(json.dumps({'task_id':spec.id,'version':spec.version,'run_id':run_id}))
        info=json.loads(seed.read_text())
        original_complete=Checkpoints.complete
        def complete(self, frame, value, usage):
            original_complete(self,frame,value,usage)
            if frame.operation=='model': pause('after_receipt')
            if frame.operation=='private': pause('after_tool_receipt')
        Checkpoints.complete=complete
        original_publish=Checkpoints.publish
        def publish(self, value):
            original_publish(self,value)
            if value['kind']=='state':pause('sdk_pause')
            if value['kind']=='result':pause('result_saved')
        Checkpoints.publish=publish
        from agent_core.controlled.workspace import Workspace
        original_save=Workspace.save
        def save(self,name,text):
            result=original_save(self,name,text)
            if name=='ce7_note':
                append(root/'effects.jsonl',{'name':name,'sha256':result['sha256']})
                pause('after_effect')
            return result
        Workspace.save=save
        def query(conn,cursor,statement,parameters,context,executemany):
            if statement.startswith('INSERT INTO controlled_checkpoints') and parameters.get('key','').startswith('receipt:model:'):
                pause('inside_transaction')
        event.listen(db.connection,'after_cursor_execute',query)
        if a.dbos:
            from agent_core.durable import DurableResearch
            durable=await DurableResearch(tasks,os.environ['CE7_TEST_DATABASE_URL']).start()
            await durable.submit('ce7-'+info['run_id'],[info['task_id']])
            while True:
                status=await durable.status('ce7-'+info['run_id'])
                if status['status'] in ('SUCCESS','ERROR','CANCELLED'):break
                await asyncio.sleep(.1)
            (root/'dbos.json').write_text(json.dumps(status,ensure_ascii=False,indent=2,default=str))
        else:
            await tasks.run_durable(info['task_id'],info['version'],info['run_id'])
        ledger=tasks.engine.ledger
        commit=tasks.ledger.get(info['run_id'])
        receipt=tasks.query('receipt','task-result:'+info['run_id'])
        with db.transaction():
            operations=[dict(r) for r in db.connection.execute(select(ledger.operations).where(ledger.operations.c.run_id==info['run_id'])).mappings()]
            checkpoints=[dict(r) for r in db.connection.execute(select(ledger.checkpoints).where(ledger.checkpoints.c.run_id==info['run_id'])).mappings()]
            manifests=[dict(r) for r in db.connection.execute(select(ledger.manifests).where(ledger.manifests.c.run_id==info['run_id'])).mappings()]
        evidence={'mode':'live' if a.live else 'fixture','result':commit.result.model_dump(mode='json') if commit else None,
            'receipt':receipt.model_dump(mode='json') if receipt else None,'budget':ledger.run_budget(info['run_id']),
            'operations':operations,'checkpoints':checkpoints,'manifests':manifests}
        (root/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str))
        print(json.dumps({'status':commit.result.status,'error':commit.result.error.model_dump() if commit.result.error else None}))
    finally:
        if durable:durable.close()
        db.close()
        if client:await client.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--directory',required=True);p.add_argument('--fault');p.add_argument('--live',action='store_true');p.add_argument('--dbos',action='store_true');p.add_argument('--hidden-key',action='store_true');a=p.parse_args()
    if a.hidden_key:
        import getpass
        os.environ['OPENAI_API_KEY']=getpass.getpass('DeepSeek credential (hidden): ')
    asyncio.run(main(a))
