"""ConversationStore adapter for the C0 contract, without application publication.

Used to exercise the independent Stage 0 service against the same repositories.
The Stage 1 CLI always uses ExecutionService for atomic result publication.
"""
from ..contracts import fail
from ..execution.ports import Conflict


class LedgerConversationStore:
    def __init__(self, database):
        self.database = database

    async def load(self, conversation_id):
        with self.database.transaction() as tx:
            return tx.conversations.load(conversation_id)

    async def append(self, conversation_id, expected_revision, run_id, messages):
        try:
            with self.database.transaction() as tx:
                tx.conversations.append(conversation_id, expected_revision, run_id, messages)
        except Conflict:
            fail('conversation_conflict', 'conversation', 'Conversation revision or append identity changed')
