import json
import os

from agents import Agent, Runner, RunConfig, RunState, ModelSettings, FunctionTool
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError, UserError
from agents.tool import set_function_tool_failure_error_function
from pydantic import BaseModel

from ..contracts import CoreFailure, OutputRepair, RunError, RunResult, Usage
from .output_repair import RepairableOutputSchema, html_documents, file_contents
from ..context.projection import ContextBudgetExceeded, ContextWindowOverflow
from ..controlled.progress import FinalizeAfterTimeout


class OpenAIEngine:
    def __init__(self, model=None, *, input_filter=None, checkpoints=None):
        # Optional SDK Model injection belongs exclusively to this adapter.
        self.model = model
        self.input_filter = input_filter
        self.checkpoints = checkpoints

    async def execute(self, request, spec, tools, invoke):
        from ..tool_routing import ToolRouter
        from dataclasses import replace
        router = ToolRouter(spec.tool_profiles, tools, request.input.tool_query)
        tool_failure = None
        def wrap(binding):
            async def call(ctx, raw):
                nonlocal tool_failure
                try:
                    result = await invoke(binding.name, ctx.tool_call_id, raw)
                except CoreFailure as exc:
                    tool_failure = exc
                    raise
                router.observe(binding.name, result)
                return result.model_dump_json()
            tool = FunctionTool(name=binding.name, description=binding.description,
                                params_json_schema=binding.input_type.model_json_schema(),
                                on_invoke_tool=call,
                                is_enabled=lambda ctx, agent: router.enabled(binding.name), needs_approval=self.checkpoints is not None)
            return set_function_tool_failure_error_function(tool, None)

        model = self.model or spec.model
        instructions = spec.instructions
        if spec.output_type:
            instructions += '\n按输出 schema 返回 JSON。'
        if isinstance(model, str):
            instructions += '\n当前调用模型：' + json.dumps(model, ensure_ascii=False) + '。'
        agent = Agent(name=spec.id, instructions=lambda ctx, agent: instructions + router.instructions(),
                      model=model, tools=[wrap(t) for t in tools],
                      output_type=RepairableOutputSchema(spec.output_type, strict_json_schema=spec.strict_output)
                      if spec.output_type else None,
                      model_settings=ModelSettings(parallel_tool_calls=False))
        usage = Usage()
        repairs = []
        remaining_turns = request.limits.max_turns
        history = [m.model_dump() for m in request.input.messages]
        def publish(kind, value):
            if self.checkpoints is not None:
                self.checkpoints.publish({'kind': kind, 'value': value,
                    'usage': usage.model_dump(mode='json'),
                    'repairs': [r.model_dump(mode='json') for r in repairs],
                    'remaining_turns': remaining_turns, 'tools_enabled': bool(agent.tools),
                    'tool_profile': router.profile, 'selected_tools': sorted(router.selected)})

        if self.checkpoints is not None:
            saved = self.checkpoints.restore()
            if saved is not None:
                if saved['kind'] == 'result':
                    return RunResult.model_validate(saved['value'])
                router.restore(saved['tool_profile'])
                router.selected = set(saved['selected_tools'])
                if not router.selected <= router.tools.keys(): raise ValueError('Saved tools exceed admitted catalog')
                usage = Usage.model_validate(saved['usage'])
                repairs = [OutputRepair.model_validate(r) for r in saved['repairs']]
                remaining_turns = saved['remaining_turns']
                if not saved['tools_enabled']:
                    agent = agent.clone(tools=[])
                history = (await RunState.from_json(agent, saved['value'])
                    if saved['kind'] == 'state' else saved['value'])
            else:
                publish('input', history)

        def failed(exc, error):
            known_usage = usage
            data = getattr(exc, 'run_data', None)
            if data is not None:
                consumed = data.context_wrapper.usage
                known_usage = known_usage.plus(Usage(requests=consumed.requests,
                    input_tokens=consumed.input_tokens, output_tokens=consumed.output_tokens))
            value = RunResult(run_id=request.run_id, status='failed', error=error,
                usage=known_usage.model_copy(update={'complete': False}), output_repairs=tuple(repairs))
            publish('result', value.model_dump(mode='json'))
            return value

        async def filter_input(data):
            # SDK enablement controls the provider surface; projection must budget
            # that same surface rather than every dormant workflow definition.
            visible = data.agent.clone(tools=[t for t in data.agent.tools if router.enabled(t.name)])
            selected = replace(data, agent=visible)
            if self.input_filter is None: return selected.model_data
            from inspect import isawaitable
            result = self.input_filter(selected)
            return await result if isawaitable(result) else result

        try:
            while True:
                try:
                    if isinstance(history, RunState):
                        for interruption in history.get_interruptions():
                            history.approve(interruption)
                    result = await Runner.run(agent, input=history, max_turns=remaining_turns,
                        run_config=RunConfig(tracing_disabled=True, call_model_input_filter=filter_input))
                except ContextWindowOverflow as exc:
                    remaining_turns=exc.remaining_turns
                    history=list(exc.input) if isinstance(exc.input,list) else [{'role':'user','content':exc.input}]
                    publish('input', history)
                    continue
                except FinalizeAfterTimeout as exc:
                    remaining_turns = exc.remaining_turns
                    history = list(exc.input) if isinstance(exc.input,list) else [{'role':'user','content':exc.input}]
                    history.append({'role':'user','content':'最后一次调查模型请求超时，结果未确认。使用已有证据收尾，明确未完成内容；不再调用工具。'})
                    agent = agent.clone(tools=[])
                    publish('input', history)
                    continue
                if result.interruptions:
                    history = result.to_state()
                    publish('state', history.to_json())
                    continue
                consumed = result.context_wrapper.usage
                usage = usage.plus(Usage(requests=consumed.requests,
                    input_tokens=consumed.input_tokens, output_tokens=consumed.output_tokens))
                if remaining_turns is not None:
                    remaining_turns -= len(result.raw_responses)
                output = result.final_output
                if isinstance(output, BaseModel) and not isinstance(output, OutputRepair):
                    protected_files = {content for repair in repairs for content in file_contents(repair.raw_output)}
                    if protected_files - set(file_contents(output.model_dump_json())):
                        output = OutputRepair(raw_output=output.model_dump_json(), issues=({
                            'type':'generated_file_missing','loc':['files'],
                            'msg':'Format correction removed or changed a generated file. Preserve every files.content verbatim; descriptions are not file deliveries.'},))
                if isinstance(output, BaseModel) and not isinstance(output, OutputRepair):
                    protected_html = {document for repair in repairs for document in html_documents(repair.raw_output)}
                    delivered_html = set(html_documents(output.model_dump_json()))
                    if protected_html - delivered_html:
                        output = OutputRepair(raw_output=output.model_dump_json(), issues=({
                            'type':'html_artifact_missing','loc':['report'],
                            'msg':'JSON format correction removed or changed a complete HTML deliverable from an earlier draft. Preserve the entire original HTML document verbatim in report or files.content; a description saying it was provided is not a deliverable.'},))
                if isinstance(output, BaseModel) and not isinstance(output, OutputRepair) and spec.validate_output:
                    issues = spec.validate_output(output.model_dump(mode='json'))
                    if issues:
                        output = OutputRepair(raw_output=output.model_dump_json(), issues=issues)
                if not isinstance(output, OutputRepair):
                    break
                repairs.append(output)
                evidence_repair = any(issue.get('type') == 'evidence_invalid' for issue in output.issues)
                protocol_repair = any(issue.get('type') == 'model_protocol' for issue in output.issues)
                # Schema/protocol corrections must not spend the evidence verifier's
                # correction attempts before it can use a returned exact-source hint.
                category_attempts = sum(
                    any(issue.get('type') == 'evidence_invalid' for issue in repair.issues) == evidence_repair
                    for repair in repairs)
                if category_attempts >= 3 or (remaining_turns is not None and remaining_turns <= 0):
                    value = RunResult(run_id=request.run_id, status='failed', usage=usage,
                        output_repairs=tuple(repairs), error=RunError(
                            code='evidence_invalid' if evidence_repair else 'model_protocol' if protocol_repair else 'output_contract',
                            origin='app' if evidence_repair else 'engine',
                            message='Final report is invalid after bounded output correction; no report was published.'))
                    publish('result', value.model_dump(mode='json'))
                    return value
                # Continue the SDK conversation, including the draft and all tool results.
                # Corrections share the original budget and cannot launch a new investigation.
                history = result.to_input_list()
                correction = (
                    '最终报告未通过资料引用校验，尚未交付。只依据已有资料工具原文修正引用：标识、片段位置、'
                    '逐字引文和可选元数据必须与原文匹配。元数据不得拼接、改写或添加解释；无法逐字填写的'
                    '可选元数据置为 null，将必要解释保留在正文。保留已有核心事实、数字和缺失说明，'
                    '工具失败、访问验证和资料不可用属于过程观察，不是正文引文；将此类说明保留在limitations，'
                    '相关声明的citations置空并在该声明missing中注明无正文证据，绝不伪造引文。'
                    '不得新增事实、删空声明或重新调查。仅输出符合原 schema 的完整 JSON，无代码围栏。'
                    if evidence_repair else
                    '最终报告未通过 JSON/schema 校验，尚未交付。请只修正格式和字段类型，保留已有证据、'
                    '数字、引用和缺失说明；不得新增事实或重新调查。仅输出符合原 schema 的 JSON，无代码围栏。')
                correction += ('格式修正必须完整保留原草稿中的交付正文与完整 HTML 文档，HTML 内部逐字不改，'
                    '保留在原 report 或 files.content 字段中并正确做 JSON 转义，所有交付文件内容逐字保留。不得把行程或代码改写成摘要，'
                    '不得用“HTML 已提供”“请保存代码”等说明替代实际 HTML。')
                history.append({'role': 'user', 'content':
                    correction + '校验问题：' + json.dumps(output.issues, ensure_ascii=False)})
                agent = agent.clone(tools=[])
                publish('input', history)
        except ContextBudgetExceeded as exc:
            return failed(exc, RunError(code='context_budget', origin='engine', message=str(exc)))
        except MaxTurnsExceeded as exc:
            return failed(exc, RunError(code='turn_limit', origin='engine', message='SDK turn limit exceeded'))
        except ModelBehaviorError as exc:
            detail = str(exc)
            key = os.environ.get('OPENAI_API_KEY')
            if key:
                detail = detail.replace(key, '[REDACTED]')
            return failed(exc, RunError(code='model_behavior', origin='engine',
                message=f'Model violated SDK protocol or output contract: {detail}'))
        except UserError as exc:
            if tool_failure is not None:
                return failed(exc, tool_failure.error)
            detail = str(exc)
            key = os.environ.get('OPENAI_API_KEY')
            if key:
                detail = detail.replace(key, '[REDACTED]')
            return failed(exc, RunError(code='sdk_configuration', origin='engine',
                message=f'SDK rejected execution configuration: {detail}'))
        except CoreFailure as exc:
            return failed(exc, exc.error)
        except Exception as exc:
            return failed(exc, RunError(code='engine_failed', origin='engine',
                message='Engine execution failed'))
        if isinstance(output, BaseModel):
            output = output.model_dump(mode="json")
        completed = RunResult(run_id=request.run_id, status="succeeded", output=output,
                              usage=usage, output_repairs=tuple(repairs))
        publish('result', completed.model_dump(mode='json'))
        return completed
