import json,urllib.request,concurrent.futures,pathlib,subprocess
root=pathlib.Path(__file__).parent
repos=['GalaxyXieyu/pi-agents-flow','edxeth/pi-subagents','SnowWarri0r/hermes-researcher','pafozz/hermes-deep-research','Muuuun/luxas']
def get(url):
 return subprocess.check_output(['curl','-fLsS','--max-time','30',url])
def job(repo):
 try:
  d=json.loads(get('https://api.github.com/repos/'+repo))
  out={k:d.get(k) for k in ['full_name','stargazers_count','forks_count','created_at','pushed_at','archived','language','default_branch','description','license']}
  target=root/repo.replace('/','__');target.mkdir(exist_ok=True)
  (target/'meta.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
  try:(target/'README.md').write_bytes(get('https://raw.githubusercontent.com/'+repo+'/'+d['default_branch']+'/README.md'))
  except Exception as e:out['readme_error']=str(e)
  return out
 except Exception as e:return {'full_name':repo,'error':str(e)}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool: result=list(pool.map(job,repos))
(root/'metadata-lightweight-ecosystem.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
for d in result: print(d['full_name'],d.get('stargazers_count'),d.get('pushed_at'),(d.get('license') or {}).get('spdx_id'), 'ARCHIVED' if d.get('archived') else '',d.get('error',''))