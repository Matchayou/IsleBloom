"""Bounded discovery inside a frozen source authorization directory."""
from typing import Literal
from pydantic import Field
from ..contracts import Value, ToolBinding, ToolResult
from .retrieval import rank


class SearchDirectory(Value):
    category: Literal['sources'] = 'sources'
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=6, ge=1, le=10)


def source_directory(selector, scopes, scope, refs, validate):
    async def search(args):
        validate()
        candidates = selector.source_candidates(scopes, scope, refs)
        matches = rank(args.query, [r['search_text'] for r in candidates], limit=args.limit)
        return ToolResult(value={'category': 'sources', 'items': [candidates[i]['value'] for i, _ in matches],
            'total_authorized': len(candidates),
            'instruction': 'Read selected refs with search_source or read_source. This directory cannot grant new sources. Additional inputs require an explicitly authorized handoff or a new run.'})
    return ToolBinding('search_context', '检索本任务获准资料目录。Search authorized source metadata and parsed text; return bounded refs for source tools.', SearchDirectory, search)
