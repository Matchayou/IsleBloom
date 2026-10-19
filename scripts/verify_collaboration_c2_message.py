"""Real-model ordinary manager-to-expert question in the isolated 8783 preview."""
import asyncio
import json
from pathlib import Path

import httpx


async def main():
    prior = json.loads(Path('docs/collaboration-c1-dbos-verification.json').read_text())
    research_id, node_id = prior['research_id'], prior['node_id']
    task_id = prior['tasks'][-1]['task_id']
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8783', timeout=20) as client:
        before_thread = (await client.get('/api/expert_thread', params={'task_id': task_id})).json()
        before_tasks = (await client.get('/api/task_activity', params={'research_id': research_id})).json()
        chat = (await client.get('/api/chat_read', params={'node_id': node_id})).json()['chat']
        message_id = 'c2-message-followup:' + node_id
        if not any(m['id'] == message_id for m in chat['messages']):
            response = await client.post('/api/chat_send', json={
                'node_id': node_id,
                'content': '请向现有综合整理专家普通追问：这份合成资料是否能支持真实市场规模数字？请它用一两句话回答并说明局限，然后转述给我。这只是对已有工作的讨论，不要求新建、重做或运行正式分析。',
                'expected_revision': chat['revision'], 'message_id': message_id})
            response.raise_for_status()
            chat = response.json()
        response = await client.post('/api/chat_reply', json={
            'node_id': node_id, 'expected_revision': chat['revision'],
            'command_id': 'c2-message-reply-2:' + node_id})
        response.raise_for_status()
        latest = None
        for _ in range(90):
            await asyncio.sleep(2)
            latest = (await client.get('/api/chat_read', params={'node_id': node_id})).json()['chat']
            if latest['messages'][-1]['role'] == 'assistant' and latest['messages'][-2]['id'] == message_id:
                break
        after_thread = (await client.get('/api/expert_thread', params={'task_id': task_id})).json()
        after_tasks = (await client.get('/api/task_activity', params={'research_id': research_id})).json()
        before_runs = {r['binding']['id'] for a in before_tasks for r in a['runs']}
        after_runs = {r['binding']['id'] for a in after_tasks for r in a['runs']}
        new_messages = after_thread['messages'][len(before_thread['messages']):]
        evidence = {'research_id': research_id, 'node_id': node_id, 'view_task_id': task_id,
            'user_message_id': message_id,
            'assistant_replied': latest['messages'][-1]['role'] == 'assistant' and latest['messages'][-2]['id'] == message_id,
            'assistant_report': latest['messages'][-1]['content'] if latest['messages'][-1]['role'] == 'assistant' else None,
            'new_expert_messages': new_messages,
            'linked_task_id': new_messages[0]['task_id'] if new_messages else None,
            'new_run_ids': sorted(after_runs - before_runs),
            'task_count_before': len(before_tasks), 'task_count_after': len(after_tasks)}
        output = Path('docs/collaboration-c2-message-verification.json')
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print(json.dumps({'assistant_replied': evidence['assistant_replied'],
            'new_expert_messages': len(new_messages), 'new_runs': len(evidence['new_run_ids']),
            'task_count_delta': len(after_tasks)-len(before_tasks), 'evidence': str(output)}, ensure_ascii=False))


asyncio.run(main())
