"""Fail closed for control files, credentials, links and build output."""
from pathlib import PurePosixPath

FORBIDDEN = {'.git', '.gitmodules', '.gitattributes', '.env', '.ssh', '.aws', '.npmrc',
             '.netrc', '.pypirc', 'node_modules', '__pycache__', '.venv', 'dist', 'build'}

def checked_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or str(path) != name or '\\' in name or
            any(ord(c) < 32 or c in ':*?[]' for c in name) or
            any(p in ('.', '..') or p.lower() in FORBIDDEN or p.lower().startswith('.env')
                for p in path.parts) or
            path.suffix.lower() in ('.pem', '.key', '.p12', '.pfx') or
            path.name.lower() in ('id_rsa', 'id_ed25519', 'credentials')):
        raise ValueError('Code path is not permitted')
    return path

def local_path(root, name):
    path = root
    for part in checked_name(name).parts:
        path = path / part
        if path.is_symlink(): raise ValueError('Links are not permitted in code workspaces')
    if path.exists() and not path.is_file(): raise ValueError('Expected a regular code file')
    if path.exists() and path.stat().st_nlink != 1: raise ValueError('Hard links are not permitted')
    return path
