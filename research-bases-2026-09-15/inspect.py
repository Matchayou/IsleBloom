import json,subprocess,pathlib,concurrent.futures
root=pathlib.Path(__file__).parent
repos=['bytedance/deer-flow','HezaoHezao/poirot','langchain-ai/deepagents','assafelovic/gpt-researcher','simstudioai/sim','langflow-ai/langflow']
def get(url):return subprocess.check_output(['curl','-fLsS','--max-time','40',url],stderr=subprocess.DEVNULL)
def job(repo):
 folder=root/repo.replace('/','__');m=json.loads((folder/'meta.json').read_text());b=m['default_branch'];out={'repo':repo}
 for name,url in [('tree','git/trees/'+b+'?recursive=1'),('releases','releases?per_page=3'),('commits','commits?per_page=3')]:
  try:
   d=json.loads(get('https://api.github.com/repos/'+repo+'/'+url));(folder/(name+'.json')).write_text(json.dumps(d,ensure_ascii=False))
   if name=='tree':
    paths=[x['path'] for x in d.get('tree',[]) if x['type']=='blob'];out['files']=len(paths);out['tests']=sum('/tests/' in '/'+p or '.test.' in p or '/test_' in '/'+p for p in paths);out['ci']=[p for p in paths if p.startswith('.github/workflows/')];out['key_paths']=[p for p in paths if any(t in p.lower() for t in ['subagent','evolution','memory','parallel','checkpoint'])][:45]
   elif name=='releases':out[name]=[{k:v.get(k) for k in ['tag_name','published_at','prerelease']} for v in d]
   else:out['commits']=[{'sha':v['sha'],'date':v['commit']['committer']['date'],'message':v['commit']['message'].split('\n')[0]} for v in d]
  except Exception as e:out[name+'_error']=str(e)
 return out
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rs=list(pool.map(job,repos))
(root/'inspection.json').write_text(json.dumps(rs,ensure_ascii=False,indent=2))
print(json.dumps(rs,ensure_ascii=False,indent=2))
