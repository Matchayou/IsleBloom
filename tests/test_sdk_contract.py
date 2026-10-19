import asyncio
import json

import pytest
from agents.models.interface import Model
from agents.items import ModelResponse
from agents.usage import Usage as SDKUsage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from agent_core.adapters.openai import OpenAIEngine
from agent_core.contracts import *
from agent_core.core import CoreRunner, AgentCatalog
from legacy_composition import MultiplyInput


class QueueModel(Model):
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.inputs = []
        self.settings = []
        self.tool_names = []
    async def get_response(self, **kwargs):
        self.inputs.append(kwargs["input"])
        self.settings.append(kwargs["model_settings"])
        self.tool_names.append([t.name for t in kwargs['tools']])
        output = self.outputs.pop(0)
        return ModelResponse(output=output, usage=SDKUsage(requests=1, input_tokens=3, output_tokens=2), response_id=None)
    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError
        yield


def call(name="mul", id="a", args=None):
    return ResponseFunctionToolCall(type="function_call", name=name, call_id=id,
                                    arguments=json.dumps(args or {"a": 12, "b": 8}))


def text(value="96"):
    return ResponseOutputMessage(id="m", type="message", role="assistant", status="completed",
                                 content=[ResponseOutputText(type="output_text", text=value, annotations=[])])


def setup(outputs, handler=None, output_type=None):
    model = QueueModel(outputs)
    seen = []
    async def multiply(args):
        seen.append(args.a)
        await asyncio.sleep(0)
        return ToolResult(value=args.a * args.b)
    spec = AgentSpec("a", "calculate", (ToolBinding("mul", "multiply", MultiplyInput, handler or multiply),), output_type=output_type)
    events = []
    r = CoreRunner(OpenAIEngine(model), AgentCatalog([spec]), events.append)
    req = RunRequest(agent_id="a", input=PreparedInput(messages=(Message(role="user", content="calculate"),)))
    return r, req, model, seen, events


async def test_sdk_two_tools_exactly_once_and_mapping():
    r, req, model, seen, events = setup([[call(id="A"), call(id="B", args={"a": 3, "b": 4})], [text()]])
    result = await r.execute(req)
    assert result.status == "succeeded", result
    assert seen == [12, 3]  # Never A/A/B.
    assert result.usage == Usage(requests=2, input_tokens=6, output_tokens=4)
    assert all(s.parallel_tool_calls is False for s in model.settings)
    assert model.inputs[0] == [{"role": "user", "content": "calculate"}]
    outputs = [x for x in model.inputs[1] if x.get("type") == "function_call_output"]
    assert [x["call_id"] for x in outputs] == ["A", "B"]
    assert [json.loads(x["output"])["value"] for x in outputs] == [96, 12]
    assert [(e.name, e.status) for e in events if e.phase == "tool"] == [("mul", "started"), ("mul", "completed")] * 2


async def test_format_repair_cannot_replace_html_handbook_with_a_delivery_claim():
    from agent_core.research.models import ResearchAnswer
    document = '<!DOCTYPE html>\n<html><head><title>Handbook</title></head><body>Seven-day itinerary</body></html>'
    raw = 'Full itinerary\n```html\n' + document + '\n```'
    missing = json.dumps({'report':'The complete HTML handbook was provided.','claims':[],
        'limitations':[],'blocking_missing':[]})
    full = json.dumps({'report':raw,'claims':[],'limitations':[],'blocking_missing':[]})
    runner, request, model, _, _ = setup([[text(raw)],[text(missing)],[text(full)]],output_type=ResearchAnswer)
    result = await runner.execute(request)
    assert result.status == 'succeeded'
    assert document in result.output['report']
    assert len(model.inputs) == 3
    assert result.output_repairs[-1].issues[0]['type'] == 'html_artifact_missing'


async def test_format_repair_fails_if_complete_html_is_still_missing():
    from agent_core.research.models import ResearchAnswer
    raw = '<!DOCTYPE html><html><body>Complete deliverable</body></html>'
    missing = json.dumps({'report':'HTML was provided.','claims':[], 'limitations':[],'blocking_missing':[]})
    runner, request, _, _, _ = setup([[text(raw)],[text(missing)],[text(missing)]],output_type=ResearchAnswer)
    result = await runner.execute(request)
    assert result.status == 'failed' and result.error.code == 'output_contract'
    assert result.output is None


