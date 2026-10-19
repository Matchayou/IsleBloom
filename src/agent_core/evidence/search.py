"""One real discovery provider. Search snippets never become source evidence."""
import asyncio
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlsplit
import httpx
from ..resources.ingestion import Discovery, FetchedSource
from ..resources.snapshots import ResourceRef
from ..context.scopes import ScopeDenied


class SourceFetchError(RuntimeError):
    def __init__(self, code, message, requested_url, final_url):
        super().__init__(message)
        self.code, self.requested_url, self.final_url = code, requested_url, final_url


def arxiv_fulltext_url(url):
    parts = urlsplit(url)
    if parts.hostname not in ('arxiv.org', 'www.arxiv.org') or parts.scheme != 'https':
        return None
    match = re.fullmatch(r'/(?:abs|html)/((?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v\d+)?)', parts.path)
    return 'https://arxiv.org/pdf/' + match[1] if match else None


class TinyFishSearchProvider:
    search_endpoint = 'https://api.search.tinyfish.ai'
    fetch_endpoint = 'https://api.fetch.tinyfish.ai'

    def __init__(self, api_key=None, client=None, max_bytes=25_000_000):
        self.api_key = api_key
        self.client = client
        self.max_bytes = max_bytes

    async def _request(self, method, endpoint, **kwargs):
        key = self.api_key if self.api_key is not None else os.environ.get('TINYFISH_API_KEY')
        if not key:
            raise RuntimeError('TINYFISH_API_KEY is required for TinyFish Search and Fetch')
        try:
            async with httpx.AsyncClient(timeout=150, follow_redirects=False, trust_env=False) if self.client is None else _Borrow(self.client) as client:
                async with client.stream(method, endpoint,
                        headers={'X-API-Key':key,'Accept':'application/json'},
                        follow_redirects=False, **kwargs) as response:
                    if not response.is_success:
                        raise RuntimeError(f'TinyFish API returned HTTP {response.status_code}')
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes: raise ValueError('TinyFish response exceeds size limit')
                        chunks.append(chunk)
                    payload = json.loads(b''.join(chunks))
                    if not isinstance(payload,dict): raise ValueError('Invalid TinyFish response')
                    return payload
        except httpx.RequestError as exc:
            raise RuntimeError(f'TinyFish request failed: {type(exc).__name__}') from None

    async def search(self, query):
        if not query.strip() or len(query) > 500:
            raise ValueError('Search query must contain 1–500 characters')
        payload = await self._request('GET',self.search_endpoint,
            params={'query':query,'language':'zh','page':0})
        if not isinstance(payload.get('results'),list): raise ValueError('Invalid TinyFish search results')
        found, seen = [], set()
        for item in payload['results']:
            if not isinstance(item,dict): raise ValueError('Invalid TinyFish search result')
            url = item.get('url', '')
            if not _public_https_url(url) or url in seen: continue
            # arXiv abstract and PDF are distinct discovery refs; neither is evidence
            # until fetched. Prefer the publisher's full-text link for paper research.
            pdf = arxiv_fulltext_url(url)
            if pdf and pdf not in seen:
                seen.add(pdf)
                found.append(Discovery(query=query, title=(item.get('title') or url) + ' [PDF full text]',
                    url=pdf, summary='arXiv full-text candidate; fetch and inspect before citing.'))
                if len(found) == 10: break
            seen.add(url)
            found.append(Discovery(query=query, title=item.get('title') or url,
                url=url, summary=item.get('snippet') or ''))
            if len(found) == 10: break
        return tuple(found)

    async def fetch(self, url):
        if not _public_https_url(url): raise ScopeDenied('Only public HTTPS sources can be fetched')
        payload = await self._request('POST',self.fetch_endpoint,
            json={'urls':[url],'format':'markdown','ttl':0,'per_url_timeout_ms':45000})
        results, errors = payload.get('results'), payload.get('errors')
        if not isinstance(results,list) or not isinstance(errors,list):
            raise ValueError('Invalid TinyFish fetch response')
        if errors:
            code = errors[0].get('error','unknown') if isinstance(errors[0],dict) else 'unknown'
            raise RuntimeError(f'TinyFish fetch failed: {str(code)[:100]}')
        if len(results) != 1 or not isinstance(results[0],dict):
            raise ValueError('TinyFish fetch did not return one source')
        item = results[0]
        final_url = item.get('final_url')
        final_path = urlsplit(final_url).path if isinstance(final_url, str) else ''
        if final_path.rstrip('/') == '/challenge' or (item.get('title') or '').lower().startswith('verifying your browser'):
            raise SourceFetchError('access_verification_required',
                'The source returned a browser verification page, not the requested paper. '
                'Search for the same paper on arXiv and fetch its PDF full text; do not cite the verification page.',
                url, final_url)
        if item.get('url') != url or not await _same_fetched_source(url, item.get('final_url')):
            raise SourceFetchError('source_url_changed', 'Fetched source redirected or changed URL', url, final_url)
        content = item.get('text')
        if item.get('format') != 'markdown' or not isinstance(content,str) or not content.strip():
            raise ValueError('TinyFish returned no usable Markdown content')
        return FetchedSource(url=url, actual_url=url,media_type='text/markdown',
            content=content.encode('utf-8'))


