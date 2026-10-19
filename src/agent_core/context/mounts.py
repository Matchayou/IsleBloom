from typing import Literal
from pydantic import Field, model_validator
from ..contracts import Value, PreparedInput
from .scopes import RunScope
from ..resources.snapshots import ResourceRef, identity
from ..memories.models import MemoryRef
from ..agent_identity import AgentIdentity


class ContextMount(Value):
    scope_id: str
    scope_version: int
    ref: ResourceRef
    mode: Literal['read_only'] = 'read_only'


class ResultMount(Value):
    """Opaque immutable result identity; the providing service validates access."""
    result_id: str
    run_id: str
    resource_refs: tuple[ResourceRef, ...] = ()
    missing: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    mode: Literal['read_only'] = 'read_only'


class ContextSnapshot(Value):
    id: str = Field(default_factory=identity)
    run_id: str
    conversation_id: str | None = None
    scope_id: str
    scope_version: int
    scope: RunScope
    mounts: tuple[ContextMount, ...]
    result_mounts: tuple[ResultMount, ...] = ()
    input: PreparedInput
    skill_catalog: tuple[dict, ...] = ()
    memory_catalog: tuple[MemoryRef, ...] = ()
    memory_refs: tuple[MemoryRef, ...] = ()
    selection: dict = Field(default_factory=dict)
    agent: AgentIdentity | None = None
    mode: Literal['assistant', 'execution', 'planning', 'discussion', 'consultation', 'learning', 'memory_capture'] = 'execution'

    @model_validator(mode='after')
    def matching_scope(self):
        if self.scope.id != self.scope_id or self.scope.version != self.scope_version:
            raise ValueError('Context scope identity mismatch')
        if any(m.scope_id != self.scope_id or m.scope_version != self.scope_version for m in self.mounts):
            raise ValueError('Mount belongs to another scope version')
        return self
