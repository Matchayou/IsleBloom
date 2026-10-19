"""Transactional in-memory evaluation repository."""
from ..execution.ports import Conflict


class MemoryEvaluation:
    def __init__(self):
        self.feedbacks, self.cases, self.experiments, self.trials = {}, {}, {}, {}

    def feedback(self, key): return self.feedbacks.get(key)
    def case(self, key): return self.cases.get(key)
    def experiment(self, key): return self.experiments.get(key)
    def trial(self, key): return self.trials.get(key)
    def list_feedback(self, research_id):
        return [v for v in self.feedbacks.values() if v.research_id == research_id]
    def list_cases(self, research_id):
        return [v for v in self.cases.values() if v.research_id == research_id]
    def list_trials(self, experiment_id):
        return [v for v in self.trials.values() if v.experiment_id == experiment_id]
    def add_feedback(self, value):
        old = self.feedback(value.id)
        if old and old != value: raise Conflict('Feedback ID reused with different content')
        self.feedbacks[value.id] = value
    def save_feedback(self, value, expected):
        if self.feedback(value.id) != expected: raise Conflict('Feedback changed')
        self.feedbacks[value.id] = value
    def add_case(self, value):
        old = self.case(value.id)
        if old and old != value: raise Conflict('Case ID reused with different content')
        self.cases[value.id] = value
    def add_experiment(self, value):
        old = self.experiment(value.id)
        if old and old != value: raise Conflict('Experiment ID reused with different content')
        self.experiments[value.id] = value
    def add_trial(self, value):
        old = self.trial(value.id)
        if old and old != value: raise Conflict('Trial ID reused with different content')
        self.trials[value.id] = value
