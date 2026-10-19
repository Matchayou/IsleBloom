"""Stage 7 local browser fixture with real PostgreSQL and scripted expert."""
import asyncio
import json
import os
from pathlib import Path

import uvicorn

from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.app.api import create_app
from agent_core.composition import build_resources, build_research, build_tasks, build_evaluation, build_evolution
from agent_core.research.chat import ChatService


async def main():
    db = PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        scopes = build_resources(db,os.environ['STAGE7_BLOBS'])
        engine = ScriptedEngine(lambda _: ([],{
            'report':'A 公司 2025 年经营现金流为 100 百万美元。',
            'fields':{'company':'A','period':'2025','operating_cash_flow':'100',
                      'capital_expenditure':'20','unit':'million USD'},
            'sources':{'operating_cash_flow':'source.txt line 1',
                       'capital_expenditure':'source.txt line 1','unit':'source.txt line 1'}}))
        research = build_research(db,scopes,engine)
        root = research.create('Stage 7 浏览器反馈验收')
        chat = await ChatService(research).start(root.id,'主线')
        scope = scopes.current(root.scope_id,research.owner)
        _,ref = scopes.ingest(scope,content=b'A 2025 CFO 100 capex 20 thousand USD',
            name='source.txt',media_type='text/plain')
        tasks = build_tasks(db,scopes,research,engine)
        evaluation = build_evaluation(db,tasks)
        evolution = build_evolution(db,tasks,evaluation)
        spec = tasks.create(research_id=root.id,node_id=chat.node_id,kind='extract',
            user_request='Extract A 2025 cash flow',required_refs=(ref,))
        receipt = (await tasks.run(spec.id))['receipt']
        Path(os.environ['STAGE7_BROWSER_IDS']).write_text(json.dumps({
            'research_id':root.id,'result_id':receipt.result_id}))
        app = create_app(research,'frontend/dist',tasks,evaluation=evaluation,evolution=evolution)
        await uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8757,log_level='warning')).serve()
    finally: db.close()


if __name__ == '__main__': asyncio.run(main())
