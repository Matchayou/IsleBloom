"""Small, atomic evaluation records, separate from product approval records."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False, default=str)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def manifest_at(root):
    value = read(Path(root)/'manifest.json')
    if digest({k: v for k, v in value.items() if k != 'protocol_sha256'}) != value['protocol_sha256']:
        raise ValueError('Batch manifest changed')
    return value


def implementation():
    paths = [ROOT/'pyproject.toml', ROOT/'uv.lock']
    for folder in ('src', 'evals/research_quality', 'config'):
        paths += [p for p in (ROOT/folder).rglob('*') if p.is_file()
                  and p.suffix in ('.py', '.json', '.yaml', '.yml', '.toml')]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}
