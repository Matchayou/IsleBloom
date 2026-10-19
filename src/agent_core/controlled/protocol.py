"""Bounded operation envelopes for trusted tools; model content cannot supply identity."""
import json
from typing import Literal
from pydantic import Field, JsonValue
from ..contracts import Value

MAX_FRAME = 4 * 1024 * 1024


class Frame(Value):
    sequence: int = Field(ge=1, strict=True)
    operation_id: str = Field(min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9:_-]+$')
    operation: Literal['model', 'tool', 'private', 'result']
    payload: dict[str, JsonValue]


def encode(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode() + b'\n'
    if len(raw) > MAX_FRAME:
        raise ValueError('Controlled protocol frame exceeds limit')
    return raw


def decode(raw):
    if not raw or len(raw) > MAX_FRAME or not raw.endswith(b'\n'):
        raise ValueError('Incomplete or oversized controlled frame')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate protocol field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
