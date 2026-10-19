import json,pathlib
root=pathlib.Path(__file__).parent
sources=root/'sources'
rows=['# 源码阅读索引','', '核对日期：2026-09-15。下列链接定位到本次读取的固定提交。仓库目录与所选源码保存在 sources 下；这里是架构选型的定向阅读记录，不代表完整代码审计或运行验证。','', '[返回架构分析](./需求—功能—参考实现.md)','']
order=['openai__openai-agents-python','stanford-oval__storm','xyflow__xyflow','dbos-inc__dbos-transact-py','volcengine__OpenViking','letta-ai__letta','letta-ai__letta-code','Future-House__paper-qa','langfuse__langfuse','mem0ai__mem0','vectorize-io__hindsight','getzep__graphiti']
manifest=[]
for name in order:
 d=sources/name;repo=name.replace('__','/');rev=json.loads((d/'revision.json').read_text());sha=rev['commit'];files=sorted((d/'code').rglob('*'))
 rows += [f'## {repo}','',f'提交：[`{sha[:12]}`](https://github.com/{repo}/commit/{sha})。','']
 if name=='letta-ai__letta':rows += ['当前 README 将活跃实现指向 letta-code；旧 V1 server 在 archive 分支。上一轮提到的 memory blocks 属于 V1 设计参考，不能继续当作当前主分支的实现路径。','']
 for f in files:
  if not f.is_file():continue
  rel=f.relative_to(d/'code').as_posix();url=f'https://github.com/{repo}/blob/{sha}/{rel}';rows.append(f'- [{rel}]({url})');manifest.append({'repository':repo,'commit':sha,'path':rel,'url':url,'local_file':str(f.resolve())})
 rows+=['']
bear=pathlib.Path('/Users/chenyouran/Documents/ChatGPT/笔记管理/BearCode')
rows+=['## BearCode（用户本地购买项目）','','本地 HEAD：`335b293b9f91c9a24b0018bf31a15df79b205f91`。依据当前工作区内容阅读，未修改或上传源码。','']
for file,symbol in [('agents/online_skill_evolution.py','OnlineSkillCandidate / online_ingest / maintain_online_skill_candidate'),('agents/skill_evolution.py','create_skill_file / evolve_skill_file'),('agents/online_skill_eval.py','_build_replay_pool / _freeze_replay_pool / _promotion_decision'),('agents/skills.py','discover_skills'),('agents/agent.py','子 Agent 创建、保存与工具调度')]:
 rows.append(f'- [{file}](<{bear/file}>)：{symbol}')
rows+=['','## 官方文档','', '- [OpenAI：Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration)','- [OpenAI：Results and state](https://developers.openai.com/api/docs/guides/agents/results)','- [Langfuse：反馈进入评测数据集](https://langfuse.com/resources/engineering/user-feedback-to-evaluation-datasets)','- [Mem0：开源版本元数据过滤](https://docs.mem0.ai/open-source/features/metadata-filtering)','']
(root/'源码阅读索引.md').write_text('\n'.join(rows))
(root/'source-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print('Indexed',len(manifest),'public source files across',len(order),'repositories')
