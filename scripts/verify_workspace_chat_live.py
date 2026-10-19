"""Exercise ordinary chat and capability boundaries on the fixed live preview."""
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    base = 'http://127.0.0.1:8769'
    report = {'base_url': base, 'passed': False, 'checks': []}
    with httpx.Client(base_url=base, timeout=90, trust_env=False, headers={'Origin': base}) as client:
        def get(action, **params):
            response = client.get('/api/' + action, params=params)
            response.raise_for_status()
            return response.json()

        def post(action, body):
            response = client.post('/api/' + action, json=body)
            response.raise_for_status()
            return response.json()

        try:
            research = post('researches', {'id': str(uuid4()), 'goal': '主助手普通问答回归验证'})
            rid = research['id']
            node_id = post('chat_start', {'research_id': rid, 'goal': research['goal']})['node_id']
            report.update(research_id=rid, node_id=node_id)
            for prompt, expected in [
                ('你现在调用的是什么模型', 'deepseek-flash'),
                ('法国的首都是哪里？用一句话回答。', '巴黎'),
                ('最新的iPhone型号是多少', None),
            ]:
                chat = get('chat_read', node_id=node_id)['chat']
                sent = post('chat_send', {'node_id': node_id, 'content': prompt, 'expected_revision': chat['revision'], 'message_id': str(uuid4())})
                command_id = str(uuid4())
                post('chat_reply', {'node_id': node_id, 'expected_revision': sent['revision'], 'command_id': command_id})
                deadline = time.monotonic() + 100
                while time.monotonic() < deadline:
                    command = next(c for c in get('projection', research_id=rid)['commands'] if c['command']['command_id'] == command_id)
                    if command['status'] != 'accepted':
                        run = get('run', id=command['run_id'])
                        report['checks'].append({'prompt': prompt, 'run': run})
                        result = run['run']['result']
                        assert run['run']['status'] == 'succeeded', result
                        assert result['usage']['requests'] > 0
                        answer = result['output']['report']
                        if expected:
                            assert expected in answer and '无法确认' not in answer, answer
                            assert result['output']['claims'] == [], result['output']
                        else:
                            assert any(term in answer for term in ('联网', '实时')), answer
                        print(json.dumps({'prompt': prompt, 'answer': answer, 'usage': result['usage']}, ensure_ascii=False), flush=True)
                        break
                    time.sleep(.5)
                else:
                    raise TimeoutError('Chat did not finish')
            report['passed'] = True
        finally:
            Path('docs/workspace-chat-live-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
