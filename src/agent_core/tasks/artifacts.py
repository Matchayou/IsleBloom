"""Deterministic report excerpts reference the exact atomically saved full output."""
from typing import Literal
from ..contracts import Value
from ..context.projection import dumps, sha


class ReportArtifact(Value):
    result_id: str
    sha256: str
    bytes: int
    chars: int
    complete: Literal[True] = True
    brief: str
    brief_truncated: bool


def describe(result_id, output):
    text=dumps(output)
    report=output['report']
    return ReportArtifact(result_id=result_id,sha256=sha(text),bytes=len(text.encode()),chars=len(text),
        brief=report[:1200],brief_truncated=len(report)>1200)
