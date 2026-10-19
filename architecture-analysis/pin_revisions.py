import pathlib,json,concurrent.futures
from fetch_sources import get,ROOT

def pin(d):
 try:
  repo=d.name.replace('__','/')
  tree=json.loads((d/'tree.json').read_text())
  meta=json.loads((d/'meta.json').read_text()) if (d/'meta.json').exists() else {'default_branch':'main'}
  commits=json.loads(get('https://api.github.com/repos/'+repo+'/commits?sha='+meta['default_branch']+'&per_page=10'))
  found=next((c for c in commits if c['commit']['tree']['sha']==tree['sha'] or c['sha']==tree['sha']),None)
  if not found:raise RuntimeError('No matching commit for fetched tree')
  info={'repository':repo,'commit':found['sha'],'tree':found['commit']['tree']['sha'],'tree_response_ref':tree['sha'],'commit_date':found['commit']['committer']['date'],'checked_date':'2026-09-15'}
  (d/'revision.json').write_text(json.dumps(info,indent=2));return repo+' '+found['sha'][:12]
 except Exception as e:return d.name+' ERROR '+str(e)
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as p:
 for r in p.map(pin,[d for d in ROOT.iterdir() if d.is_dir()]):print(r,flush=True)
