"""Persistent, real-model collaboration demonstration. Credentials stay in .env."""
import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
import uvicorn
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.parsing.text import TextParser
from agent_core.app.api import create_app
from agent_core.composition import build_resources, build_controlled_services
from agent_core.controlled.provider import LiveModelProvider
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand

ROOT=Path('.agent-evals/automatic-collaboration-live-20261013/acceptance')
DATABASE='postgresql+psycopg://chenyouran@127.0.0.1:18749/auto_review_live_acceptance_20261013'
SOURCE='''合成资料：海岛商店季度数据。仅供协作验收，不是真实企业或市场数据。
月份,订单数,客单价（元）,退款金额（元）,广告费用（元）
1月,100,50,200,800
2月,120,55,300,900
3月,90,60,400,700
口径：毛收入=订单数×客单价；净收入=毛收入−退款金额。
广告效率=季度净收入/季度广告费用，不能视为利润率或因果效果。
没有商品成本、客群、对照组或历史年度数据。
'''
REQUEST='''请把已上传的海岛商店合成季度数据整理成中文经营分析报告，派发给 expert_synthesize。
最终验收要求：1.逐月毛收入、净收入和季度合计，展示计算过程；2.广告效率比值，解释其不等于利润或因果；
3.指出订单数下降与客单价变化，列出至少两项不能从资料推断的结论；4.给出两条可执行的后续核查建议；
5.明确资料是合成样本，不是真实经营结论。交付必须包含以上所有内容。
本轮由系统自动评审和修订，无需让我填写意见或人工确认。'''

async def main():
    load_dotenv('.env',override=False)
    ROOT.mkdir(parents=True,exist_ok=True)
    db=PostgresDatabase(DATABASE)
    scopes=build_resources(db,ROOT/'blobs',parser=TextParser())
    research,tasks,projects=build_controlled_services(db,scopes,private_root=ROOT/'private',
        model_provider=LiveModelProvider(),owner='automatic-live-demo-verified')
    seed=ROOT/'seed.json'
    if not seed.exists():
        project=projects.create('真实模型 · 自动协作闭环')
        member=await projects.create_main_task(project.id,'经营分析 · 派发、评审与修订')
        node=research.query('list_nodes',member.research_id)[0]
        _,ref=scopes.ingest(scopes.current(research.research(member.research_id).scope_id,research.owner),
            content=SOURCE.encode(),name='海岛商店合成季度数据.csv',media_type='text/plain')
        await research.submit(NodeCommand(command_id=member.research_id+':mount',research_id=member.research_id,
            node_id=node.id,kind='mount',resource_refs=(ref,)))
        chat=ChatService(research,tasks).send(node.id,REQUEST,0,message_id='live-demo-request')
        seed.write_text(json.dumps({'project_id':project.id,'research_id':member.research_id,
            'node_id':node.id,'chat_revision':chat.revision,'source_ref':ref.model_dump(mode='json')},ensure_ascii=False,indent=2))
    print(json.dumps({'url':'http://127.0.0.1:18750','seed':str(seed),'real_provider':True}),flush=True)
    try:
        await uvicorn.Server(uvicorn.Config(create_app(research,'frontend/dist',tasks),
            host='127.0.0.1',port=18750,log_level='warning')).serve()
    finally:db.close()

if __name__=='__main__':asyncio.run(main())
