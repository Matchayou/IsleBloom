import json
from pydantic import Field
from ..contracts import ToolBinding, ToolResult, Value, fail
from ..resources.snapshots import ResourceRef
from .scopes import ScopeDenied


class ReadSource(Value):
    ref: ResourceRef
    location: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=12000)


class SearchSource(Value):
    ref: ResourceRef
    query: str


class ScopedTool:
    def __init__(self, scopes, scope):
        self.scopes, self.scope = scopes, scope

    def require(self, name):
        self.scopes.valid(self.scope)
        if name not in self.scope.tools:
            raise ScopeDenied('Tool not permitted')

    def read_binding(self):
        async def read(args):
            try:
                self.require('read_source')
                fragments = self.scopes.read(self.scope, args.ref)
                if args.location:
                    fragments = tuple(f for f in fragments if f.location == args.location or
                                      f.location.startswith(args.location + ':'))
                metadata = self.scopes.resources.source_metadata(args.ref)
                value = {'ref': args.ref.model_dump(), **metadata, 'fragments': [f.model_dump() for f in fragments]}
                from .projection import dumps, sha, page
                text = dumps(value)
                if args.offset or len(text) > min(args.limit, self.scope.max_tool_chars):
                    count = min(args.limit, max(1, self.scope.max_tool_chars - 700))
                    value = page(text, sha(text), offset=args.offset, limit=count)
                    value.pop('artifact_id')
                    value.pop('pointer')
                    value.pop('available_pointers')
                    value.update(page_type='source', read_tool='read_source', ref=args.ref.model_dump(), location=args.location,
                        instruction='Continue with read_source using next_cursor. The sha256 is a checksum, not a context artifact ID.')
                    if not value['complete']:
                        value['next_cursor'] = {'ref':args.ref.model_dump(), 'location':args.location,
                            'offset':value['end'], 'limit':args.limit}
                self.require('read_source')
                return ToolResult(value=value)
            except (PermissionError, KeyError, ValueError):
                fail('scope_denied', 'tool', 'Source unavailable in this scope')
        return ToolBinding('read_source', 'Read an immutable source. For large sources, use '
                           'search_source first and pass a page or fragment location here. Follow next_cursor with read_source; source pages are not context artifacts.', ReadSource, read)

    def search_binding(self):
        import re
        async def search(args):
            try:
                self.require('read_source')
                query = args.query.strip()
                if not query or len(query) > 200: raise ValueError('Query must contain 1–200 characters')
                terms = [t for t in re.findall(r'[\w]+',query.casefold()) if len(t) >= 2]
                if not terms: raise ValueError('Query has no searchable terms')
                fragments = self.scopes.read(self.scope,args.ref)
                ranked = sorted(((sum(text.count(term) for term in terms) +
                    (100 if query.casefold() in text else 0),i,f)
                    for i,f in enumerate(fragments) for text in [re.sub(r'\s+',' ',f.text.casefold())]),
                    key=lambda row:(-row[0],row[1]))
                hits = []
                for score,_,fragment in ranked:
                    if score <= 0 or len(hits) >= 4: break
                    text = re.sub(r'\s+',' ',fragment.text)
                    first = min((text.casefold().find(term) for term in terms if term in text.casefold()),default=0)
                    start = max(0,first-300)
                    hits.append({'location':fragment.location,'kind':fragment.kind,
                        'text':text[start:start+800], 'excerpt':start>0 or len(text)>800})
                value = {'ref':args.ref.model_dump(),'query':query,'fragments':hits,
                    **self.scopes.resources.source_metadata(args.ref),
                    'missing':[] if hits else ['No matching located fragment']}
                while len(json.dumps(value,ensure_ascii=False)) > self.scope.max_tool_chars and hits:
                    hits.pop()
                value['missing'] = [] if hits else ['No matching located fragment fits the source result budget']
                self.require('read_source')
                return ToolResult(value=value)
            except (PermissionError,KeyError,ValueError):
                fail('scope_denied','tool','Source search unavailable in this scope')
        return ToolBinding('search_source','Search located fragments in one permitted immutable source version.',SearchSource,search)


class NoteInput(Value):
    title: str
    text: str


def note_binding(guard):
    async def write(args):
        try:
            guard.require('write_note')
            current = guard.scopes.current(guard.scope.id, guard.scope.owner)
            # Conservative provenance: a model cannot claim its output forgot a source.
            dependencies = tuple(dict.fromkeys(current.allowed + current.inherited))
            current, ref = guard.scopes.ingest(current, name=args.title, content=args.text.encode(),
                media_type='text/plain', kind='note', dependencies=dependencies, automatic=True)
            guard.require('write_note')
            return ToolResult(value={'ref': ref.model_dump(), 'scope_id': current.id})
        except (PermissionError, KeyError, ValueError):
            fail('scope_denied', 'tool', 'Note cannot be written in this scope')
    return ToolBinding('write_note', 'Create a separate note in your own workspace. Existing mounted sources cannot be modified.', NoteInput, write)
