"""Trusted SDK execution through a bounded, audited controlled-tool channel."""
import asyncio
import json
from uuid import uuid4
from dataclasses import replace

from ..adapters.openai import OpenAIEngine
from ..contracts import ToolResult
from .model import BrokerModel
from .protocol import Frame, encode, decode


class Channel:
    def __init__(self, workspace, broker):
        self.sequence = 0
        self.workspace, self.broker = workspace, broker
        self.attempt = uuid4().hex
        self.lock = asyncio.Lock()
        self.owner = None

    async def call(self, operation, payload, operation_id=None):
        # A read-only memory tool may perform an audited model call while its
        # outer tool receipt is pending. Serialize other tasks, allow this owner.
        if self.owner is asyncio.current_task():
            return await self._call(operation, payload, operation_id)
        async with self.lock:
            self.owner = asyncio.current_task()
            try: return await self._call(operation, payload, operation_id)
            finally: self.owner = None

    async def _call(self, operation, payload, operation_id):
        replay = None
        if self.broker.checkpoints is not None:
            operation_id, replay = self.broker.checkpoints.operation(operation, payload, operation_id)
        self.sequence += 1
        sequence = self.sequence
        request = dict(sequence=sequence, operation_id=operation_id or uuid4().hex,
                       operation=operation, payload=payload)
        frame = Frame.model_validate(decode(encode(request)))
        self.workspace.save(f'transcript_{self.attempt}_{sequence}_request', encode(request).decode())
        if replay is None:
            value = await self.broker.handle(frame)
        else:
            self.broker.validate()
            self.broker.sequence = sequence
            value = replay
            if operation == 'result':
                from ..contracts import RunResult
                self.broker.result = RunResult.model_validate(payload).model_copy(
                    update={'usage': self.broker.ledger.usage(self.broker.request.run_id)})
        self.workspace.save(f'transcript_{self.attempt}_{sequence}_response', encode(value).decode())
        if operation in ('private', 'tool'):
            self.workspace.save('tool_' + uuid4().hex, encode(value).decode())
        return value


