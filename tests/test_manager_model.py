"""Manager selection is independent of the expert's fixed model."""
from test_collaboration import workspace
from test_information_requests import setup, output

from agent_core.app.research_execution import build_execution
from agent_core.contracts import RunResult


async def test_manager_and_information_consultation_share_model(workspace, monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL', 'expert-model')
    monkeypatch.setenv('OPENAI_MANAGER_MODEL', 'deepseek-v4-pro')
    db, scopes, research, tasks, _, collaboration = workspace
    node, _, _, intent = await setup(workspace)
    scope = scopes.current(node.scope_id, research.owner)
    _, catalog = build_execution(db, scopes, {node.id: scope}, research, research.engine)
    assert catalog.get(node.id).model == 'deepseek-v4-pro'
    assert tasks.spec(intent.task_id).model == 'expert-model'
    seen = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            seen.append((agent.id, agent.model))
            if agent.id == 'manager_information':
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'available': False, 'answer': 'No further information.', 'missing': ['year']})
            await invoke('request_manager_information', 'ask', {
                'question': 'Which year?', 'progress': 'Read the sample.'})
            return RunResult(run_id=request.run_id, status='succeeded', output=output())

    tasks.engine = Engine()
    result = await collaboration.execute(intent.command_id)
    assert result['receipts'][0].delivery == 'pending_review'
    assert ('manager_information', 'deepseek-v4-pro') in seen
    assert all(model == 'expert-model' for agent, model in seen if agent != 'manager_information')
