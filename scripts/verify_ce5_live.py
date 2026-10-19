"""CE5 real Responses API, real Git and PostgreSQL, fresh synthetic code fixture."""
import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.coding.git import git, output
from agent_core.coding.models import CodingContract, CODING_TOOLS
from agent_core.coding.workspace import CodingService, TestProfile
from agent_core.composition import build_resources, build_controlled_services
from agent_core.context.audit import audit_http_request
from agent_core.context.projection import sha
from agent_core.contracts import RunLimits
from agent_core.controlled.ledger import digest

async def run(args):
    root=Path(args.output).resolve();root.mkdir(parents=True,exist_ok=False)
    source=root/'fixture';source.mkdir();git(source,'init')
    for name in ('a','b'):(source/f'{name}.py').write_text('ANSWER = 0\n')
    git(source,'add','.');git(source,'commit','-m','CE5 synthetic fixed baseline')
    baseline=output(source,'rev-parse','HEAD')
    validator=root/'trusted_validator.py'
    validator.write_text('''import ast,os,pathlib,sys
root=pathlib.Path(os.environ['CODE_SOURCE'])
for name in sys.argv[1:]:
    tree=ast.parse((root/(name+'.py')).read_text())
    assert len(tree.body)==1 and isinstance(tree.body[0],ast.Assign)
    statement=tree.body[0]
    assert len(statement.targets)==1 and isinstance(statement.targets[0],ast.Name) and statement.targets[0].id=='ANSWER'
    assert isinstance(statement.value,ast.Constant) and statement.value.value=={'a':7,'b':11}[name]
assert not (root/'.git').exists()
print('Approved AST checks passed for '+','.join(sys.argv[1:])+'; no repository code executed')
''')
    profiles=[TestProfile(n,(sys.executable,'-I',str(validator),*names),'python-ast-fixture-v1') for n,names in [('a',('a',)),('b',('b',)),('combined',('a','b'))]]
    service=CodingService(root/'code',{'fixture':source},profiles)
    evidence={'stage':'CE5','fixture':'synthetic code; REAL model/Git/PostgreSQL','http_statuses':[],'status':'running'}
    from openai import AsyncOpenAI,DefaultAsyncHttpxClient
    from agents.models.openai_provider import OpenAIProvider
    async def response_hook(response):evidence['http_statuses'].append(response.status_code)
    client=AsyncOpenAI(base_url='https://api.deepseek.com',max_retries=0,http_client=DefaultAsyncHttpxClient(
        timeout=120,event_hooks={'request':[audit_http_request],'response':[response_hook]}))
    db=PostgresDatabase(os.environ['CE5_TEST_DATABASE_URL'])
    try:
        scopes=build_resources(db,root/'blobs',parser=TextParser())
        research,tasks,projects=build_controlled_services(db,scopes,private_root=root/'private',
            model_provider=OpenAIProvider(openai_client=client,use_responses=True),coding=service)
        if research.query('list_researches',research.owner):raise ValueError('Fresh database required')
        project=projects.create('CE5 real coding acceptance');main=await projects.create_main_task(project.id,'Independent coding candidates')
        research_root=research.research(main.research_id);scope=scopes.current(research_root.scope_id,'local')
        scope=scopes._replace(scope,tools=tuple(sorted(set(scope.tools)|CODING_TOOLS)))
        specs=[]
        for name,value in [('a',7),('b',11)]:
            contract=CodingContract(repository_id='fixture',baseline=baseline,read_paths=('a.py','b.py'),
                write_paths=(f'{name}.py',f'{name}-note.txt'),test_profiles=(name,))
            specs.append(tasks.create(research_id=research_root.id,kind='coding',coding=contract,model='deepseek-flash',
                user_request=f'固定合成编码验收。把 {name}.py 改为仅一行 ANSWER = {value}，新增 {name}-note.txt 说明修改。先读文件；使用受控测试 {name}，通过后 freeze_code。最终 candidate_ref 使用冻结工具返回的固定引用，机器元数据由服务附上。报告说明只做了 AST 静态验证，未运行代码；待独立复核，未采用。不要读写另一个专家文件，不要联网。',
                limits=RunLimits(max_turns=18,max_calls=20,timeout_seconds=240)))
        results=await asyncio.gather(*(tasks.run(spec.id) for spec in specs))
        receipts=[r['receipt'] for r in results]
        evidence['receipts']=[r.model_dump(mode='json') for r in receipts]
        evidence['outputs']=[tasks.ledger.get(r.run_id).result.model_dump(mode='json') for r in receipts]
        checks={'real_http':bool(evidence['http_statuses']) and all(s==200 for s in evidence['http_statuses']),
                'both_saved_pending':all(r.code_candidate and r.delivery=='pending_review' and r.adoption=='for_discussion' for r in receipts),
                'original_untouched':all((source/f'{n}.py').read_text()=='ANSWER = 0\n' for n in ('a','b'))}
        if checks['both_saved_pending']:
            candidates=[r.code_candidate for r in receipts]
            contract=CodingContract(repository_id='fixture',baseline=baseline,read_paths=('a.py','b.py'),
                write_paths=('a.py','b.py','a-note.txt','b-note.txt'),test_profiles=('combined',),
                candidate_workspaces=tuple(c.workspace_id for c in candidates))
            integration_spec=tasks.create(research_id=research_root.id,kind='coding',coding=contract,
                user_request='Integrate both fixed candidates, resolve conflicts if any, rerun combined trusted checks.',model='deepseek-flash')
            generation=digest({'root':scope.id,'version':scope.version})
            integrated=service.admit(integration_spec,generation,lambda:scopes.valid(scope))
            combined_test=await integrated.test('combined');combined=integrated.freeze()
            evidence.update(integration=combined.model_dump(mode='json'),integration_task=integration_spec.id,
                root_scope=scope.model_dump(mode='json'),contracts=[s.model_dump(mode='json') for s in specs])
            checks.update(combination_retested=combined_test['exit_code']==0,
                distinct_workspaces=len({c.workspace_id for c in candidates}|{combined.workspace_id})==3,
                new_files_included=all(f'{n}-note.txt' in combined.files for n in ('a','b')))
            before=len(evidence['http_statuses']);tasks.executors.clear()
            for receipt in receipts:tasks.retry_save(receipt.run_id)
            checks['save_retry_zero_model']=len(evidence['http_statuses'])==before
        evidence['checks']=checks
        evidence['budgets']={r.run_id:tasks.engine.ledger.run_budget(r.run_id) for r in receipts}
        if not all(checks.values()):raise ValueError('CE5 live acceptance failed')
        evidence['status']='control_passed_semantic_review_required'
    except BaseException as exc:
        evidence.update(status='failed',failure_type=type(exc).__name__);raise
    finally:
        (root/'live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str))
        service.close();db.close();await client.close()
    print(json.dumps({'status':evidence['status'],'output':str(root/'live.json')}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--hidden-key',action='store_true');args=parser.parse_args()
    if args.hidden_key:os.environ['OPENAI_API_KEY']=getpass.getpass('DeepSeek credential (hidden): ')
    if not os.environ.get('OPENAI_API_KEY'):raise SystemExit('Credential required')
    try:asyncio.run(run(args))
    except Exception as exc:print(type(exc).__name__);raise SystemExit(1)
