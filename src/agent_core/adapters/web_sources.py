"""Bounded search of explicitly approved web sources; no unrestricted web crawl."""
from urllib.parse import urlsplit
import httpx
from bs4 import BeautifulSoup
from ..resources.ingestion import Discovery, FetchedSource


class HttpSources:
    def __init__(self, max_bytes=5_000_000):
        self.max_bytes = max_bytes

    async def fetch(self, url, allowed_urls):
        parts = urlsplit(url)
        if url not in allowed_urls or parts.scheme not in ('https', 'http') or parts.username or parts.password:
            raise PermissionError('URL not approved')
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            async with client.stream('GET', url) as response:
                response.raise_for_status()  # Redirect destinations require their own explicit authorization.
                if response.is_redirect:
                    raise PermissionError('Redirect requires explicit destination approval')
                chunks, size = [], 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > self.max_bytes:
                        raise ValueError('Source byte budget exceeded')
                    chunks.append(part)
                media = response.headers.get('content-type', 'application/octet-stream').split(';')[0]
                return FetchedSource(url=url, actual_url=str(response.url), media_type=media, content=b''.join(chunks))

    async def search(self, query, allowed_urls):
        # Search is deliberately limited to the user-approved source catalog.
        for url in allowed_urls:
            fetched = await self.fetch(url, allowed_urls)
            if fetched.media_type not in ('text/html', 'text/plain', 'text/markdown'):
                continue
            text = fetched.content.decode('utf-8', errors='strict')
            if fetched.media_type == 'text/html':
                soup = BeautifulSoup(text, 'html.parser')
                title = soup.title.get_text(' ', strip=True) if soup.title else url
                for item in soup(['script', 'style', 'nav']):
                    item.decompose()
                text = soup.get_text(' ', strip=True)
            else:
                title = url
            # Relevance is separate from authorization; an unmatched query returns nothing.
            terms = query.casefold().split()
            positions = [text.casefold().find(term) for term in terms if term in text.casefold()]
            if positions:
                start = max(0, min(positions) - 80)
                yield Discovery(query=query, title=title, url=url, summary=text[start:start + 800])
