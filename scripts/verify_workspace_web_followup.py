"""Check that a follow-up reads saved web bodies without repeating web requests."""
import json
import time
from pathlib import Path
from uuid import uuid4
import httpx


def main():
    previous = json.loads(Path('docs/workspace-web-live-verification.json').read_text())
    assert previous['passed']
    report = {'passed': False, 'research_id': previous['research_id'], 'node_id': previous['node_id']}
    with httpx.Client(base_url=previous['base_url'], timeout=200, trust_env=False, headers={'Origin': previous['base_url']}) as client:
        def get(action, **params):
            response = client.get('/api/' + action, params=params)
            response.raise_for_status()
            return response.json()
        def post(action, body):
            response = client.post('/api/' + action, json=body)
            response.raise_for_status()
            return response.json()
        try:
            chat = get('chat_read', node_id=previous['node_id'])['chat']
            sent = post('chat_send', {'node_id': chat['node_id'], 'content': '请实际读取刚才保存的网页正文，给出一句支持型号结论的逐字原文和来源链接。不要重新联网搜索或抓取。简洁回答。',
                                     'expected_revision': chat['revision'], 'message_id': str(uuid4())})
            command_id = str(uuid4())
            post('chat_reply', {'node_id': chat['node_id'], 'expected_revision': sent['revision'], 'command_id': command_id})
            deadline = time.monotonic() + 200
            while time.monotonic() < deadline:
                command = next(c for c in get('projection', research_id=previous['research_id'])['commands'] if c['command']['command_id'] == command_id)
                if command['status'] != 'accepted':
                    run = get('run', id=command['run_id'])
                    report['run'] = run
                    assert run['run']['status'] == 'succeeded', run['run']['result']
                    payload = json.loads(run['run']['request']['input']['messages'][0]['content'])
                    assert payload['content'] == [] and payload['sources']
                    names = [e['event']['name'] for e in run['events'] if e['event']['phase'] == 'tool' and e['event']['status'] == 'completed']
                    assert set(names) & {'read_source', 'search_source'}, names
                    assert not set(names) & {'search_web', 'fetch_web'}, names
                    result = get('result', id='result:' + command['run_id'])
                    assert result['evidence']['evidence']
                    report.update(passed=True, result=result, tools=names)
                    print(json.dumps({'passed': True, 'tools': names, 'answer': result['output']}, ensure_ascii=False))
                    break
                time.sleep(1)
            else: raise TimeoutError('Follow-up did not finish')
        finally:
            Path('docs/workspace-web-followup.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
