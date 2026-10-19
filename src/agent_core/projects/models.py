"""Project navigation is separate from research execution and scope grants."""
from typing import Literal
from pydantic import Field
from ..contracts import Value
from ..resources.snapshots import identity


class Project(Value):
    id: str = Field(default_factory=identity)
    owner: str
    name: str = Field(min_length=1)
    revision: int = 0


class MainTask(Value):
    research_id: str
    project_id: str
    title: str = Field(min_length=1)
    revision: int = 0


class Folder(Value):
    id: str = Field(default_factory=identity)
    project_id: str
    parent_id: str | None = None
    name: str = Field(min_length=1)
    revision: int = 0


class Placement(Value):
    project_id: str
    kind: Literal['research', 'node', 'task']
    object_id: str
    folder_id: str | None = None
    title: str | None = None
    revision: int = 0

