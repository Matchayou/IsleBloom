"""Fresh PostgreSQL fixture for the current project/Agent browser suite."""
import asyncio
import os
import json
from uuid import uuid4

import uvicorn

from agent_core.adapters.postgres import PostgresDatabase
from agent_core.app.api import create_app
from agent_core.composition import build_resources, build_controlled_services
from agent_core.research.chat import ChatService
from verify_plan_browser_server import Provider as PlanProvider


class Provider(PlanProvider):
    def get_model(self, name):
        fallback = super().get_model(name)
        class Model:
            async def stream_response(self, **kwargs):
                from types import SimpleNamespace
                from openai.types.responses import Response
                saved = await self.get_response(**kwargs)
                for item in saved.output:
                    for content in getattr(item, 'content', []):
                        if content.type == 'output_text':
                            yield SimpleNamespace(type='response.output_text.delta', delta=content.text)
                response = Response(id=uuid4().hex, object='response', created_at=0,
                    model='browser-fixture', status='completed', output=saved.output,
                    parallel_tool_calls=False, tool_choice='auto', tools=[],
                    usage={'input_tokens':saved.usage.input_tokens, 'output_tokens':saved.usage.output_tokens,
                        'total_tokens':saved.usage.input_tokens+saved.usage.output_tokens,
                        'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':0}, 'output_tokens_details':{'reasoning_tokens':0}})
                yield SimpleNamespace(type='response.completed', response=response)

            async def get_response(self, **kwargs):
                from agents import ModelResponse
                from agents.usage import Usage
                from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
                raw = kwargs['input'][0]['content']
                if isinstance(raw, list):
                    raw = ''.join(part.get('text', '') for part in raw)
                payload = json.loads(raw)
                evidence = 'Browser evidence fixture' in payload.get('goal', '')
                if evidence:
                    outputs = [json.loads(v['output'])['value'] for v in kwargs['input'] if v.get('type') == 'function_call_output']
                    reading = next((v for v in outputs if v.get('fragments')), None)
                    if reading:
                        fragment = reading['fragments'][0]
                        value = {'report':fragment['text'], 'claims':[{'statement':fragment['text'],
                            'citations':[{'ref':reading['ref'], 'location':fragment['location'],
                                'quote':fragment['text'], 'relation':'supports', 'reasoning':'Exact synthetic fixture text.'}]}]}
                        output = [ResponseOutputMessage(id=uuid4().hex, type='message', role='assistant', status='completed',
                            content=[ResponseOutputText(type='output_text',text=json.dumps(value),annotations=[])])]
                    else:
                        tool = 'read_source' if outputs else 'search_tools'
                        args = {'ref':payload['sources'][0]['ref']} if outputs else {'query':'read_source'}
                        output = [ResponseFunctionToolCall(type='function_call', name=tool,
                            call_id=uuid4().hex, arguments=json.dumps(args))]
                    return ModelResponse(output=output, usage=Usage(requests=1,input_tokens=3,output_tokens=4),response_id=None)
                response = await fallback.get_response(**kwargs)
                schema = kwargs.get('output_schema')
                if schema and 'blocking_missing' not in schema.json_schema().get('properties', {}):
                    for item in response.output:
                        for content in getattr(item, 'content', []):
                            if content.type == 'output_text':
                                value = json.loads(content.text)
                                value.pop('blocking_missing', None)
                                content.text = json.dumps(value)
                return response
        return Model()


async def main():
    os.environ.pop('TINYFISH_API_KEY', None)
    db = PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        scopes = build_resources(db, '/private/tmp/full-project-browser/blobs')
        research, tasks, projects = build_controlled_services(db, scopes,
            private_root='/private/tmp/full-project-browser/private', model_provider=Provider())
        first_project = projects.create('Persistence A', 'persistence-a')
        second_project = projects.create('Persistence B', 'persistence-b')
        first = await projects.create_main_task(first_project.id, 'First', 'persistence-main-1')
        await projects.create_main_task(first_project.id, 'Second', 'persistence-main-2')
        await projects.create_main_task(second_project.id, 'Third', 'persistence-main-3')
        node = research.query('list_nodes', first.research_id)[0]
        chat_service = ChatService(research, tasks)
        chat = chat_service.send(node.id, '分析固定样本，保留未知。', 0)
        await chat_service.branch(node.id, chat.messages[0].id, '风险讨论分支', chat.revision)
        for expert, goal in (('expert_synthesize', '补充局限性并重新总结虚构市场'),
                             ('expert_search', '分析样本风险')):
            intent = research.collaboration.delegate_task(command_id=expert, node_id=node.id,
                expert_id=expert, task_instruction=goal, delivery_requirements='保留未知与来源边界')
            projects.arrange(first_project.id, 'task', intent.task_id, 0, title=goal)
            if expert == 'expert_synthesize':
                await projects.discuss(intent.task_id, '这份结论的来源是什么？', 0)
        await uvicorn.Server(uvicorn.Config(create_app(research, 'frontend/dist', tasks),
            host='127.0.0.1', port=int(os.environ.get('BROWSER_TEST_PORT', '18744')), log_level='warning')).serve()
    finally:
        db.close()


if __name__ == '__main__':
    asyncio.run(main())
