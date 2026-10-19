"""Ingest the frozen public annual reports into an isolated Stage 5 database."""
import json
import os
from pathlib import Path
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_research

ROOT = Path('/tmp/stage5-public-2025')
FILES = ('tesla-2025.pdf','ford-2025.pdf','gm-2025.pdf')


def main():
    database = PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        scopes = build_resources(database,Path(os.environ['STAGE5_BLOBS']))
        research = build_research(database,scopes).create('2025 汽车公司现金流与竞争风险比较',research_id='stage5-public-2025')
        refs = {}
        scope = scopes.current(research.scope_id,research.owner)
        for name in FILES:
            print('parsing',name,flush=True)
            scope,ref = scopes.ingest(scope,content=(ROOT/name).read_bytes(),name=name,media_type='application/pdf')
            _,snapshot,parsed = scopes.resources.describe(ref)
            refs[name] = {'ref':ref.model_dump(),'sha256':snapshot.blob_hash,
                'status':parsed.status,'fragments':len(parsed.fragments),
                'issues':[v.model_dump() for v in parsed.issues]}
            print(name,parsed.status,len(parsed.fragments),'fragments',len(parsed.issues),'issues',flush=True)
            (ROOT/'ingestion.json').write_text(json.dumps(refs,ensure_ascii=False,indent=2))
    finally:
        database.close()


if __name__ == '__main__': main()
