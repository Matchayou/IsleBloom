from __future__ import annotations

import asyncio
from copy import deepcopy
from contextvars import ContextVar

from pydantic import ValidationError

from .contracts import (
    AgentSpec, CoreEvent, CoreFailure, Emit, EnginePort, Message, PreparedInput,
    RunError, RunRequest, RunResult, ToolBinding, ToolResult, Usage, Value, fail,
)


execution_deadline = ContextVar('execution_deadline', default=None)


class AgentCatalog:
    def __init__(self, specs: list[AgentSpec]):
        self._specs = {s.id: s for s in specs}
        if len(self._specs) != len(specs):
            raise ValueError("Duplicate agent ID")
        for spec in specs:
            names = [t.name for t in spec.tools]
            if len(set(names)) != len(names) or "delegate" in names:
                raise ValueError("Duplicate or reserved tool name")
            if any(target not in self._specs for target in spec.delegates):
                raise ValueError("Unknown registered delegate")

    def get(self, agent_id: str) -> AgentSpec:
        if agent_id not in self._specs:
            fail("unknown_agent", "core", "Agent is not registered")
        return self._specs[agent_id]


class DelegateInput(Value):
    agent_id: str
    input: str


class CoreRunner:
    def __init__(self, engine: EnginePort, catalog: AgentCatalog, emit: Emit = lambda _: None):
        self.engine, self.catalog, self.emit = engine, catalog, emit

    async def execute(self, request: RunRequest) -> RunResult:
        child_usage = Usage()

        def event(phase, status, **kwargs):
            self.emit(CoreEvent(run_id=request.run_id, parent_run_id=request.parent_run_id,
                                phase=phase, status=status, **kwargs))

        event("run", "started")
        try:
            if request.depth > request.limits.max_depth:
                fail("depth_limit", "core", "Delegation depth exceeded")
            spec = self.catalog.get(request.agent_id)
            lock = asyncio.Lock()
            calls = 0
            active_call_id = None
            completed = {}
            stopped: CoreFailure | None = None

            async def delegate(args: DelegateInput) -> ToolResult:
                nonlocal child_usage
                if args.agent_id not in spec.delegates:
                    fail("unauthorized_delegate", "core", "Delegate is not approved")
                child = RunRequest(agent_id=args.agent_id, parent_run_id=request.run_id,
                                   input=PreparedInput(messages=(Message(role="user", content=args.input),)),
                                   limits=request.limits, depth=request.depth + 1)
                event("delegate", "started", child_run_id=child.run_id, tool_call_id=active_call_id)
                result = await self.execute(child)
                child_usage = child_usage.plus(result.usage)
                event("delegate", "completed" if result.status == "succeeded" else result.status,
                      child_run_id=child.run_id, tool_call_id=active_call_id, error=result.error)
                if result.status == "cancelled":
                    raise asyncio.CancelledError
                if result.status != "succeeded":
                    raise CoreFailure(result.error.model_copy(update={"cause_id": child.run_id}))
                return ToolResult(value=result.output)

            bindings = spec.tools
            if spec.delegates:
                bindings += (ToolBinding("delegate", "Call an approved expert with explicit independent input: "
                                         + ", ".join(spec.delegates), DelegateInput, delegate),)
            by_name = {t.name: t for t in bindings}

            async def invoke(name: str, call_id: str, arguments: dict | str) -> ToolResult:
                nonlocal calls, stopped, active_call_id
                async with lock:
                    if stopped:
                        raise stopped
                    if call_id in completed:
                        previous_name, previous_args, result = completed[call_id]
                        if name != previous_name or arguments != previous_args:
                            fail("tool_call_conflict", "core", "Tool call ID reused with different input")
                        return result
                    event("tool", "started", name=name, tool_call_id=call_id)
                    try:
                        if name not in by_name:
                            fail("unknown_tool", "tool", "Tool is not approved")
                        if calls >= request.limits.max_calls:
                            fail("call_limit", "core", "Tool call limit exceeded")
                        calls += 1
                        active_call_id = call_id
                        binding = by_name[name]
                        try:
                            args = (binding.input_type.model_validate_json(arguments)
                                    if isinstance(arguments, str) else
                                    binding.input_type.model_validate(arguments))
                        except ValidationError as exc:
                            remaining = request.limits.max_calls - calls
                            result = ToolResult(value={
                                'status': 'invalid_input', 'executed': False,
                                'error': 'Tool arguments must be a JSON object matching the tool schema.',
                                'issues': [{**issue, 'loc': list(issue['loc'])} for issue in
                                    exc.errors(include_url=False, include_context=False, include_input=False)],
                                'remaining_tool_calls': remaining,
                                'instruction': ('Correct the arguments and retry with a new tool call ID. '
                                    'Preserve the intended action and source identities; do not guess missing values.'
                                    if remaining else 'No tool calls remain. Explain the incomplete action and its limits.')})
                            completed[call_id] = (name, deepcopy(arguments), result)
                            event('tool', 'failed', name=name, tool_call_id=call_id,
                                  error=RunError(code='invalid_tool_input', origin='tool',
                                                 message='Tool input violates its JSON/schema contract'))
                            return result
                        result = await binding.handler(args)
                        if not isinstance(result, ToolResult):
                            fail("invalid_tool_result", "tool", "Handler must return ToolResult")
                        if isinstance(result.value, dict):
                            result = result.model_copy(update={'value': {**result.value,
                                'remaining_tool_calls': request.limits.max_calls - calls}})
                        completed[call_id] = (name, deepcopy(arguments), result)
                        event("tool", "completed", name=name, tool_call_id=call_id)
                        return result
                    except asyncio.CancelledError:
                        event("tool", "cancelled", name=name, tool_call_id=call_id)
                        raise
                    except Exception as exc:
                        stopped = exc if isinstance(exc, CoreFailure) else CoreFailure(
                            RunError(code="tool_failed", origin="tool", message="Tool handler failed"))
                        event("tool", "failed", name=name, tool_call_id=call_id, error=stopped.error)
                        raise stopped from None

            deadline = asyncio.timeout(request.limits.timeout_seconds)
            deadline_token = execution_deadline.set(deadline)
            try:
                async with deadline:
                    result = await self.engine.execute(request, spec, bindings, invoke)
            finally:
                execution_deadline.reset(deadline_token)
            if result.status == "succeeded":
                try:
                    if spec.output_type:
                        output = spec.output_type.model_validate(result.output).model_dump(mode="json")
                    elif isinstance(result.output, str):
                        output = result.output
                    else:
                        raise ValueError("Expected text")
                except (ValueError, ValidationError):
                    fail("output_contract", "engine", "Output violates agent contract")
                result = result.model_copy(update={"output": output})
        except asyncio.CancelledError:
            result = RunResult(run_id=request.run_id, status="cancelled",
                               error=RunError(code="cancelled", origin="core", message="Run cancelled"))
        except TimeoutError:
            result = RunResult(run_id=request.run_id, status="failed",
                               error=RunError(code="timeout", origin="core", message="Run deadline exceeded", retryable=True))
        except Exception as exc:
            error = exc.error if isinstance(exc, CoreFailure) else RunError(
                code="engine_failed", origin="engine", message="Engine execution failed")
            result = RunResult(run_id=request.run_id, status="failed", error=error)
        result = result.model_copy(update={"usage": result.usage.plus(child_usage)})
        if result.status != "succeeded":
            result = result.model_copy(update={"usage": result.usage.model_copy(update={"complete": False})})
        event("run", "completed" if result.status == "succeeded" else result.status, error=result.error)
        return result
