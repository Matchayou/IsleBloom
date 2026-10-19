"""Continue the existing synthetic C4 case using its two successful reports."""
import json
import time
import argparse
from pathlib import Path

import httpx

BASE = 'http://127.0.0.1:8783/api'
RESEARCH_ID = 'c4-live-conflict-main'
NODE_ID = 'ee4ba59e-88e4-49df-8bcf-231fae81a800'
MESSAGE_ID = 'c4-compare-successful-reports-v1'
COMMAND_ID = 'c4-compare-successful-reports-reply-v1'
PROMPT = ('现在专家 A 的最新第 3 版和专家 B 的第 1 版都已成功交付。'
          '请阅读两份报告和原始合成资料，比较它们的依据、结论和差异，判断是否真的互相矛盾，'
          '解释原因并给出目前能成立的结论。如果还有关键疑问，请向现有专家追问核查，'
          '再把答复纳入比较。保留已成功的报告，不要重复运行整份分析。'
          '本次只核查已有合成资料，不联网；资料里没有的内容明确说明缺失，'
          '不强行选一个数字，也不把未复核的结果当成正式采用。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', type=int, default=1)
    attempt = parser.parse_args().attempt
    if attempt < 1:
        parser.error('--attempt must be positive')
    path = Path('docs/collaboration-c4-disagreement-verification.json' if attempt == 1
        else f'docs/collaboration-c4-disagreement-attempt-{attempt}.json')
    if path.exists():
        evidence = json.loads(path.read_text())
        status = evidence['manager_run']['run']['status']
        print(json.dumps({'status': status, 'evidence': str(path),
            'reused': True, 'note': 'Existing evidence preserved. Use a new attempt number for a new run.'}))
        if status != 'succeeded':
            raise SystemExit(1)
        return
    message_id = MESSAGE_ID if attempt == 1 else f'c4-compare-successful-reports-v{attempt}'
    command_id = COMMAND_ID if attempt == 1 else f'c4-compare-successful-reports-reply-v{attempt}'
    with httpx.Client(base_url=BASE, timeout=30) as client:
        def get(action, **params):
            response = client.get('/' + action, params=params)
            response.raise_for_status()
            return response.json()
        def post(action, payload):
            response = client.post('/' + action, json=payload)
            response.raise_for_status()
            return response.json()
        before = get('task_activity', research_id=RESEARCH_ID)
        chat = get('chat_read', node_id=NODE_ID)['chat']
        if not any(m['id'] == message_id for m in chat['messages']):
            chat = post('chat_send', {'node_id': NODE_ID, 'content': PROMPT,
                'expected_revision': chat['revision'], 'message_id': message_id})
        commands = get('projection', research_id=RESEARCH_ID)['commands']
        command = next((c for c in commands if c['command']['command_id'] == command_id), None)
        if command is None:
            command = post('chat_reply', {'node_id': NODE_ID,
                'expected_revision': chat['revision'], 'command_id': command_id})
        deadline = time.monotonic() + 210
        detail = None
        while time.monotonic() < deadline:
            commands = get('projection', research_id=RESEARCH_ID)['commands']
            command = next(c for c in commands if c['command']['command_id'] == command_id)
            if command.get('run_id'):
                detail = get('run', id=command['run_id'])
                if detail['run']['status'] in ('succeeded', 'failed', 'cancelled', 'save_failed'):
                    break
            time.sleep(2)
        after = get('task_activity', research_id=RESEARCH_ID)
        sessions = {row['spec']['id']: get('expert_thread', task_id=row['spec']['id']) for row in after}
        evidence = {'synthetic_data': True, 'research_id': RESEARCH_ID, 'node_id': NODE_ID,
            'prompt': PROMPT, 'command': command, 'manager_run': detail,
            'before': before, 'after': after, 'expert_sessions': sessions}
        def task_state(rows):
            return {row['spec']['id']: {'version': row['spec']['version'],
                'runs': {run['binding']['id']: {
                    'execution_status': run['execution_status'],
                    'receipt': run['receipt'], 'output': run['output']}
                    for run in row['runs']}} for row in rows}
        completed_tools = [e['event']['name'] for e in detail['events']
            if e['event']['phase'] == 'tool' and e['event']['status'] == 'completed'] if detail else []
        status = detail['run']['status'] if detail else command['status']
        evidence['checks'] = {
            'manager_succeeded': status == 'succeeded',
            'expert_versions_runs_receipts_outputs_unchanged': task_state(before) == task_state(after),
            'no_network_tools': not {'search_web', 'fetch_web'}.intersection(completed_tools),
            'no_formal_adoption': 'adopt_result' not in completed_tools}
        evidence['semantic_review'] = 'Not automated. Inspect report and claims; execution success does not establish correct disagreement handling.'
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': status, 'tools': completed_tools, 'checks': evidence['checks'],
            'error': detail['run']['result']['error'] if detail and detail['run']['result'] else None,
            'evidence': str(path)}, ensure_ascii=False), flush=True)
        if not all(evidence['checks'].values()):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
