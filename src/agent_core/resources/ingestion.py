"""Network value objects, separate from authorization and model tools."""
from ..contracts import Value


class Discovery(Value):
    query: str
    title: str
    url: str
    summary: str


class FetchedSource(Value):
    url: str
    actual_url: str
    media_type: str
    content: bytes
