"""Actual Responses/PostgreSQL probes; SIGKILL only this script's child workers."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4
from dotenv import load_dotenv
from sqlalchemy import select
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from agents.models.openai_provider import OpenAIProvider
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources, build_controlled_services
from agent_core.context.selection import ContextSelector
from agent_core.context.skills import SkillCatalog
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand
from agent_core.contracts import RunLimits
from agent_core.memories.capture import MemoryCapturer
from agent_core.memories.writing import MemoryWriter
from agent_core.controlled.checkpoints import Checkpoints
from agent_core.context.scopes import ScopeDenied
from agent_core.context.memory_budget import MEMORY_TOKEN_BUDGET

CASES=('user','project','temporary','budget')
FAULTS=('after_ready','after_intent','after_tool_receipt','inside_commit','after_commit')
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')
def append(path,value):
    with path.open('a') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
async def worker(a):
    load_dotenv('.env',override=False)
    os.environ['OPENAI_MANAGER_MODEL']='deepseek-flash'
    root=Path(a.directory).resolve();root.mkdir(parents=True,exist_ok=True)
    def pause(point):
        if a.fault==point:
            (root/'paused').write_text(point)
            while True:time.sleep(.1)
    async def sent(request):
        body=json.loads(request.content);raw=json.dumps(body)
        purpose='review' if 'Review a proposed long-term memory write.' in raw else 'selection' if 'Select historical memories relevant' in raw else 'capture' if 'Extract at most one durable memory candidate' in raw else 'source'
        append(root/'http.jsonl',{'purpose':purpose,'model':body.get('model')})
    client=AsyncOpenAI(max_retries=0,http_client=DefaultAsyncHttpxClient(event_hooks={'request':[sent]}))
    db=PostgresDatabase(a.database);owner='capture-live:'+hashlib.sha256(str(root).encode()).hexdigest()
    try:
        scopes=build_resources(db,root/'blobs',parser=TextParser())
        research,tasks,projects=build_controlled_services(db,scopes,private_root=root/'private',
            model_provider=OpenAIProvider(openai_client=client,use_responses=True),owner=owner)
        research.context.selector=ContextSelector(SkillCatalog(root/'empty-skills'))
        seed_path=root/'seed.json'
        if not seed_path.exists():
            record=research.create('Capture acceptance '+uuid4().hex)
            project=projects.create('Synthetic current project');await projects.create_main_task(project.id,record.goal,record.id)
            other=projects.create('Synthetic foreign project')
            foreign=research.memories.create(kind='preference',content='FOREIGN_PROJECT_SECRET',source='Synthetic',scope='project',scope_id=other.id,confirmed=True)
            original={'user':'以后所有回答请先给结论，再解释原因。',
                'project':'这个项目以后统一使用人民币万元口径。',
                'temporary':'仅本次回答短一点，不是长期偏好。只确认收到，不调用工具。',
                'budget':'以后先给结论，再解释原因，保留必要细节。'}[a.case]
            original += ' 本轮仅确认收到，不产生资料事实或引用，claims、blocking_missing、limitations、files均为空。'
            large=None;small=None
            if a.case=='budget':
                large=research.memories.create(kind='preference',content='回答时先给结论，再解释原因。'+('解释须涵盖必要细节。'*160),source='Synthetic',confirmed=True)
                small=research.memories.create(kind='preference',content='先给结论，再解释原因，保留必要细节。',source='Synthetic',confirmed=True)
            chat_service=ChatService(research,tasks);chat=await chat_service.start(record.id,record.goal)
            chat=chat_service.send(chat.node_id,original,chat.revision)
            command=NodeCommand(command_id=uuid4().hex,research_id=record.id,node_id=chat.node_id,kind='chat_reply',chat_revision=chat.revision,
                limits=RunLimits(max_turns=8,max_calls=0,timeout_seconds=180))
            save(seed_path,{'research_id':record.id,'project_id':project.id,'foreign_id':foreign.id,'command':command.model_dump(mode='json'),
                'large_id':large.id if large else None,'small_id':small.id if small else None,'message_id':chat.messages[-1].id})
        seed=json.loads(seed_path.read_text());command=await research.submit(NodeCommand.model_validate(seed['command']))
        source_run=research.ledger.get(command.run_id)
        if source_run.result.status!='succeeded':raise RuntimeError('Source failed: '+str(source_run.result.error))
        capturer=research.memories.capture;jobs=capturer.records()
        if len(jobs)!=1:raise RuntimeError('Expected one queued message capture')
        job=jobs[0]
        original_prepare=MemoryCapturer.prepare
        def prepare(self,value):
            ready=original_prepare(self,value);pause('after_ready');return ready
        MemoryCapturer.prepare=prepare
        original_submit=MemoryWriter.submit
        async def submit(self,snapshot,research_id,args):
            value=await original_submit(self,snapshot,research_id,args)
            if value.get('status')=='queued':append(root/'intents.jsonl',{'write_id':value['write_id']});pause('after_intent')
            return value
        MemoryWriter.submit=submit
        original_complete=Checkpoints.complete
        def complete(self,frame,value,usage):
            original_complete(self,frame,value,usage)
            if frame.operation=='tool' and frame.payload.get('name')=='write_memory':pause('after_tool_receipt')
        Checkpoints.complete=complete
        original_publish=MemoryWriter.publish_in
        def publish(self,tx,run_id,result):
            original_publish(self,tx,run_id,result)
            if run_id!=command.run_id:pause('inside_commit')
        MemoryWriter.publish_in=publish
        original_finish=MemoryCapturer.finish
        def finish(self,value,result):
            done=original_finish(self,value,result);pause('after_commit');return done
        MemoryCapturer.finish=finish
        done=await capturer.process(job.id)
        before=(root/'http.jsonl').read_text()
        repeated=await capturer.process(job.id)
        repeat_http_unchanged=before==(root/'http.jsonl').read_text()
        from agent_core.memories.service import MemoryService
        other_service=MemoryService(db,owner+':other',research)
        try:await MemoryCapturer(other_service).process(job.id);foreign_denied=False
        except ScopeDenied:foreign_denied=True
        writes=research.memories.writes.records(done.request.run_id) if done.request else []
        ledger=tasks.engine.ledger
        with db.transaction() as tx:
            memories=[v.model_dump(mode='json') for v in tx.memories.list(owner)]
            foreign_after=tx.memories.get(seed['foreign_id']).model_dump(mode='json')
            operations=[dict(v) for v in db.connection.execute(select(ledger.operations).where(ledger.operations.c.run_id==done.request.run_id)).mappings()] if done.request else []
            checkpoints=[dict(v) for v in db.connection.execute(select(ledger.checkpoints).where(ledger.checkpoints.c.run_id.in_([done.request.run_id,command.run_id]))).mappings()] if done.request else []
        checkpoint_values=[]
        for work in (root/'private').iterdir():
            for row in checkpoints:
                path=work/'context'/row['artifact']
                if path.exists():
                    raw=path.read_bytes()
                    if hashlib.sha256(raw).hexdigest()!=row['artifact']:raise RuntimeError('Artifact hash mismatch')
                    checkpoint_values.append(json.loads(raw))
        save(root/'evidence.json',{'case':a.case,'capture':done.model_dump(mode='json'),'writes':[v.model_dump(mode='json') for v in writes],
            'memories':memories,'foreign_after':foreign_after,'foreign_denied':foreign_denied,'repeat_http_unchanged':repeat_http_unchanged,
            'checkpoint_values':checkpoint_values,'operations':operations,'checkpoints':checkpoints,'http':[json.loads(v) for v in before.splitlines()],
            'intents':(root/'intents.jsonl').read_text().splitlines() if (root/'intents.jsonl').exists() else [],'source_run_id':command.run_id})
    finally:db.close();await client.close()
def grade(root):
    e=json.loads((root/'evidence.json').read_text());seed=json.loads((root/'seed.json').read_text());case=e['case']
    writes=e['writes'];c=e['capture']
    checks={'completed':c['state']=='completed','usage_confirmed':bool(c['result'] and c['result']['usage']['complete']),
        'repeat_no_model':e['repeat_http_unchanged'],'owner_denied':e['foreign_denied'],'foreign_project_unchanged':e['foreign_after']['version']==1,
        'all_operations_confirmed':all(v['status']=='confirmed' for v in e['operations']),
        'no_retry_operations':all(':retry:' not in v['id'] for v in e['operations']),
        'no_foreign_content':'FOREIGN_PROJECT_SECRET' not in json.dumps(e['capture']['request']) and not any('FOREIGN_PROJECT_SECRET' in json.dumps(v) for v in e['checkpoint_values'])}
    if case in ('user','project'):
        checks['one_write']=len(writes)==1 and len(e['intents'])==1
        checks['draft_only']=len(writes)==1 and writes[0]['resolution']['status']=='draft' and not writes[0]['confirmed']
        checks['scope']=len(writes)==1 and writes[0]['request']['scope']==case and writes[0]['scope_id']==(seed['project_id'] if case=='project' else None)
    else:checks['no_new_memory']=not writes
    if case=='budget':
        # Check checkpoint artifact from disk; the ledger holds immutable refs.
        budgets=[]
        for work in (root/'private').iterdir():
            for row in e['checkpoints']:
                if row['key']=='memory:initial' and (work/'context'/row['artifact']).exists():
                    value=json.loads((work/'context'/row['artifact']).read_text())['value']
                    if 'budget' in value:budgets.append(value)
        checks['memory_budget_recorded']=bool(budgets) and all(v['budget']['token_upper_bound']<=MEMORY_TOKEN_BUDGET for v in budgets)
        checks['large_optional_omitted']=any(any(r['id']==seed['large_id'] for r in v['budget']['omitted']) for v in budgets)
        checks['complete_small_body']=any(any(m['id']==seed['small_id'] and m['content']=='先给结论，再解释原因，保留必要细节。' for m in v['approved_memory']) for v in budgets)
    if (root/'kill.json').exists():
        checks['sigkill']=json.loads((root/'kill.json').read_text())['exit_code']==-signal.SIGKILL
        checks['capture_rounds_once']=sum(v['purpose']=='capture' for v in e['http'])==2
        checks['review_once']=sum(v['purpose']=='review' for v in e['http'])==1
        checks['tool_once']=sum(v['kind']=='tool' for v in e['operations'])==1
    return {'case':case,'passed':all(checks.values()),'checks':checks}
def orchestrate(a):
    root=Path(a.directory).resolve();root.mkdir(parents=True,exist_ok=False);report={'cases':[],'recovery':[]}
    def execute(name,case,fault=None):
        d=root/name;d.mkdir();cmd=[sys.executable,__file__,'--worker','--database',a.database,'--directory',str(d),'--case',case]
        with (d/'worker.log').open('a') as log:
            if fault:
                p=subprocess.Popen(cmd+['--fault',fault],stdout=log,stderr=subprocess.STDOUT);deadline=time.monotonic()+240
                try:
                    while not (d/'paused').exists() and p.poll() is None and time.monotonic()<deadline:time.sleep(.1)
                    if not (d/'paused').exists():raise RuntimeError('Fault marker missing: '+name)
                    p.send_signal(signal.SIGKILL);code=p.wait(timeout=5);save(d/'kill.json',{'point':fault,'exit_code':code})
                finally:
                    if p.poll() is None:p.kill();p.wait()
            subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=260,check=True)
        result=grade(d);print(json.dumps({'stage':name,**result}),flush=True);return result
    try:
        for case in ((a.only_case,) if a.only_case else CASES):report['cases'].append(execute(case,case));save(root/'summary.json',report)
        if not a.quality_only:
            for fault in FAULTS:report['recovery'].append(execute(fault,'user',fault));save(root/'summary.json',report)
        report['passed']=all(v['passed'] for v in report['cases']+report['recovery'])
    finally:save(root/'summary.json',report)
    if not report.get('passed'):raise SystemExit(1)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--directory',required=True);p.add_argument('--worker',action='store_true');p.add_argument('--case',choices=CASES,default='user');p.add_argument('--fault',choices=FAULTS);p.add_argument('--only-case',choices=CASES);p.add_argument('--quality-only',action='store_true');a=p.parse_args()
    if not a.database.rsplit('/',1)[-1].startswith('memory_capture_'):raise ValueError('Dedicated memory_capture_ database required')
    if a.worker:asyncio.run(worker(a))
    else:orchestrate(a)
