"""Real SIGKILL, SDK and PostgreSQL; deterministic provider is NOT live LLM evidence."""
import json,os,signal,subprocess,sys,time
from pathlib import Path
import pytest

@pytest.mark.parametrize('point,dbos', [(p,False) for p in ['before_request','after_response','inside_transaction','after_receipt','sdk_pause','after_tool_receipt','result_saved','after_effect']] + [('sdk_pause',True)])
def test_sigkill_resumes_persisted_state_and_never_repeats_committed_effect(point,dbos,tmp_path):
    url=os.environ.get('CE7_TEST_DATABASE_URL')
    if not url:pytest.skip('CE7 dedicated PostgreSQL required')
    cmd=[sys.executable,'scripts/verify_ce7_worker.py','--directory',str(tmp_path)]
    if dbos:cmd.append('--dbos')
    with (tmp_path/'worker.log').open('w') as log:
        proc=subprocess.Popen(cmd+['--fault',point],stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+30
            while not (tmp_path/'paused').exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.05)
            assert (tmp_path/'paused').exists(),(tmp_path/'worker.log').read_text()
            proc.send_signal(signal.SIGKILL);assert proc.wait(timeout=5)==-signal.SIGKILL
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
        result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=60)
        assert result.returncode==0,(tmp_path/'worker.log').read_text()
    value=json.loads((tmp_path/'evidence.json').read_text())
    assert value['result']['status']==('failed' if point=='after_effect' else 'succeeded'),value['result']
    if point=='after_effect':assert value['result']['error']['code']=='operation_unconfirmed'
    effects=(tmp_path/'effects.jsonl').read_text().splitlines()
    assert len(effects)==1
    models=[o for o in value['operations'] if o['kind']=='model']
    assert len(models)==(1 if point=='after_effect' else 3 if point in ('before_request','after_response','inside_transaction') else 2)
    assert value['budget']['models']==len(models)
    assert value['result']['usage']['complete']==(point not in ('before_request','after_response','inside_transaction','after_effect'))
    assert value['budget']['tools']==1
    evidence=Path(os.environ.get('ACCEPTANCE_EVIDENCE_DIR', str(tmp_path)));evidence.mkdir(parents=True, exist_ok=True)
    if dbos:assert json.loads((tmp_path/'dbos.json').read_text())['status']=='SUCCESS'
    (evidence/(point+('-dbos' if dbos else '')+'.json')).write_text(json.dumps({'point':point,'dbos':dbos,'exit':-signal.SIGKILL,'effects':effects,'evidence':value},ensure_ascii=False,indent=2))