class _Borrow:
    def __init__(self, client): self.client = client
    async def __aenter__(self): return self.client
    async def __aexit__(self, *_): return False


async def _same_fetched_source(url, final_url):
    if final_url == url: return True
    if not isinstance(final_url, str): return False
    try:
        requested, returned = urlsplit(url), urlsplit(final_url)
        address = ipaddress.ip_address(returned.hostname or '')
        if (not address.is_global or returned.scheme != 'https' or
                returned.port not in (None, 443) or returned.username or returned.password or
                (returned.path, returned.query, returned.fragment) !=
                (requested.path, requested.query, requested.fragment)):
            return False
        resolved = await asyncio.get_running_loop().getaddrinfo(
            requested.hostname, 443, type=socket.SOCK_STREAM)
        return str(address) in {entry[4][0] for entry in resolved}
    except (ValueError, OSError):
        return False


def _public_https_url(url):
    if not isinstance(url,str): return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower().rstrip('.')
        try:
            if not ipaddress.ip_address(host).is_global: return False
        except ValueError:
            pass
        return (parts.scheme == 'https' and bool(parts.hostname) and not parts.username and
                not parts.password and parts.port in (None, 443) and
                host != 'localhost' and not host.endswith(('.local','.localhost')))
    except ValueError:
        return False


class SearchService:
    def __init__(self, research, provider=None):
        self.research = research
        self.provider = provider or TinyFishSearchProvider()

    async def discover(self, research_id, query):
        root = self.research.research(research_id)
        scope = self.research.scopes.current(root.scope_id, self.research.owner)
        values = await self.provider.search(query)
        self.research.scopes.valid(scope)
        scope = self.research.scopes.current(root.scope_id, self.research.owner)
        results = []
        for value in values:
            self.research.scopes.valid(scope)
            scope, ref = self.research.scopes.ingest(scope, content=value.summary.encode(),
                name=value.title, media_type='text/plain', kind='discovery', automatic=True,
                url=value.url, query=query, title=value.title, summary=value.summary)
            results.append({'ref':ref, 'title':value.title, 'url':value.url,
                'summary':value.summary, 'kind':'discovery_only'})
        return results

    async def fetch(self, research_id, discovery_ref):
        ref = ResourceRef.model_validate(discovery_ref)
        root = self.research.research(research_id)
        scope = self.research.scopes.current(root.scope_id, self.research.owner)
        self.research.scopes.check(scope, ref)
        resource, _, _ = self.research.scopes.resources.describe(ref)
        if resource.kind != 'discovery': raise ValueError('Fetch requires a discovery reference')
        ingestions = [entry for entry in self.research.scopes.resources.repository.ingestions(scope.id)
            if entry.resource_id == ref.resource_id and entry.snapshot_id == ref.snapshot_id]
        if len(ingestions) != 1 or not ingestions[0].url:
            raise ScopeDenied('Discovery URL unavailable')
        fetched = await self.provider.fetch(ingestions[0].url)
        self.research.scopes.check(scope, ref)
        if fetched.actual_url != ingestions[0].url:
            raise RuntimeError('Fetched source URL changed')
        scope = self.research.scopes.current(root.scope_id, self.research.owner)
        scope, body_ref = self.research.scopes.ingest(scope, content=fetched.content,
            name=ingestions[0].title or fetched.actual_url, media_type=fetched.media_type,
            kind='web', automatic=True, url=ingestions[0].url, actual_url=fetched.actual_url,
            query=ingestions[0].query, title=ingestions[0].title)
        _, _, parsed = self.research.scopes.resources.describe(body_ref)
        return {'ref':body_ref, 'parse':parsed, 'url':fetched.actual_url}
