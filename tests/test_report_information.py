"""Same-session history and manager-authorized fixed report delivery."""
import json

import pytest

from test_collaboration import workspace, seeded, fixed_review_advice
from test_information_requests import output
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.contracts import RunResult
from agent_core.context.scopes import ScopeDenied
from agent_core.collaboration.models import InformationRequest
from agent_core.research.models import ChatMessage


async def reports(workspace, *, mount_own_source=True):
    _, scopes, research, tasks, _, c = workspace
    node, ref, _ = await seeded(workspace)
    root = scopes.current(research.research(node.research_id).scope_id, research.owner)
    _, extra = scopes.ingest(root, name='peer.txt', media_type='text/plain',
        content=b'Peer source, available only after manager authorization.')
    tasks.engine = ScriptedEngine(lambda request: ([], output('Historical draft; pending review.')))
    saved = []
    for expert, source in [('expert_search', ref), ('expert_synthesize', extra)]:
        intent = c.delegate_task(command_id='prior-' + expert, node_id=node.id,
            expert_id=expert, task_instruction='Initial analysis', delivery_requirements='State uncertainty',
            resource_refs=(source,))
        outcome = await c.execute(intent.command_id)
        saved.append((intent, outcome['receipts'][0]))
    with c.database.transaction() as tx:
        session = tx.collaboration.session(saved[0][0].session_id)
        note = ChatMessage(id='own-history-note', role='user', content='Own historical discussion note.',
            task_id=saved[0][0].task_id, spec_version=1, resource_refs=(ref,),
            result_ids=(saved[0][1].result_id,))
        tx.collaboration.save_session(session.model_copy(update={'revision': session.revision + 1,
            'messages': (*session.messages, note)}), session.revision)
    followup = c.delegate_task(command_id='history-followup', node_id=node.id,
        session_id=saved[0][0].session_id, task_instruction='Compare historical reports',
        delivery_requirements='Preserve old versions and review state',
        resource_refs=(ref,) if mount_own_source else ())
    return node, extra, saved, followup


@pytest.mark.parametrize('mode', ['grant', 'unread', 'revoke', 'none'])
async def test_report_discovery_read_grant_and_continue(workspace, mode):
    _, scopes, research, tasks, _, c = workspace
    node, extra, saved, followup = await reports(workspace)
    own, peer = saved[0][1], saved[1][1]
    original_records = [tasks.ledger.get(receipt.run_id) for _, receipt in saved]
    observed = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if agent.id == 'manager_information':
                payload = json.loads(request.input.messages[0].content)
                assert payload['requested_result_ids'] == [peer.result_id]
                directory = (await invoke('read_information', 'directory', {})).value
                assert {r['result_id'] for r in directory['results']} == {own.result_id, peer.result_id}
                if mode in ('grant', 'revoke'):
                    read = (await invoke('read_information', 'report', {'result_ids': [peer.result_id]})).value
                    report = read['results'][0]
                    assert report['output']['report'].startswith('Historical draft')
                    assert report['spec_version'] == 1 and report['delivery'] == 'pending_review'
                if mode == 'revoke':
                    scope = scopes.current(research.research(node.research_id).scope_id, research.owner)
                    scopes.revoke(scope, extra)
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'available': mode != 'none', 'answer': 'Read this fixed draft; not independently reviewed.',
                    'result_ids': [peer.result_id] if mode != 'none' else [],
                    'missing': ['Peer report not provided'] if mode == 'none' else []})
            if json.loads(request.input.messages[0].content).get('context_mode') == 'discussion':
                assert any(m['role'] == 'expert' and '索要信息' in m['content']
                    for m in json.loads(request.input.messages[0].content)['history'])
                return RunResult(run_id=request.run_id, status='succeeded', output='Historical report discussion continues.')
            assert 'read_context' in {tool.name for tool in tools}
            directory = (await invoke('read_context', 'own-directory', {})).value
            assert [r['result_id'] for r in directory['results']] == [own.result_id]
            assert directory['results'][0]['access'] == 'available'
            body = (await invoke('read_context', 'own-body', {'result_ids': [own.result_id]})).value
            assert body['results'][0]['run_id'] == own.run_id
            messages = (await invoke('read_context', 'own-discussion', {
                'message_ids': ['own-history-note', 'foreign-message-id']})).value
            assert messages['messages'][0]['content'] == 'Own historical discussion note.'
            assert messages['unavailable'] == [{'message_id': 'foreign-message-id', 'reason': 'not_found'}]
            denied = (await invoke('read_context', 'peer-before-grant', {'result_ids': [peer.result_id]})).value
            assert denied['results'] == []
            assert denied['unavailable'][0]['reason'] == 'authorization_required'
            reply = (await invoke('request_manager_information', 'ask', {
                'question': 'Please provide the peer report.', 'progress': 'Read my earlier report.',
                'requested_result_ids': [peer.result_id]})).value
            observed.append(reply)
            if mode == 'grant':
                assert reply['report_grants'] == [{'result_id': peer.result_id, 'task_id': peer.task_id,
                    'spec_version': 1, 'run_id': peer.run_id}]
                assert reply['context']['results'][0]['output']['report'].startswith('Historical draft')
                read = (await invoke('read_context', 'peer-after-grant', {'result_ids': [peer.result_id]})).value
                assert read['results'][0]['delivery'] == 'pending_review'
                source = (await invoke('read_source', 'authorized-source', {'ref': extra.model_dump()})).value
                assert 'Peer source' in json.dumps(source)
            else:
                assert not reply['available'] and not reply['context']['results']
            return RunResult(run_id=request.run_id, status='succeeded', output=output('Continued original task.'))

    tasks.engine = Engine()
    outcome = await c.execute(followup.command_id)
    run = outcome['runs'][0]
    record = tasks.ledger.get(run.id)
    assert record.status == ('failed' if mode in ('unread', 'revoke') else 'succeeded'), record.result
    if mode in ('unread', 'revoke'):
        assert not observed
        with pytest.raises(ScopeDenied): scopes.check(scopes.repository.scope(run.scope_id), extra)
    else:
        info = c.information.get(run.id)
        restored = InformationRequest.model_validate_json(info.model_dump_json())
        assert restored == info and info.requested_result_ids == (peer.result_id,)
        assert len(outcome['runs']) == 1 and tasks.spec(followup.task_id).version == 1
        if mode == 'grant':
            assert extra in outcome['receipts'][0].resource_refs
            assert extra not in tasks.spec(followup.task_id).required_refs
            token = scopes.running.set(run.id)
            try:
                bad_pin = restored.report_grants[0].model_copy(update={'spec_version': 999})
                with pytest.raises(ScopeDenied):
                    c.information._deliver(tasks.spec(followup.task_id), run,
                        restored.model_copy(update={'report_grants': (bad_pin,)}))
                with scopes.track(run.id, scopes.repository.scope(run.scope_id)):
                    repeated = c.information._deliver(tasks.spec(followup.task_id), run, restored)
                assert repeated.value['context']['results'][0]['run_id'] == peer.run_id
            finally:
                scopes.running.reset(token)
            thread = c.projects.expert_thread(followup.task_id)
            await c.projects.discuss(followup.task_id, 'Explain the saved report.', thread.revision, 'after-info')
        else:
            assert 'Peer report not provided' in record.result.output['missing']
    assert [tasks.ledger.get(receipt.run_id) for _, receipt in saved] == original_records
    assert all(tasks.receipt(receipt.result_id).adoption != 'adopted' for _, receipt in saved)


