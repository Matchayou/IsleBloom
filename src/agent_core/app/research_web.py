"""Research web discovery and immutable body acquisition in the active run."""
import json
import logging
from pydantic import Field
from ..contracts import Value, ToolBinding, ToolResult, fail
from ..context.scopes import ScopeDenied
from ..context.tool_guard import ScopedTool, ReadSource, SearchSource
from ..resources.snapshots import ResourceRef
from ..evidence.search import SourceFetchError


logger = logging.getLogger(__name__)


class WebSearch(Value):
    query: str = Field(min_length=1, max_length=500)


class WebFetch(Value):
    ref: ResourceRef
    query: str | None = Field(default=None, min_length=2, max_length=200,
        description='For full papers or long documents, provide a focused phrase to return located evidence excerpts immediately after saving the complete body. Avoid spending the tool budget on repeated abstract reads or serialized whole-document pages.')


def web_bindings(research, scope):
    scopes = research.scopes

    def current(name):
        scopes.valid(scope)
        if name not in scope.tools:
            raise ScopeDenied('Web tool not permitted')
        binding = research.query('run', scopes.running.get())
        research.validate_binding(binding)
        node = research.node(binding.node_id)
        if node.scope_id != scope.id:
            raise ScopeDenied('Web tool belongs to another node')
        return binding.run_id, node.research_id

    return source_bindings(research, scope, current)


def source_bindings(research, scope, current):
    """Use the same saved discovery/fetch service for node and expert runs."""
    scopes = research.scopes

    async def search(args):
        _, research_id = current('search_web')
        values = await research.search.discover(research_id, args.query)
        current('search_web')
        results = [{'ref': v['ref'].model_dump(), 'title': v['title'][:300],
                    'url': v['url'], 'summary': v['summary'][:600], 'kind': 'discovery_only'} for v in values]
        while len(json.dumps(results, ensure_ascii=False)) > scope.max_tool_chars and results:
            results.pop()
        return ToolResult(value={'results': results, 'instruction': 'Fetch relevant results with fetch_web before citing facts. Summaries are not evidence.'})

    async def fetch(args):
        run_id, research_id = current('fetch_web')
        root = research.research(research_id)
        entries = scopes.resources.repository.ingestions(root.scope_id)
        if not any(e.run_id == run_id and e.kind == 'discovery' and
                   e.resource_id == args.ref.resource_id and e.snapshot_id == args.ref.snapshot_id for e in entries):
            raise ScopeDenied('Fetch requires a search result from this run')
        root_scope = scopes.current(root.scope_id, scope.owner)
        permitted = [ref for ref in (*root_scope.allowed, *root_scope.inherited)
            if ref.resource_id == args.ref.resource_id and ref.snapshot_id == args.ref.snapshot_id]
        if not permitted:
            raise ScopeDenied('Discovery authorization was revoked')
        if args.ref not in permitted:
            for ref in permitted: scopes.check(root_scope, ref)
            return ToolResult(value={'status':'invalid_input','executed':False,
                'error':'discovery_ref_mismatch','available_discovery_refs':[ref.model_dump() for ref in permitted],
                'instruction':'The parse_id differs from the authorized search result. Copy the entire discovery ref exactly and retry fetch_web; do not reconstruct UUID fields.'})
        value = await research.search.fetch(research_id, args.ref)
        # A failed parse remains queryable but cannot be used as evidence.
        if value['parse'].status not in ('ready', 'partial'):
            return ToolResult(value={'status': 'unavailable', 'url': value['url'],
                                     'missing': [i.reason for i in value['parse'].issues]})
        target = scopes.current(scope.id, scope.owner)
        source = scopes.current(root.scope_id, scope.owner)
        scopes.acquire(target, source, value['ref'])
        current('fetch_web')
        guard = ScopedTool(scopes, scope)
        body = (await guard.search_binding().handler(SearchSource(ref=value['ref'],query=args.query))
                if args.query else await guard.read_binding().handler(ReadSource(ref=value['ref'])))
        return ToolResult(value={**body.value, 'kind': 'web_body', 'url': value['url'], 'parse_status': value['parse'].status,
                                 'instruction': 'Use THIS body ref for citations, search_source and read_source. The earlier search discovery ref cannot be read or cited.',
                                 'issues': [i.model_dump() for i in value['parse'].issues]})

    def wrap(name, schema, handler, description):
        async def run(args):
            try:
                return await handler(args)
            except (PermissionError, KeyError) as exc:
                logger.warning('%s denied for active run: %s', name, exc)
                fail('scope_denied', 'tool', 'Web source unavailable in this run')
            except SourceFetchError as exc:
                return ToolResult(value={'status': 'unavailable', 'code': exc.code,
                    'error': str(exc), 'requested_url': exc.requested_url, 'final_url': exc.final_url,
                    'instruction': 'Search for an official full-text copy of the same paper, prefer arXiv PDF over abs. Fetch a new discovery ref; never treat an access verification page as evidence.'})
            except (RuntimeError, ValueError) as exc:
                # Provider errors are explicit tool observations, allowing another source.
                return ToolResult(value={'status': 'unavailable', 'error': str(exc),
                                         'instruction': 'Do not invent source content. Try another source or report this limitation.'})
        return ToolBinding(name, description, schema, run)

    candidates = (
        ('search_web', WebSearch, search, 'Search the public web for current information. For papers, prefer PDF full-text candidates over abstract pages. Returns discovery references and URLs; use fetch_web to read bodies before answering.'),
        ('fetch_web', WebFetch, fetch, 'Fetch a discovery ref returned by search_web in this run. Saves the complete immutable body. For papers and long docs, supply query to return located excerpts in this same call; use search_source/read_source on the returned body ref for further evidence.'),
    )
    return tuple(wrap(*v) for v in candidates if v[0] in scope.tools)
