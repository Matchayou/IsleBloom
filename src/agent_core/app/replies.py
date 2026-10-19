"""Chat command admission and reconnectable, transient SSE previews."""
import asyncio
import json

from starlette.responses import StreamingResponse

from ..contracts import RunLimits
from ..controlled.streaming import reply_preview
from ..execution.ports import Conflict
from ..context.scopes import ScopeDenied
from ..research.models import NodeCommand

CHAT_TIMEOUT_SECONDS = 900
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted', 'blocked', 'save_failed'}


class Preview:
    def __init__(self):
        self.value = {'text': '', 'phase': 'thinking'}
        self.changed = asyncio.Event()

    def update(self, **value):
        self.value = {**self.value, **value}
        self.changed.set()


class ChatReplies:
    def __init__(self, service, chat, tasks):
        self.service, self.chat, self.tasks = service, chat, tasks
        self.active = {}
        self.admission = asyncio.Lock()

    def record(self, command_id, node_id):
        self.chat.read(node_id)
        record = self.service.query('command', command_id)
        if record is None: raise KeyError(command_id)
        if record.node_id != node_id or record.command.kind != 'chat_reply':
            raise ScopeDenied('Reply belongs to another node')
        if record.run_id and record.status == 'accepted':
            binding = self.service.query('run', record.run_id)
            self.service.validate_binding(binding)
        return record

    def status(self, record):
        run = self.service.ledger.get(record.run_id) if record.run_id else None
        status = run.status if run else record.status
        reason = run.result.error.code if run and run.result and run.result.error else record.reason
        if status == 'saving' and record.reason == 'save_failed': status = 'save_failed'
        return {'status': status, 'reason': reason, 'command_id': record.command.command_id,
                'run_id': record.run_id, 'timeout_seconds': record.command.limits.timeout_seconds}

    async def start(self, data):
        async with self.admission:
            return await self._start(data)

    async def _start(self, data):
        current = self.chat.read(data['node_id'])
        command_id = data['command_id']
        old = self.service.query('command', command_id)
        if old:
            # Re-delivery returns the frozen command, never rebuilds server-owned revisions.
            if (old.command.kind != 'chat_reply' or old.node_id != current.node_id or
                    old.command.chat_revision != data['expected_revision']):
                raise Conflict('Command ID reused with different content')
            self.record(command_id, current.node_id)
            return old
        command = NodeCommand(command_id=command_id, research_id=current.research_id,
            node_id=current.node_id, kind='chat_reply',
            expected_revision=self.service.node(current.node_id).revision,
            chat_revision=data['expected_revision'],
            limits=RunLimits(timeout_seconds=CHAT_TIMEOUT_SECONDS, max_calls=12))
        preview = Preview()
        self.active[command_id] = preview

        async def execute():
            token = reply_preview.set((current.node_id, preview.update))
            try:
                return await self.service.submit(command)
            finally:
                reply_preview.reset(token)
                self.active.pop(command_id, None)
                preview.changed.set()

        task = asyncio.create_task(execute())
        self.tasks.add(task)
        def finished(t):
            self.tasks.discard(t)
            if not t.cancelled(): t.exception()
        task.add_done_callback(finished)
        await asyncio.sleep(0)
        return task.result() if task.done() else self.service.query('command', command_id)

    def stream(self, command_id, node_id):
        self.record(command_id, node_id)  # Check access before sending HTTP headers.

        async def events():
            last = None
            while True:
                preview = self.active.get(command_id)
                if preview: preview.changed.clear()
                try:
                    record = self.record(command_id, node_id)
                    value = {**(preview.value if preview else {'text': '', 'phase': 'thinking'}),
                             **self.status(record)}
                except (ScopeDenied, PermissionError, Conflict, KeyError):
                    yield 'event: revoked\ndata: {}\n\n'
                    return
                body = json.dumps(value, ensure_ascii=False)
                if body != last:
                    yield f'data: {body}\n\n'
                    last = body
                    # Coalesce fast token bursts without delaying the first snapshot.
                    await asyncio.sleep(.04)
                if value['status'] in TERMINAL: return
                # A missing live worker cannot stream forever after a process restart.
                if preview is None:
                    yield 'data: ' + json.dumps({**value, 'status': 'interrupted'}) + '\n\n'
                    return
                try:
                    await asyncio.wait_for(preview.changed.wait(), timeout=1)
                except TimeoutError:
                    yield ': keep-alive\n\n'

        return StreamingResponse(events(), media_type='text/event-stream',
            headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no',
                     'X-Content-Type-Options': 'nosniff'})
