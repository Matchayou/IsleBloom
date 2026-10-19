import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import pytest

@pytest.mark.parametrize('mode',['running','before','after'])
def test_research_sigkill_atomic_reopen(mode,tmp_path):
    url=os.environ.get('TEST_DATABASE_URL')
    if not url:pytest.skip('Real PostgreSQL required')
    root=Path(__file__).resolve().parents[1]
    rid=str(uuid4());blobs=str(tmp_path/'blobs')
    process=subprocess.run([sys.executable,str(root/'tests/stage4_crash_worker.py'),mode,rid,blobs],capture_output=True,text=True,timeout=30)
    assert process.returncode==-9,process.stderr
    info=json.loads(process.stdout)
    query=subprocess.run([sys.executable,'-m','agent_core.research_cli','--blobs',blobs,'show',rid],capture_output=True,text=True,
        env={**os.environ,'DATABASE_URL':url},timeout=30)
    assert query.returncode==0,query.stderr
    value=json.loads(query.stdout);node=value['nodes'][0]
    assert node['node']['id']==info['node_id']
    assert node['execution_status']==('succeeded' if mode=='after' else 'interrupted')
    assert len(node['versions'])==(1 if mode=='after' else 0)
    assert node['node']['active_run_id'] is None
    assert bool(node['node']['current_result_id'])==(mode=='after')