async def test_fenced_json_correction_preserves_decoded_file_bytes():
    from agent_core.research.models import ResearchAnswer
    document = '<!DOCTYPE html>\n<html><body>旅行验证</body></html>\n'
    value = {'report':'File delivered', 'files':[{'name':'guide.html','content':document}],
             'claims':[], 'limitations':[], 'blocking_missing':[]}
    full = json.dumps(value, ensure_ascii=False)
    runner, request, model, _, _ = setup([[text('```json\n'+full+'\n```')], [text(full)]],
                                        output_type=ResearchAnswer)
    result = await runner.execute(request)
    assert result.status == 'succeeded'
    assert result.output['files'][0]['content'] == document
    assert len(model.inputs) == 2


async def test_format_correction_cannot_drop_csv_delivery():
    from agent_core.research.models import ResearchAnswer
    value = {'report':'Budget delivered', 'files':[{'name':'budget.csv','content':'day,cost\n1,100\n'}],
             'claims':[], 'limitations':[], 'blocking_missing':[]}
    full = json.dumps(value)
    missing = json.dumps({**value, 'files':[]})
    runner, request, model, _, _ = setup([[text('```json\n'+full+'\n```')],
                                        [text(missing)], [text(full)]], output_type=ResearchAnswer)
    result = await runner.execute(request)
    assert result.status == 'succeeded'
    assert result.output['files'][0]['content'] == 'day,cost\n1,100\n'
    assert result.output_repairs[-1].issues[0]['type'] == 'generated_file_missing'


async def test_format_error_does_not_spend_evidence_correction_attempts():
    from dataclasses import replace
    from agent_core.research.models import ResearchAnswer
    candidates=[text('not JSON')]+[text(json.dumps({'report':report,'claims':[],
        'limitations':[],'blocking_missing':[]})) for report in ('wrong ref','wrong punctuation','verified')]
    runner, request, model, _, _ = setup([[candidate] for candidate in candidates],output_type=ResearchAnswer)
    def validate(output):
        return () if output['report']=='verified' else ({'type':'evidence_invalid','loc':['claims'],
            'msg':'Copy the returned source excerpt exactly.'},)
    spec=runner.catalog.get('a')
    runner.catalog=AgentCatalog([replace(spec,validate_output=validate)])
    result=await runner.execute(request)
    assert result.status=='succeeded',result
    assert result.output['report']=='verified' and len(model.inputs)==4


async def test_sdk_tool_failure_stops_siblings():
    async def broken(_):
        raise ValueError("secret")
    r, req, model, seen, events = setup([[call(id="A"), call(id="B")], [text()]], broken)
    result = await r.execute(req)
    assert result.error.code == "tool_failed", result
    assert len(model.inputs) == 1
    assert len([e for e in events if e.phase == "tool" and e.status == "started"]) == 1


@pytest.mark.parametrize("outputs,code", [([[call(name="unknown")]], "model_behavior")])
async def test_sdk_errors(outputs, code):
    r, req, *_ = setup(outputs)
    assert (await r.execute(req)).error.code == code


@pytest.mark.parametrize('raw', ['{"a":12,', '{"a":"bad","b":8}', '{"a":12}',
    '[]', 'null', '"not an object"', '{"a":12,"b":8,"unexpected":1}'])
async def test_sdk_model_can_correct_invalid_tool_arguments(raw):
    bad = ResponseFunctionToolCall(type='function_call', name='mul', call_id='bad', arguments=raw)
    r, req, model, seen, events = setup([[bad], [call(id='corrected')], [text()]])
    result = await r.execute(req)
    assert result.status == 'succeeded', result
    assert seen == [12] and result.output == '96'
    feedback = next(item for item in model.inputs[1] if item.get('type') == 'function_call_output')
    value = json.loads(feedback['output'])['value']
    assert value['status'] == 'invalid_input' and value['executed'] is False
    assert value['issues'] and value['remaining_tool_calls'] == req.limits.max_calls - 1
    assert result.usage.requests == 3
    assert [e.status for e in events if e.phase == 'tool'] == ['started', 'failed', 'started', 'completed']


async def test_sdk_invalid_json_cannot_bypass_call_budget():
    bad = lambda key: ResponseFunctionToolCall(type='function_call', name='mul', call_id=key, arguments='{')
    r, req, model, seen, events = setup([[bad('1')], [bad('2')], [bad('3')], [text()]])
    result = await r.execute(req.model_copy(update={'limits': RunLimits(max_calls=2)}))
    assert result.error.code == 'call_limit' and not seen
    assert len(model.inputs) == 3


