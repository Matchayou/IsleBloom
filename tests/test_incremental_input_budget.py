"""Measured prefix budgeting; no live-model or tokenizer-ratio assumptions."""
from copy import deepcopy
import json
from uuid import uuid4
import pytest
from openai.types.responses import ResponseReasoningItem
from sqlalchemy import select
from agent_core.controlled.input_budget import estimate_input, byte_bound
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand
from test_context_projection import controlled
from test_controlled_collaboration import seed, call, answer


def fixture_request():
    return {'model':'fixture', 'instructions':'Fixed instructions', 'tools':[], 'output_schema':None,
        'settings':{'max_tokens':8192,'timeout':60},'input':[{'role':'user','content':'固定条件'*4000}]}


def test_unchanged_items_use_measurement_and_all_changes_are_charged():
    previous=fixture_request(); observation={'public_input':previous,'input_tokens':9000}
    current={**previous,'settings':{**previous['settings'],'timeout':40},
        'input':previous['input']+[{'type':'reasoning','content':[{'type':'reasoning_text','text':'新增推理'*3000}]}]}
    result=estimate_input(current,observation)
    assert result['method']=='measured_input_plus_utf8_delta'
    assert result['tokens'] >= 9000+byte_bound(current['input'][1:])
    assert result['tokens'] < byte_bound(current)
    for field,value in [('model','other'),
            ('input',[{'role':'user','content':'changed'}]+current['input'][1:])]:
        changed={**current,field:value}
        assert estimate_input(changed,observation)['method']=='utf8_upper_bound'
    for field,value in [('instructions','changed'),('tools',[{'name':'new'}]),
            ('output_schema',{'type':'object'}),('settings',{'max_tokens':4096})]:
        changed={**current,field:value}
        estimate=estimate_input(changed,observation)
        assert estimate['changed_header_charged'] is True
        assert estimate['delta_upper_bound'] > result['delta_upper_bound']
    for value in (None,0,-1,True):
        assert estimate_input(current,{**observation,'input_tokens':value})['method']=='utf8_upper_bound'
    assert estimate_input(current)['method']=='utf8_upper_bound'
    # A removed/reordered historical item cannot borrow the old token count.
    assert estimate_input({**current,'input':current['input'][1:]},observation)['method']=='utf8_upper_bound'


def test_exact_provider_output_replay_uses_confirmed_output_count_once():
    previous=fixture_request()
    generated={'type':'reasoning','content':[{'type':'reasoning_text','text':'确认过的输出'*3000}],
        'id':'fixed','encrypted_content':None,'summary':[]}
    observation={'public_input':previous,'input_tokens':9000,'output_tokens':5000,
        'response_input_items':[generated]}
    current={**previous,'input':previous['input']+[generated]}
    measured=estimate_input(current,observation)
    unmeasured=estimate_input(current,{**observation,'response_input_items':[]})
    assert 14000<=measured['tokens']<unmeasured['tokens']
    changed=deepcopy(current);changed['input'][-1]['content'][0]['text']+='changed'
    assert estimate_input(changed,observation)['tokens']>=unmeasured['tokens']
    duplicate={**current,'input':current['input']+[generated]}
    assert estimate_input(duplicate,observation)['tokens']>=measured['tokens']+byte_bound([generated])


