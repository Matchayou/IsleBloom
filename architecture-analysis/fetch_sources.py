import concurrent.futures,json,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).parent/'sources'
REPOS=['openai/openai-agents-python','stanford-oval/storm','xyflow/xyflow','dbos-inc/dbos-transact-py','volcengine/OpenViking','letta-ai/letta','Future-House/paper-qa','langfuse/langfuse','mem0ai/mem0','vectorize-io/hindsight','getzep/graphiti']
def get(url):return subprocess.check_output(['curl','-fLsS','--max-time','40',url])
def tree(repo):
 try:
  d=ROOT/repo.replace('/','__');d.mkdir(parents=True,exist_ok=True)
  meta=json.loads(get('https://api.github.com/repos/'+repo));(d/'meta.json').write_text(json.dumps(meta,indent=2))
  branch=meta['default_branch'];data=get('https://api.github.com/repos/'+repo+'/git/trees/'+branch+'?recursive=1');(d/'tree.json').write_bytes(data)
  t=json.loads(data); print(repo,branch,t.get('sha'),len(t.get('tree',[])), 'truncated',t.get('truncated'),flush=True)
 except Exception as e:print(repo,'ERROR',str(e),flush=True)
def file(item):
 repo,path=item
 try:
  d=ROOT/repo.replace('/','__');tree=json.loads((d/'tree.json').read_text());sha=tree['sha'];target=d/'code'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(get('https://raw.githubusercontent.com/'+repo+'/'+sha+'/'+path));return repo+' '+path
 except Exception as e:return str(item)+' ERROR '+str(e)
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
  if len(sys.argv)>1:
   for s in pool.map(file,json.loads(pathlib.Path(sys.argv[1]).read_text())):print(s,flush=True)
  else:list(pool.map(tree,REPOS))
