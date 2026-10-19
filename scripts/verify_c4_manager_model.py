"""Reassess saved C4 reports with the configured manager; preserve expert work."""
import json
import time
import argparse
from pathlib import Path

import httpx
from dotenv import dotenv_values


PROMPT = ('请重新读取两位专家第一阶段与补查阶段的报告，以及他们已经取得的官方原始资料，'
    '判断两条数字线索是否真的互相矛盾，解释差异原因，说明哪些结论有证据、哪些仍未知。'
    '目标仍是 2026 年 3 月 10 日首次发布的美国 2025 年第四季度数据。'
    '请自行核查先前结论，不把历史主助手答复当成证据。'
    '本次只复验主助手判断，不重新联网、不重新运行专家任务，保留原报告和待复核状态。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=('official', 'synthetic'), default='official')
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if args.attempt < 1 or args.timeout < 1:
        parser.error('attempt and timeout must be positive')
    case = args.case
    suffix = '' if args.attempt == 1 else f'-{args.attempt}'
    path = Path(f'docs/collaboration-c4-manager-pro-{case}{suffix}.json')
    if path.exists():
        raise SystemExit(f'Existing evidence preserved: {path}')
    research_id = 'c4-autonomous-research-4-main' if case == 'official' else 'c4-live-conflict-main'
    label = 'c4-manager-pro-' + case
    command_id = label + '-reply' + suffix
    prompt = PROMPT
    if case == 'synthetic':
        from verify_collaboration_c4_disagreement import PROMPT as synthetic_prompt
        prompt = synthetic_prompt
    config = dotenv_values('.env')
    evidence = {'research_id': research_id, 'prompt': prompt,
        'timeout_seconds': args.timeout,
        'configured_manager_model': config.get('OPENAI_MANAGER_MODEL'),
        'configured_expert_model': config.get('OPENAI_MODEL'),
        'comparison_limit': 'Existing reports preserved; chat history and prompt differ from the earlier automatic summary. Not a controlled model benchmark.'}
    with httpx.Client(base_url='http://127.0.0.1:8783/api', timeout=30) as client:
        def get(action, **params):
            r = client.get('/' + action, params=params)
            r.raise_for_status()
            return r.json()

        def post(action, data):
            r = client.post('/' + action, json=data)
            r.raise_for_status()
            return r.json()

        projection = get('projection', research_id=research_id)
        node = next(r['node'] for r in projection['nodes'] if r['node']['display_parent_id'] is None)
        node_id = node['id']
        evidence['node_id'] = node_id
        evidence['before'] = get('task_activity', research_id=research_id)
        chat = get('chat_read', node_id=node_id)['chat']
        if case == 'official' and not any(m['id'] == label for m in chat['messages']):
            chat = post('chat_send', {'node_id': node_id, 'content': prompt,
                'expected_revision': chat['revision'], 'message_id': label})
        command = next((r for r in projection['commands']
                        if r['command']['command_id'] == command_id), None)
        if command is None:
            if case == 'official':
                post('command', {'node_id': node_id, 'research_id': research_id,
                    'kind': 'chat_reply', 'chat_revision': chat['revision'],
                    'command_id': command_id,
                    'limits': {'max_turns': 12, 'max_calls': 12, 'timeout_seconds': args.timeout}})
            else:
                evidence['input_mode'] = 'Independent comparison of saved reports; full chat omitted because it exceeded the input limit.'
                evidence['blocked_chat_attempt'] = next(r for r in projection['commands']
                    if r['command']['command_id'] == 'c4-compare-successful-reports-reply-v7')
                post('command', {'command_id': command_id, 'research_id': research_id,
                    'node_id': node_id, 'kind': 'compare', 'goal': prompt,
                    'expected_revision': node['revision'], 'resource_refs': node['resource_refs'],
                    'limits': {'max_turns': 12, 'max_calls': 12, 'timeout_seconds': args.timeout}})
        deadline = time.monotonic() + args.timeout + 60
        detail = None
        while time.monotonic() < deadline:
            command = next(r for r in get('projection', research_id=research_id)['commands']
                           if r['command']['command_id'] == command_id)
            if command.get('run_id'):
                detail = get('run', id=command['run_id'])
                if detail['run']['status'] in ('succeeded', 'failed', 'cancelled', 'save_failed'):
                    break
            time.sleep(3)
        evidence.update(command=command, manager_run=detail,
                        after=get('task_activity', research_id=research_id))

        def fixed(rows):
            return {r['spec']['id']: {'spec': r['spec'], 'runs': [
                {k: v[k] for k in ('binding', 'receipt', 'output', 'execution_status')}
                for v in r['runs']]} for r in rows}

        names = [e['event']['name'] for e in detail['events']
                 if e['event']['phase'] == 'tool' and e['event']['status'] == 'completed'] if detail else []
        evidence['checks'] = {
            'manager_succeeded': bool(detail and detail['run']['status'] == 'succeeded'),
            'expert_work_unchanged': fixed(evidence['before']) == fixed(evidence['after']),
            'no_new_web_or_expert_run_or_adoption': not bool(set(names) & {
                'search_web', 'fetch_web', 'delegate_task', 'revise_task', 'adopt_result'})}
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'evidence': str(path), 'checks': evidence['checks'], 'tools': names}), flush=True)


if __name__ == '__main__':
    main()
