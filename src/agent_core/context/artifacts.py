"""Immutable, session-local context artifacts; only admitted hashes are addressable."""
import os
import tempfile
from contextvars import ContextVar
from pathlib import Path
from pydantic import Field
from ..contracts import Value, ToolBinding, ToolResult
from .projection import dumps, sha, page, InvalidContextPointer, InvalidContextRange


current_artifacts = ContextVar('current_context_artifacts', default=None)

def after_complete_read(value, callback):
    artifacts = current_artifacts.get()
    if artifacts is None:
        callback()  # Explicit scripted executions have no model projection.
    else:
        artifacts.callbacks[sha(dumps(value))] = callback


class ContextArtifactUnavailable(ValueError):
    pass


class ReadContextPage(Value):
    artifact_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    pointer: str = ''
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=12000)


class ContextArtifacts:
    def __init__(self, workspace, validate, record_read):
        self.workspace, self.validate, self.record_read = workspace, validate, record_read
        self.callbacks, self.ranges = {}, {}
        self.root = workspace.root / 'context'
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink(): raise ValueError('Context root cannot be a symlink')
        workspace.db.execute('CREATE TABLE IF NOT EXISTS context_artifacts (id TEXT PRIMARY KEY)')
        workspace.db.commit()

    def save_text(self, text):
        self.validate()
        key = sha(text)
        fd, temporary = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(text.encode()); f.flush(); os.fsync(f.fileno())
            os.replace(temporary, self.root/key)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
            with self.workspace.db:
                self.workspace.db.execute('INSERT OR IGNORE INTO context_artifacts VALUES (?)', (key,))
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        self.validate()
        return key

    def load_text(self, artifact_id):
        """Trusted reader; same immutable hash and authorization checks, no model read claim."""
        self.validate()
        if not self.workspace.db.execute('SELECT 1 FROM context_artifacts WHERE id=?',(artifact_id,)).fetchone():
            raise ContextArtifactUnavailable('Context artifact is not available in this session')
        fd=os.open(self.root/artifact_id,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'r') as stream:text=stream.read()
        if sha(text)!=artifact_id:raise ValueError('Context artifact hash mismatch')
        self.validate()
        return text

    def read(self, args):
        self.validate()
        if not self.workspace.db.execute('SELECT 1 FROM context_artifacts WHERE id=?',(args.artifact_id,)).fetchone():
            raise ContextArtifactUnavailable('Context artifact is not available in this session')
        try:
            fd=os.open(self.root/args.artifact_id, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise ContextArtifactUnavailable('Context artifact content is unavailable') from exc
        with os.fdopen(fd,'r') as f: text=f.read()
        if sha(text)!=args.artifact_id: raise ValueError('Context artifact hash mismatch')
        result=page(text,args.artifact_id,args.pointer,args.offset,args.limit)
        self.validate()
        self.record_read(result)
        if not args.pointer:
            ranges = self.ranges.setdefault(args.artifact_id, [])
            ranges.append((result['start'],result['end']))
            covered=0
            for start,end in sorted(ranges):
                if start > covered: break
                covered=max(covered,end)
            if covered == result['total_chars'] and args.artifact_id in self.callbacks:
                self.callbacks.pop(args.artifact_id)()
        return result

    def deliver(self, value, maximum=8000):
        # Full authoritative tool result is durable BEFORE a preview can be returned.
        text=dumps(value)
        key=self.save_text(text)
        # Core appends call-budget metadata after the business handler registers
        # its receipt. Associate that receipt with the final delivered artifact.
        body = {k:v for k,v in value.items() if k != 'remaining_tool_calls'} if isinstance(value,dict) else value
        body_key = sha(dumps(body))
        if body_key != key and body_key in self.callbacks:
            self.callbacks[key] = self.callbacks.pop(body_key)
        if len(text)<=maximum:
            self.record_read({'artifact_id':key,'pointer':'','start':0,'end':len(text),
                'total_chars':len(text),'complete':True,'sha256':key})
            if key in self.callbacks: self.callbacks.pop(key)()
            return value
        return self.read(ReadContextPage(artifact_id=key, limit=maximum))

    def binding(self):
        async def read(args):
            try: return ToolResult(value=self.read(args))
            except ContextArtifactUnavailable:
                return ToolResult(value={'status':'invalid_input','executed':False,
                    'error':'context_artifact_unavailable',
                    'instruction':'This context artifact is unavailable in this session. Use an artifact_id returned by a context page in this session. For source pages use read_source with their ref and next_cursor, not their checksum. If no valid reference is available, report the evidence gap and continue with available evidence.'})
            except InvalidContextRange as exc:
                return ToolResult(value={'status':'invalid_input','executed':False,
                    'total_chars':exc.total_chars,
                    'instruction':'Offset exceeds this fixed artifact/pointer. Start at offset 0, then use its own returned next_cursor; never reuse a cursor from another artifact or pointer.',
                    'restart_cursor':{'artifact_id':args.artifact_id,'pointer':args.pointer,'offset':0,'limit':args.limit}})
            except InvalidContextPointer:
                return ToolResult(value={'status':'invalid_input','executed':False,
                    'instruction':'JSON pointer does not exist. Read the containing object or use the available_pointers from the preceding page; do not guess field names.'})
        return ToolBinding('read_context_page',
            'Read a fixed context artifact by hash and JSON pointer. Follow next_cursor until complete; concatenate text ranges to reconstruct the exact original JSON. No arbitrary paths.',
            ReadContextPage, read)
