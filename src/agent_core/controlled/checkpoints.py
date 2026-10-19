"""Private immutable state, fenced publication, and stable operation receipts.

This is storage for the SDK runner, not an execution loop. Unknown side effects
are never retried. A model retry gets another reservation and may be billed twice.
"""
import json
from dataclasses import asdict
from agents import ModelSettings
from agents.run import ModelInputData
from ..contracts import CoreFailure, RunError
from ..context.projection import dumps
from .ledger import digest


class Checkpoints:
    def __init__(self, broker, code_snapshot=lambda: None):
        self.broker = broker
        self.ledger = broker.ledger
        self.run_id = broker.request.run_id
        self.identity = digest(self.ledger.binding(self.run_id))
        self.code_snapshot = code_snapshot
        self.model_cursor = 0

    def load(self, key):
        self.broker.validate()
        self.ledger.check_writer(self.broker.workspace, self.broker.token, self.broker.epoch)
        ref = self.ledger.checkpoint(self.run_id, key)
        if ref is None:
            return None
        record = json.loads(self.broker.artifacts.load_text(ref))
        if record['binding'] != self.identity:
            raise ValueError('Checkpoint binding changed')
        budget = self.ledger.run_budget(self.run_id)
        if any(budget[k] < record['budget'][k] for k in ('models', 'tools')):
            raise ValueError('Checkpoint ledger cursor moved backwards')
        return record['value']

    def artifact(self, value):
        budget = self.ledger.run_budget(self.run_id)
        return self.broker.artifacts.save_text(dumps({'binding': self.identity,
            'budget': {k: budget[k] for k in ('models', 'tools')}, 'value': value}))

    def save(self, key, value):
        ref = self.artifact(value)
        self.ledger.save_checkpoint(self.broker.workspace, self.broker.token,
            self.broker.epoch, self.run_id, key, ref)

    def restore(self):
        head = self.load('sdk')
        if head is not None:
            self.model_cursor = head['model_cursor']
        expected = self.load('code')
        if expected is not None and expected != self.code_snapshot():
            raise ValueError('Code changed outside a committed controlled operation')
        return head

    def publish(self, value):
        self.save('code', self.code_snapshot())
        self.save('sdk', {**value, 'model_cursor': self.model_cursor})

    async def prepare(self, data, prepare):
        key = 'input:' + str(self.model_cursor + 1)
        saved = self.load(key)
        if saved is not None:
            from openai.types.shared import Reasoning
            settings = dict(saved['settings'])
            if settings.get('reasoning') is not None:
                settings['reasoning'] = Reasoning.model_validate(settings['reasoning'])
            self.broker.settings = ModelSettings(**settings)
            self.broker.projection.exclusions = saved['exclusions']
            return ModelInputData(**saved['input'])
        result = await prepare(data)
        self.save(key, {'input': asdict(result), 'settings': self.broker.settings.to_json_dict(),
                       'exclusions': self.broker.projection.exclusions})
        return result

    def operation(self, kind, payload, supplied):
        if kind == 'model' and payload.get('purpose', 'task') == 'task':
            self.model_cursor += 1
            base = 'model:' + str(self.model_cursor)
        elif kind == 'model':
            base = payload['purpose'] + ':' + digest(payload)
        else:
            base = supplied
        attempt = 0
        while True:
            operation_id = base if not attempt else base + ':retry:' + str(attempt)
            old = self.ledger.operation(self.run_id, operation_id)
            if old is None:
                return operation_id, None
            if old['request_hash'] != digest(payload) or old['kind'] != kind:
                raise ValueError('Stable operation ID reused with different request')
            receipt = self.load('receipt:' + operation_id)
            if receipt is not None:
                return operation_id, receipt['response']
            if kind != 'model':
                if kind == 'tool' and payload.get('name') == 'write_memory' and self.broker.memory_context:
                    memories, _, _ = self.broker.memory_context
                    recovered = memories.writes.replay(self.run_id, payload['call_id'], payload)
                    if recovered is not None:
                        from .protocol import Frame
                        self.complete(Frame(sequence=1, operation_id=operation_id, operation=kind, payload=payload), recovered, None)
                        return operation_id, recovered
                raise CoreFailure(RunError(code='operation_unconfirmed', origin='engine',
                    message='Side-effect completion is unknown; execution stopped. Inspect the saved operation before admitting a new run.'))
            capture = self.broker.memory_context and self.broker.memory_context[1].mode == 'memory_capture'
            if payload.get('purpose') in ('memory', 'memory_write_review') or capture:
                raise CoreFailure(RunError(code='memory_query_unconfirmed', origin='engine',
                    message='Memory side-query completion is unknown; inspect the saved operation before admitting a new run.'))
            attempt += 1

    def complete(self, frame, value, usage):
        ref = self.artifact({'response': value})
        # The complete response is durable before this transaction publishes its
        # reference and usage together. Orphan private files are harmless.
        self.ledger.settle(self.broker.workspace, self.broker.token, self.broker.epoch,
            self.run_id, frame.operation_id, usage, artifact=ref,
            code_artifact=self.artifact(self.code_snapshot()))
