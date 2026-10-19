"""Message snapshots and explicit conclusion transfers on research node identities."""
from ..context.scopes import ScopeDenied
from ..execution.ports import Conflict
from ..resources.snapshots import identity
from .models import ChatMessage, ChatThread, ChatTransfer, NodeCommand


class ChatService:
    def __init__(self, research, tasks=None):
        self.research, self.tasks = research, tasks

    def _validate_message(self, research_id, message, node_id=None):
        from ..controlled.engine import ControlledEngine
        engine = self.tasks.engine if self.tasks is not None else self.research.engine
        if message.run_id and isinstance(engine, ControlledEngine):
            engine.validate_run(message.run_id, require_started=True)
        root = self.research.research(research_id)
        scope = self.research.scopes.current(root.scope_id, self.research.owner)
        branch_scope = (self.research.scopes.current(self.research.node(node_id).scope_id, self.research.owner)
                        if node_id else None)
        for ref in message.resource_refs:
            self.research.scopes.check(scope, ref)
            if branch_scope: self.research.scopes.check(branch_scope, ref)
        for key in message.result_ids:
            if key.startswith('task-result:'):
                if self.tasks is None: raise Conflict('Task results unavailable')
                receipt = self.tasks.receipt(key)
                if self.tasks.spec(receipt.task_id, receipt.spec_version).research_id != research_id:
                    raise ScopeDenied('Foreign task result')
                for ref in receipt.resource_refs:
                    self.research.scopes.check(scope, ref)
                    if branch_scope: self.research.scopes.check(branch_scope, ref)
            else:
                version = self.research.check_result(key, branch_scope or scope)
                if self.research.node(version.node_id).research_id != research_id:
                    raise ScopeDenied('Foreign research result')

    def read(self, node_id):
        node = self.research.node(node_id)
        chat = self.research.query('chat', node_id)
        if chat is None: raise KeyError(node_id)
        if node.selection == 'excluded': raise ScopeDenied('Branch excluded')
        for message in (*chat.inherited, *chat.messages):
            self._validate_message(chat.research_id, message, node_id)
        return chat

    def _select_message_refs(self, node_id, refs):
        if not refs: return
        node = self.research.node(node_id)
        root = self.research.research(node.research_id)
        source = self.research.scopes.current(root.scope_id, self.research.owner)
        scope = self.research.scopes.current(node.scope_id, self.research.owner)
        for ref in refs:
            self.research.scopes.check(source, ref)
            if ref not in scope.grants:
                scope = self.research.scopes.grant(scope, source, ref)
        selected = tuple(dict.fromkeys((*scope.allowed, *refs)))
        if selected != scope.allowed:
            self.research.scopes.select(scope, selected, scope.inherited, web_urls=scope.web_urls)

    async def start(self, research_id, goal):
        self.research.research(research_id)
        existing = [n for n in self.research.query('list_nodes', research_id)
                    if n.display_parent_id is None and self.research.query('chat', n.id)]
        if existing: return self.read(existing[0].id)
        command = NodeCommand(command_id=f'chat-main:{research_id}', research_id=research_id,
                              kind='create', goal=goal, allowed_tools=('read_source','search_source','search_web','fetch_web'))
        record = await self.research.submit(command)
        value = ChatThread(node_id=record.node_id, research_id=research_id)
        with self.research.database.transaction() as tx:
            old = tx.research.chat(record.node_id)
            if old is None: tx.research.create_chat(value)
            else: value = old
        return value

    def send(self, node_id, content, expected_revision, message_id=None):
        chat = self.read(node_id)
        if not content.strip(): raise ValueError('Empty message')
        value = ChatMessage(id=message_id or identity(), role='user', content=content)
        return self._append(chat, value, expected_revision)

    def _append(self, chat, message, expected_revision):
        old = next((m for m in chat.messages if m.id == message.id), None)
        if old:
            if old != message: raise Conflict('Message ID reused with different content')
            return chat
        if chat.revision != expected_revision: raise Conflict('Chat revision changed')
        self._select_message_refs(chat.node_id, message.resource_refs)
        value = chat.model_copy(update={'revision':chat.revision + 1,
                                        'messages':(*chat.messages, message)})
        with self.research.database.transaction() as tx:
            tx.research.save_chat(value, chat.revision)
        return value

    async def branch(self, source_node_id, fork_message_id, goal, expected_revision, command_id=None):
        if command_id:
            old = self.research.query('command', command_id)
            if old:
                frozen = self.research.query('chat', old.node_id)
                if (old.command.kind != 'branch' or old.command.node_id != source_node_id or
                    old.command.goal != goal or frozen and (frozen.fork_message_id != fork_message_id or
                    frozen.fork_source_revision != expected_revision)):
                    raise Conflict('Branch command ID reused with different content')
                if frozen: return self.read(old.node_id)
        source = self.read(source_node_id)
        if source.revision != expected_revision: raise Conflict('Source chat revision changed')
        visible = (*source.inherited, *source.messages)
        ids = [m.id for m in visible]
        if fork_message_id not in ids: raise ValueError('Fork message not found')
        inherited = visible[:ids.index(fork_message_id) + 1]
        refs = tuple(dict.fromkeys(ref for m in inherited for ref in m.resource_refs))
        parent = self.research.node(source_node_id)
        sibling_count = sum(n.display_parent_id == source_node_id
                            for n in self.research.query('list_nodes', source.research_id))
        command = NodeCommand(command_id=command_id or identity(), research_id=source.research_id,
                              kind='branch', node_id=source_node_id, goal=goal, resource_refs=refs, allowed_tools=parent.allowed_tools,
                              position=(parent.position[0] + 280, parent.position[1] + sibling_count * 180))
        record = await self.research.submit(command)
        value = ChatThread(node_id=record.node_id, research_id=source.research_id,
                           fork_message_id=fork_message_id, fork_source_revision=source.revision,
                           inherited=inherited)
        with self.research.database.transaction() as tx:
            old = tx.research.chat(record.node_id)
            if old is None: tx.research.create_chat(value)
            elif old != value: raise Conflict('Branch command changed')
        return value

    def publish_expert(self, node_id, result_id, expected_revision):
        if self.tasks is None: raise Conflict('Tasks unavailable')
        chat = self.read(node_id)
        receipt = self.tasks.receipt(result_id)
        spec = self.tasks.spec(receipt.task_id, receipt.spec_version)
        if spec.research_id != chat.research_id or spec.node_id != node_id:
            raise ScopeDenied('Task belongs to another branch')
        if receipt.delivery != 'passed': raise Conflict('Expert result needs independent review')
        self.tasks._check_node(spec)
        record = self.tasks.ledger.get(receipt.run_id)
        if record is None or record.result is None: raise Conflict('Expert output missing')
        answer = record.result.output
        message = ChatMessage(id=result_id, role='expert', content=answer['report'],
            resource_refs=receipt.resource_refs, result_ids=(result_id,),
            assumptions=receipt.assumptions, limitations=receipt.limitations)
        self._validate_message(chat.research_id, message)
        return self._append(chat, message, expected_revision)

    def transfer(self, source_node_id, source_message_id, target_node_id, expected_revision,
                 expected_node_revision, transfer_id=None):
        source, target = self.read(source_node_id), self.read(target_node_id)
        if source.research_id != target.research_id or source_node_id == target_node_id:
            raise Conflict('Transfer needs two branches in one research')
        message = next((m for m in source.messages if m.id == source_message_id), None)
        if message is None or message.role not in ('expert', 'assistant'):
            raise Conflict('Only a complete conclusion can be transferred')
        self._validate_message(source.research_id, message)
        value = ChatTransfer(id=transfer_id or identity(), research_id=source.research_id,
            source_node_id=source_node_id, target_node_id=target_node_id,
            source_message_id=source_message_id, target_revision=expected_revision,
            target_node_revision=expected_node_revision,
            content=message.content, resource_refs=message.resource_refs,
            result_ids=message.result_ids, assumptions=message.assumptions,
            limitations=message.limitations)
        old = self.research.query('transfer', value.id)
        if old:
            if old != value: raise Conflict('Transfer ID reused with different content')
            return old
        if target.revision != expected_revision: raise Conflict('Target chat revision changed')
        if self.research.node(target_node_id).revision != expected_node_revision:
            raise Conflict('Target node revision changed')
        self._select_message_refs(target_node_id, value.resource_refs)
        copied = ChatMessage(id=value.id, role='transfer', content=value.content,
            resource_refs=value.resource_refs, result_ids=value.result_ids,
            assumptions=value.assumptions, limitations=value.limitations)
        new_target = target.model_copy(update={'revision':target.revision + 1,
            'messages':(*target.messages, copied)})
        with self.research.database.transaction() as tx:
            tx.research.save_chat(new_target, target.revision)
            tx.research.create_transfer(value)
        return value

    def adopt(self, transfer_id):
        old = self.research.query('transfer', transfer_id)
        if old is None: raise KeyError(transfer_id)
        self.read(old.source_node_id)
        self.read(old.target_node_id)
        if self.research.node(old.target_node_id).revision != old.target_node_revision:
            raise Conflict('Target node changed since transfer')
        for ref in old.resource_refs:
            root = self.research.research(old.research_id)
            self.research.scopes.check(self.research.scopes.current(root.scope_id, self.research.owner), ref)
        if old.status == 'adopted': return old
        value = old.model_copy(update={'status':'adopted'})
        with self.research.database.transaction() as tx: tx.research.save_transfer(value, old)
        return value
