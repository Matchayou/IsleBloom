"""Isolated browser acceptance: PostgreSQL + controlled SDK with a fixture model."""
import asyncio
import json
import os
from uuid import uuid4
from agents import ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
import uvicorn
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_controlled_services
from agent_core.app.api import create_app

class Provider:
    def get_model(self,name):
        class Model:
            async def get_response(self,**kwargs):
                await asyncio.sleep(.15)
                if '研究规划助手' in kwargs['system_instructions']:
                    needs_question='需要澄清' in json.dumps(kwargs['input'],ensure_ascii=False)
                    value=dict(analysis='先检查固定资料，再形成综合报告。',assumptions=[],
                        questions=['请确认报告期'] if needs_question else [],
                        steps=[dict(key='read',goal='核对固定资料',instructions='读取获准资料，保留未知',deliverable='资料摘要',depends_on=[],allowed_tools=['read_source','search_source']),
                               dict(key='report',goal='综合研究报告',instructions='综合上游摘要，说明局限性',deliverable='完整报告',depends_on=['read'],allowed_tools=['read_source','search_source'])])
                else: value=dict(report='浏览器验收固定报告：已完成当前步骤，真实市场结论仍待核实。',blocking_missing=[],claims=[])
                return ModelResponse(output=[ResponseOutputMessage(id=uuid4().hex,type='message',role='assistant',status='completed',
                    content=[ResponseOutputText(type='output_text',text=json.dumps(value),annotations=[])])],usage=Usage(requests=1,input_tokens=3,output_tokens=4),response_id=None)
        return Model()

async def main():
    db=PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        scopes=build_resources(db,'/private/tmp/plan-browser/blobs')
        research,tasks,projects=build_controlled_services(db,scopes,private_root='/private/tmp/plan-browser/private',model_provider=Provider())
        project=projects.create('Plan 验证','plan-browser-project')
        await projects.create_main_task(project.id,'计划模式验收','plan-browser-research')
        await uvicorn.Server(uvicorn.Config(create_app(research,'frontend/dist',tasks),host='127.0.0.1',port=int(os.environ.get('BROWSER_PLAN_PORT','18769')),log_level='warning')).serve()
    finally: db.close()

if __name__=='__main__': asyncio.run(main())
