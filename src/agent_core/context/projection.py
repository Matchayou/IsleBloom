"""Deterministic projection: preserve every user constraint, externalize optional data."""
import hashlib
import json
from copy import deepcopy
from ..contracts import Message, PreparedInput
from .scopes import ScopeDenied


class ContextBudgetExceeded(ScopeDenied):
    pass


class ContextWindowOverflow(Exception):
    """One explicit provider window rejection may reproject known input once."""
    def __init__(self, input, remaining_turns):
        self.input, self.remaining_turns = input, remaining_turns
        super().__init__('Provider context window rejected; reproject once')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def project_payload(payload, maximum):
    # Apply both the character contract and a conservative UTF-8 input bound.
    # CJK history can fit a character limit while exceeding the model window.
    def size(value): return len(dumps(value).encode())
    full = dumps(payload)
    if len(full.encode()) <= maximum:
        return PreparedInput(messages=(Message(role='user', content=full),))
    artifact = sha(full)
    exclusions = []
    protected = []
    def constraints(value, path=''):
        if isinstance(value, dict):
            if value.get('role') == 'user' and isinstance(value.get('content'), str):
                protected.append({'path': path, **value})
            else:
                for k, v in value.items(): constraints(v, path+'/'+str(k))
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value): constraints(v, path+'/'+str(i))
    constraints(payload)
    # User statements are conservatively retained verbatim, including mid-history
    # corrections. No inference, extraction, summarization or silent supersession.
    selected = {k: deepcopy(payload[k]) for k in
        ('goal', 'task', 'question', 'progress', 'current_instruction', 'current_message_id', 'handoff', 'history_rule', 'memory_rule', 'skills', 'dynamic_context', 'context_mode', 'agent', 'project') if k in payload}
    if protected: selected['user_constraints'] = protected
    selected['external_context'] = {'artifact_id': artifact, 'chars': len(full),
        'format': 'json_text', 'keys': list(payload),
        'instruction': 'Use read_context_page with artifact_id and a JSON pointer (e.g. /results/0) to read fixed original context. Pages concatenate exactly; no summary was generated.'}
    if size(selected) > maximum:
        raise ContextBudgetExceeded('Required instructions and user constraints exceed input budget; narrow the task explicitly')
    # Deterministic priority; retain complete recent assistant turns when they fit.
    for key in ('approved_memory', 'history', 'messages', 'results', 'sources', 'content',
                'required_results', 'task_results', 'supplemental_messages', 'related_results', 'context_selection', 'authorized_directory'):
        if key not in payload or key in selected: continue
        value = payload[key]
        if key in ('history', 'messages') and isinstance(value, list):
            recent = []
            for turn in reversed(value):
                if size([turn,*recent]) > maximum//4: break
                recent.insert(0, turn)
                if len(recent) == 4: break
            if len(recent) < len(value): exclusions.append({'pointer':'/'+key,'reason':'older_complete_turns_externalized','omitted_turns':len(value)-len(recent)})
            value = recent
        candidate = {**selected, key: value}
        if size(candidate) <= maximum: selected[key] = value
        else: exclusions.append({'pointer': '/'+key, 'reason': 'externalized_for_budget'})
    for key in payload:
        if key not in selected and not any(e['pointer']=='/'+key for e in exclusions):
            exclusions.append({'pointer':'/'+key,'reason':'externalized_for_budget'})
    selected['projection_exclusions'] = exclusions
    # Metadata itself consumes the same character budget.
    if size(selected) > maximum:
        for key in ('content', 'results', 'sources', 'messages', 'history', 'approved_memory'):
            selected.pop(key, None)
            if size(selected) <= maximum: break
    if size(selected) > maximum: raise ContextBudgetExceeded('Projection metadata exceeds input budget')
    return PreparedInput(messages=(Message(role='user', content=dumps(selected)),),
                         external_context={artifact: full}, projection_exclusions=tuple(exclusions))


class InvalidContextPointer(ValueError):
    pass


class InvalidContextRange(ValueError):
    def __init__(self, total_chars):
        self.total_chars=total_chars
        super().__init__('Offset is outside the fixed version')


def page(text, artifact_id, pointer='', offset=0, limit=4000):
    if not 1 <= limit <= 12000 or offset < 0: raise ValueError('Invalid page range')
    value = json.loads(text)
    try:
        if pointer:
            if not pointer.startswith('/'): raise InvalidContextPointer('Expected a JSON pointer')
            for part in pointer[1:].split('/'):
                part = part.replace('~1', '/').replace('~0', '~')
                value = value[int(part)] if isinstance(value, list) else value[part]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise InvalidContextPointer('Pointer does not identify a value in this fixed artifact') from exc
    selected = dumps(value)
    if offset > len(selected): raise InvalidContextRange(len(selected))
    end = min(len(selected), offset+limit)
    return {'page_type': 'context', 'artifact_id': artifact_id, 'pointer': pointer, 'sha256': sha(selected),
        'format': 'json_text', 'available_pointers': ['/'+str(k).replace('~','~0').replace('/','~1') for k in value] if isinstance(value,dict) else [], 'text': selected[offset:end], 'start': offset, 'end': end,
        'total_chars': len(selected), 'remaining_chars': len(selected)-end,
        'complete': end == len(selected),
        'next_cursor': None if end == len(selected) else
            {'artifact_id': artifact_id, 'pointer': pointer, 'offset': end, 'limit': limit}}
