"""CE7 public SDK export/resume proof; simulated Model, real installed SDK."""
import json
import os
from importlib.metadata import version
import pytest
from agents import Agent,Runner,RunConfig,RunState,FunctionTool,ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall,ResponseOutputMessage,ResponseOutputText

@pytest.mark.parametrize('explicit_null', [False, True])
async def test_public_approval_boundary_serializes_and_restores_without_model_replay(tmp_path, explicit_null):
    from agents import Model
    counts={'model':0,'tool':0}
    class FixtureModel(Model):
        async def get_response(self,**kwargs):
            counts['model']+=1
            output=([ResponseFunctionToolCall(type='function_call',call_id='stable-call',name='record',arguments='{}',
                **({'async':None} if explicit_null else {}))]
                if counts['model']==1 else [ResponseOutputMessage(type='message',id='final',role='assistant',status='completed',
                    content=[ResponseOutputText(type='output_text',text='done',annotations=[])])])
            from agent_core.controlled.model import RESPONSE, response_json
            response = ModelResponse(output=output,usage=Usage(requests=1,input_tokens=3,output_tokens=4),response_id=None)
            return RESPONSE.validate_python(response_json(response))
        async def stream_response(self,*a,**k):raise NotImplementedError;yield
    async def tool(ctx,raw):counts['tool']+=1;return 'saved'
    model=FixtureModel()
    def agent():return Agent(name='fixed-agent',model=model,tools=[FunctionTool(name='record',description='Record fixed data',
        params_json_schema={'type':'object','properties':{},'required':[],'additionalProperties':False},
        on_invoke_tool=tool,needs_approval=True)])
    config=RunConfig(tracing_disabled=True)
    paused=await Runner.run(agent(),'Record it',max_turns=4,run_config=config)
    assert len(paused.interruptions)==1 and counts=={'model':1,'tool':0}
    payload=paused.to_state().to_json()
    path=tmp_path/'state.json';path.write_text(json.dumps(payload))
    fresh_agent=agent();restored=await RunState.from_json(fresh_agent,json.loads(path.read_text()))
    assert len(restored.get_interruptions())==1
    restored.approve(restored.get_interruptions()[0])
    completed=await Runner.run(fresh_agent,restored,run_config=config)
    assert completed.final_output=='done' and counts=={'model':2,'tool':1}
    assert completed.context_wrapper.usage.requests==2
    assert completed.to_state().to_json()
    completed_state = await RunState.from_json(agent(), completed.to_state().to_json())
    replayed = await Runner.run(agent(), completed_state, run_config=config)
    # A completed RunState is NOT a persisted result: the public runner calls the
    # model again. Production must store and return its validated RunResult.
    assert replayed.final_output == 'done' and counts == {'model': 3, 'tool': 1}
    evidence={'sdk':version('openai-agents'),'public_apis':['FunctionTool.needs_approval','RunResult.to_state',
        'RunState.to_json','RunState.from_json','RunState.get_interruptions','RunState.approve','Runner.run'],
        'checks':{'before_tool_boundary':True,'json_disk_round_trip':True,'fresh_agent_restore':True,
            'first_model_not_repeated':True,'tool_after_resume_once':True,
            'completed_state_repeats_model':True},'counts':counts,
        'explicit_nullable_tool_field':explicit_null,
        'scope':'Simulated model; SDK state proof, not process-crash or external-model acceptance.'}
    from pathlib import Path
    root = Path(os.environ.get('ACCEPTANCE_EVIDENCE_DIR', str(tmp_path)))
    root.mkdir(parents=True, exist_ok=True)
    (root/f'sdk-public-state-{explicit_null}.json').write_text(json.dumps(evidence,indent=2))
