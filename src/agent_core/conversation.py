from typing import Protocol

from .contracts import Message, Value


class ConversationSnapshot(Value):
    revision: int = 0
    messages: tuple[Message, ...] = ()


class ConversationStore(Protocol):
    async def load(self, conversation_id: str) -> ConversationSnapshot: ...
    async def append(self, conversation_id: str, expected_revision: int,
                     run_id: str, messages: tuple[Message, ...]) -> None: ...


