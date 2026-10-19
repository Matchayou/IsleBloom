"""Reopen CE3 actual manifests, budgets and complete artifacts; no model calls."""
import argparse
import json
import os
import sqlite3
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources,build_controlled_services
from agent_core.context.projection import sha

class NoModel:
    def get_model(self,name):raise AssertionError('No model permitted during persistence verification')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('evidence');p.add_argument('--output',required=True)
    p.add_argument('--baseline');args=p.parse_args();root=Path(args.evidence).resolve()
    evidence=json.loads((root/'live.json').read_text());url=os.environ['CE3_TEST_DATABASE_URL']
    if not (make_url(url).database or '').startswith('ce3_'):raise ValueError('Dedicated CE3 database required')
    db=PostgresDatabase(url)
    try:
        scopes=build_resources(db,root/'blobs')
        research,tasks,_=build_controlled_services(db,scopes,private_root=root/'private',model_provider=NoModel())
        run_id=evidence['run_id'];ledger=tasks.engine.ledger
        tasks.engine.validate_run(run_id,require_started=True)
        assert research.ledger.get(run_id).result.model_dump(mode='json')==evidence['result']
        with db.transaction():
            manifests=[dict(r) for r in db.connection.execute(select(ledger.manifests).where(ledger.manifests.c.run_id==run_id)).mappings()]
            reads=[dict(r) for r in db.connection.execute(select(ledger.reads).where(ledger.reads.c.run_id==run_id)).mappings()]
        assert sorted(manifests,key=lambda r:r['operation_id'])==sorted(evidence['manifests'],key=lambda r:r['operation_id'])
        assert sorted(reads,key=lambda r:r['id'])==sorted(evidence['reads'],key=lambda r:r['id'])
        for m in manifests:
            assert sha(m['manifest']['wire_text'])==m['manifest']['wire_sha256']
            assert json.loads(m['manifest']['wire_text'])==m['manifest']['wire_body']
        binding=ledger.binding(run_id);workspace=root/'private'/tasks.engine.workspace_id(binding)
        connection=sqlite3.connect(workspace/'index.sqlite')
        try:keys=[r[0] for r in connection.execute('SELECT id FROM context_artifacts')]
        finally:connection.close()
        for key in keys:
            text=(workspace/'context'/key).read_text()
            assert sha(text)==key
            json.loads(text)
        assert ledger.usage(run_id).model_dump(mode='json')==evidence['result']['usage']
        result={'status':'passed','manifests':len(manifests),'read_ranges':len(reads),'full_artifacts':len(keys),
            'wire_hashes_exact':True,'saved_result_and_budget_exact':True,'artifact_hashes_exact':True,'model_calls':0}
        if args.baseline:assert result==json.loads(Path(args.baseline).read_text())
        Path(args.output).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:db.close()

if __name__=='__main__':main()