async def run(request, spec, tools, workspace, broker, checkpoints=None):
    broker.checkpoints = checkpoints
    channel = Channel(workspace, broker)
    private = tuple(broker.private_tools.values())
    private_names = {t.name for t in private}

    async def invoke(name, call_id, arguments):
        value = await channel.call('private' if name in private_names else 'tool',
            dict(name=name, call_id=call_id, arguments=arguments), 'tool:' + call_id)
        return ToolResult.model_validate(value)

    from ..controlled.model_projection import ModelProjection
    from .progress import FINAL_INSTRUCTION
    from ..controlled.model_projection import model_settings
    from ..context.compaction import Compactor
    async def summarize(value):
        broker.settings=model_settings(max(.01,broker.policy.remaining_seconds()-broker.policy.reserve_seconds),
            model=spec.model, purpose='summary')
        response=await channel.call('model', broker.summary_payload(value))
        metadata=response.pop('_summary_metadata',{})
        incomplete=(metadata.get('status')=='incomplete' or bool(metadata.get('incomplete_details')) or metadata.get('finish_reason')=='length' or
            'length' in metadata.get('finish_reasons',[]))
        output=response['output'];texts=[]
        for item in output:
            if item.get('status')=='incomplete':incomplete=True
            if item.get('type')=='message':
                texts.extend(c['text'] for c in item.get('content',[]) if c.get('type')=='output_text')
            elif item.get('type')!='reasoning':incomplete=True
        return {'text':'\n'.join(texts),'incomplete':incomplete,'usage':response['usage']}
    compactor=Compactor(artifacts=broker.artifacts,broker=broker,summarize=summarize)
    projection = ModelProjection(broker.artifacts, spec.model,
        protected_assistant_contents=compactor.rendered)
    broker.projection = projection
    from ..context.memory_retrieval import current_memory_query, select_memories, MemorySelection
    from ..context.projection import dumps

    async def memory_query(value):
        return await side_query(broker.memory_payload(value), MemorySelection)

    async def write_review(value):
        from ..memories.write_models import WriteReview
        return await side_query(broker.write_review_payload(value), WriteReview)

    async def side_query(payload, schema):
        response = await channel.call('model', payload)
        metadata = response.get('_summary_metadata', {})
        if (metadata.get('status') == 'incomplete' or metadata.get('incomplete_details') or
                metadata.get('finish_reason') == 'length' or 'length' in metadata.get('finish_reasons', [])):
            raise ValueError('Incomplete memory selection')
        texts = []
        for item in response['output']:
            if item.get('status') == 'incomplete': raise ValueError('Incomplete memory selection')
            if item.get('type') == 'message':
                for content in item.get('content', []):
                    if content.get('type') != 'output_text': raise ValueError('Non-text memory selection')
                    texts.append(content['text'])
            elif item.get('type') != 'reasoning': raise ValueError('Memory side query cannot call tools')
        broker.validate()
        return schema.model_validate_json('\n'.join(texts)).model_dump()

    async def initial_memory(data):
        if broker.memory_context is None: return data
        memories, snapshot, research_id = broker.memory_context
        values = memories.validate(research_id, snapshot.memory_catalog, agent=snapshot.agent)
        if not values: return data
        saved = checkpoints.load('memory:initial')
        if saved is None:
            selected, choices = [], []
            required = {r.id for r in snapshot.memory_refs}
            from ..context.memory_budget import bound, MEMORY_TOKEN_BUDGET
            oversized = [v for v in values if v.id not in required and bound((v,)) > MEMORY_TOKEN_BUDGET]
            packet = json.loads(request.input.messages[0].content)
            if packet.get('external_context'):
                packet = json.loads(broker.artifacts.load_text(packet['external_context']['artifact_id']))
            context = packet.get('history', [])[-3:]
            for namespace in ('user', 'project', 'specialist'):
                rows, reasons = await select_memories(request.input.tool_query or request.input.messages[-1].content,
                    [v for v in values if v.scope == namespace and v.id not in required and v not in oversized], context=context)
                selected.extend(rows)
                choices.extend(reasons)
            memories.validate(research_id, snapshot.memory_catalog, agent=snapshot.agent)
            from ..context.memory_budget import allocate
            mandatory = [v for v in values if v.id in required]
            allocated, budget = allocate(mandatory, selected)
            budget['omitted'].extend({'id':v.id,'version':v.version,'reason':'memory_injection_budget'} for v in oversized)
            selected = [v for v in allocated if v.id not in required]
            chosen = {v.id for v in selected}
            choices = [c for c in choices if c['id'] in chosen]
            saved = {'budget':budget, 'algorithm': 'llm-side-query', 'selection': choices,
                'approved_memory': list(memories.contributions(selected)),
                'memory_rule': 'Historical approved preferences and experience, not source evidence. Current user instructions take precedence.'}
            checkpoints.save('memory:initial', saved)
        if not saved['approved_memory']: return data
        content = dumps(saved)
        items = list(data.model_data.input)
        if not any(item.get('role') == 'user' and item.get('content') == content for item in items):
            items.insert(1, {'role': 'user', 'content': content})
        return replace(data, model_data=replace(data.model_data, input=items))

    async def prepare(data):
        broker.validate()
        data = await initial_memory(data)
        projection.input_observation = broker.ledger.input_observation(request.run_id)
        if broker.plan_budget:
            usage = broker.ledger.plan_usage(broker.plan_budget['node_ids'])
            projection.window_tokens = min(projection.window_tokens,
                max(0,broker.plan_budget['max_tokens']-usage.input_tokens-usage.output_tokens))
        state = broker.policy.prepare_model()
        finalizing = state['phase'] == 'finalizing'
        remaining = broker.policy.remaining_seconds()
        broker.settings = model_settings(max(.01, remaining if finalizing else remaining-broker.policy.reserve_seconds),
            model=spec.model, finalizing=finalizing)
        projection.settings = broker.settings
        if finalizing:
            data = replace(data, agent=data.agent.clone(tools=[]),
                model_data=replace(data.model_data,
                    instructions=(data.model_data.instructions or '') + FINAL_INSTRUCTION))
        from ..context.projection import ContextBudgetExceeded
        try:
            result=await compactor.prepare(data,projection)
        except ContextBudgetExceeded:
            if not broker.plan_budget: raise
            from ..contracts import CoreFailure,RunError
            raise CoreFailure(RunError(code='plan_token_budget',origin='engine',
                message='Required instructions and output reserve exceed remaining approved plan tokens; request not sent')) from None
        # Summary requests consume the same original budget and may trigger finalization.
        state=broker.policy.prepare_model()
        remaining=broker.policy.remaining_seconds()
        broker.settings=model_settings(max(.01,remaining if state['phase']=='finalizing' else remaining-broker.policy.reserve_seconds),
            model=spec.model, finalizing=state['phase']=='finalizing')
        projection.settings=broker.settings
        if state['phase']=='finalizing' and not finalizing:
            result=replace(result,instructions=(result.instructions or '')+FINAL_INSTRUCTION)
        return result
    from ..context.artifacts import current_artifacts
    artifact_token = current_artifacts.set(broker.artifacts)
    memory_token = current_memory_query.set(memory_query)
    from ..memories.write_models import current_write_review
    review_token = current_write_review.set(write_review)
    try:
        async def durable_prepare(data):
            return await checkpoints.prepare(data, prepare)
        result = await OpenAIEngine(BrokerModel(channel),
            input_filter=durable_prepare if checkpoints else prepare,
            checkpoints=checkpoints).execute(request, spec,
                tuple(t for t in tools if t.name not in private_names) + private, invoke)
    finally:
        current_memory_query.reset(memory_token)
        current_write_review.reset(review_token)
        current_artifacts.reset(artifact_token)
        from time import time
        workspace.save('context_activity', json.dumps({'last_active_at':time()}))
    workspace.save('result_' + uuid4().hex, result.model_dump_json())
    await channel.call('result', result.model_dump(mode='json'), 'result:' + request.run_id)
    return broker.result
