"""The same product scenarios run against scripted and real SDK engines."""
import json
from uuid import uuid4
from agent_core.research.models import NodeCommand, ResearchPlan, PlanStep
from agent_core.orchestration.models import DispatchPolicy
from agent_core.research.projection import project
from agent_core.context.scopes import ScopeDenied


async def cmd(s,r,kind,node=None,**kwargs):
    return await s.submit(NodeCommand(command_id=str(uuid4()),research_id=r.id,kind=kind,node_id=node,**kwargs))


async def brainstorm(s):
    r=s.create('独立头脑风暴验收')
    root=await cmd(s,r,'create',goal='头脑风暴：社区旧物共享。给出两种方向，保留 BASE_IDEA_V1。创意不用伪造引用。')
    await cmd(s,r,'run',root.node_id)
    base=s.node(root.node_id).current_result_id
    branches=[]
    for index,(marker,direction) in enumerate([('BRANCH_A_728','线下邻里'),('BRANCH_B_953','线上预约')]):
        c=await cmd(s,r,'branch',root.node_id,goal=f'只探索{direction}，结果保留 {marker}，不要加入另一分支内容。',result_ids=(base,),position=(index*340,200))
        await cmd(s,r,'run',c.node_id)
        branches.append(s.node(c.node_id))
    for node,foreign in zip(branches,['BRANCH_B_953','BRANCH_A_728']):
        binding=s.query('list_runs',node.id)[0]
        record=s.ledger.get(binding.run_id)
        assert foreign not in record.request.input.model_dump_json()
        assert foreign not in json.dumps(record.result.output,ensure_ascii=False)
        assert s.query('inputs',node.current_result_id)==(base,)
    await cmd(s,r,'continue',root.node_id,goal='增加 BASE_IDEA_V2，保留旧结果。')
    assert len(s.query('list_results',root.node_id))==2
    assert all(s.query('inputs',n.current_result_id)==(base,) for n in branches)
    comparison=await cmd(s,r,'compare',position=(180,400),display_parent_id=root.node_id,goal='明确比较这两个独立方向，保留两个分支标记，区分创意与事实。',result_ids=tuple(n.current_result_id for n in branches))
    node=s.node(comparison.node_id)
    assert len(s.query('inputs',node.current_result_id))==2
    return {'research_id':r.id,'base':base,'branches':[n.current_result_id for n in branches],
        'comparison':node.current_result_id,'output':s.read_result(node.current_result_id)[1]}


async def companies(s,fixture_dir):
    r=s.create('三家公司同报告期现金流与竞争风险比较')
    scope=s.scopes.current(r.scope_id,s.owner);refs=[]
    for path in sorted(fixture_dir.glob('company-*.txt')):
        scope,ref=s.scopes.ingest(scope,content=path.read_bytes(),name=path.name,media_type='text/plain')
        refs.append(ref)
    assert len(refs)==3
    company_nodes=[];steps=[]
    for index,(ref,name) in enumerate(zip(refs,['青松公司','白鹭公司','海棠公司'])):
        c=await cmd(s,r,'create',goal=name+'分析',resource_refs=(ref,),position=(index*340,0))
        dimensions=[]
        for row,dimension in enumerate(['现金流','竞争风险']):
            d=await cmd(s,r,'branch',c.node_id,goal=f'分析{name}的{dimension}。先用 read_source 读取资料，再校验报告期、币种、单位、口径。保留公司名称与关键数字。',resource_refs=(ref,),position=(index*340,200+row*180))
            dimensions.append(d.node_id);steps.append(PlanStep(node_id=d.node_id))
        steps.append(PlanStep(node_id=c.node_id,depends_on=tuple(dimensions)))
        company_nodes.append(c.node_id)
    aggregate=await cmd(s,r,'create',position=(340,600),goal='输出三家公司比较表和报告：报告期、单位、经营现金流、资本开支、自由现金流和竞争风险。缺失明确标记。')
    steps.append(PlanStep(node_id=aggregate.node_id,depends_on=tuple(company_nodes)))
    from agent_core.research.planning_models import PlanDecision
    plan=ResearchPlan(research_id=r.id,steps=tuple(steps),policy=DispatchPolicy(max_runs=10,max_concurrency=3,timeout_seconds=900))
    proposal=await s.planning.import_plan(plan, 'scenario:'+str(uuid4()))
    s.planning.confirm(PlanDecision(proposal_id=proposal.id,expected_revision=proposal.revision))
    finished=await s.run_plan(proposal.id)
    assert finished.status=='succeeded',finished.reason
    mapping={old.node_id:new.node_id for old,new in zip(plan.steps,proposal.plan.steps)}
    comparison=s.node(mapping[aggregate.node_id]).current_result_id
    company_results=tuple(s.node(mapping[key]).current_result_id for key in company_nodes)
    assert comparison and len(s.query('inputs',comparison))==3
    # Follow-up research uses a new branch: approved plan definitions remain immutable.
    follow=await cmd(s,r,'branch',mapping[company_nodes[0]],goal='复核青松现金流可持续性，保留公司名与数字。',
        resource_refs=(refs[0],),result_ids=(company_results[0],))
    await cmd(s,r,'run',follow.node_id)
    latest=(s.node(follow.node_id).current_result_id,*company_results[1:])
    updated_command=await cmd(s,r,'compare',goal='重新汇总：输出三家公司现金流、竞争风险比较表及报告，标明期间、单位。',result_ids=latest)
    updated=s.node(updated_command.node_id).current_result_id
    assert updated!=comparison and s.read_result(comparison)
    await cmd(s,r,'exclude',company_nodes[2])
    try: await cmd(s,r,'compare',goal='拒绝混合旧摘要',result_ids=(updated,))
    except ScopeDenied: pass
    else: raise AssertionError('Mixed comparison admitted excluded company')
    clean=await cmd(s,r,'compare',position=(680,600),goal='只比较获准两家公司，禁止海棠公司资料进入。',result_ids=latest[:2])
    clean_node=s.node(clean.node_id)
    run=s.query('list_runs',clean_node.id)[0]
    snapshot=s.scopes.repository.snapshot(run.run_id)
    assert refs[2] not in tuple(m.ref for m in snapshot.mounts)
    assert 'COMPANY_C_SECRET_963' not in snapshot.input.model_dump_json()
    return {'research_id':r.id,'company_nodes':[mapping[key] for key in company_nodes],'original_comparison':comparison,
        'updated_comparison':updated,'restricted_comparison':clean_node.current_result_id,
        'output':s.read_result(updated)[1]}



def _scripted_report(request):
    """Contract fixture, explicitly not model quality evidence."""
    payload=json.loads(request.input.messages[0].content)
    records=[]
    for source in payload.get('content',[]):
        try: record=json.loads('\n'.join(f['text'] for f in source['fragments']))
        except ValueError: continue
        if record.get('record_type')=='company_financials':records.append(record)
    if records:
        lines=['|公司|报告期|经营现金流|资本开支|自由现金流|竞争风险|','|---|---|---|---|---|---|']
        for r in records:
            lines.append(f"|{r['company']}|{r['period']} {r['unit']}|{r['operating_cash_flow']}|{r['capex']}|{r['operating_cash_flow']-r['capex']}|{r['competition_risk']}|")
        return [],'脚本验收产物\n'+'\n'.join(lines)+'\n\n报告：仅按固定材料组装，未进行真实模型分析。'
    return [],payload['goal']+'\n'+'\n'.join(str(v['output']) for v in payload.get('results',[]))


def scripted(request):
    steps, report = _scripted_report(request)
    return steps, {'report': report, 'blocking_missing': []}
