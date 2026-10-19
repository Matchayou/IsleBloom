"""Run a labeled synthetic-data, real-model multi-expert acceptance case on port 8783."""
import json
import time
from pathlib import Path

import httpx


BASE = 'http://127.0.0.1:8783/api'
PROJECT_ID = 'c4-live-conflict-verification'
RESEARCH_ID = 'c4-live-conflict-main'
EVIDENCE = Path('docs/collaboration-c4-live-verification.json')
SOURCE = ("Synthetic C4 verification only. These figures are invented and must never be reported as real market data.\n"
          "Brief A claims a 12% share for an adjusted quarterly sample. Its method excludes small stores.\n"
          "Brief B claims a 21% share for an unadjusted quarterly sample. Its method includes small stores.\n"
          "The two denominators and seasonal adjustments differ. Neither brief has an external citation or independent review.\n")
PROMPT = ("这是一次使用合成资料的多专家协作验收。请先调用 find_experts 和 read_context，读取已上传的 "
          "c4-live-conflict.txt。请并行派发两项独立任务：让 expert_search 审查 Brief A 的 12% 调整口径，"
          "让 expert_synthesize 审查 Brief B 的 21% 未调整口径；两项任务后台执行，"
          "都附上相同的固定资料引用。收到两份专家结果后，在主对话比较口径分歧、缺失和局限。"
          "所有数字都是虚构的测试数据，绝不能当成真实市场结论；未通过独立复核时不得正式采用。")


def main():
    with httpx.Client(timeout=30.0) as client:
        def get(action, **params):
            response = client.get(f'{BASE}/{action}', params=params)
            response.raise_for_status()
            return response.json()

        def post(action, body, **params):
            response = client.post(f'{BASE}/{action}', params=params, json=body)
            response.raise_for_status()
            return response.json()

        project = post('projects', {'id': PROJECT_ID, 'name': 'C4 real-model conflict verification'})
        post('project_main_task', {'project_id': project['id'], 'research_id': RESEARCH_ID,
                                   'title': 'Two independent experts compare synthetic conflicting briefs'})
        contents = get('project_contents', project_id=PROJECT_ID)
        node = next(row for row in contents['conversations'] if row['kind'] == 'node'
                    and row['research_id'] == RESEARCH_ID and row['parent_id'] is None)
        node_id = node['id']
        library = get('resources', research_id=RESEARCH_ID)
        if not any(row['resource']['name'] == 'c4-live-conflict.txt' for row in library['resources']):
            response = client.post(f'{BASE}/upload', params={'research_id': RESEARCH_ID,
                'name': 'c4-live-conflict.txt'}, content=SOURCE.encode(),
                headers={'Content-Type': 'text/plain'})
            response.raise_for_status()
        chat = get('chat_read', node_id=node_id)['chat']
        if not any(message['id'] == 'c4-live-user-request' for message in chat['messages']):
            chat = post('chat_send', {'node_id': node_id, 'content': PROMPT,
                'expected_revision': chat['revision'], 'message_id': 'c4-live-user-request'})
        if not any(message['role'] == 'assistant' for message in chat['messages']):
            post('chat_reply', {'node_id': node_id, 'expected_revision': chat['revision'],
                'command_id': 'c4-live-manager-reply'})
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline:
            chat = get('chat_read', node_id=node_id)['chat']
            activities = get('task_activity', research_id=RESEARCH_ID)
            if len(activities) >= 2 and all(row['dispatch_status'] in ('completed', 'failed')
                                           for row in activities) and any(m['role'] == 'assistant'
                                           for m in chat['messages']):
                break
            time.sleep(3)
        projection = get('projection', research_id=RESEARCH_ID)
        runs = []
        for command in projection['commands']:
            run_id = command.get('run_id')
            if run_id:
                detail = get('run', id=run_id)
                runs.append({'run_id': run_id, 'status': detail['run']['status'],
                    'usage': detail['run'].get('result', {}).get('usage') if detail['run'].get('result') else None,
                    'tools': [event['event']['name'] for event in detail['events']
                              if event['event']['phase'] == 'tool' and event['event']['status'] == 'started']})
        evidence = {'project_id': PROJECT_ID, 'research_id': RESEARCH_ID, 'node_id': node_id,
            'synthetic_data': True, 'source_name': 'c4-live-conflict.txt',
            'manager_messages': [message['content'] for message in chat['messages'] if message['role'] == 'assistant'],
            'experts': [{'task_id': row['spec']['id'], 'expert_id': row['spec']['expert_id'],
                         'status': row['dispatch_status'], 'receipts': [run['receipt']['delivery']
                         for run in row['runs'] if run['receipt']]} for row in activities],
            'manager_runs': runs}
        EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'evidence': str(EVIDENCE), 'expert_count': len(activities),
            'manager_reply_count': len(evidence['manager_messages']),
            'statuses': [row['status'] for row in evidence['experts']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
