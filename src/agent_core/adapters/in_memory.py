from ..conversation import ConversationSnapshot
from ..contracts import fail


class InMemoryConversationStore:
    """Single event-loop store. No durable or cross-process guarantees."""
    def __init__(self):
        self._snapshots = {}
        self._appends = {}

    async def load(self, conversation_id):
        return self._snapshots.get(conversation_id, ConversationSnapshot())

    async def append(self, conversation_id, expected_revision, run_id, messages):
        key = (conversation_id, run_id)
        payload = (expected_revision, messages)
        if key in self._appends:
            if self._appends[key] != payload:
                fail("conversation_conflict", "conversation", "Run ID reused with different append")
            return
        current = await self.load(conversation_id)
        if current.revision != expected_revision:
            fail("conversation_conflict", "conversation", "Conversation revision changed")
        self._snapshots[conversation_id] = ConversationSnapshot(
            revision=current.revision + 1, messages=current.messages + messages)
        self._appends[key] = payload
