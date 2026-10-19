"""New-process CE5 receipt, immutable code and budget verification; no model permitted."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import select
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.composition import build_resources,build_controlled_services
from agent_core.coding.git import git, output
from agent_core.context.projection import dumps,sha

class NoModel:
    def get_model(self,name):raise AssertionError('Persistence check must never call a model')

def run(root,output_path):
    root=Path(root).resolve();live=json.loads((root/'live.json').read_text())
    db=PostgresDatabase(os.environ['CE5_TEST_DATABASE_URL'])
    try:
        scopes=build_resources(db,root/'blobs',parser=TextParser())
        research,tasks,_=build_controlled_services(db,scopes,private_root=root/'private',model_provider=NoModel())
        checks={};records=[]
        for old in live['receipts']:
            receipt=tasks.receipt(old['result_id']);artifact=tasks.read_artifact(old['result_id'])
            assert receipt.model_dump(mode='json')==old
            candidate=receipt.code_candidate;repo=root/'code'/('repo-'+candidate.repository_id)
            content=git(repo,'ls-tree','-rz',candidate.commit).stdout
            hashes={}
            for entry in filter(None,content.split(b'\0')):
                meta,name=entry.split(b'\t',1);oid=meta.split()[2]
                hashes[name.decode()]=hashlib.sha256(git(repo,'cat-file','blob',oid.decode()).stdout).hexdigest()
            assert hashes==candidate.files and sha(dumps(hashes))==candidate.content_sha256
            patch=git(repo,'diff','--binary','--no-ext-diff','--no-textconv',candidate.baseline,candidate.commit,'--').stdout.decode()
            assert patch==artifact['output']['code_patch']
            assert tasks.retry_save(receipt.run_id).status=='completed'
            budget=json.loads(json.dumps(tasks.engine.ledger.run_budget(receipt.run_id),default=str))
            assert budget==live['budgets'][receipt.run_id]
            with db.transaction():
                manifests=[dict(r) for r in db.connection.execute(select(tasks.engine.ledger.manifests).where(
                    tasks.engine.ledger.manifests.c.run_id==receipt.run_id)).mappings()]
            assert manifests and all(m['state']=='responded' and sha(m['manifest']['wire_text'])==m['manifest']['wire_sha256'] for m in manifests)
            records.append({'receipt':receipt.model_dump(mode='json'),'artifact':artifact['artifact'].model_dump(mode='json'),
                'code_files':hashes,'patch_sha256':sha(patch),'budget':budget,
                'wire_hashes':sorted(m['manifest']['wire_sha256'] for m in manifests)})
        data={'stage':'CE5','checks':{'new_process_receipts':True,'fixed_code_hashes':True,
              'formal_patch_matches_commit':True,'budget_unchanged':True,'wire_manifests_intact':True,
              'save_retry_without_model':True},'records':records,'model_calls':0}
        Path(output_path).write_text(json.dumps(data,ensure_ascii=False,indent=2))
        print(json.dumps(data['checks']))
    finally:db.close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('--output',required=True);a=p.parse_args();run(a.root,a.output)
