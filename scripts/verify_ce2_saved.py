"""Read CE2 live evidence in a fresh process without any model credentials."""
import argparse
import json
import os
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_controlled_services
from agent_core.controlled.workspace import Workspace

class NoModel:
    def get_model(self, name):
        raise AssertionError('Saved evidence verification must never call a model')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('evidence');p.add_argument('--output',required=True);p.add_argument('--baseline')
    args=p.parse_args();root=Path(args.evidence).resolve()
    old=json.loads((root/'live.json').read_text())
    url=os.environ['CE2_TEST_DATABASE_URL']
    if not (make_url(url).database or '').startswith('ce2_'): raise ValueError('Dedicated CE2 database required')
    db=PostgresDatabase(url)
    try:
        scopes=build_resources(db,root/'blobs')
        research,tasks,projects=build_controlled_services(db,scopes,private_root=root/'private',model_provider=NoModel())
        ledger=tasks.engine.ledger
        with db.transaction():
            rows=db.connection.execute(select(ledger.runs)).mappings().all()
            assert all(row.writer is None for row in db.connection.execute(select(ledger.workspaces)))
        assert len(rows)==len(old['bindings'])
        for row in rows:
            saved=next(x for x in old['bindings'] if x['run_id']==row['id'])
            assert row['binding']==saved
        for e in old['experts']:
            receipt=tasks.receipt(e['receipt']['result_id'])
            assert receipt.model_dump(mode='json')==e['receipt']
            binding=ledger.binding(e['run_id'])
            w=Workspace(root/'private'/tasks.engine.workspace_id(binding))
            try: assert w.read('ce2_draft')==e['note']
            finally: w.close()
        chat=projects.expert_thread(old['experts'][0]['task_id'])
        assert any(m.id==old['discussion']['id'] for m in chat.messages)
        result={'status':'passed','same_run_bindings_and_budgets':True,'private_hashes_match':True,
                'receipts_unchanged_and_not_adopted':True,'discussion_preserved':True,
                'no_active_writers':True,'model_calls':0,
                'budgets':{r['id']:{'models':r['models'],'tools':r['tools']} for r in rows}}
        if args.baseline:
            assert result==json.loads(Path(args.baseline).read_text())
        Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    finally: db.close()

if __name__=='__main__': main()
