from typing import Literal

from ..contracts import ExecutePort, RunRequest, RunResult, Value
from .ledger import EventSink, Ledger
from .ports import Commit, Conflict, RunRecord


class ExecutionOutcome(Value):
    run_id: str
    status: Literal['completed', 'failed', 'cancelled', 'running', 'interrupted', 'save_failed', 'confirming', 'conflict']
    result: RunResult | None = None
    preview: RunResult | None = None
    error: str | None = None


def outcome(record):
    return ExecutionOutcome(run_id=record.run_id,
                            status={'succeeded': 'completed', 'saving': 'confirming'}.get(record.status, record.status),
                            result=record.result)


class PublicationError(Exception):
    """The computation has not crossed the durable publication boundary."""
    def __init__(self, value):
        self.outcome = value
        super().__init__(value.status)


class ExecutionService:
    def __init__(self, runner: ExecutePort, ledger: Ledger, events: EventSink, notify=lambda _: None, validate_commit=lambda commit: commit):
        self.runner, self.ledger, self.events, self.notify = runner, ledger, events, notify
        self.validate_commit = validate_commit
        self.pending: dict[str, Commit] = {}

    def _notify(self, status):
        try:
            self.notify(status)
        except Exception:
            pass

    async def execute(self, request: RunRequest, *, resume_interrupted=False) -> RunResult:
        """ExecutePort for independent, explicitly prepared runs; no session mutation."""
        previous = self.ledger.get(request.run_id)
        if previous:
            if previous.conversation_id is not None or previous.request != request:
                raise Conflict('Run ID reused with different request')
            if resume_interrupted and previous.status in ('running', 'interrupted'):
                previous = None
            else:
                value = self.retry_save(request.run_id) if request.run_id in self.pending else outcome(previous)
        if previous is None:
            if self.ledger.get(request.run_id) is None:
                self.ledger.start(RunRecord(run_id=request.run_id, conversation_id=None,
                    process_id=self.ledger.database.process_id, request=request, expected_revision=0))
            self.events.register(request.run_id)
            result = await self.runner.execute(request)
            if result.run_id != request.run_id:
                raise Conflict('Execution returned a different run ID')
            self.pending[request.run_id] = Commit(commit_id=f'run:{request.run_id}',
                run_id=request.run_id, conversation_id=None, expected_revision=0, messages=(), result=result)
            value = self.retry_save(request.run_id)
        if value.result is None:
            raise PublicationError(value)
        return value.result

    def retry_save(self, run_id):
        commit = self.pending.get(run_id)
        if commit is None:
            record = self.ledger.get(run_id)
            if record is None:
                raise Conflict('Unknown run')
            return outcome(record)
        self._notify('正在保存')
        try:
            # Resolve a previous uncertain commit before touching its terminal state.
            previous = self.ledger.committed(commit.commit_id)
            if previous is not None:
                if previous != commit:
                    raise Conflict('Commit ID reused with different content')
                record = self.ledger.get(run_id)
            else:
                commit = self.validate_commit(commit)
                self.pending[run_id] = commit
                self.events.flush(run_id)
                self.ledger.saving(run_id)
                record = self.ledger.commit(commit)
        except Conflict as exc:
            self._notify('保存冲突')
            return ExecutionOutcome(run_id=run_id, status='conflict', preview=commit.result, error=str(exc))
        except Exception:
            self._notify('正在确认保存状态')
            try:
                previous = self.ledger.committed(commit.commit_id)
                if previous == commit:
                    record = self.ledger.get(run_id)
                elif previous is not None:
                    raise Conflict('Commit ID reused with different content')
                else:
                    self._notify('保存失败')
                    return ExecutionOutcome(run_id=run_id, status='save_failed', preview=commit.result,
                                            error='Not committed; retry saving the same run')
            except Conflict:
                return ExecutionOutcome(run_id=run_id, status='conflict', preview=commit.result, error='Commit conflict')
            except Exception:
                return ExecutionOutcome(run_id=run_id, status='confirming', preview=commit.result,
                                        error='Storage unavailable; commit outcome unknown')
        del self.pending[run_id]
        self.events.release(run_id)
        self._notify('已完成' if record.status == 'succeeded' else record.status)
        return outcome(record)
