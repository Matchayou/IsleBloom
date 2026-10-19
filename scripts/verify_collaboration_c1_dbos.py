"""Drive the isolated local preview through its HTTP and DBOS path."""
import asyncio
import json
from pathlib import Path

import httpx


async def main():
    prior = json.loads(Path('docs/collaboration-c1-live-verification.json').read_text())
    research_id = prior['research_id']
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8783', timeout=20) as client:
        projection = (await client.get('/api/projection', params={'research_id': research_id})).json()
        node = next(v['node'] for v in projection['nodes'] if v['node']['display_parent_id'] is None)
        node_id = node['id']
        chat = (await client.get('/api/chat_read', params={'node_id': node_id})).json()['chat']
        message_id = 'dbos-followup:' + node_id
        if not any(m['id'] == message_id for m in chat['messages']):
            response = await client.post('/api/chat_send', json={
                'node_id': node_id, 'content': '请再次让同一位综合整理专家复核这份合成样本是否能支持真实市场规模结论，保留来源和局限；收到结果后继续回复。',
                'expected_revision': chat['revision'], 'message_id': message_id})
            response.raise_for_status()
            chat = response.json()
        command_id = 'dbos-chat:' + node_id
        response = await client.post('/api/chat_reply', json={
            'node_id': node_id, 'expected_revision': chat['revision'], 'command_id': command_id})
        response.raise_for_status()
        latest = None
        activities = []
        for _ in range(90):
            await asyncio.sleep(2)
            latest = (await client.get('/api/chat_read', params={'node_id': node_id})).json()['chat']
            activities = (await client.get('/api/task_activity', params={'research_id': research_id})).json()
            if latest['messages'][-1]['role'] == 'assistant' and latest['messages'][-2]['id'] == message_id:
                break
        evidence = {'research_id': research_id, 'node_id': node_id,
            'message_id': message_id, 'chat_revision': latest['revision'],
            'assistant_replied': latest['messages'][-1]['role'] == 'assistant' and latest['messages'][-2]['id'] == message_id,
            'assistant_report': latest['messages'][-1]['content'] if latest['messages'][-1]['role'] == 'assistant' else None,
            'tasks': [{'task_id': a['spec']['id'], 'session_id': a['spec'].get('expert_session_id'),
                'dispatch_status': a.get('dispatch_status'), 'job_status': a.get('job_status'),
                'runs': [{'execution_status': r['execution_status'],
                    'delivery': r['receipt']['delivery'] if r['receipt'] else None}
                    for r in a['runs']]} for a in activities]}
        output = Path('docs/collaboration-c1-dbos-verification.json')
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print(json.dumps({'assistant_replied': evidence['assistant_replied'],
            'task_count': len(evidence['tasks']),
            'dispatch_statuses': [v['dispatch_status'] for v in evidence['tasks']],
            'evidence': str(output)}, ensure_ascii=False))


asyncio.run(main())
