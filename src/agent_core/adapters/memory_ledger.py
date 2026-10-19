from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from ..conversation import ConversationSnapshot
from ..execution.ports import Conflict


class MemoryConversations:
    def __init__(self):
        self.snapshots, self.appends = {}, {}

    def load(self, conversation_id):
        return self.snapshots.get(conversation_id, ConversationSnapshot())

    def append(self, conversation_id, expected_revision, run_id, messages):
        if not messages:
            raise Conflict('Empty conversation increment')
        key = (conversation_id, run_id)
        payload = (expected_revision, messages)
        if key in self.appends:
            if self.appends[key] != payload:
                raise Conflict('Run ID reused with different append')
            return
        current = self.load(conversation_id)
        if current.revision != expected_revision:
            raise Conflict('Conversation revision changed')
        self.snapshots[conversation_id] = ConversationSnapshot(revision=current.revision + 1,
                                                               messages=current.messages + messages)
        self.appends[key] = payload


class MemoryRuns:
    def __init__(self):
        self.records, self.commits, self.timeline, self.groups = {}, {}, {}, {}

    def create_dispatch(self, group):
        if group.plan.group_id in self.groups:
            raise Conflict('Dispatch already exists')
        self.groups[group.plan.group_id] = group.model_copy(deep=True)

    def get_dispatch(self, group_id):
        group = self.groups.get(group_id)
        return group.model_copy(deep=True) if group else None

    def finish_dispatch(self, group):
        old = self.get_dispatch(group.plan.group_id)
        if old is None or old.plan != group.plan or old.status != 'running':
            if old == group:
                return
            raise Conflict('Dispatch cannot finish')
        self.groups[group.plan.group_id] = group.model_copy(deep=True)

    def get(self, run_id):
        record = self.records.get(run_id)
        return record.model_copy(deep=True) if record else None

    def list(self, conversation_id):
        return [self.get(r.run_id) for r in self.records.values() if r.conversation_id == conversation_id]

    def start(self, record):
        if record.run_id in self.records:
            raise Conflict('Run already exists')
        if record.conversation_id is not None and any(r.conversation_id == record.conversation_id and r.status in ('running', 'saving')
               for r in self.records.values()):
            raise Conflict('Conversation has an unfinished run')
        self.records[record.run_id] = record.model_copy(deep=True)

    def saving(self, run_id):
        record = self.records[run_id]
        if record.status not in ('running', 'saving', 'interrupted'):
            raise Conflict('Run cannot enter saving')
        self.records[run_id] = record.model_copy(update={'status': 'saving'})

    def committed(self, commit_id):
        value = self.commits.get(commit_id)
        return value.model_copy(deep=True) if value else None

    def finish(self, commit):
        self.commits[commit.commit_id] = commit.model_copy(deep=True)
        self.records[commit.run_id] = self.records[commit.run_id].model_copy(update={
            'status': commit.result.status, 'result': commit.result.model_copy(deep=True), 'commit_id': commit.commit_id})

    def append_event(self, event):
        key = (event.root_run_id, event.sequence)
        if event.root_run_id not in self.records:
            raise Conflict('Unknown root run')
        if key in self.timeline:
            if self.timeline[key] != event:
                raise Conflict('Event sequence reused with different content')
            return
        if event.sequence != len(self.events(event.root_run_id)) + 1:
            raise Conflict('Event sequence gap')
        self.timeline[key] = event.model_copy(deep=True)

    def events(self, run_id):
        return [e.model_copy(deep=True) for (root, _), e in sorted(self.timeline.items()) if root == run_id]


class MemoryDatabase:
    """Single event-loop transactions; explicit ephemeral application mode."""
    def __init__(self):
        self.process_id = str(uuid4())
        self.conversations, self.runs = MemoryConversations(), MemoryRuns()
        self.repositories = {}

    @contextmanager
    def transaction(self):
        conversations, runs = deepcopy(self.conversations), deepcopy(self.runs)
        repositories = deepcopy(self.repositories)
        yield SimpleNamespace(conversations=conversations, runs=runs, **repositories)
        self.repositories = repositories
        self.conversations, self.runs = conversations, runs

    def close(self):
        pass
