from dataclasses import dataclass
from collections.abc import Callable

from ..contracts import RunResult, fail


@dataclass(frozen=True)
class Call:
    name: str
    arguments: dict
    id: str


class ScriptedEngine:
    """Deterministic control-contract adapter; never a semantic model substitute."""
    def __init__(self, script: Callable):
        self.script = script
        self.requests = []

    async def execute(self, request, spec, tools, invoke):
        self.requests.append(request)
        steps, output = self.script(request)
        if request.limits.max_turns is not None and len(steps) + 1 > request.limits.max_turns:
            fail("turn_limit", "engine", "Scripted turn limit exceeded")
        values = []
        for step in steps:
            if isinstance(step, Exception):
                raise step
            values.append((await invoke(step.name, step.id, step.arguments)).value)
        if callable(output):
            output = output(request, values)
        return RunResult(run_id=request.run_id, status="succeeded", output=output)
