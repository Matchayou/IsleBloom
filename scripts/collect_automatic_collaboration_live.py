"""Collect read-only evidence from the isolated real-model demonstration."""
import json
import time
from pathlib import Path
import httpx

ROOT=Path('.agent-evals/automatic-collaboration-live-20261013/acceptance')
OUT=Path('docs/automatic-collaboration-live-20261013')
BASE='http://127.0.0.1:18750/api'

def main():
    seed=json.loads((ROOT/'seed.json').read_text())
    with httpx.Client(base_url=BASE,timeout=30) as client:
        def get(path,**params):
            response=client.get('/'+path,params=params)
            response.raise_for_status()
            return response.json()
        deadline=time.monotonic()+600
        last=None
        while time.monotonic()<deadline:
            activities=get('task_activity',research_id=seed['research_id'])
            executor=next((a for a in activities if a['collaboration_role']=='executor'),None)
            loop=executor.get('automatic_review') if executor else None
            state=(loop['status'],loop['current_version'],loop['revisions_completed']) if loop else ('waiting',0,0)
            if state!=last:print(json.dumps({'status':state[0],'version':state[1],'revisions':state[2]},ensure_ascii=False),flush=True);last=state
            if loop and loop['status'] in ('passed','exhausted','failed','blocked','cancelled'):break
            time.sleep(5)
        else:raise TimeoutError('Live collaboration did not reach a terminal state')
        analysis=get('run_analysis',research_id=seed['research_id'])
        projection=get('projection',research_id=seed['research_id'])
        evidence={'real_model':True,'synthetic_data':True,'models':{'manager':'deepseek-flash','executor':executor['spec']['model'],'reviewer':executor['spec']['model']},
            'url':BASE.removesuffix('/api'),'identity':seed,'manager_command':json.loads((ROOT/'manager-command.json').read_text()),
            'loop':loop,'activities':activities,'analysis':analysis,'projection':projection,
            'artifacts':[get('task_artifact',id=r['result_id']) for r in loop['rounds']],
            'manager_chat':get('chat_read',node_id=seed['node_id'])}
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'evidence':str(OUT/'evidence.json'),'status':loop['status'],'revisions':loop['revisions_completed'],
            'verdicts':[r['advice']['verdict'] for r in loop['rounds']]},ensure_ascii=False),flush=True)
        assert loop['status'] in ('passed','exhausted') and 1<=loop['revisions_completed']<=2
        assert len(loop['rounds'])==loop['revisions_completed']+1
        assert loop['rounds'][0]['advice']['verdict']=='needs_revision'
        assert loop['rounds'][-1]['advice']['verdict']==('passed' if loop['status']=='passed' else 'needs_revision')
        if loop['status']=='exhausted':assert loop['revisions_completed']==2
        for round in loop['rounds']:
            run=next(r for r in analysis['runs'] if r['run_id']==round['review_run_id'])
            assert run['status']=='succeeded'
            assert any(e['name']=='read_context' and e['status']=='completed' for e in run['timeline'])
        revision_run=next(r for r in analysis['runs'] if r['run_id']==loop['rounds'][-1]['result_id'].removeprefix('task-result:'))
        assert any(e['name']=='read_context' and e['status']=='completed' for e in revision_run['timeline'])
        assert any(e['name']=='delegate_task' and e['status']=='completed'
            for r in analysis['runs'] if r['kind']=='node' for e in r['timeline'])
        assert all(r['status']=='succeeded' and not r['unconfirmed_operations'] for r in analysis['runs'])
        print('Real model collaboration acceptance passed.',flush=True)

if __name__=='__main__':main()
