"""Real-model public market research through the isolated 8783 preview."""
import asyncio
import json
import os
from pathlib import Path

import httpx
import psycopg


async def main():
    attempt = os.environ.get('COLLABORATION_C3_ATTEMPT', '2')
    project_id = 'c3-market-public-verify-' + attempt
    research_id = 'c3-market-main-verify-' + attempt
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8783', timeout=20) as client:
        response = await client.post('/api/projects', json={
            'id': project_id, 'name': 'C3 public market research'})
        response.raise_for_status()
        response = await client.post('/api/project_main_task', json={
            'project_id': project_id, 'research_id': research_id,
            'title': '2025 Q4 US retail e-commerce market'})
        response.raise_for_status()
        projection = (await client.get('/api/projection', params={'research_id': research_id})).json()
        node = next(v['node'] for v in projection['nodes'] if v['node']['display_parent_id'] is None)
        node_id = node['id']
        chat = (await client.get('/api/chat_read', params={'node_id': node_id})).json()['chat']
        message_id = 'c3-market-user-request-' + attempt
        if not any(m['id'] == message_id for m in chat['messages']):
            response = await client.post('/api/chat_send', json={
                'node_id': node_id, 'expected_revision': chat['revision'], 'message_id': message_id,
                'content': '请让具备联网能力的市场调研专家，调查美国 2025 年第四季度零售电商销售规模与占零售总额的比例。自行搜索并抓取美国人口普查局的官方正文，说明季度、季节调整口径、来源链接和局限；不要把搜索摘要当证据。你收到专家结果后注明独立复核状态再告诉我。我没有提供任何来源正文。'})
            response.raise_for_status()
            chat = response.json()
        response = await client.post('/api/chat_reply', json={
            'node_id': node_id, 'expected_revision': chat['revision'],
            'command_id': 'c3-market-manager-run-' + attempt})
        response.raise_for_status()
        command = response.json()
        latest = chat
        for _ in range(150):
            await asyncio.sleep(2)
            latest = (await client.get('/api/chat_read', params={'node_id': node_id})).json()['chat']
            if len(latest['messages']) >= 2 and latest['messages'][-1]['role'] == 'assistant' and latest['messages'][-2]['id'] == message_id:
                break
        activities = (await client.get('/api/task_activity', params={'research_id': research_id})).json()
        resources = (await client.get('/api/resources', params={'research_id': research_id})).json()
        run_id = command.get('run_id')
        manager_run = (await client.get('/api/run', params={'id': run_id})).json() if run_id else None
        task_rows = []
        for activity in activities:
            task_rows.append({'task_id': activity['spec']['id'],
                'expert_id': activity['spec']['expert_id'],
                'required_refs': activity['spec']['required_refs'],
                'dispatch_status': activity.get('dispatch_status'),
                'runs': [{'run_id': row['binding']['id'], 'execution_status': row['execution_status'],
                    'receipt': row['receipt']} for row in activity['runs']]})
        with psycopg.connect('host=127.0.0.1 port=55449 dbname=collaboration_verify user=chenyouran') as database:
            with database.cursor() as cursor:
                cursor.execute("SELECT payload FROM resource_ingestions WHERE payload->>'run_id' IN "
                    "(SELECT id FROM task_runs WHERE task_id IN "
                    "(SELECT task_id FROM task_specs WHERE research_id=%s)) "
                    "AND payload->>'kind'='web'", (research_id,))
                fetched_sources = [row[0] for row in cursor.fetchall()]
                cursor.execute("SELECT r.run_id,r.status,r.result->'usage',r.result->'error' "
                    "FROM runs r JOIN task_runs tr ON tr.id=r.run_id "
                    "JOIN task_specs ts ON ts.task_id=tr.task_id WHERE ts.research_id=%s",
                    (research_id,))
                expert_runs = [dict(run_id=row[0], status=row[1], usage=row[2], error=row[3])
                    for row in cursor.fetchall()]
                cursor.execute("SELECT command_id,status,payload->>'resume_status',payload->>'resume_attempt' "
                    "FROM collaboration_intents WHERE research_id=%s", (research_id,))
                intents = [dict(command_id=row[0], status=row[1], resume_status=row[2], resume_attempt=row[3])
                    for row in cursor.fetchall()]
                used_snapshots = [ref['snapshot_id'] for task in task_rows for row in task['runs']
                    if row['execution_status'] == 'succeeded' and row['receipt']
                    for ref in row['receipt']['resource_refs']]
                cursor.execute("SELECT payload FROM resource_ingestions "
                    "WHERE payload->>'snapshot_id'=ANY(%s)", (used_snapshots,))
                receipt_sources = [row[0] for row in cursor.fetchall()]
        evidence = {'project_id': project_id, 'research_id': research_id, 'node_id': node_id,
            'user_message_id': message_id, 'source_body_supplied_by_user': False,
            'manager_run_id': run_id,
            'manager_status': manager_run['run']['status'] if manager_run else None,
            'tool_names': [v['event']['name'] for v in manager_run['events']
                if v['event']['phase'] == 'tool' and v['event']['status'] == 'completed'] if manager_run else [],
            'assistant_replied': latest['messages'][-1]['role'] == 'assistant' and latest['messages'][-2]['id'] == message_id,
            'assistant_report': latest['messages'][-1]['content'] if latest['messages'][-1]['role'] == 'assistant' else None,
            'tasks': task_rows, 'resources': resources,
            'fetched_sources': fetched_sources, 'receipt_sources': receipt_sources,
            'expert_runs': expert_runs, 'intents': intents}
        evidence['acceptance_passed'] = bool(evidence['assistant_replied'] and
            any(v['status'] == 'succeeded' for v in expert_runs) and
            any(v['kind'] == 'web' and 'census.gov' in v['url']
                for v in receipt_sources) and
            any(row['receipt'] and row['receipt']['resource_refs'] and
                row['receipt']['delivery'] in ('pending_review', 'passed')
                for task in task_rows for row in task['runs']))
        output = Path('docs/collaboration-c3-market-attempt-' + attempt + '.json')
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print(json.dumps({'acceptance_passed': evidence['acceptance_passed'],
            'assistant_replied': evidence['assistant_replied'],
            'manager_status': evidence['manager_status'],
            'tools': evidence['tool_names'], 'task_count': len(task_rows),
            'evidence': str(output)}, ensure_ascii=False))


asyncio.run(main())
