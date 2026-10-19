"""The application owns publication; repositories only write their own records."""
import json

from .ports import Commit, Conflict, Database, RunRecord, StoredRunEvent
from ..contracts import CoreEvent


def answer_text(result):
    return result.output if isinstance(result.output, str) else json.dumps(result.output, ensure_ascii=False)


class Ledger:
    def __init__(self, database: Database):
        self.database = database

    def create_dispatch(self, group):
        with self.database.transaction() as tx:
            tx.runs.create_dispatch(group)

    def get_dispatch(self, group_id):
        with self.database.transaction() as tx:
            return tx.runs.get_dispatch(group_id)

    def finish_dispatch(self, group):
        with self.database.transaction() as tx:
            tx.runs.finish_dispatch(group)

    def get(self, run_id):
        with self.database.transaction() as tx:
            return tx.runs.get(run_id)

    def history(self, conversation_id):
        with self.database.transaction() as tx:
            return tx.conversations.load(conversation_id)

    def runs(self, conversation_id):
        with self.database.transaction() as tx:
            return tx.runs.list(conversation_id)

    def events(self, run_id):
        with self.database.transaction() as tx:
            return tx.runs.events(run_id)

    def start(self, record: RunRecord):
        with self.database.transaction() as tx:
            tx.runs.start(record)

    def saving(self, run_id):
        with self.database.transaction() as tx:
            tx.runs.saving(run_id)

    def committed(self, commit_id):
        with self.database.transaction() as tx:
            return tx.runs.committed(commit_id)

    def commit(self, commit: Commit):
        commit = Commit.model_validate(commit.model_dump())
        with self.database.transaction() as tx:
            self.commit_in(tx, commit)
        return self.get(commit.run_id)


    def commit_in(self, tx, commit):
        """Public write boundary for application-composed atomic publication."""
        previous = tx.runs.committed(commit.commit_id)
        if previous is not None:
            if previous != commit:
                raise Conflict('Commit ID reused with different content')
            return tx.runs.get(commit.run_id)
        record = tx.runs.get(commit.run_id)
        if record is None or record.commit_id or record.status not in ('running', 'saving', 'interrupted'):
            raise Conflict('Run cannot be committed')
        if (record.conversation_id != commit.conversation_id or
                record.expected_revision != commit.expected_revision):
            raise Conflict('Commit does not match admitted run')
        if commit.messages and (commit.messages[0] != record.request.input.messages[-1] or
                               commit.messages[1].content != answer_text(commit.result)):
            raise Conflict('Conversation increment does not match execution')
        if commit.messages:
            tx.conversations.append(commit.conversation_id, commit.expected_revision,
                                    commit.run_id, commit.messages)
        tx.runs.finish(commit)

class EventSink:
    """Core's synchronous observer never throws into the model loop.

    Each event is durably attempted immediately. Failed writes remain in memory;
    publication requires flush() to succeed. A crash may lose that unsaved tail.
    """
    def __init__(self, ledger: Ledger, observe=lambda _: None):
        self.ledger, self.observe = ledger, observe
        self.roots = {}
        self.pending: dict[str, list[StoredRunEvent]] = {}
        self.sequences = {}

    def register(self, run_id):
        self.roots[run_id] = run_id
        self.pending[run_id] = []
        self.sequences[run_id] = len(self.ledger.events(run_id))

    def __call__(self, event: CoreEvent):
        root = self.roots.get(event.run_id) or self.roots[event.parent_run_id]
        self.roots[event.run_id] = root
        self.sequences[root] += 1
        self.pending[root].append(StoredRunEvent(root_run_id=root, sequence=self.sequences[root], event=event))
        try:
            self.flush(root)
        except Exception:
            pass
        try:
            self.observe(event)
        except Exception:
            pass  # A display failure cannot alter execution or publication.

    def flush(self, root):
        for event in list(self.pending[root]):
            with self.ledger.database.transaction() as tx:
                tx.runs.append_event(event)
            self.pending[root].remove(event)

    def release(self, root):
        self.pending.pop(root, None)
        self.sequences.pop(root, None)
        self.roots = {k: v for k, v in self.roots.items() if v != root}
