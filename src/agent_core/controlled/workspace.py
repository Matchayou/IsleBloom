"""Untrusted private notebook. Never used as authorization or publication state."""
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from pydantic import Field
from ..contracts import Value, ToolBinding, ToolResult


class WriteNote(Value):
    name: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    text: str = Field(max_length=256_000)


class ReadNote(Value):
    name: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')


class Workspace:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError('Private root cannot be a symlink')
        index = self.root / 'index.sqlite'
        if index.is_symlink():
            raise ValueError('Private index cannot be a symlink')
        self.db = sqlite3.connect(index)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS notes (name TEXT PRIMARY KEY, digest TEXT NOT NULL)')
        self.db.commit()

    def save(self, name, text):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name):
            raise ValueError('Invalid note name')
        raw = text.encode()
        if len(raw) > 4_194_304:
            raise ValueError('Private record too large')
        digest = hashlib.sha256(raw).hexdigest()
        target = self.root / digest
        # Content-addressed files: failed index commit leaves an orphan, never a dangling ref.
        fd, temp = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
            dirfd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(dirfd)
            finally: os.close(dirfd)
            with self.db:
                self.db.execute('INSERT INTO notes VALUES (?,?) ON CONFLICT(name) DO UPDATE SET digest=excluded.digest', (name, digest))
        finally:
            if os.path.exists(temp): os.unlink(temp)
        return {'name': name, 'sha256': digest, 'bytes': len(raw), 'saved': True}

    def read(self, name):
        row = self.db.execute('SELECT digest FROM notes WHERE name=?', (name,)).fetchone()
        if row is None: raise ValueError('Private record not found')
        digest = row[0]
        if not re.fullmatch('[a-f0-9]{64}', digest): raise ValueError('Invalid private index')
        fd = os.open(self.root / digest, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream: raw = stream.read(4_194_305)
        if len(raw) > 4_194_304 or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Private record hash mismatch')
        return {'name': name, 'sha256': digest, 'text': raw.decode(), 'complete': True}

    def close(self):
        self.db.close()

    def bindings(self):
        async def write(args):
            if args.name == 'context_activity': raise ValueError('Reserved runtime record')
            return ToolResult(value=self.save(args.name, args.text))
        async def read(args): return ToolResult(value=self.read(args.name))
        return (ToolBinding('write_private_note', 'Save a private draft; this does not publish evidence.', WriteNote, write),
                ToolBinding('read_private_note', 'Read a saved private draft and verify its hash.', ReadNote, read))
