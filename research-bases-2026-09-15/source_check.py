import pathlib,json,subprocess,concurrent.futures
root=pathlib.Path(__file__).parent
files={
'bytedance/deer-flow':['backend/packages/harness/deerflow/subagents/context_snapshot.py','backend/packages/harness/deerflow/subagents/executor.py','backend/packages/harness/deerflow/subagents/runtime.py','backend/packages/harness/deerflow/subagents/config.py','backend/packages/harness/deerflow/subagents/report_contract.py','frontend/src/content/en/harness/subagents.mdx','.github/workflows/backend-unit-tests.yml'],
'HezaoHezao/poirot':['poirot/backend/agents/multiagent/subagent.py','poirot/backend/agents/multiagent/runtimes/subagent_runtime.py','poirot/backend/agents/skill/evolution/gates/score_delta_gate.py','poirot/backend/agents/skill/evolution/triggers/metric_monitor.py','poirot/backend/agents/multiagent/evolution/promotion_gate.py'],
'langchain-ai/deepagents':['examples/deep_research/README.md','examples/deep_research/agent.py','libs/deepagents/deepagents/middleware/subagents.py'],
'assafelovic/gpt-researcher':['gpt_researcher/skills/deep_research.py','docs/docs/gpt-researcher/gptr/deep_research.md'],
'simstudioai/sim':['apps/sim/executor/orchestrators/parallel.ts','apps/docs/content/docs/workflows/blocks/parallel.mdx','LICENSE'],
'langgenius/dify':['LICENSE'],'virattt/dexter':['LICENSE']}
def fetch(item):
 repo,path=item;folder=root/repo.replace('/','__');branch=json.loads((folder/'meta.json').read_text())['default_branch'];url='https://raw.githubusercontent.com/'+repo+'/'+branch+'/'+path
 p=folder/'source'/path;p.parent.mkdir(parents=True,exist_ok=True)
 r=subprocess.run(['curl','-fLsS','--max-time','30',url],capture_output=True)
 if r.returncode==0:p.write_bytes(r.stdout)
 return [repo,path,r.returncode]
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 for item in pool.map(fetch,[(r,p) for r,ps in files.items() for p in ps]):print(*item)
