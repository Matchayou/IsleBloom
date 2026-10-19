import json,pathlib,csv
root=pathlib.Path(__file__).parent
rs=json.loads((root/'metadata.json').read_text())+json.loads((root/'metadata-extra.json').read_text())
notes={
'bytedance/deer-flow':('研究产品','完整产品首选；递归委派、范围策略和研究节点需二开'),
'HezaoHezao/poirot':('研究内核','理念贴合；叶子子 Agent；小社区，适合参考与实验'),
'langchain-ai/open_deep_research':('研究示例','已归档，保留算法和评估参考'),
'langchain-ai/deepagents':('Agent 内核','长期核心候选；有官方深度研究示例，UI 需开发'),
'assafelovic/gpt-researcher':('研究组件','节点内检索、引用和报告候选；需测试失败边界'),
'LearningCircuit/local-deep-research':('研究产品','本地模型、多搜索源与私有资料方向备选'),
'jina-ai/node-DeepResearch':('研究组件','迭代检索问答；项目明确不以长篇报告为目标'),
'dzhng/deep-research':('研究示例','简洁的深度/广度递归研究参考；不是完整工作台'),
'Alibaba-NLP/DeepResearch':('研究模型','研究模型及推理/训练生态；不是现成可视化产品'),
'stanford-oval/storm':('研究组件','多角度研究与写作参考；近期维护证据偏弱'),
'FlowiseAI/Flowise':('可视化编排','已归档，Agentflow V2 仅优先作为设计参考'),
'langflow-ai/langflow':('可视化编排','Python 组件和现成流程产品强候选'),
'simstudioai/sim':('可视化编排','画布产品强候选；并行/嵌套容器，代码体量大'),
'langgenius/dify':('应用平台','成熟但有许可证附加条件；研究图需二开'),
'langchain-ai/langgraph':('图运行时','子图、状态映射、持久化；与 Deep Agents 配合'),
'crewAIInc/crewAI':('Agent 编排','角色团队与 Flows 备选；研究节点图仍需开发'),
'agno-agi/agno':('Agent 平台','SDK、AgentOS、存储/工具集成备选；非现成研究树'),
'microsoft/agent-framework':('Agent 编排','Python/.NET 编排候选；微软生态优先时考虑'),
'microsoft/autogen':('Agent 编排','维护模式；新项目优先看 Microsoft Agent Framework'),
'lupantech/AgentFlow':('策略训练','Flow-GRPO 和模块化规划训练参考；后期考虑'),
'microsoft/agent-lightning':('策略训练','Agent RL 基础设施；需要数据和训练资源'),
'stanfordnlp/dspy':('程序优化','有评估集后优化研究提示词和模块'),
'langchain-ai/langmem':('记忆/优化','LangGraph 路线的记忆与提示词优化候选'),
'mem0ai/mem0':('记忆','独立用户记忆组件；不替代分支范围治理'),
'virattt/dexter':('金融研究','财务研究工具与评估参考；数据源适配和授权需核验'),
'TauricResearch/TradingAgents':('金融多 Agent','分析师分工与讨论机制参考；偏交易决策流程'),
'666ghj/BettaFish':('舆情研究','中文舆情与多引擎报告参考；GPL-2.0'),
'TencentCloudADP/youtu-agent':('Agent 框架','开源模型适配、配置与评估方向备选；非现成研究树'),
'agentscope-ai/agentscope':('Agent 框架','活跃的 Agent 服务与多 Agent 构建备选；需研究产品层'),
'jd-opensource/OxyGent':('Agent 编排','模块化、可观察和演进理念相关；生态规模较小')}
license_override={'FlowiseAI/Flowise':'README 声明 Apache-2.0；API 未识别','langgenius/dify':'Dify 修改版 Apache-2.0','microsoft/autogen':'代码 MIT；文档 CC-BY-4.0','virattt/dexter':'README 声明 MIT；LICENSE 文件未核实','TencentCloudADP/youtu-agent':'自定义/未分类，需核对原文','jd-opensource/OxyGent':'README 声明 Apache-2.0；API 未识别'}
rows=[]
for r in rs:
 name=r['full_name'];category,note=notes[name];lic=license_override.get(name,(r.get('license') or {}).get('spdx_id','未核实'))
 rows.append({'repo':name,'category':category,'stars':r['stargazers_count'],'pushed_at':r['pushed_at'],'archived':r['archived'],'license':lic,'assessment':note,'url':'https://github.com/'+name})
with (root/'projects.csv').open('w',newline='',encoding='utf-8-sig') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
with (root/'report.md').open('a') as f:
 f.write('\n| 项目 | 分类 | Stars | 最后推送 | 许可初筛 | 定位判断 |\n|---|---|---:|---|---|---|\n')
 for r in rows:f.write(f"| [{r['repo']}]({r['url']}) | {r['category']} | {r['stars']:,} | {r['pushed_at'][:10]} | {r['license']} | {r['assessment']} |\n")
 f.write('\n## 8. 核查材料与限制\n\n- metadata.json / metadata-extra.json：30 个仓库 API 元数据快照。\n- inspection.json：六个重点候选的文件树统计、CI 路径、发布和默认分支提交。\n- 各项目目录：README、元数据；重点项目另附 tree.json、releases.json、commits.json 和 source/ 抽样源码。\n- projects.csv：可筛选的完整候选表。\n- 测试文件数来自路径规则统计，包含测试辅助文件，不能当作测试用例数或测试通过率。未查询全量贡献者、未系统抽样 issue 响应速度，不对这些未核验项作排名。\n- 获取对象是调查时的默认分支；发布版本与主线功能可能不同。后续二开应固定选定提交，并重新验证代码与文档差异。\n')
print('saved',len(rows),'projects')
for n in ['bytedance/deer-flow','assafelovic/gpt-researcher','HezaoHezao/poirot']:
 r=next(x for x in rs if x['full_name']==n);print(n,r['default_branch'])
print('report chars',len((root/'report.md').read_text()))