async def test_sdk_permission_failure_remains_terminal():
    async def denied(_):
        fail('scope_denied', 'tool', 'Source unavailable')
    r, req, model, seen, events = setup([[call()], [call(id='retry')], [text()]], denied)
    result = await r.execute(req)
    assert result.error.code == 'scope_denied'
    assert len(model.inputs) == 1


async def test_sdk_turn_limit_and_output_schema():
    r, req, *_ = setup([[call()]])
    assert (await r.execute(req.model_copy(update={"limits": RunLimits(max_turns=1)}))).error.code == "turn_limit"
    r, req, *_ = setup([[text('{"a":12,"b":8}')]], output_type=MultiplyInput)
    assert (await r.execute(req)).output == {"a": 12, "b": 8}
    r, req, *_ = setup([[text('invalid')]] * 3, output_type=MultiplyInput)
    assert (await r.execute(req)).error.code == "output_contract"


async def test_sdk_c0_delegation_and_followup():
    from legacy_composition import build
    model = QueueModel([
        [call()],
        [call(name="delegate", id="d", args={"agent_id": "expression", "input": "结果 96"})],
        [text("计算结果是 96。")], [text("计算结果是 96。")], [text("刚才是 96。")],
    ])
    # Composition names its normal tool multiply.
    model.outputs[0] = [call(name="multiply")]
    service = build(OpenAIEngine(model))
    first = await service.send("c", "assistant", "private marker: calculate")
    assert first.status == "succeeded", first
    second = await service.send("c", "assistant", "previous result?")
    assert second.status == "succeeded"
    assert model.inputs[2] == [{"role": "user", "content": "结果 96"}]
    assert len(model.inputs[4]) == 3
    assert model.inputs[4][1]["content"] == first.output
    assert first.usage.requests == 4  # Includes child once.


async def test_sdk_serial_handlers_and_cancel_cleanup():
    entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    seen = []
    async def block(args):
        seen.append(args.a)
        entered.set()
        try:
            await release.wait()
        finally:
            cleaned.set()
        return ToolResult(value=args.a)
    r, req, _, _, _ = setup([[call(id="A"), call(id="B", args={"a": 3, "b": 4})], [text()]], block)
    task = asyncio.create_task(r.execute(req))
    await entered.wait()
    assert seen == [12]
    task.cancel()
    result = await task
    assert result.status == "cancelled", result
    assert cleaned.is_set() and seen == [12]


async def test_sdk_duplicate_call_id_does_not_repeat():
    r, req, _, seen, _ = setup([[call(id="A")], [call(id="A")], [text()]])
    result = await r.execute(req)
    assert result.status == "succeeded", result
    assert seen == [12]


@pytest.mark.parametrize('draft', ['{"a":12,', '{"a":12}', '{"a":12,"b":"bad"}',
    '```json\n{"a":12,"b":8}\n```'])
async def test_final_output_repair_keeps_history_and_disables_tools(draft):
    r, req, model, seen, _ = setup([[call()], [text(draft)], [text('{"a":12,"b":8}')]],
        output_type=MultiplyInput)
    result = await r.execute(req)
    assert result.status == 'succeeded' and result.output == {'a': 12, 'b': 8}
    assert seen == [12] and model.tool_names == [['mul'], ['mul'], []]
    assert any(item.get('type') == 'function_call_output' for item in model.inputs[-1])
    assert draft in json.dumps(model.inputs[-1], ensure_ascii=False).replace('\\"', '"').replace('\\n', '\n')
    assert '校验问题' in model.inputs[-1][-1]['content']
    assert result.output_repairs[0].raw_output == draft and result.output_repairs[0].issues
    assert result.usage == Usage(requests=3, input_tokens=9, output_tokens=6)


@pytest.mark.parametrize('max_turns,requests', [(1, 1), (2, 2), (8, 3)])
async def test_final_output_repair_is_bounded_and_retains_invalid_drafts(max_turns, requests):
    r, req, model, _, _ = setup([[text('{')]] * 3, output_type=MultiplyInput)
    result = await r.execute(req.model_copy(update={'limits': RunLimits(max_turns=max_turns)}))
    assert result.error.code == 'output_contract' and result.output is None
    assert len(model.inputs) == len(result.output_repairs) == result.usage.requests == requests


async def test_final_output_repair_cannot_reexecute_research_tools():
    r, req, model, seen, _ = setup([[call()], [text('{')], [call(id='again')]], output_type=MultiplyInput)
    result = await r.execute(req)
    assert result.error.code == 'model_behavior' and seen == [12]
    assert len(result.output_repairs) == 1
