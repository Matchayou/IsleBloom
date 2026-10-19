"""Server-only admission for consultations and discussions without a formal run."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Admission:
    request: object
    research_id: str
    session_id: str
    task_id: str
    spec_version: int
    kind: str
    validate: object


current_admission = ContextVar('controlled_admission', default=None)


@contextmanager
def admit(request, *, research_id, session_id, task_id, spec_version, kind, validate):
    if kind not in ('discussion', 'consultation', 'preview', 'planning', 'learning', 'memory_capture'):
        raise ValueError('Unknown auxiliary entrance')
    validate()
    token = current_admission.set(Admission(request, research_id, session_id,
        task_id, spec_version, kind, validate))
    try:
        yield
        validate()
    finally:
        current_admission.reset(token)
