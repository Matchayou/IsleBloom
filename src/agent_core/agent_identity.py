"""Execution identity is independent of a model, session and method template."""
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Value


class AgentIdentity(Value):
    mode: Literal['assistant', 'temporary', 'specialist']
    id: str = Field(min_length=1)
    instructions: str = ''

    @model_validator(mode='after')
    def temporary_role(self):
        if self.mode == 'temporary' and not self.instructions.strip():
            raise ValueError('Temporary agents require a task-specific role')
        return self
