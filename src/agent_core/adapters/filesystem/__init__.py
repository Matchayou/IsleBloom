"""Content-addressed immutable raw files; no model-supplied filesystem paths."""
import hashlib
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


class FileBlobStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest):
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid blob digest')
        return self.root / digest

    def put(self, content):
        digest = hashlib.sha256(content).hexdigest()
        path = self._path(digest)
        if path.exists():
            self.get(digest)
            return digest
        with NamedTemporaryFile(dir=self.root, delete=False) as f:
            temp = Path(f.name)
            try:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
                os.link(temp, path)
            except FileExistsError:
                self.get(digest)
            finally:
                temp.unlink(missing_ok=True)
        return digest

    def get(self, digest):
        path = self._path(digest)
        if path.is_symlink():
            raise ValueError('Blob symlinks forbidden')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError('Blob integrity failure')
        return content
