"""Two real expert drafts, then a natural-language request for autonomous web follow-up."""
import argparse
import json
import time
from pathlib import Path

import httpx
import psycopg


NOTES = '''Acceptance briefing, not a verified source or market conclusion.
Topic: US retail e-commerce share, 2025 Q4, original Census release published March 10, 2026.
Note A reports 16.6 percent. The note omits the adjustment basis and calculation table.
Note B reports 18.3 percent. The note omits the adjustment basis and calculation table.
These are unverified leads for checking against original public sources; do not infer which is correct from this briefing.
'''
SETUP = ('这是分阶段验收。第一阶段请安排 expert_search 作为专家 A 审查简报中的 A 线索，'
    'expert_synthesize 作为专家 B 审查 B 线索，分别交付简短初步报告，说明已知和待核实项。'
    '仅本阶段不联网，只读已上传的 acceptance-briefing.txt；它是未经核实的线索，不是官方证据。'
    '保留两份报告供下一阶段进一步核查，不正式采用。')
FOLLOWUP = ('两份初步报告的数字有差异。现在允许联网，请让这两位专家各自补查官方原始资料，'
    '弄清各自数字的含义和差异原因，再根据他们找到的新证据给我判断。'
    '目标是 2026 年 3 月 10 日首次发布的美国 2025 年第四季度数据，不要混入后续修订。'
    '保留原来的初步报告；找不到依据就说明搜索结果和缺口，不强行选一个数字。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--resume-from', type=int)
    args = parser.parse_args()
    attempt = args.attempt
    if attempt < 1:
        parser.error('attempt must be positive')
    path = Path(f'docs/collaboration-c4-autonomous-research-{attempt}.json')
    if path.exists():
        print(f'Existing evidence preserved: {path}')
        return
    project_id = f'c4-autonomous-research-{attempt}'
    research_id = project_id + '-main'
    previous = None
    if args.resume_from is not None:
        previous = json.loads(Path(f'docs/collaboration-c4-autonomous-research-{args.resume_from}.json').read_text())
        project_id, research_id = previous['project_id'], previous['research_id']
    evidence = {'project_id': project_id, 'research_id': research_id,
        'real_models': True, 'real_web_provider': True,
        'briefing_is_unverified_test_input': True, 'briefing': NOTES,
        'setup_prompt': SETUP, 'followup_prompt': FOLLOWUP}
    evidence['resume_from'] = args.resume_from
    with httpx.Client(base_url='http://127.0.0.1:8783/api', timeout=30) as client:
        def get(action, **params):
            r = client.get('/' + action, params=params); r.raise_for_status(); return r.json()

        def post(action, data):
            r = client.post('/' + action, json=data); r.raise_for_status(); return r.json()

        def save():
            path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')

        def turn(label, prompt):
            chat = get('chat_read', node_id=node_id)['chat']
            message_id = project_id + '-' + label
            if not any(m['id'] == message_id for m in chat['messages']):
                chat = post('chat_send', {'node_id': node_id, 'content': prompt,
                    'expected_revision': chat['revision'], 'message_id': message_id})
            command_id = message_id + '-reply'
            commands = get('projection', research_id=research_id)['commands']
            if not any(c['command']['command_id'] == command_id for c in commands):
                post('chat_reply', {'node_id': node_id, 'expected_revision': chat['revision'],
                    'command_id': command_id})
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                chat = get('chat_read', node_id=node_id)['chat']
                activity = get('task_activity', research_id=research_id)
                projection = get('projection', research_id=research_id)
                runs = [get('run', id=c['run_id']) for c in projection['commands'] if c.get('run_id')]
                suffix = chat['messages'][next(i for i, m in enumerate(chat['messages']) if m['id'] == message_id) + 1:]
                if any(m['role'] == 'assistant' for m in suffix) and activity and all(
                        r['dispatch_status'] in ('completed', 'failed') for r in activity) and all(
                        r['run']['status'] in ('succeeded', 'failed', 'cancelled', 'save_failed') for r in runs) and any(
                        c['command']['command_id'].startswith('resume:') and c['status'] == 'completed'
                        and c.get('run_id') and any(m['id'] == 'chat-reply:' + c['run_id'] for m in suffix)
                        for c in projection['commands']):
                    break
                command = next(c for c in projection['commands'] if c['command']['command_id'] == command_id)
                if command.get('run_id') and get('run', id=command['run_id'])['run']['status'] == 'failed' and not activity:
                    break
                time.sleep(5)
            return {'chat': chat, 'activity': activity, 'manager_runs': runs}

        post('projects', {'id': project_id, 'name': 'C4 autonomous official-source follow-up'})
        post('project_main_task', {'project_id': project_id, 'research_id': research_id,
            'title': 'Two experts resolve differing Q4 2025 e-commerce figures'})
        nodes = get('projection', research_id=research_id)['nodes']
        node_id = next(n['node']['id'] for n in nodes if n['node']['display_parent_id'] is None)
        evidence['node_id'] = node_id
        if not get('resources', research_id=research_id)['resources']:
            r = client.post('/upload', params={'research_id': research_id, 'name': 'acceptance-briefing.txt'},
                content=NOTES.encode(), headers={'Content-Type': 'text/plain'})
            r.raise_for_status()
        before = previous['after'] if previous else turn('setup', SETUP)
        evidence['before'] = before
        ready = previous is not None or (len(before['activity']) == 2 and all(any(r['execution_status'] == 'succeeded'
            and r['receipt'] and r['receipt']['delivery'] == 'pending_review' for r in row['runs'])
            for row in before['activity']))
        print(json.dumps({'stage': 'setup', 'ready': ready, 'task_count': len(before['activity'])}), flush=True)
        if not ready:
            evidence['status'] = 'setup_not_ready'; save(); return
        after = turn(f'followup-{attempt}', FOLLOWUP)
        evidence['after'] = after
        evidence['sessions'] = {row['spec']['id']: get('expert_thread', task_id=row['spec']['id'])
            for row in after['activity']}
        run_ids = [r['binding']['id'] for row in after['activity'] for r in row['runs']]
        with psycopg.connect('postgresql://chenyouran@127.0.0.1:55449/collaboration_verify',
                options='-c default_transaction_read_only=on') as db:
            evidence['expert_events'] = {rid: [r[0] for r in db.execute(
                'SELECT event FROM run_events WHERE root_run_id=%s ORDER BY sequence', (rid,))]
                for rid in run_ids}
            evidence['web_sources'] = [r[0] for r in db.execute(
                "SELECT payload FROM resource_ingestions WHERE payload->>'run_id'=ANY(%s) AND payload->>'kind'='web'",
                (run_ids,))]
        originals = {row['spec']['id']: row for row in before['activity']}
        def fixed(row):
            return {'spec': row['spec'], 'runs': [{k: r[k] for k in ('binding', 'receipt', 'output')}
                for r in row['runs']]}
        followups = [row for row in after['activity'] if row['spec']['id'] not in originals]
        searched = set()
        for row in followups:
            names = {e['name'] for r in row['runs'] for e in evidence['expert_events'][r['binding']['id']]
                if e['phase'] == 'tool' and e['status'] == 'completed'}
            if {'search_web', 'fetch_web'} <= names:
                searched.add(row['spec']['expert_id'])
        evidence['checks'] = {
            'original_reports_preserved': all(fixed(row) == fixed(originals[row['spec']['id']])
                for row in after['activity'] if row['spec']['id'] in originals),
            'both_original_experts_searched_and_fetched': searched == {'expert_search', 'expert_synthesize'},
            'only_original_sessions': {r['spec']['expert_session_id'] for r in after['activity']} ==
                {r['spec']['expert_session_id'] for r in before['activity']},
            'followup_reports_succeeded': len(followups) >= 2 and all(any(
                r['execution_status'] == 'succeeded' and r['receipt'] and r['receipt']['delivery'] == 'pending_review'
                for r in row['runs']) for row in followups),
            'manager_replied_after_followup': after['chat']['messages'][-1]['role'] == 'assistant',
            'no_formal_adoption': not any(r['receipt'] and r['receipt']['adoption'] == 'adopted'
                for row in after['activity'] for r in row['runs'])}
        evidence['semantic_review'] = 'Pending manual inspection; execution checks alone do not establish correct judgment.'
        save()
        print(json.dumps({'evidence': str(path), 'checks': evidence['checks'],
            'task_count': len(after['activity'])}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
