"""Fresh-process recovery must revalidate current task/authority, before any call."""
import json,os,signal,subprocess,sys,time
from uuid import uuid4
import pytest
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources,build_controlled_services
from agent_core.adapters.parsing.text import TextParser
from test_controlled_collaboration import Provider

@pytest.mark.parametrize('mutation',['task','permission','cancel'])
def test_recovery_rejects_changed_admission(mutation,tmp_path):
    url=os.environ.get('CE7_TEST_DATABASE_URL')
    if not url:pytest.skip('Dedicated CE7 PostgreSQL required')
    cmd=[sys.executable,'scripts/verify_ce7_worker.py','--directory',str(tmp_path)]
    with (tmp_path/'worker.log').open('w') as log:
        proc=subprocess.Popen(cmd+['--fault','sdk_pause'],stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+30
            while not (tmp_path/'paused').exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.05)
            assert (tmp_path/'paused').exists(),(tmp_path/'worker.log').read_text()
            proc.send_signal(signal.SIGKILL);assert proc.wait(timeout=5)==-signal.SIGKILL
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
        info=json.loads((tmp_path/'seed.json').read_text());db=PostgresDatabase(url)
        try:
            scopes=build_resources(db,tmp_path/'blobs',parser=TextParser())
            research,tasks,_=build_controlled_services(db,scopes,private_root=tmp_path/'private',model_provider=Provider())
            spec=tasks.spec(info['task_id'])
            if mutation=='task':
                tasks.create(research_id=spec.research_id,kind='synthesize',user_request='New task instruction',task_id=spec.id)
            elif mutation=='permission':
                root=research.research(spec.research_id);scope=scopes.current(root.scope_id,'local')
                scopes._replace(scope,tools=())
            else:tasks.engine.ledger.cancel_task(spec.id,spec.version)
        finally:db.close()
        result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=30)
        assert result.returncode!=0
    assert len((tmp_path/'requests.jsonl').read_text().splitlines())==1
    assert not (tmp_path/'effects.jsonl').exists()
