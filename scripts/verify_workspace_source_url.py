"""Verify source URLs reach real-model replies independently of chat history."""
import json
import time
from pathlib import Path
from uuid import uuid4
import httpx


def main():
    base = 'http://127.0.0.1:8771'
    report = {'passed': False, 'base_url': base, 'checks': []}
    with httpx.Client(base_url=base, timeout=200, trust_env=False, headers={'Origin': base}) as client:
        def get(action, **params):
            r = client.get('/api/' + action, params=params); r.raise_for_status(); return r.json()
        def post(action, body):
            r = client.post('/api/' + action, json=body); r.raise_for_status(); return r.json()
        def wait(rid, cid):
            deadline = time.monotonic() + 200
            while time.monotonic() < deadline:
                command = next(c for c in get('projection', research_id=rid)['commands'] if c['command']['command_id'] == cid)
                if command['status'] != 'accepted':
                    assert command['status'] == 'completed', command
                    if not command['run_id']: return
                    run = get('run', id=command['run_id']); report['checks'].append(run)
                    assert run['run']['status'] == 'succeeded', run['run']['result']
                    return run
                time.sleep(.5)
            raise TimeoutError('Source URL verification timed out')
        def ask(rid, nid, text):
            chat = get('chat_read', node_id=nid)['chat']
            sent = post('chat_send', {'node_id': nid, 'content': text, 'expected_revision': chat['revision'], 'message_id': str(uuid4())})
            cid = str(uuid4())
            post('chat_reply', {'node_id': nid, 'expected_revision': sent['revision'], 'command_id': cid})
            return wait(rid, cid)
        try:
            research = post('researches', {'id': str(uuid4()), 'goal': '来源 URL 与说明规则验证'})
            rid = research['id']
            nid = post('chat_start', {'research_id': rid, 'goal': research['goal']})['node_id']
            report.update(research_id=rid, node_id=nid)
            results = post('search', {'research_id': rid, 'query': 'site:docs.python.org/3/library/json.html json.loads JSON decoder'})
            discovery = next(v for v in results if v['url'] == 'https://docs.python.org/3/library/json.html')
            source = post('fetch_discovery', {'research_id': rid, 'ref': discovery['ref']})
            report['source'] = source
            node = next(p['node'] for p in get('projection', research_id=rid)['nodes'] if p['node']['id'] == nid)
            cid = str(uuid4())
            post('command', {'research_id': rid, 'node_id': nid, 'command_id': cid, 'kind': 'mount',
                             'expected_revision': node['revision'], 'resource_refs': [source['ref']]})
            wait(rid, cid)
            first = ask(rid, nid, '根据已保存的资料，Python 的 json.loads 用于什么？请读取正文，用一句中文回答并附资料的来源链接。')
            payload = json.loads(first['run']['request']['input']['messages'][0]['content'])
            assert source['url'] not in payload['goal']
            assert payload['sources'][0]['actual_url'] == source['url']
            assert payload['sources'][0]['fetched_at']
            answer = first['run']['result']['output']['report']
            assert source['url'] in answer
            assert not any(v in answer for v in ('规范 URL', '未经核对', '未重新联网', '未逐字核对', '未重新搜索', '未重新抓取'))
            first_tools = [e['event']['name'] for e in first['events'] if e['event']['phase'] == 'tool' and e['event']['status'] == 'completed']
            assert set(first_tools) & {'search_source', 'read_source'}
            second = ask(rid, nid, '这次回答是否重新联网了？一句话回答。')
            second_answer = second['run']['result']['output']['report']
            assert '未重新联网' in second_answer
            assert not any(e['event']['name'] in ('search_web', 'fetch_web') for run in (first, second) for e in run['events'])
            report['passed'] = True
            print(json.dumps({'passed': True, 'answer': answer, 'when_asked': second_answer, 'tools': first_tools}, ensure_ascii=False))
        finally:
            Path('docs/workspace-source-url-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
