"""Read-only inspection of admitted tool requests and confirmed receipts."""
import hashlib
import json
import os
import re
import sqlite3

from .ledger import digest


class ToolRecords:
    def __init__(self, engine, run_id):
        self.engine, self.run_id = engine, run_id
        self.binding = engine.ledger.binding(run_id)
        self.root = engine.private_root / engine.workspace_id(self.binding) if self.binding else None

    def text(self, path, expected):
        if not re.fullmatch('[a-f0-9]{64}', expected):
            raise ValueError('Invalid saved artifact identity')
        fd = os.open(path / expected, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(4_194_305)
        if len(raw) > 4_194_304 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Saved tool record hash mismatch')
        return json.loads(raw)

    def requests(self):
        if self.root is None or not (self.root / 'index.sqlite').exists():
            return {}
        if self.root.is_symlink() or (self.root / 'index.sqlite').is_symlink():
            raise ValueError('Invalid saved workspace')
        operations = {o['id']: o for o in self.engine.ledger.operation_history(self.run_id)
                      if o['kind'] in ('tool', 'private')}
        result = {}
        # Never open Workspace here: its constructor creates files and tables.
        connection = sqlite3.connect((self.root / 'index.sqlite').as_uri() + '?mode=ro', uri=True)
        try:
            for name, checksum in connection.execute("SELECT name,digest FROM notes WHERE name LIKE 'transcript_%_request'"):
                frame = self.text(self.root, checksum)
                operation = operations.get(frame.get('operation_id'))
                payload = frame.get('payload', {})
                if operation is None or frame.get('operation') != operation['kind']:
                    continue
                if digest(payload) != operation['request_hash']:
                    continue
                # Private notebooks remain private. Context pagination and tool
                # discovery are ordinary model-visible tool calls.
                if operation['kind'] == 'private' and payload.get('name') not in ('read_context_page', 'search_tools'):
                    continue
                result[payload['call_id']] = dict(operation_id=operation['id'],
                    name=payload['name'], input=payload['arguments'], confirmation=operation['status'])
        finally:
            connection.close()
        return result

    def output(self, operation_id):
        checksum = self.engine.ledger.checkpoint(self.run_id, 'receipt:' + operation_id)
        if checksum is None:
            return None, False
        context = self.root / 'context'
        if context.is_symlink():
            raise ValueError('Invalid saved context directory')
        receipt = self.text(context, checksum)
        if receipt['binding'] != digest(self.binding):
            raise ValueError('Tool receipt belongs to another run')
        return receipt['value']['response']['value'], True
