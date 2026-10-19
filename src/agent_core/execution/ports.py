from __future__ import annotations
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ..contracts import CoreEvent, Message, RunRequest, RunResult, Value
from ..conversation import ConversationSnapshot


def now():
    return datetime.now(timezone.utc)


class Conflict(Exception):
    """An identity, revision or state conflicts. Never retry by executing the model."""


class SaveUnavailable(Exception):
    """Storage unavailable; commit outcome may be unknown."""


class RunRecord(Value):
    run_id: str
    conversation_id: str | None
    process_id: str
    request: RunRequest
    expected_revision: int
    status: Literal['running', 'saving', 'succeeded', 'failed', 'cancelled', 'interrupted'] = 'running'
    result: RunResult | None = None
    commit_id: str | None = None
    created_at: datetime = Field(default_factory=now)


class StoredRunEvent(Value):
    root_run_id: str
    sequence: int
    event: CoreEvent
    occurred_at: datetime = Field(default_factory=now)


class Commit(Value):
    commit_id: str
    run_id: str
    conversation_id: str | None
    expected_revision: int
    messages: tuple[Message, ...]
    result: RunResult

    @model_validator(mode='after')
    def validate_result(self):
        if self.result.run_id != self.run_id:
            raise ValueError('Result run ID mismatch')
        if self.conversation_id is None:
            if self.messages or self.expected_revision != 0:
                raise ValueError('Independent runs cannot append conversation messages')
        elif self.result.status == 'succeeded':
            if len(self.messages) != 2 or [m.role for m in self.messages] != ['user', 'assistant']:
                raise ValueError('Success needs user and assistant messages')
        elif self.messages:
            raise ValueError('Failed runs cannot append conversation messages')
        return self


class ConversationRepository(Protocol):
    def load(self, conversation_id: str) -> ConversationSnapshot: ...
    def append(self, conversation_id: str, expected_revision: int,
               run_id: str, messages: tuple[Message, ...]) -> None: ...


class RunRepository(Protocol):
    def create_dispatch(self, group) -> None: ...
    def get_dispatch(self, group_id: str): ...
    def finish_dispatch(self, group) -> None: ...

    def get(self, run_id: str) -> RunRecord | None: ...
    def list(self, conversation_id: str) -> list[RunRecord]: ...
    def start(self, record: RunRecord) -> None: ...
    def saving(self, run_id: str) -> None: ...
    def committed(self, commit_id: str) -> Commit | None: ...
    def finish(self, commit: Commit) -> None: ...
    def append_event(self, event: StoredRunEvent) -> None: ...
    def events(self, run_id: str) -> list[StoredRunEvent]: ...


class Transaction(Protocol):
    conversations: ConversationRepository
    runs: RunRepository


class Database(Protocol):
    process_id: str
    def transaction(self) -> AbstractContextManager[Transaction]: ...
    def close(self) -> None: ...
