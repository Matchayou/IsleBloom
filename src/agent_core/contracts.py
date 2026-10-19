from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Message(Value):
    role: Literal["user", "assistant"]
    content: str


class PreparedInput(Value):
    messages: tuple[Message, ...]
    external_context: dict[str, str] = Field(default_factory=dict)
    projection_exclusions: tuple[dict[str, JsonValue], ...] = ()
    tool_query: str | None = None


class RunLimits(Value):
    max_turns: int | None = Field(default=None, ge=1)
    timeout_seconds: float = Field(default=60, gt=0)
    max_depth: int = Field(default=1, ge=0, le=1)
    max_calls: int = Field(default=8, ge=0)


class RunRequest(Value):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_run_id: str | None = None
    agent_id: str
    input: PreparedInput
    limits: RunLimits = Field(default_factory=RunLimits)
    depth: int = Field(default=0, ge=0)


class RunError(Value):
    code: str
    origin: Literal["core", "engine", "tool", "conversation", "app"]
    message: str
    retryable: bool = False
    cause_id: str | None = None


class Usage(Value):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    complete: bool = True

    def plus(self, other: Usage) -> Usage:
        return Usage(requests=self.requests + other.requests,
                     input_tokens=self.input_tokens + other.input_tokens,
                     output_tokens=self.output_tokens + other.output_tokens,
                     complete=self.complete and other.complete)


class OutputRepair(Value):
    raw_output: str
    issues: tuple[dict, ...]


class RunResult(Value):
    run_id: str
    status: Literal["succeeded", "failed", "cancelled"]
    output: str | dict | None = None
    usage: Usage = Field(default_factory=Usage)
    error: RunError | None = None
    output_repairs: tuple[OutputRepair, ...] = ()


class CoreEvent(Value):
    run_id: str
    parent_run_id: str | None
    tool_call_id: str | None = None
    child_run_id: str | None = None
    phase: Literal["run", "tool", "delegate"]
    status: Literal["started", "completed", "failed", "cancelled"]
    name: str | None = None
    error: RunError | None = None


class ToolResult(Value):
    value: JsonValue


@dataclass(frozen=True)
class ToolBinding:
    name: str
    description: str
    input_type: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolProfile:
    tools: tuple[str, ...]
    instructions: str


@dataclass(frozen=True)
class AgentSpec:
    id: str
    instructions: str
    tools: tuple[ToolBinding, ...] = ()
    delegates: tuple[str, ...] = ()
    output_type: type[BaseModel] | None = None
    model: str = "gpt-4.1-mini"
    strict_output: bool = False
    tool_profiles: dict[str, ToolProfile] = field(default_factory=dict)
    validate_output: Callable[[dict], tuple[dict, ...]] | None = None


class CoreFailure(Exception):
    def __init__(self, error: RunError):
        self.error = error
        super().__init__(error.message)


def fail(code: str, origin: str, message: str, cause_id: str | None = None):
    raise CoreFailure(RunError(code=code, origin=origin, message=message, cause_id=cause_id))


Emit = Callable[[CoreEvent], None]
Invoke = Callable[[str, str, dict | str], Awaitable[ToolResult]]


class EnginePort(Protocol):
    async def execute(self, request: RunRequest, spec: AgentSpec,
                      tools: tuple[ToolBinding, ...], invoke: Invoke) -> RunResult: ...


class ExecutePort(Protocol):
    async def execute(self, request: RunRequest) -> RunResult: ...
