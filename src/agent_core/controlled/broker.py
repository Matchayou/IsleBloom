"""Trusted, narrow forwarding surface. Identity/model/credentials are never client fields."""
import asyncio
from agents import FunctionTool, ModelSettings
from .progress import ProgressPolicy, FinalizeAfterTimeout
from agents.models.interface import ModelTracing
from pydantic import JsonValue, ValidationError
from typing import Literal
from ..adapters.output_repair import RepairableOutputSchema
from ..contracts import CoreFailure, RunError, RunResult, Value
from .model import response_json
from .workspace import WriteNote, ReadNote


class ModelInput(Value):
    purpose: Literal['task', 'summary', 'memory', 'memory_write_review'] = 'task'
    input: list[dict[str, JsonValue]] | str
    tools: list[str]
    instructions: str | None


class ToolInput(Value):
    name: str
    call_id: str
    arguments: str | dict[str, JsonValue]


class Broker:
    def __init__(self, *, request, spec, tools, invoke, model, ledger, workspace,
                 token, epoch, validate, private_tools=(), artifacts=None, plan_budget=None,
                 memory_model=None, memory_model_name=None, memory_context=None):
        self.request, self.spec, self.tools, self.invoke = request, spec, tools, invoke
        self.model, self.ledger, self.workspace = model, ledger, workspace
        self.memory_model, self.memory_model_name, self.memory_context = memory_model, memory_model_name, memory_context
        self.token, self.epoch, self.validate = token, epoch, validate
        self.private_tools = {t.name: t for t in private_tools}
        # Runtime discovery includes admitted private primitives as well as
        # business tools. Execute it in the broker so it sees the actual catalog.
        if any(t.name == 'search_tools' for t in tools):
            from ..tool_routing import ToolRouter
            catalog = tools + tuple(private_tools)
            self.private_tools['search_tools'] = ToolRouter(spec.tool_profiles, catalog).search_binding()
            self.tools = tuple(t for t in tools if t.name != 'search_tools')
        self.sequence = 0
        self.result = None
        self.artifacts, self.projection = artifacts, None
        self.policy = ProgressPolicy(ledger, request,plan_budget=plan_budget)
        self.settings = None
        self.overflow_reprojected = False
        self.overflow_input_bytes = None
        self.checkpoints = None
        self.plan_budget = plan_budget

    def memory_payload(self, value):
        from ..context.memory_retrieval import MEMORY_INSTRUCTION
        from ..context.projection import dumps
        if self.memory_model is None: raise ValueError('Memory side-query model unavailable')
        return {'purpose': 'memory', 'input': [{'role': 'user', 'content': dumps(value)}],
            'instructions': MEMORY_INSTRUCTION, 'tools': []}

    def write_review_payload(self, value):
        from ..memories.write_models import WRITE_REVIEW_INSTRUCTION
        from ..context.projection import dumps, ContextBudgetExceeded
        from ..context.memory_retrieval import MAX_INPUT_BYTES
        if len(dumps(value).encode()) > MAX_INPUT_BYTES: raise ContextBudgetExceeded('Memory write review input exceeds budget')
        return {'purpose': 'memory_write_review', 'input': [{'role': 'user', 'content': dumps(value)}],
            'instructions': WRITE_REVIEW_INSTRUCTION, 'tools': []}

    def summary_payload(self, value):
        from ..context.compaction import MemorySummary, SUMMARY_INSTRUCTION
        from ..controlled.model_projection import estimated_tokens, WINDOW_TOKENS, OUTPUT_TOKENS, MARGIN_TOKENS, model_settings
        from ..context.projection import dumps, ContextBudgetExceeded
        payload={'purpose':'summary','input':[{'role':'user','content':dumps(value)}],
                 'instructions':SUMMARY_INSTRUCTION,'tools':[]}
        public={'model':self.spec.model,'instructions':payload['instructions'],'input':payload['input'],
            'tools':[],'output_schema':RepairableOutputSchema(MemorySummary,strict_json_schema=True).json_schema(),
            'settings':(self.settings or model_settings()).to_json_dict()}
        if estimated_tokens(public)+OUTPUT_TOKENS+MARGIN_TOKENS>WINDOW_TOKENS:
            raise ContextBudgetExceeded('Summary input and output reserve exceed model window; request not sent')
        return payload

    async def handle(self, frame):
        self.validate()
        if self.result is not None or frame.sequence != self.sequence + 1:
            raise ValueError('Controlled sequence mismatch or operation after completion')
        self.sequence = frame.sequence
        # Validate the narrow operation before reserving budget. Identity is bound to this object.
        if frame.operation == 'model':
            payload = ModelInput.model_validate(frame.payload)
            from ..context.compaction import SUMMARY_INSTRUCTION
            from ..context.memory_retrieval import MEMORY_INSTRUCTION
            from ..memories.write_models import WRITE_REVIEW_INSTRUCTION
            valid_instructions=(payload.instructions==WRITE_REVIEW_INSTRUCTION if payload.purpose=='memory_write_review' else
                payload.instructions==MEMORY_INSTRUCTION if payload.purpose=='memory' else
                payload.instructions==SUMMARY_INSTRUCTION if payload.purpose=='summary' else
                isinstance(payload.instructions,str) and payload.instructions.startswith(self.spec.instructions))
            if not valid_instructions:
                raise ValueError('Model instructions differ from frozen admission')
            if payload.purpose != 'task' and payload.tools:
                raise ValueError('Auxiliary model calls cannot invoke tools')
            if payload.purpose in ('memory', 'memory_write_review') and self.memory_model is None:
                raise ValueError('Memory side-query model unavailable')
            names = {t.name for t in self.tools} | self.private_tools.keys()
            if len(set(payload.tools)) != len(payload.tools) or not set(payload.tools) <= names:
                raise ValueError('Model requested unauthorized tools')
            if isinstance(payload.input, list):
                for item in payload.input:
                    if item.get('type') not in (None, 'message', 'function_call', 'function_call_output', 'reasoning'):
                        raise ValueError('CE1 supports text and local function items only')
                    if 'content' in item and not isinstance(item['content'], str):
                        content = item['content']
                        text_types = ('reasoning_text',) if item.get('type') == 'reasoning' else ('input_text', 'output_text')
                        if not isinstance(content, list) or any(not isinstance(x, dict) or x.get('type') not in text_types for x in content):
                            raise ValueError('Remote media and hosted operations are unavailable')
        elif frame.operation in ('tool', 'private'):
            payload = ToolInput.model_validate(frame.payload)
            if frame.operation_id != 'tool:' + payload.call_id: raise ValueError('Tool operation identity mismatch')
            if frame.operation == 'private':
                if payload.name not in self.private_tools:
                    raise ValueError('Unknown private operation')
            elif payload.name not in {t.name for t in self.tools}:
                raise ValueError('Tool is not in frozen capability intersection')
        else:
            payload = RunResult.model_validate(frame.payload)
            if payload.run_id != self.request.run_id or frame.operation_id != 'result:' + self.request.run_id:
                raise ValueError('Result identity mismatch')
            if payload.status == 'succeeded':
                if self.spec.output_type:
                    self.spec.output_type.model_validate(payload.output)
                elif not isinstance(payload.output, str):
                    raise ValueError('Discussion requires a text result')
        if frame.operation in ('tool','private'):
            state = self.ledger.progress(self.request.run_id)
            budget = self.ledger.run_budget(self.request.run_id)
            if (state['phase'] == 'finalizing' or budget['tools'] >= self.request.limits.max_calls or
                    self.policy.remaining_seconds() <= self.policy.reserve_seconds):
                if state['phase'] == 'researching':
                    self.ledger.set_phase(self.request.run_id, 'finalizing', 'investigation_budget')
                value = {'value':{'status':'investigation_closed','executed':False,
                    'instruction':'Investigation is closed. Deliver the supported result and explicit gaps using the reserved finalization budget.'}}
                if self.checkpoints is None:
                    self.ledger.reject_tool(self.workspace,self.token,self.epoch,self.request.run_id,frame)
                else:
                    self.ledger.reject_tool(self.workspace,self.token,self.epoch,self.request.run_id,frame,
                        artifact=self.checkpoints.artifact({'response':value}))
                return value
        if frame.operation=='model' and payload.purpose=='task' and self.overflow_input_bytes is not None:
            from ..context.projection import dumps
            if len(dumps(payload.input).encode())>=self.overflow_input_bytes:
                raise CoreFailure(RunError(code='context_window',origin='engine',
                    message='Context reprojection did not reduce input; no repeat provider request'))
            self.overflow_input_bytes=None
        if frame.operation == 'model' and payload.purpose in ('memory', 'memory_write_review'):
            row = self.ledger.run_budget(self.request.run_id)
            if (self.ledger.progress(self.request.run_id)['phase'] != 'researching' or
                    (self.request.limits.max_turns is not None and
                     self.request.limits.max_turns - row['models'] <= self.policy.reserve_turns) or
                    self.policy.remaining_seconds() <= self.policy.reserve_seconds):
                raise CoreFailure(RunError(code='memory_query_budget', origin='engine',
                    message='Memory selection exceeds the admitted investigation budget; no side query sent'))
        self.ledger.reserve(self.workspace, self.token, self.epoch,
                            self.request.run_id, frame, self.request.limits)
        usage = None
        confirmed = True
        if frame.operation == 'model':
            async def forbidden(*_): raise RuntimeError('Tools only execute through broker operations')
            wire_tools = [FunctionTool(name=t.name, description=t.description,
                params_json_schema=t.input_type.model_json_schema(), on_invoke_tool=forbidden)
                for t in self.tools if t.name in payload.tools]
            for tool in self.private_tools.values():
                if tool.name in payload.tools:
                    wire_tools.append(FunctionTool(name=tool.name, description=tool.description,
                        params_json_schema=tool.input_type.model_json_schema(), on_invoke_tool=forbidden))
            if self.ledger.progress(self.request.run_id)['phase'] == 'finalizing': wire_tools = []
            from ..context.audit import before_send, after_receive
            from ..controlled.model_projection import estimated_tokens, WINDOW_TOKENS, OUTPUT_TOKENS, MARGIN_TOKENS, model_settings
            from ..context.projection import dumps, sha, ContextBudgetExceeded
            from ..context.compaction import MemorySummary
            from ..context.memory_retrieval import MemorySelection
            from ..memories.write_models import WriteReview
            side_query = payload.purpose in ('memory', 'memory_write_review')
            selected_model = self.memory_model if side_query else self.model
            selected_name = self.memory_model_name if side_query else self.spec.model
            output_schema = (RepairableOutputSchema(WriteReview,strict_json_schema=True) if payload.purpose=='memory_write_review' else
                RepairableOutputSchema(MemorySelection,strict_json_schema=True) if payload.purpose=='memory' else
                RepairableOutputSchema(MemorySummary,strict_json_schema=True) if payload.purpose=='summary' else
                RepairableOutputSchema(self.spec.output_type,strict_json_schema=self.spec.strict_output) if self.spec.output_type else None)
            settings = (model_settings(model=selected_name, purpose='memory', finalizing=True)
                if side_query else self.settings or model_settings())
            from dataclasses import replace
            settings = replace(settings, timeout=min(settings.timeout or self.policy.remaining_seconds(),
                self.policy.remaining_seconds()))
            if side_query:
                settings = replace(settings, timeout=min(30, self.policy.remaining_seconds()-self.policy.reserve_seconds))
            if not wire_tools:
                delivery=model_settings(model=selected_name,purpose=payload.purpose,finalizing=True)
                settings=replace(settings,tool_choice='none',reasoning=delivery.reasoning)
            public_input = {'model':selected_name, 'instructions':payload.instructions, 'input':payload.input,
                'tools':[{'name':t.name, 'description':t.description, 'parameters':t.params_json_schema} for t in wire_tools],
                'output_schema':output_schema.json_schema() if output_schema else None,
                'settings':settings.to_json_dict()}
            from .input_budget import estimate_input
            observation = self.ledger.input_observation(self.request.run_id) if payload.purpose == 'task' else None
            input_estimate = estimate_input(public_input, observation)
            estimate = input_estimate['tokens']
            if self.plan_budget:
                used = self.ledger.plan_usage(self.plan_budget['node_ids'],
                    excluding=(self.request.run_id,frame.operation_id))
                remaining = self.plan_budget['max_tokens'] - used.input_tokens - used.output_tokens
                # Reuse the conservative provider-measured baseline plus byte
                # bounds for changed input, rather than repeatedly recharging
                # unchanged items at their much larger UTF-8 byte count.
                required = estimate + (settings.max_tokens or OUTPUT_TOKENS) + MARGIN_TOKENS
                if not used.complete or required > remaining:
                    self.ledger.settle(self.workspace,self.token,self.epoch,self.request.run_id,frame.operation_id,
                        {'requests':0,'input_tokens':0,'output_tokens':0,'complete':True})
                    raise CoreFailure(RunError(code='plan_token_budget',origin='engine',
                        message='Remaining approved plan tokens cannot cover this model input and output reserve; request not sent'))
            if estimate + OUTPUT_TOKENS + MARGIN_TOKENS > WINDOW_TOKENS:
                raise ContextBudgetExceeded('Final model input exceeds reserved token budget')
            manifest = {'purpose':payload.purpose,'public_input':public_input, 'public_input_sha256':sha(dumps(public_input)),
                'characters':len(dumps(public_input)), 'estimated_input_tokens':estimate,
                'input_estimate': input_estimate,
                'output_reserve_tokens':OUTPUT_TOKENS, 'margin_tokens':MARGIN_TOKENS,
                'excluded':self.projection.exclusions if self.projection else [],
                'business_excluded':list(self.request.input.projection_exclusions), 'wire_body':None,
                'usage':None}
            from .model_projection import RAW_WINDOW_TOKENS
            manifest['context_budget'] = {'raw_window_tokens':RAW_WINDOW_TOKENS,
                'effective_window_tokens':self.projection.window_tokens if self.projection else WINDOW_TOKENS}
            self.validate()
            self.ledger.save_manifest(self.request.run_id, frame.operation_id, 'assembled', manifest)
            def sent(body, raw):
                self.validate()
                prepared_manifest = {**manifest, 'wire_body':body, 'wire_text':raw, 'wire_sha256':sha(raw)}
                self.ledger.save_manifest(self.request.run_id, frame.operation_id, 'sent', prepared_manifest)
                manifest.update(prepared_manifest)
            audit_token = before_send.set(sent)
            response_metadata = {}
            response_token = after_receive.set(lambda metadata: response_metadata.update(metadata))
            from time import monotonic
            started = monotonic()
            try:
                async with asyncio.timeout(settings.timeout):
                    kwargs = dict(system_instructions=payload.instructions,
                        input=payload.input, model_settings=settings,
                        tools=wire_tools, output_schema=output_schema,
                        handoffs=[], tracing=ModelTracing.DISABLED,
                        previous_response_id=None, conversation_id=None, prompt=None)
                    from .streaming import preview_for, stream_model
                    emit = preview_for(self.request) if payload.purpose == 'task' else None
                    if emit is not None:
                        response = await stream_model(selected_model, kwargs, emit, self.validate, response_metadata)
                    else:
                        response = await selected_model.get_response(**kwargs)
            except BaseException as exc:
                manifest['duration_seconds']=monotonic()-started
                manifest['response_metadata']=response_metadata
                manifest['failure']={'type':type(exc).__name__,'http_status':getattr(exc,'status_code',None),'code':getattr(exc,'code',None),'param':getattr(exc,'param',None)}
                from .streaming import IncompleteResponse
                if isinstance(exc, IncompleteResponse):
                    terminal = exc.response
                    reason = terminal.incomplete_details.reason if terminal.incomplete_details else None
                    measured = terminal.usage
                    self.validate()
                    if measured is not None:
                        usage = {'requests':1, 'input_tokens':measured.input_tokens,
                            'output_tokens':measured.output_tokens, 'complete':True}
                        manifest['usage'] = {**usage,
                            'cached_input_tokens':measured.input_tokens_details.cached_tokens,
                            'cache_tokens_included_in_input':True}
                        self.ledger.settle(self.workspace,self.token,self.epoch,self.request.run_id,
                            frame.operation_id,usage)
                    self.ledger.save_manifest(self.request.run_id,frame.operation_id,
                        'incomplete' if measured is not None else 'unknown',manifest)
                    limited = reason == 'max_output_tokens'
                    raise CoreFailure(RunError(code='output_token_limit' if limited else 'model_response_incomplete',
                        origin='engine', message=(f'Provider reached the {settings.max_tokens}-token output limit; '
                            'reasoning and answer share this budget. No complete result was saved.' if limited else
                            'Provider returned an incomplete response. No complete result was saved.'))) from None
                self.ledger.save_manifest(self.request.run_id, frame.operation_id,
                    'unknown' if manifest['wire_body'] is not None else 'dispatch_failed', manifest)
                if not isinstance(exc, Exception): raise
                if (payload.purpose=='task' and getattr(exc,'status_code',None)==400 and
                        getattr(exc,'code',None) in ('context_length_exceeded','context_window_exceeded')):
                    self.ledger.save_manifest(self.request.run_id,frame.operation_id,'rejected_context',manifest)
                    remaining=(None if self.request.limits.max_turns is None else
                        self.request.limits.max_turns-self.ledger.run_budget(self.request.run_id)['models'])
                    if not self.overflow_reprojected and (remaining is None or remaining>0):
                        from ..context.projection import ContextWindowOverflow
                        self.overflow_reprojected=True
                        self.overflow_input_bytes=len(dumps(payload.input).encode())
                        if self.projection:
                            self.projection.window_tokens=int(self.projection.window_tokens*.75)
                            self.projection.force_compaction=True
                        raise ContextWindowOverflow(payload.input,remaining) from None
                    raise CoreFailure(RunError(code='context_window',origin='engine',
                        message='Provider rejected context after one bounded reprojection')) from None
                from openai import APITimeoutError
                if payload.purpose=='task' and isinstance(exc,(TimeoutError,APITimeoutError)) and self.ledger.progress(self.request.run_id)['phase']=='researching':
                    self.ledger.set_phase(self.request.run_id,'finalizing','investigation_timeout')
                    remaining=(None if self.request.limits.max_turns is None else
                        self.request.limits.max_turns-self.ledger.run_budget(self.request.run_id)['models'])
                    if (remaining is None or remaining>0) and self.policy.remaining_seconds()>0:
                        raise FinalizeAfterTimeout(payload.input,remaining) from None
                status = getattr(exc, 'status_code', None)
                detail = type(exc).__name__ + (f'; HTTP {status}' if isinstance(status, int) else '')
                raise CoreFailure(RunError(code='model_request_failed', origin='engine',
                    message=f'Provider request failed ({detail})')) from None
            finally:
                before_send.reset(audit_token)
                after_receive.reset(response_token)
            cache=response_metadata.get('usage',{}).get('input_tokens_details',{}).get('cached_tokens')
            manifest['usage'] = {'input_tokens':response.usage.input_tokens,'output_tokens':response.usage.output_tokens,
                'cached_input_tokens':cache,'sdk_normalized_cached_input_tokens':response.usage.input_tokens_details.cached_tokens,
                'cache_tokens_included_in_input':True}
            manifest['response_metadata']=response_metadata
            manifest['response_input_items']=response.to_input_items()
            manifest['duration_seconds']=monotonic()-started
            self.ledger.save_manifest(self.request.run_id, frame.operation_id, 'responded', manifest)
            value = response_json(response)
            if payload.purpose in ('summary', 'memory', 'memory_write_review'):
                value['_summary_metadata']=response_metadata
            usage = {'requests': response.usage.requests, 'input_tokens': response.usage.input_tokens,
                     'output_tokens': response.usage.output_tokens, 'complete': True}
        elif frame.operation in ('tool','private'):
            from .streaming import preview_for
            emit = preview_for(self.request)
            if emit is not None: emit(phase='researching')
            try:
                async with asyncio.timeout(max(.01,self.policy.remaining_seconds()-self.policy.reserve_seconds)):
                    if frame.operation == 'tool':
                        from ..memories.write_models import current_write_operation
                        from .ledger import digest
                        op_token = current_write_operation.set({'run_id': self.request.run_id,
                            'call_id': payload.call_id, 'name': payload.name, 'hash': digest(frame.payload)})
                        try:
                            value = (await self.invoke(payload.name,payload.call_id,payload.arguments)).model_dump(mode='json')
                        finally: current_write_operation.reset(op_token)
                    else:
                        binding=self.private_tools[payload.name]
                        try:
                            args=(binding.input_type.model_validate_json(payload.arguments) if isinstance(payload.arguments,str)
                                else binding.input_type.model_validate(payload.arguments))
                        except ValidationError as exc:
                            value={'value':{'status':'invalid_input','executed':False,
                                'issues':[{**issue,'loc':list(issue['loc'])} for issue in
                                    exc.errors(include_url=False,include_context=False,include_input=False)],
                                'remaining_tool_calls':max(0,self.request.limits.max_calls-self.ledger.run_budget(self.request.run_id)['tools']),
                                'instruction':'Correct the arguments using the provided tool schema and exact context references; retry with a new tool call ID. Do not guess artifact or source identities.'}}
                        else:
                            value=(await binding.handler(args)).model_dump(mode='json')
            except TimeoutError:
                confirmed=False
                self.ledger.set_phase(self.request.run_id,'finalizing','investigation_timeout')
                value={'value':{'status':'investigation_timeout','executed':None,
                    'instruction':'The last tool did not confirm completion. Deliver available evidence and this unresolved gap.'}}
            else:
                if isinstance(value['value'], dict) and 'remaining_tool_calls' in value['value']:
                    # Core's in-memory counter restarts with the process. The
                    # persistent broker budget is authoritative across recovery.
                    value['value']['remaining_tool_calls'] = max(0, self.request.limits.max_calls -
                        self.ledger.run_budget(self.request.run_id)['tools'])
                progress=self.policy.after_tool(payload.name,payload.arguments,value['value'])
                if progress['repeat_streak'] and isinstance(value['value'],dict):
                    from ..context.projection import dumps
                    original=self.artifacts.save_text(dumps(value['value']))
                    value={'value':{**value['value'],'execution_notice':{
                        'repeat_without_new_information':progress['repeat_streak'], 'artifact_id':original,
                        'instruction':'This exact version, parameters and result were already read. Use this saved reference; repeated calls without new information will end investigation.'}}}
        else:
            value = {'accepted': True, 'published': False}
        if (frame.operation in ('private', 'tool') and payload.name != 'read_context_page'
                and self.artifacts is not None):
            # Publish evidence-read callbacks before the durable receipt, so a
            # crash/replay cannot lose the business receipt behind a saved read.
            value = {'value': self.artifacts.deliver(value['value'])}
        self.validate()
        if confirmed:
            if self.checkpoints is not None:
                self.checkpoints.complete(frame, value, usage)
            else:
                self.ledger.settle(self.workspace, self.token, self.epoch, self.request.run_id,
                                   frame.operation_id, usage)
        if frame.operation == 'result':
            self.result = payload.model_copy(update={'usage': self.ledger.usage(self.request.run_id)})
        return value
