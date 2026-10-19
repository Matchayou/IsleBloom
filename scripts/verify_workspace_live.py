"""Verify real model Q&A and source tools through the running workspace API.

Uses only a newly created synthetic research. The server owns provider credentials.
"""
import json
import time
from pathlib import Path
from uuid import uuid4
import httpx

BASE = 'http://127.0.0.1:8768'
REPORT = Path('docs/workspace-live-verification.json')

def main():
    report = {'base_url': BASE, 'configured_model': 'deepseek-flash', 'synthetic_only': True, 'passed': False, 'checks': []}
    client = httpx.Client(base_url=BASE, timeout=90, trust_env=False, headers={'Origin': BASE})
    def get(action, **params):
        response = client.get('/api/' + action, params=params)
        response.raise_for_status()
        return response.json()
    def post(action, body):
        response = client.post('/api/' + action, json=body)
        response.raise_for_status()
        return response.json()
    def await_command(rid, command_id):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            projection = get('projection', research_id=rid)
            record = next((c for c in projection['commands'] if c['command']['command_id'] == command_id), None)
            if record and record['status'] != 'accepted':
                if record['run_id']:
                    value = get('run', id=record['run_id'])
                    report['checks'].append(value)
                    assert value['run']['status'] == 'succeeded', value['run']['result']
                    return value
                assert record['status'] == 'completed', record
                return record
            time.sleep(.5)
        raise TimeoutError('Workspace command did not finish')
    def ask(rid, node_id, prompt):
        chat = get('chat_read', node_id=node_id)['chat']
        sent = post('chat_send', {'node_id': node_id, 'content': prompt, 'expected_revision': chat['revision'], 'message_id': str(uuid4())})
        command_id = str(uuid4())
        post('chat_reply', {'node_id': node_id, 'expected_revision': sent['revision'], 'command_id': command_id})
        return await_command(rid, command_id)
    try:
        research = post('researches', {'id': str(uuid4()), 'goal': '真实模型验收 · 问答与资料工具'})
        rid = research['id']
        chat = post('chat_start', {'research_id': rid, 'goal': research['goal']})
        node_id = chat['node_id']
        report.update(research_id=rid, node_id=node_id)
        qa = ask(rid, node_id, '这是一道自包含算术题，无需外部资料：146 乘以 7 等于多少？请用一句中文回答。')
        assert '1022' in qa['run']['result']['output']['report']
        assert qa['run']['result']['usage']['requests'] > 0
        print('Real Q&A passed.', flush=True)
        marker = 'VERIFIED-' + uuid4().hex[:12]
        source = f'Synthetic tool verification fixture. No private data.\nNEBULA_BATCH\nVerification marker: {marker}\nCrate A: 29 units.\nCrate B: 14 units.\n'
        response = client.post('/api/upload', params={'research_id': rid, 'name': '工具验收资料.txt'}, content=source.encode(), headers={'Content-Type': 'text/plain'})
        response.raise_for_status()
        ref = response.json()['ref']
        node = next(n['node'] for n in get('projection', research_id=rid)['nodes'] if n['node']['id'] == node_id)
        mount_id = str(uuid4())
        post('command', {'command_id': mount_id, 'research_id': rid, 'node_id': node_id, 'kind': 'mount', 'expected_revision': node['revision'], 'resource_refs': [ref]})
        await_command(rid, mount_id)
        tool_run = ask(rid, node_id, '请实际调用 search_source 查询已挂载资料里的 NEBULA_BATCH，再调用 read_source 读取命中片段。请回答 Verification marker 的完整值，以及 Crate A 与 Crate B 的合计数量；给出原文位置和逐字引用。不要猜测标记值。')
        completed = [row['event'] for row in tool_run['events'] if row['event']['phase'] == 'tool' and row['event']['status'] == 'completed']
        assert {'search_source', 'read_source'} <= {event['name'] for event in completed}, completed
        answer = tool_run['run']['result']['output']['report']
        assert marker in answer and '43' in answer, answer
        report.update(passed=True, tool_names=[event['name'] for event in completed], expected_marker=marker, expected_total=43,
                      chat=get('chat_read', node_id=node_id), resources=get('resources', research_id=rid))
        print(json.dumps({'passed': True, 'research_id': rid, 'node_id': node_id, 'tools': report['tool_names'],
                          'usage': [check['run']['result']['usage'] for check in report['checks']]}, ensure_ascii=False), flush=True)
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        client.close()

if __name__ == '__main__':
    main()
