from ..execution.ports import Conflict
from ..evidence.models import EvidenceBundle


class MemoryEvidence:
    def __init__(self):
        self.bundles = {}

    def publish(self, result_id, bundle):
        if result_id in self.bundles: raise Conflict('Evidence already published')
        self.bundles[result_id] = bundle

    def read(self, result_id):
        return self.bundles.get(result_id, EvidenceBundle())

    def review(self, relation_id, assessment, reason):
        for key, bundle in self.bundles.items():
            for i, relation in enumerate(bundle.relations):
                if relation.id == relation_id:
                    value = relation.model_copy(update={'assessment':assessment,'review_reason':reason})
                    relations = list(bundle.relations)
                    relations[i] = value
                    self.bundles[key] = bundle.model_copy(update={'relations':tuple(relations)})
                    return value
        raise KeyError(relation_id)