async def test_sdk_long_reasoning_two_dispatches_finish_without_summary_or_lost_results(controlled):
    db,scopes,research,tasks,projects,c,provider=controlled
    node,ref,chat=await seed(controlled)
    chat=ChatService(research,tasks).send(node.id,'派发两位专家，保留以下约束：'+'仅分析指定材料。'*600,chat.revision)
    # The provider returns real SDK structures and measured-usage-shaped fixture
    # values. This is a deterministic regression, not a live semantic evaluation.
    original=provider.get_model
    def get_model(name):
        model=original(name); get=model.get_response
        async def measured(**kwargs):
            response=await get(**kwargs)
            response.usage.input_tokens=9485
            return response
        model.get_response=measured
        return model
    provider.get_model=get_model
    reasoning=ResponseReasoningItem(type='reasoning',id='long-reasoning',summary=[],status='completed',
        content=[{'type':'reasoning_text','text':'保留推理协议内容。'*1100}])
    async def dispatch(kwargs):
        return [reasoning,*call('delegate_task',expert_id='expert_search',
            task_instruction='核查原始资料。'*100,delivery_requirements='结论、引用与缺口',resource_refs=[ref.model_dump()]),
            *call('delegate_task',expert_id='expert_synthesize',task_instruction='独立整理证据。'*100,
            delivery_requirements='结论、引用与缺口',resource_refs=[ref.model_dump()])]
    async def finish(kwargs):
        assert kwargs['tools'], 'Unexpected summary request'
        returned=[i for i in kwargs['input'] if i.get('type')=='function_call_output']
        assert all(json.loads(i['output'])['value']['status']=='accepted' for i in returned[-2:])
        assert next(i for i in kwargs['input'] if i.get('type')=='reasoning')['content']==reasoning.model_dump()['content']
        return answer({'report':'两位专家已派发，正在处理。','claims':[]})
    provider.plans.append([call('select_task_tools',profile='experts'),call('find_experts',required_tools=['search_web']),dispatch,finish])
    command=await research.submit(NodeCommand(command_id=uuid4().hex,research_id=node.research_id,
        node_id=node.id,kind='chat_reply',chat_revision=chat.revision))
    record=research.ledger.get(command.run_id)
    assert record.status=='succeeded',record.result
    assert len(c.query('intents_for',node.research_id))==2 and len(provider.requests)==4
    with db.transaction():
        ledger=tasks.engine.ledger
        manifests=db.connection.execute(select(ledger.manifests.c.manifest).where(ledger.manifests.c.run_id==command.run_id)).scalars().all()
    assert len(manifests)==4 and all(m['purpose']=='task' for m in manifests)
    final=max(manifests,key=lambda m:len(m['public_input']['input']))
    assert final['input_estimate']['method']=='measured_input_plus_utf8_delta'
    assert final['input_estimate']['utf8_upper_bound']+8192+4096>65536
    assert final['estimated_input_tokens']+8192+4096<=65536
    # A newly constructed run-side reader recovers the measurement from durable
    # manifests; it doesn't rely on an in-memory last-response cache.
    observed=ledger.input_observation(command.run_id)
    assert observed['public_input']==final['public_input'] and observed['input_tokens']==9485


async def test_sdk_bad_source_json_is_correctable_and_charged(controlled):
    from openai.types.responses import ResponseFunctionToolCall
    db,scopes,research,tasks,projects,c,provider=controlled
    node,ref,chat=await seed(controlled)
    bad=ResponseFunctionToolCall(type='function_call',name='read_source',call_id='bad-json',arguments='{"ref":')
    async def correct(kwargs):
        feedback=json.loads(kwargs['input'][-1]['output'])['value']
        assert feedback['status']=='invalid_input' and feedback['executed'] is False
        return call('read_source',ref=ref.model_dump())
    async def finish(kwargs):
        assert 'confirmed count 7' in kwargs['input'][-1]['output']
        return answer({'report':'7; market size unknown.'})
    provider.plans.append([[bad],correct,finish])
    intent=c.delegate_task(command_id=uuid4().hex,node_id=node.id,expert_id='expert_synthesize',
        task_instruction='Read selected source',delivery_requirements='Facts and gaps',resource_refs=(ref,))
    result=await c.execute(intent.command_id)
    assert result['intent'].status=='completed',result
    run_id=result['receipts'][0].run_id
    assert tasks.engine.ledger.run_budget(run_id)['tools']==2
    assert len(provider.requests)==3


def test_changed_summary_is_fully_charged_without_discarding_original_measurement():
    previous=fixture_request()
    previous['input']=[{'role':'assistant','content':'Old memory'}]+previous['input']
    current={**previous,'input':[{'role':'assistant','content':'New summary'}]+previous['input'][1:]}
    result=estimate_input(current,{'public_input':previous,'input_tokens':9000})
    assert result['method']=='measured_input_plus_utf8_delta' and result['unchanged_items']==1
    assert result['tokens']>=9000+byte_bound([current['input'][0]])
