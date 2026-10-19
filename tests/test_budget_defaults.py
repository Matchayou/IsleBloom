"""Budget boundaries and idle compaction, without paid model requests."""
import json
from types import SimpleNamespace

import pytest
from agents.run import ModelInputData

from agent_core.contracts import RunLimits
from agent_core.context.artifacts import ContextArtifacts
from agent_core.context.projection import dumps
from agent_core.controlled.model_projection import (
    ModelProjection, RAW_WINDOW_TOKENS, WINDOW_TOKENS, OUTPUT_TOKENS, MARGIN_TOKENS,
    TOOL_TRIM_THRESHOLD, COMPACTION_THRESHOLD, RECENT_TOOL_RESULTS, IDLE_COMPACTION_SECONDS,
)
from agent_core.controlled.workspace import Workspace


def data(items):
    return SimpleNamespace(model_data=ModelInputData(input=items, instructions='fixed'),
        agent=SimpleNamespace(tools=[], output_type=None))


def tools(size, count=4):
    items = [{'role':'user', 'content':'Preserve the current constraint.'}]
    for i in range(count):
        items.extend([{'type':'function_call','call_id':str(i),'name':'read_source','arguments':'{}'},
            {'type':'function_call_output','call_id':str(i),'output':dumps({'text':'x'*size,'version':i})}])
    return items


def test_default_budget_values_and_explicit_turn_limits():
    assert RunLimits().max_turns is None
    assert RunLimits(max_turns=1).max_turns == 1
    assert (RAW_WINDOW_TOKENS, WINDOW_TOKENS) == (200000, 180000)
    assert (TOOL_TRIM_THRESHOLD, COMPACTION_THRESHOLD, RECENT_TOOL_RESULTS, IDLE_COMPACTION_SECONDS) == (.6,.7,3,300)
    with pytest.raises(ValueError):
        RunLimits(max_turns=0)


@pytest.mark.parametrize('fraction, trimmed', [(.599,False), (.6,True)])
def test_tool_trim_threshold_preserves_recent_three(tmp_path, fraction, trimmed):
    work = Workspace(tmp_path)
    try:
        artifacts = ContextArtifacts(work,lambda:None,lambda _:None)
        projection = ModelProjection(artifacts,'fixture')
        items = tools(35000)
        public = {'model':'fixture','settings':projection.settings.to_json_dict(),
            'instructions':'fixed','tools':[],'output_schema':None,'input':items}
        projection.input_observation = {'public_input':public,
            'input_tokens':round(WINDOW_TOKENS*fraction)-OUTPUT_TOKENS-MARGIN_TOKENS-(2+16*len(items)+64)}
        result = projection(data(items))
        assert (result.input[2] != items[2]) == trimmed
        assert result.input[3:] == items[3:]
        assert result.input[0] == items[0]
        assert projection.context_pressure is False
    finally:
        work.close()


@pytest.mark.parametrize('fraction, pressure', [(.699,False),(.7,True)])
def test_overall_compaction_threshold(tmp_path, fraction, pressure):
    work = Workspace(tmp_path)
    try:
        projection = ModelProjection(ContextArtifacts(work,lambda:None,lambda _:None),'fixture')
        items = tools(50000, count=3)
        projection.input_observation = {'public_input':{'model':'fixture','settings':projection.settings.to_json_dict(),
            'instructions':'fixed','tools':[],'output_schema':None,'input':items},
            'input_tokens':round(WINDOW_TOKENS*fraction)-OUTPUT_TOKENS-MARGIN_TOKENS-(2+16*len(items)+64)}
        assert projection(data(items)).input == items
        assert projection.context_pressure == pressure
    finally:
        work.close()


@pytest.mark.parametrize('idle_seconds, trimmed', [(299,False),(300,True)])
def test_idle_micro_compaction_is_persistent_and_keeps_recent_results(tmp_path, monkeypatch, idle_seconds, trimmed):
    monkeypatch.setattr('agent_core.controlled.model_projection.time', lambda:1000)
    work = Workspace(tmp_path)
    work.save('context_activity',json.dumps({'last_active_at':1000-idle_seconds}))
    work.close()
    work = Workspace(tmp_path)
    try:
        artifacts = ContextArtifacts(work,lambda:None,lambda _:None)
        projection = ModelProjection(artifacts)
        items = tools(2000)
        result = projection(data(items))
        assert (result.input[2] != items[2]) == trimmed
        assert result.input[3:] == items[3:]
        if trimmed:
            key = json.loads(result.input[2]['output'])['external_context']['artifact_id']
            assert json.loads(artifacts.load_text(key)) == items[2]
    finally:
        work.close()


async def test_unlimited_sdk_executes_beyond_old_default_eight_turns():
    from test_sdk_contract import setup, call, text
    runner, request, provider, _, _ = setup([[call(id=str(i))] for i in range(9)]+[[text()]])
    request = request.model_copy(update={'limits':RunLimits(max_calls=12)})
    result = await runner.execute(request)
    assert result.status == 'succeeded', result
    assert len(provider.inputs) == 10


def test_unlimited_progress_does_not_reserve_model_turns():
    from datetime import datetime,timedelta,timezone
    from agent_core.controlled.progress import ProgressPolicy
    state = {'phase':'researching'}
    ledger = SimpleNamespace(run_budget=lambda _:dict(models=10000,tools=0,
        deadline=datetime.now(timezone.utc)+timedelta(seconds=1000)),
        progress=lambda _:state,task_model_durations=lambda _:(),
        set_phase=lambda _,phase,reason:state.update(phase=phase,reason=reason))
    request = SimpleNamespace(run_id='unlimited',limits=RunLimits(timeout_seconds=1200))
    assert ProgressPolicy(ledger,request).prepare_model() == {'phase':'researching'}
