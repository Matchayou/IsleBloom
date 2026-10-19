import json
from ..contracts import Value, ToolBinding, ToolResult, fail
from .tool_guard import ScopedTool
from .scopes import ScopeDenied


class SearchInput(Value):
    query: str


class FetchInput(Value):
    url: str


class NetworkTools(ScopedTool):
    def __init__(self, scopes, scope, provider):
        super().__init__(scopes, scope)
        self.provider = provider

    def current(self):
        self.scopes.valid(self.scope)
        return self.scopes.current(self.scope.id, self.scope.owner)

    def discovery(self, value):
        # Register before return, including rejected candidates. Never grant rejected information.
        current = self.current()
        metadata = dict(name=value.title, content=value.summary.encode(), media_type='text/plain', kind='discovery',
            query=value.query, title=value.title, url=value.url, summary=value.summary)
        if value.url not in current.web_urls:
            self.scopes.resources.ingest(owner=current.owner, scope_id=current.id, **metadata)
            return None
        self.require('search_sources')
        current, ref = self.scopes.ingest(current, automatic=True, **metadata)
        return {'ref': ref.model_dump(), 'kind': 'discovery_only', **value.model_dump()}

    async def search(self, args):
        self.require('search_sources')
        values = []
        async for candidate in self.provider.search(args.query, self.scope.web_urls):
            value = self.discovery(candidate)
            if value is not None:
                values.append(value)
            if len(values) >= 10:
                break
        self.require('search_sources')
        if len(json.dumps(values, ensure_ascii=False)) > self.scope.max_tool_chars:
            raise ScopeDenied('Discovery budget exceeded')
        return ToolResult(value=values)

    async def fetch(self, args):
        self.require('fetch_source')
        if args.url not in self.scope.web_urls:
            raise ScopeDenied('URL not approved')
        fetched = await self.provider.fetch(args.url, self.scope.web_urls)
        current = self.current()
        if fetched.actual_url not in current.web_urls:
            raise ScopeDenied('Actual URL not approved')
        current, ref = self.scopes.ingest(current, content=fetched.content, name=args.url,
            media_type=fetched.media_type, kind='web', url=args.url, actual_url=fetched.actual_url,
            automatic=True)
        # Ingestion success is distinct from parse success. A failed parse stays visible to the user.
        _, _, parsed = self.scopes.resources.describe(ref)
        value = {'ref': ref.model_dump(), 'parse_status': parsed.status,
                 'missing': [i.model_dump() for i in parsed.issues]}
        if parsed.status in ('ready', 'partial'):
            value['fragments'] = [f.model_dump() for f in self.scopes.read(current, ref)]
        self.require('fetch_source')
        if len(json.dumps(value, ensure_ascii=False)) > self.scope.max_tool_chars:
            raise ScopeDenied('Fetched content budget exceeded')
        return ToolResult(value=value)

    def bindings(self):
        def wrap(name, fn, schema, description):
            async def run(args):
                try:
                    return await fn(args)
                except (PermissionError, KeyError, ValueError):
                    fail('scope_denied', 'tool', 'Network source unavailable in this scope')
            return ToolBinding(name, description, schema, run)
        values = []
        if 'search_sources' in self.scope.tools:
            values.append(wrap('search_sources', self.search, SearchInput, 'Search the approved URL catalog. Returns discovery summaries, not verified source bodies.'))
        if 'fetch_source' in self.scope.tools:
            values.append(wrap('fetch_source', self.fetch, FetchInput, 'Fetch an approved exact URL, store its immutable version, and read successfully parsed content.'))
        return values
