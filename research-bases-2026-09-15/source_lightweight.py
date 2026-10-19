import pathlib,json,subprocess,concurrent.futures
root=pathlib.Path(__file__).parent
files={
'badlogic/pi-mono':['packages/agent/README.md','packages/coding-agent/README.md','packages/coding-agent/docs/sdk.md','packages/coding-agent/examples/extensions/subagent/index.ts'],
'NousResearch/hermes-agent':['tools/delegate_tool.py','pyproject.toml'],
'openai/openai-agents-python':['examples/research_bot/README.md','examples/research_bot/manager.py','examples/financial_research_agent/README.md']}

def fetch(item):
 repo,path=item;folder=root/repo.replace('/','__');branch=json.loads((folder/'meta.json').read_text())['default_branch'];url='https://raw.githubusercontent.com/'+repo+'/'+branch+'/'+path
 p=folder/'source'/path;p.parent.mkdir(parents=True,exist_ok=True)
 r=subprocess.run(['curl','-fLsS','--max-time','30',url],capture_output=True)
 if r.returncode==0:p.write_bytes(r.stdout)
 return [repo,path,r.returncode]
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 for item in pool.map(fetch,[(r,p) for r,ps in files.items() for p in ps]):print(*item)
