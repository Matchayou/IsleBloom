"""Real-model automatic web research through the live workspace HTTP API."""
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    base = 'http://127.0.0.1:8770'
    report = {'base_url': base, 'passed': False}
    with httpx.Client(base_url=base, timeout=200, trust_env=False, headers={'Origin': base}) as client:
        def get(action, **params):
            response = client.get('/api/' + action, params=params)
            response.raise_for_status()
            return response.json()

        def post(action, body):
            response = client.post('/api/' + action, json=body)
            response.raise_for_status()
            return response.json()

        try:
            research = post('researches', {'id': str(uuid4()), 'goal': '自主联网搜索与引用验收'})
            rid = research['id']
            chat = post('chat_start', {'research_id': rid, 'goal': research['goal']})
            node_id = chat['node_id']
            report.update(research_id=rid, node_id=node_id)
            prompt = '现在最新的 iPhone 型号是什么？请核实 Apple 官方信息，给出来源链接和支持答案的原文引用。简洁回答。'
            sent = post('chat_send', {'node_id': node_id, 'content': prompt, 'expected_revision': chat['revision'], 'message_id': str(uuid4())})
            command_id = str(uuid4())
            post('chat_reply', {'node_id': node_id, 'expected_revision': sent['revision'], 'command_id': command_id})
            deadline = time.monotonic() + 200
            while time.monotonic() < deadline:
                command = next(c for c in get('projection', research_id=rid)['commands'] if c['command']['command_id'] == command_id)
                if command['status'] != 'accepted':
                    run = get('run', id=command['run_id'])
                    report['run'] = run
                    assert run['run']['status'] == 'succeeded', run['run']['result']
                    completed = [e['event']['name'] for e in run['events'] if e['event']['phase'] == 'tool' and e['event']['status'] == 'completed']
                    assert {'search_web', 'fetch_web'} <= set(completed), completed
                    result = get('result', id='result:' + command['run_id'])
                    report['result'] = result
                    assert result['evidence']['evidence'], result
                    assert result['version']['resource_refs'], result
                    for evidence in result['evidence']['evidence']:
                        located = get('citation', result_id=result['version']['id'], evidence_id=evidence['id'])
                        assert located['source']['kind'] == 'web'
                    report.update(passed=True, completed_tools=completed,
                                  chat=get('chat_read', node_id=node_id), resources=get('resources', research_id=rid))
                    print(json.dumps({'passed': True, 'research_id': rid, 'node_id': node_id,
                                      'tools': completed, 'usage': run['run']['result']['usage'], 'answer': result['output']}, ensure_ascii=False), flush=True)
                    break
                time.sleep(1)
            else:
                raise TimeoutError('Live web chat did not finish')
        except Exception as exc:
            report['error'] = str(exc)
            raise
        finally:
            Path('docs/workspace-web-live-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
