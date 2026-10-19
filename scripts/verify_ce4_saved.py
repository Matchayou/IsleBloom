"""New-process CE4 saved artifact, phase and budget checks without any model provider."""
import argparse,json,os
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources,build_controlled_services
from agent_core.context.projection import sha
class NoModel:
    def get_model(self,name):raise AssertionError('Persistence check must never invoke a model')
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('evidence');p.add_argument('--output',required=True);p.add_argument('--baseline');args=p.parse_args()
    root=Path(args.evidence).resolve();evidence=json.loads((root/'live.json').read_text());url=os.environ['CE4_TEST_DATABASE_URL']
    if not make_url(url).database.startswith('ce4_'):raise ValueError('CE4 database required')
    db=PostgresDatabase(url)
    try:
        scopes=build_resources(db,root/'blobs');_,tasks,_=build_controlled_services(db,scopes,private_root=root/'private',model_provider=NoModel())
        run_id=evidence['run_id'];artifact=tasks.read_artifact(evidence['receipt']['result_id']);ledger=tasks.engine.ledger
        assert artifact['receipt'].model_dump(mode='json')==evidence['receipt']
        assert artifact['artifact'].model_dump(mode='json')==evidence['artifact']
        assert tasks.ledger.get(run_id).result.model_dump(mode='json')==evidence['result']
        assert ledger.progress(run_id)==evidence['phase'] and ledger.usage(run_id).model_dump(mode='json')==evidence['usage']
        assert tasks.retry_save(run_id).status=='completed'
        with db.transaction():manifests=[dict(r) for r in db.connection.execute(select(ledger.manifests).where(ledger.manifests.c.run_id==run_id)).mappings()]
        assert sorted(manifests,key=lambda r:r['operation_id'])==sorted(evidence['manifests'],key=lambda r:r['operation_id'])
        assert all(sha(m['manifest']['wire_text'])==m['manifest']['wire_sha256'] for m in manifests)
        result={'status':'passed','artifact_hash':artifact['artifact'].sha256,'receipt_exact':True,'phase_exact':True,'budget_exact':True,'manifests':len(manifests),'save_retry_no_generation':True,'model_calls':0}
        if args.baseline:assert result==json.loads(Path(args.baseline).read_text())
        Path(args.output).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:db.close()
if __name__=='__main__':main()
