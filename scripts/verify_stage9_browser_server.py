"""Stage 9 local browser fixture with scripted expert outputs."""
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
        scopes = build_resources(db, '/private/tmp/stage9-pg-20261003/blobs')
        def script(request):
            payload = json.loads(request.input.messages[0].content)
            selected = payload.get('approved_memory', [])
            report = ('本次指令优先：先看竞争风险。' if '本次先看竞争风险' in payload['goal'] else '按任务要求分析。')
            report += ' 已应用偏好。' if selected else ' 未应用偏好。'
            return [], {'report':report,'fields':{'company':'A','period':'2025','operating_cash_flow':'100',
                'capital_expenditure':'20','unit':'million USD'},
                'sources':{'operating_cash_flow':'line 1','capital_expenditure':'line 1','unit':'line 1'},'missing':[]}
        engine = ScriptedEngine(script)
        research = build_research(db, scopes, engine)
        root = research.create('Stage 9 浏览器偏好验收')
        chat = await ChatService(research).start(root.id, '主线')
        scope = scopes.current(root.scope_id, 'local')
        _, ref = scopes.ingest(scope,content=b'A 2025 CFO 100 capex 20 million USD',name='company-A.txt',media_type='text/plain')
        tasks = build_tasks(db,scopes,research,engine)
        evaluation = build_evaluation(db,tasks)
        evolution = build_evolution(db,tasks,evaluation)
        Path('/private/tmp/stage9-browser-ids.json').write_text(json.dumps({'research_id':root.id,'node_id':chat.node_id,'ref':ref.model_dump(mode='json')}))
        await uvicorn.Server(uvicorn.Config(create_app(research,'frontend/dist',tasks,evaluation=evaluation,evolution=evolution),host='127.0.0.1',port=8759,log_level='warning')).serve()
    finally: db.close()


if __name__ == '__main__': asyncio.run(main())
