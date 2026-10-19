"""Immutable content identities; access never follows a content hash."""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import Field
from ..contracts import Value


def identity():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class ResourceRef(Value):
    resource_id: str
    snapshot_id: str
    parse_id: str


class Resource(Value):
    id: str = Field(default_factory=identity)
    owner: str
    name: str
    kind: Literal['upload', 'web', 'discovery', 'note']


class SourceSnapshot(Value):
    id: str = Field(default_factory=identity)
    resource_id: str
    blob_hash: str
    media_type: str
    created_at: datetime = Field(default_factory=now)
    # Dependencies are full version references, never mutable latest pointers.
    dependencies: tuple[ResourceRef, ...] = ()


class ResourceIngestion(Value):
    id: str = Field(default_factory=identity)
    resource_id: str
    snapshot_id: str
    owner: str
    scope_id: str
    run_id: str | None = None
    name: str
    kind: Literal['upload', 'web', 'discovery', 'note']
    at: datetime = Field(default_factory=now)
    url: str | None = None
    actual_url: str | None = None
    query: str | None = None
    title: str | None = None
    summary: str | None = None


class Fragment(Value):
    location: str = Field(min_length=1)
    text: str = Field(min_length=1)
    kind: Literal['text', 'table', 'ocr'] = 'text'


class ParseIssue(Value):
    location: str
    reason: str


class ParseResult(Value):
    id: str = Field(default_factory=identity)
    snapshot_id: str
    parser_key: str
    status: Literal['pending', 'processing', 'ready', 'partial', 'failed'] = 'pending'
    phase: str = 'queued'
    fragments: tuple[Fragment, ...] = ()
    issues: tuple[ParseIssue, ...] = ()
    ocr_pages: tuple[int, ...] = ()
    cached_from: str | None = None


class ParsedContent(Value):
    fragments: tuple[Fragment, ...] = ()
    issues: tuple[ParseIssue, ...] = ()
    ocr_pages: tuple[int, ...] = ()
