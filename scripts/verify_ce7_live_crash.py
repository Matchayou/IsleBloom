"""Actual Responses + DBOS + SIGKILL and fresh process, no key on argv or disk."""
import argparse,getpass,json,os,signal,subprocess,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--directory',required=True);p.add_argument('--point',choices=['after_tool_receipt','after_response'],required=True);a=p.parse_args()
os.environ['OPENAI_API_KEY']=getpass.getpass('DeepSeek credential (hidden): ')
root=Path(a.directory).resolve();root.mkdir(parents=True,exist_ok=False)
cmd=[sys.executable,'scripts/verify_ce7_worker.py','--directory',str(root),'--live','--dbos']
def lines(name):
 path=root/name
 return len(path.read_text().splitlines()) if path.exists() else 0
with (root/'worker.log').open('w') as log:
 proc=subprocess.Popen(cmd+['--fault',a.point],stdout=log,stderr=subprocess.STDOUT)
 try:
  deadline=time.monotonic()+240
  while not (root/'paused').exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.1)
  if not (root/'paused').exists():raise RuntimeError('No fault marker; see private acceptance log')
  before={'http':lines('http.jsonl'),'requests':lines('requests.jsonl'),'effects':lines('effects.jsonl')}
  proc.send_signal(signal.SIGKILL);assert proc.wait(timeout=5)==-signal.SIGKILL
 finally:
  if proc.poll() is None:proc.kill();proc.wait()
 resumed=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=300)
 if resumed.returncode:raise RuntimeError('Recovery process failed; see log')
e=json.loads((root/'evidence.json').read_text());status=json.loads((root/'dbos.json').read_text())
checks={'completed':e['result']['status']=='succeeded','dbos_completed':status['status']=='SUCCESS',
 'one_note_effect':lines('effects.jsonl')==1,'one_stable_tool':sum(o['kind']=='private' for o in e['operations'])==1,
 'same_run_id':e['result']['run_id']==json.loads((root/'seed.json').read_text())['run_id'],
 'reservations_preserved':e['budget']['models']==lines('requests.jsonl')<=8,
 'known_or_unknown_usage':e['result']['usage']['complete']==(a.point!='after_response'),
 'review_required':e['receipt']['delivery']=='pending_review'}
(root/'acceptance.json').write_text(json.dumps({'point':a.point,'before_kill':before,'after':{'requests':lines('requests.jsonl'),'effects':lines('effects.jsonl')},'kill_exit':-signal.SIGKILL,'checks':checks},indent=2))
print(json.dumps({'directory':str(root),'checks':checks}))
if not all(checks.values()):raise SystemExit(1)
