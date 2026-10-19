"""Historical record seeding only; full-history assembly is not a product path."""
from uuid import uuid4
from agent_core.contracts import Message, PreparedInput, RunLimits, RunRequest
from agent_core.execution.ledger import answer_text
from agent_core.execution.ports import Commit, Conflict, RunRecord
from agent_core.execution.service import ExecutionService, outcome

class LegacyExecutionService(ExecutionService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active = set()

    async def send(self, conversation_id, agent_id, text, *, run_id=None, expected_revision=None, limits=None):
        run_id = run_id or str(uuid4())
        limits = limits or RunLimits()
        previous = self.ledger.get(run_id)
        if previous:
            if (previous.conversation_id != conversation_id or previous.request.agent_id != agent_id or
                    previous.request.input.messages[-1].content != text or previous.request.limits != limits or
                    (expected_revision is not None and previous.expected_revision != expected_revision)):
                raise Conflict('Run ID reused with different request')
            if run_id in self.pending:
                return self.retry_save(run_id)
            return outcome(previous)  # Never re-execute a recorded run, including interrupted.
        if conversation_id in self.active or any(
                r.status in ('running', 'saving') for r in self.ledger.runs(conversation_id)):
            raise Conflict('Conversation has an unfinished run')
        snapshot = self.ledger.history(conversation_id)
        if expected_revision is not None and expected_revision != snapshot.revision:
            raise Conflict('Conversation revision changed')
        user = Message(role='user', content=text)
        request = RunRequest(run_id=run_id, agent_id=agent_id, limits=limits,
                             input=PreparedInput(messages=snapshot.messages + (user,)))
        self.ledger.start(RunRecord(run_id=run_id, conversation_id=conversation_id,
                                   process_id=self.ledger.database.process_id, request=request,
                                   expected_revision=snapshot.revision))
        self.events.register(run_id)
        self.active.add(conversation_id)
        self._notify('正在生成')
        try:
            result = await self.runner.execute(request)
            messages = (user, Message(role='assistant', content=answer_text(result))) if result.status == 'succeeded' else ()
            self.pending[run_id] = Commit(commit_id=f'run:{run_id}', run_id=run_id,
                conversation_id=conversation_id, expected_revision=snapshot.revision, messages=messages, result=result)
            return self.retry_save(run_id)
        finally:
            self.active.remove(conversation_id)

