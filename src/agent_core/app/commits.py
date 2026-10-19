"""C1 transaction composition. Execution itself knows no research types."""
from ..execution.ledger import Ledger
from ..research.models import NodeResultVersion, ChatMessage


class ResearchLedger(Ledger):
    def __init__(self, database, evidence):
        super().__init__(database)
        self.evidence = evidence

    def commit(self, commit):
        binding = None
        bundle = None
        source_snapshot = None
        if commit.result.status == 'succeeded':
            with self.database.transaction() as tx: binding = tx.research.run(commit.run_id)
            if binding is not None:
                source_snapshot = self.evidence.research.scopes.repository.snapshot(binding.run_id)
                binding = binding.model_copy(update={'resource_refs': self.evidence.research.run_resource_refs(binding)})
                bundle = self.evidence.prepare(f'result:{binding.run_id}', binding, commit.result.output)
        published_refs = binding.resource_refs if binding else ()
        with self.database.transaction() as tx:
            if tx.runs.committed(commit.commit_id) is not None:
                self.commit_in(tx, commit)
            else:
                self.commit_in(tx, commit)
                binding = tx.research.run(commit.run_id)
                if binding is not None:
                    node = tx.research.node(binding.node_id)
                    command = tx.research.command(binding.command_id)
                    eligible = node.selection == 'selected' and node.active_run_id == binding.run_id and node.revision == binding.node_revision
                    changes = {}
                    if eligible and commit.result.status == 'succeeded':
                        if binding.chat_revision is not None:
                            chat = tx.research.chat(node.id)
                            if chat is None or chat.revision != binding.chat_revision:
                                raise ValueError('Chat revision changed before publication')
                            message = ChatMessage(id=f'chat-reply:{binding.run_id}', role='assistant',
                                run_id=binding.run_id,
                                content=commit.result.output['report'], resource_refs=published_refs,
                                result_ids=binding.result_ids)
                            tx.research.save_chat(chat.model_copy(update={'revision':chat.revision + 1,
                                'messages':(*chat.messages, message)}), chat.revision)
                            collaboration = getattr(tx, 'collaboration', None)
                            if collaboration is not None:
                                delivered = set(binding.notification_ids)
                                for intent_id in delivered:
                                    intent = collaboration.intent(intent_id)
                                    if intent and intent.status in ('completed', 'failed') and intent.resume_status in ('pending', 'deferred'):
                                        collaboration.save_intent(intent.model_copy(update={'resume_status': 'complete'}), intent.status)
                                for notice_id in binding.notification_ids:
                                    notice = collaboration.notice(notice_id)
                                    if notice and notice.status in ('pending', 'deferred'):
                                        collaboration.save_notice(notice.model_copy(update={'status': 'complete'}), notice.status)
                        versions = tx.research.list_results(node.id)
                        missing = tuple(dict.fromkeys((*binding.missing, *commit.result.output['blocking_missing'])))
                        result = NodeResultVersion(id=f'result:{binding.run_id}', node_id=node.id,
                            version=len(versions) + 1, run_id=binding.run_id,
                            context_snapshot_id=binding.context_snapshot_id,
                            instruction_version=binding.instruction_version,
                            completion='partial' if missing else 'complete', missing=missing,
                            limitations=tuple(commit.result.output['limitations']),
                            resource_refs=published_refs)
                        tx.research.publish(result, binding.result_ids)
                        tx.evidence.publish(result.id, bundle)
                        self.evidence.research.memories.capture.record_in(tx, binding, source_snapshot)
                        changes['current_result_id'] = result.id
                        changes['resource_refs'] = published_refs
                    if node.active_run_id == binding.run_id:
                        changes['active_run_id'] = None
                    if changes:
                        tx.research.save_node(node.model_copy(update=changes), node.revision)
                    tx.research.save_command(command.model_copy(update={'status': 'completed',
                        'reason': None if eligible and commit.result.status == 'succeeded' else
                        (commit.result.error.code if commit.result.error else 'publication_ineligible')}))
            self.evidence.research.memories.writes.publish_in(tx, commit.run_id, commit.result)
        return self.get(commit.run_id)
