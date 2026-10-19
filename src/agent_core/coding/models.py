"""Operator admitted code capabilities; no commands or host paths from models."""
from typing import Literal
from pydantic import Field, model_validator
from ..contracts import Value

CODING_TOOLS = frozenset({'read_code', 'write_code', 'code_status', 'code_diff',
                          'test_code', 'checkpoint_code', 'freeze_code'})

class CodingContract(Value):
    repository_id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    baseline: str = Field(pattern=r'^[0-9a-f]{40}$')
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    test_profiles: tuple[str, ...] = Field(min_length=1)
    candidate_workspaces: tuple[str, ...] = ()
    test_profile_hashes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def paths(self):
        from .paths import checked_name
        for name in (*self.read_paths, *self.write_paths): checked_name(name)
        if len(set(self.test_profiles)) != len(self.test_profiles):
            raise ValueError('Duplicate test profile')
        return self

class CodeCandidate(Value):
    workspace_id: str
    repository_id: str
    baseline: str
    commit: str
    tree: str
    content_sha256: str
    files: dict[str, str]
    tests: tuple[dict, ...]
    complete: Literal[True] = True
