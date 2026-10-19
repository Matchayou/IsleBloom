from datetime import datetime, timezone
from typing import Literal
from pydantic import Field, field_validator, model_validator
from ..contracts import Value
from ..resources.snapshots import identity


class MemoryEntry(Value):
    id: str = Field(default_factory=identity)
    version: int = 1
    owner: str
    publication_id: str | None = None
    scope: Literal['user', 'project', 'specialist'] = 'user'
    scope_id: str | None = None
    kind: Literal['preference', 'experience']
    content: str
    source: str
    status: Literal['draft', 'confirmed'] = 'draft'
    expires_at: datetime | None = None

    @model_validator(mode='after')
    def namespace(self):
        if (self.scope == 'user') != (self.scope_id is None):
            raise ValueError('User memory has no scope_id; project and specialist memory require scope_id')
        if self.scope_id is not None and not self.scope_id.strip():
            raise ValueError('Memory scope_id required')
        return self

    @field_validator('expires_at')
    @classmethod
    def aware_expiry(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError('Expiration must include a time zone')
        return value

    def available(self):
        return self.status == 'confirmed' and (self.expires_at is None or self.expires_at > datetime.now(timezone.utc))


class MemoryRef(Value):
    id: str
    version: int


class CreateMemory(Value):
    kind: Literal['preference', 'experience']
    content: str
    source: str
    scope: Literal['user', 'project', 'specialist'] = 'user'
    scope_id: str | None = None
    confirmed: bool = False
    expires_at: datetime | None = None
