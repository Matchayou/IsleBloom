from sqlalchemy import Column, ForeignKey, Integer, String, Table, Text, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert

from ...contracts import Message
from ...conversation import ConversationSnapshot
from ...execution.ports import Conflict


def define_tables(metadata):
    conversations = Table('conversations', metadata,
        Column('id', String, primary_key=True), Column('revision', Integer, nullable=False))
    messages = Table('conversation_messages', metadata,
        Column('conversation_id', String, ForeignKey('conversations.id'), primary_key=True),
        Column('revision', Integer, primary_key=True), Column('position', Integer, primary_key=True),
        Column('run_id', String, nullable=False), Column('expected_revision', Integer, nullable=False),
        Column('message', JSONB, nullable=False))
    return conversations, messages


class PostgresConversations:
    def __init__(self, connection, tables):
        self.connection, (self.conversations, self.messages) = connection, tables

    def load(self, conversation_id):
        c, m = self.conversations, self.messages
        revision = self.connection.scalar(select(c.c.revision).where(c.c.id == conversation_id))
        rows = self.connection.scalars(select(m.c.message).where(m.c.conversation_id == conversation_id)
                                      .order_by(m.c.revision, m.c.position))
        return ConversationSnapshot(revision=revision or 0, messages=tuple(Message.model_validate(v) for v in rows))

    def append(self, conversation_id, expected_revision, run_id, messages):
        if not messages:
            raise Conflict('Empty conversation increment')
        c, m = self.conversations, self.messages
        self.connection.execute(insert(c).values(id=conversation_id, revision=0).on_conflict_do_nothing())
        revision = self.connection.scalar(select(c.c.revision).where(c.c.id == conversation_id).with_for_update())
        existing = self.connection.execute(select(m).where(m.c.conversation_id == conversation_id,
                          m.c.run_id == run_id).order_by(m.c.position)).mappings().all()
        if existing:
            if (existing[0]['expected_revision'] != expected_revision or
                    tuple(Message.model_validate(r['message']) for r in existing) != messages):
                raise Conflict('Run ID reused with different append')
            return
        if revision != expected_revision:
            raise Conflict('Conversation revision changed')
        self.connection.execute(insert(m), [dict(conversation_id=conversation_id, revision=revision + 1,
            position=i, run_id=run_id, expected_revision=expected_revision, message=message.model_dump(mode='json'))
            for i, message in enumerate(messages)])
        self.connection.execute(update(c).where(c.c.id == conversation_id).values(revision=revision + 1))
