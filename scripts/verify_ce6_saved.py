"""New-process CE6 summary/raw artifacts, exact provider accounting and saved receipt."""
import argparse,json,os
from pathlib import Path
from sqlalchemy import select
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources,build_controlled_services
from agent_core.context.artifacts import ContextArtifacts
from agent_core.context.projection import sha
from agent_core.controlled.workspace import Workspace
class NoModel:
    def get_model(self,name):raise AssertionError('Saved verification cannot call model')
def run(root,output):
    root=Path(root).resolve();old=json.loads((root/'live.json').read_text())
    db=PostgresDatabase(os.environ['CE6_TEST_DATABASE_URL'])
    try:
        scopes=build_resources(db,root/'blobs',parser=TextParser())
        _,tasks,_=build_controlled_services(db,scopes,private_root=root/'private',model_provider=NoModel())
        receipt=tasks.receipt(old['receipt']['result_id']);assert receipt.model_dump(mode='json')==old['receipt']
        ledger=tasks.engine.ledger;budget=json.loads(json.dumps(ledger.run_budget(receipt.run_id),default=str));assert budget==old['budget']
        with db.transaction():
            heads=list(db.connection.execute(select(ledger.compaction_heads.c.head).where(ledger.compaction_heads.c.workspace_id==old['workspace_id'])).scalars())
            manifests=[dict(r) for r in db.connection.execute(select(ledger.manifests).where(ledger.manifests.c.run_id==receipt.run_id)).mappings()]
        work=Workspace(root/'private'/old['workspace_id']);hashes={};notes={}
        try:
            artifacts=ContextArtifacts(work,lambda:tasks.engine.validate_run(receipt.run_id,require_started=True),lambda _:None)
            for name, in work.db.execute('SELECT name FROM notes ORDER BY name'):
                if not name.startswith(('transcript_','tool_','result_')):
                    notes[name]=work.read(name)['sha256']
            assert notes, 'Requested private note must actually exist'
            for head in heads:
                for key in [head['summary_artifact'],head['input_artifact'],*head['sources'].values()]:
                    raw=artifacts.load_text(key);assert sha(raw)==key;hashes[key]=sha(raw)
        finally:work.close()
        assert all(m['state']=='responded' and sha(m['manifest']['wire_text'])==m['manifest']['wire_sha256'] for m in manifests)
        measured=[m['manifest']['usage'] for m in manifests]
        usage=ledger.usage(receipt.run_id)
        assert usage.input_tokens==sum(m['input_tokens'] for m in measured)
        assert usage.output_tokens==sum(m['output_tokens'] for m in measured)
        assert usage.requests==len(manifests)
        assert tasks.retry_save(receipt.run_id).status=='completed'
        data={'stage':'CE6','checks':{'saved_receipt':True,'budget_unchanged':True,'summary_and_raw_hashes':True,'private_note_hashes':True,
            'exact_wire_manifests':True,'cache_not_added_twice':True,'retry_save_zero_model':True},
            'heads':heads,'hashes':hashes,'notes':notes,'budget':budget,'usage':usage.model_dump(mode='json'),'model_calls':0}
        Path(output).write_text(json.dumps(data,ensure_ascii=False,indent=2));print(json.dumps(data['checks']))
    finally:db.close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('--output',required=True);a=p.parse_args();run(a.root,a.output)
