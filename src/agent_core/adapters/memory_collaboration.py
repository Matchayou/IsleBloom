"""Memory repository with the same command identities as PostgreSQL."""
from ..execution.ports import Conflict


class MemoryCollaboration:
    def __init__(self):
        self.information_requests = {}
        self.review_loops = {}
        self.sessions, self.packets, self.intents, self.messages, self.revisions, self.cancellations, self.dependencies, self.notices, self.reads = {}, {}, {}, {}, {}, {}, {}, {}, {}

    def session(self, key): return self.sessions.get(key)
    def review_loop(self, task_id): return self.review_loops.get(task_id)
    def review_loops_for(self, research_id):
        return [v for v in self.review_loops.values() if v.research_id == research_id]
    def save_review_loop(self, value, expected_revision):
        old = self.review_loops.get(value.id)
        if old is None or old.revision != expected_revision:
            raise Conflict('Automatic review changed')
        self.review_loops[value.id] = value
    def information_request(self, key): return self.information_requests.get(key)
    def information_for(self, task_id):
        return [v for v in self.information_requests.values() if v.task_id == task_id]
    def save_information_request(self, value, expected_attempts):
        old = self.information_requests.get(value.id)
        if old is None or old.status != 'waiting' or old.attempts != expected_attempts:
            raise Conflict('Information request changed')
        self.information_requests[value.id] = value
    def sessions_for(self, research_id):
        return [v for v in self.sessions.values() if v.research_id == research_id]
    def packet(self, key): return self.packets.get(key)
    def intent(self, key): return self.intents.get(key)
    def message(self, key): return self.messages.get(key)
    def pending_messages(self):
        return [v for v in self.messages.values() if v.status == 'queued']
    def messages_for(self, session_id):
        return [v for v in self.messages.values() if v.session_id == session_id]
    def revision(self, key): return self.revisions.get(key)
    def cancellation(self, key): return self.cancellations.get(key)
    def cancellations_for(self, task_id):
        return [v for v in self.cancellations.values() if v.task_id == task_id]
    def pending_cancellations(self):
        return [v for v in self.cancellations.values() if v.status in ('requested', 'stopping')]
    def dependencies_for(self, research_id):
        return [v for v in self.dependencies.values() if v.research_id == research_id]
    def notice(self, key): return self.notices.get(key)
    def notices_for(self, research_id):
        return [v for v in self.notices.values() if v.research_id == research_id]
    def pending_notices(self):
        return [v for v in self.notices.values() if v.status == 'pending']
    def pending_revisions(self):
        return [v for v in self.revisions.values() if v.status in ('queued', 'running')
                or (v.status == 'saved' and v.mode != 'save')]
    def intent_for_task(self, task_id):
        return next((v for v in self.intents.values() if v.task_id == task_id), None)
    def pending(self):
        return [v for v in self.intents.values() if v.status in ('accepted', 'queued')]
    def intents_for(self, research_id):
        return [v for v in self.intents.values() if v.research_id == research_id]
    def intents_for_status(self, status):
        return [v for v in self.intents.values() if v.status == status]
    def record_read(self, run_id, ref):
        self.reads[(run_id, ref.resource_id, ref.snapshot_id, ref.parse_id)] = ref
    def read_refs(self, run_id):
        return [ref for (key, _, _, _), ref in self.reads.items() if key == run_id]
    def insert(self, kind, key, value):
        target = getattr(self, kind)
        if key in target: raise Conflict('Collaboration identity exists')
        target[key] = value
    def save_session(self, value, expected):
        old = self.sessions.get(value.id)
        if old is None or old.revision != expected: raise Conflict('Expert session revision changed')
        self.sessions[value.id] = value
    def save_intent(self, value, expected_status):
        old = self.intents.get(value.command_id)
        if old is None or old.status != expected_status: raise Conflict('Dispatch state changed')
        self.intents[value.command_id] = value
    def save_message(self, value, expected_status):
        old = self.messages.get(value.id)
        if old is None or old.status != expected_status: raise Conflict('Agent message state changed')
        self.messages[value.id] = value
    def save_revision(self, value, expected_status):
        old = self.revisions.get(value.id)
        if old is None or old.status != expected_status: raise Conflict('Task revision state changed')
        self.revisions[value.id] = value
    def save_cancellation(self, value, expected_status):
        old = self.cancellations.get(value.id)
        if old is None or old.status != expected_status: raise Conflict('Task cancellation state changed')
        self.cancellations[value.id] = value
    def save_notice(self, value, expected_status):
        old = self.notices.get(value.id)
        if old is None or old.status != expected_status: raise Conflict('Manager notice state changed')
        self.notices[value.id] = value