async def test_own_session_does_not_bypass_source_authorization(workspace):
    _, _, _, tasks, _, c = workspace
    _, _, saved, followup = await reports(workspace, mount_own_source=False)
    own_id = saved[0][1].result_id

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            directory = (await invoke('read_context', 'list', {})).value
            assert directory['results'][0]['access'] == 'authorization_required'
            read = (await invoke('read_context', 'read', {'result_ids': [own_id]})).value
            assert not read['results'] and read['unavailable'][0]['reason'] == 'authorization_required'
            return RunResult(run_id=request.run_id, status='succeeded', output=output())

    tasks.engine = Engine()
    result = await c.execute(followup.command_id)
    assert result['intent'].status == 'completed'


async def test_foreign_report_is_not_disclosed_and_missing_id_is_distinct(workspace):
    _, _, research, tasks, _, c = workspace
    node, extra, saved, followup = await reports(workspace)
    foreign_node, foreign_ref, _ = await seeded(workspace)
    tasks.engine = ScriptedEngine(lambda request: ([], output('FOREIGN REPORT SECRET')))
    foreign = c.delegate_task(command_id='foreign-report', node_id=foreign_node.id,
        expert_id='expert_search', task_instruction='Foreign analysis',
        delivery_requirements='Report', resource_refs=(foreign_ref,))
    foreign_id = (await c.execute(foreign.command_id))['receipts'][0].result_id

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if agent.id == 'manager_information':
                read = (await invoke('read_information', 'foreign', {'result_ids': [foreign_id, 'missing-id']})).value
                assert not read['results'] and 'FOREIGN REPORT SECRET' not in json.dumps(read)
                assert [r['reason'] for r in read['unavailable']] == ['access_denied', 'not_found']
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'available': False, 'answer': 'Foreign report access denied; other report not found.',
                    'missing': ['Authorized peer evidence']})
            read = (await invoke('read_context', 'foreign', {'result_ids': [foreign_id]})).value
            assert read['unavailable'][0]['reason'] == 'access_denied' and not read['results']
            reply = (await invoke('request_manager_information', 'ask', {
                'question': 'Check these reports', 'progress': 'Read own report',
                'requested_result_ids': [foreign_id, 'missing-id']})).value
            assert not reply['available'] and not reply['report_grants']
            return RunResult(run_id=request.run_id, status='succeeded', output=output())

    tasks.engine = Engine()
    result = await c.execute(followup.command_id)
    assert result['intent'].status == 'completed'
