"""Serialized Git jobs and per-task worktrees with content-bound test evidence.

Trusted test profiles are operator-owned executables outside every checkout. They
receive an exported code directory as data. Repository scripts are never selected
or run by this service. Profiles must not interpret that data as host commands.
This is a trusted local service, not an OS sandbox.
"""
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
from .git import git, output, ENV
from .models import CodingContract, CodeCandidate
from .paths import checked_name, local_path
from ..context.projection import dumps, sha

@dataclass(frozen=True)
class TestProfile:
    name: str
    argv: tuple[str, ...]
    environment_id: str
    timeout_seconds: float = 30

class CodingService:
    def __init__(self, root, repositories, profiles):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.repositories = {k: Path(v).resolve() for k, v in repositories.items()}
        self.profiles = {p.name: p for p in profiles}
        for p in profiles:
            if not p.argv or not Path(p.argv[0]).is_absolute() or not p.environment_id:
                raise ValueError('Trusted test executable and environment identity required')
            for arg in p.argv:
                if Path(arg).is_absolute() and any(Path(arg).resolve().is_relative_to(p)
                        for p in (self.root, *self.repositories.values())):
                    raise ValueError('Test commands must live outside coding workspaces')
        self.db = sqlite3.connect(self.root / 'state.sqlite')
        self.db.execute('CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, identity TEXT NOT NULL, state TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS grants (scope_id TEXT, version INTEGER, name TEXT, contract TEXT, PRIMARY KEY(scope_id,version,name))')
        self.db.commit()
        self.active = set()
        self.writers = set()

    @contextmanager
    def locked(self):
        with (self.root / 'git-jobs.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(lock, fcntl.LOCK_UN)

    def _save(self, key, identity, state):
        self.db.execute('INSERT OR REPLACE INTO workspaces VALUES (?, ?, ?)', (key, dumps(identity), dumps(state)))
        self.db.commit()

    def _load(self, key):
        row = self.db.execute('SELECT identity,state FROM workspaces WHERE id=?', (key,)).fetchone()
        if row is None: raise ValueError('Unknown code workspace')
        return json.loads(row[0]), json.loads(row[1])

    def admit(self, spec, generation, validate):
        contract = spec.coding
        if contract is None or contract.repository_id not in self.repositories:
            raise ValueError('Coding repository was not admitted by operator')
        if not contract.test_profile_hashes or self.bind_contract(contract) != contract:
            raise ValueError('Test environment was not frozen at task admission')
        identity = {'research_id': spec.research_id, 'task_id': spec.id, 'spec_version': spec.version,
                    'generation': generation, 'contract': contract.model_dump(mode='json')}
        key = sha(dumps(identity))
        validate()
        with self.locked():
            repo = self.root / ('repo-' + contract.repository_id)
            if not repo.exists():
                # Clone only object storage into a fresh service-owned config. No checkout/filter runs.
                git(self.root, 'clone', '--bare', '--local', '--no-hardlinks', '--',
                    str(self.repositories[contract.repository_id]), str(repo))
                git(repo, 'config', '--remove-section', 'remote.origin', check=False)
                if (repo / 'objects/info/alternates').exists():
                    raise ValueError('Repositories with alternate object stores are unsupported')
            if output(repo, 'rev-parse', '--verify', contract.baseline + '^{commit}') != contract.baseline:
                raise ValueError('Unknown baseline commit')
            entries = git(repo, 'ls-tree', '-rz', contract.baseline).stdout.split(b'\0')
            baseline_paths = set()
            for entry in filter(None, entries):
                meta, name = entry.split(b'\t', 1)
                mode, typ, oid = meta.split()
                checked_name(name.decode())
                if mode not in (b'100644', b'100755') or typ != b'blob':
                    raise ValueError('Only regular source files can be admitted')
                baseline_paths.add(name.decode())
            if not set(contract.read_paths) <= baseline_paths | set(contract.write_paths):
                raise ValueError('Read grant names a missing file')
            row = self.db.execute('SELECT identity,state FROM workspaces WHERE id=?', (key,)).fetchone()
            path = self.root / ('task-' + key)
            if row is None:
                git(repo, 'worktree', 'add', '-b', 'codex/task-' + key, str(path), contract.baseline)
                state = {'phase': 'editing', 'tests': [], 'integration': []}
                self._save(key, identity, state)
                try:
                    for candidate_index, candidate_key in enumerate(contract.candidate_workspaces):
                        prior_identity, prior = self._load(candidate_key)
                        if (prior['phase'] != 'frozen' or prior_identity['generation'] != generation or
                                prior_identity['research_id'] != spec.research_id):
                            raise PermissionError('Integration candidate authorization mismatch')
                        candidate = CodeCandidate.model_validate(prior['candidate'])
                        if candidate.repository_id != contract.repository_id or candidate.baseline != contract.baseline:
                            raise ValueError('Integration candidates require the same repository and baseline')
                        patch = git(repo, 'diff', '--binary', '--no-ext-diff', '--no-textconv',
                                    contract.baseline, candidate.commit, '--').stdout
                        merged = git(path, 'apply', '--3way', '--index', data=patch, check=False) if patch else None
                        state['integration'].append({'workspace_id': candidate_key, 'commit': candidate.commit,
                            'status': 'applied' if merged is None or merged.returncode == 0 else 'conflict',
                            'diagnostic': merged.stderr.decode(errors='replace') if merged else ''})
                        if merged is not None and merged.returncode:
                            state['pending_candidates'] = list(contract.candidate_workspaces[candidate_index + 1:])
                            state['phase'] = 'conflict'
                            state['resolution_task'] = {'task_id': spec.id, 'spec_version': spec.version,
                                'required_candidates': list(contract.candidate_workspaces),
                                'reason': 'Resolve integration conflicts before new validation and publication'}
                            break
                except BaseException:
                    state['phase'] = 'blocked'
                    self._save(key, identity, state)
                    raise
                self._save(key, identity, state)
            elif json.loads(row[0]) != identity:
                raise ValueError('Code workspace identity conflict')
        return CodeWorkspace(self, key, path, contract, identity, validate)

    def profile_hash(self, name):
        profile = self.profiles[name]
        files = {str(Path(arg).resolve()): hashlib.sha256(Path(arg).read_bytes()).hexdigest()
                 for arg in profile.argv if Path(arg).is_absolute() and Path(arg).is_file()}
        return sha(dumps({'argv': profile.argv, 'environment_id': profile.environment_id,
                          'timeout_seconds': profile.timeout_seconds, 'files': files}))

    def bind_contract(self, contract):
        contract = CodingContract.model_validate(contract)
        if contract.repository_id not in self.repositories or not set(contract.test_profiles) <= self.profiles.keys():
            raise ValueError('Operator repository or test profile is not registered')
        hashes = {name: self.profile_hash(name) for name in contract.test_profiles}
        if contract.test_profile_hashes and contract.test_profile_hashes != hashes:
            raise ValueError('Frozen trusted test profile changed')
        return contract.model_copy(update={'test_profile_hashes': hashes})

    def grant(self, scope, name, contract):
        contract = self.bind_contract(contract)
        if not name or len(name) > 100: raise ValueError('Code grant name required')
        row = self.db.execute('SELECT contract FROM grants WHERE scope_id=? AND version=? AND name=?',
            (scope.id, scope.version, name)).fetchone()
        raw = contract.model_dump_json()
        if row is not None and row[0] != raw: raise ValueError('Code grant name already frozen; use a new name')
        self.db.execute('INSERT OR IGNORE INTO grants VALUES (?,?,?,?)', (scope.id, scope.version, name, raw))
        self.db.commit()
        return contract

    def grants(self, scope):
        return {name: CodingContract.model_validate_json(raw) for name, raw in self.db.execute(
            'SELECT name,contract FROM grants WHERE scope_id=? AND version=?', (scope.id, scope.version))}

    @classmethod
    def from_config(cls, path):
        value = json.loads(Path(path).read_text())
        if set(value) != {'root', 'repositories', 'profiles'}: raise ValueError('Unexpected coding config fields')
        return cls(value['root'], value['repositories'], [TestProfile(**p) for p in value['profiles']])

    def close(self): self.db.close()

class CodeWorkspace:
    def __init__(self, service, key, path, contract, identity, validate):
        self.service, self.key, self.path = service, key, path
        self.contract, self.identity, self.validate = contract, identity, validate

    @contextmanager
    def writer(self):
        self.state()
        if self.key in self.service.writers: raise PermissionError('Code workspace already has a writer')
        self.service.writers.add(self.key)
        try: yield
        finally: self.service.writers.discard(self.key)

    def state(self):
        self.validate()
        identity, state = self.service._load(self.key)
        if identity != self.identity or state['phase'] in ('revoked', 'archived', 'blocked'):
            raise PermissionError('Code workspace is unavailable')
        return state

    def save(self, state): self.service._save(self.key, self.identity, state)

    def manifest(self):
        self.state()
        files = {}
        for path in sorted(self.path.rglob('*')):
            name = path.relative_to(self.path).as_posix()
            if name == '.git': continue
            if path.is_symlink(): raise ValueError('Links cannot enter a code snapshot')
            if path.is_dir(): continue
            local_path(self.path, name)
            if path.stat().st_size > 1_000_000: raise ValueError('Code file exceeds admitted size')
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return files

    def read(self, name, version='working'):
        self.state()
        if name not in set(self.contract.read_paths) | set(self.contract.write_paths):
            raise PermissionError('Code file not granted')
        path = local_path(self.path, name)
        if version == 'baseline':
            return git(self.path, 'show', self.contract.baseline + ':' + name).stdout.decode()
        if version != 'working': raise ValueError('Only working and fixed baseline versions allowed')
        if path.stat().st_size > 1_000_000: raise ValueError('Code file exceeds admitted size')
        return path.read_text()

    def write(self, name, text):
        with self.service.locked():
            state = self.state()
            if state['phase'] not in ('editing', 'conflict') or self.key in self.service.active:
                raise PermissionError('Code writes are stopped')
            if name not in self.contract.write_paths: raise PermissionError('Code write not granted')
            if len(text.encode()) > 1_000_000: raise ValueError('Code file exceeds admitted size')
            path = local_path(self.path, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(text); stream.flush(); os.fsync(stream.fileno())
                os.replace(temp, path)
            finally:
                if os.path.exists(temp): os.unlink(temp)
            state['tests'] = []
            self.save(state)
            return {'path': name, 'sha256': sha(text)}

    def status(self):
        return {'workspace_id': self.key, 'phase': self.state()['phase'], 'baseline': self.contract.baseline,
                'files': self.manifest(), 'read_paths': list(self.contract.read_paths),
                'write_paths': list(self.contract.write_paths), 'test_profiles': list(self.contract.test_profiles), 'integration': self.state().get('integration', []),
                'resolution_task': self.state().get('resolution_task'),
                'candidate_ref': (self.key + ':' + self.state()['candidate']['commit']) if self.state()['phase'] == 'frozen' else None}

    def execution_snapshot(self):
        return {'status': self.status(), 'head': output(self.path, 'rev-parse', 'HEAD'),
                'state_sha256': sha(dumps(self.state()))}

    def diff(self):
        self.state(); self.manifest()
        return {'patch': output(self.path, 'diff', '--no-ext-diff', '--no-textconv', self.contract.baseline, '--'),
                'new_files': {name: self.read(name) for name in self.contract.write_paths
                    if local_path(self.path, name).exists() and git(self.path, 'cat-file', '-e',
                        self.contract.baseline + ':' + name, check=False).returncode}}

    def checkpoint(self):
        with self.service.locked():
            state = self.state()
            if state['phase'] not in ('editing', 'conflict') or self.key in self.service.active:
                raise PermissionError('Code workspace is not writable')
            files = self.manifest()
            baseline = set(git(self.path, 'ls-tree', '-rz', '--name-only', self.contract.baseline).stdout.decode().strip('\0').split('\0'))
            if set(files) - baseline - set(self.contract.write_paths):
                raise PermissionError('Unadmitted files cannot enter candidate')
            # Also detect trusted-host tampering of files outside the write grant.
            changed = set(filter(None, git(self.path, 'diff', '--name-only', '-z', self.contract.baseline, '--').stdout.decode().split('\0')))
            if changed - set(self.contract.write_paths): raise PermissionError('Changes outside write grant')
            if state['phase'] == 'conflict':
                for name in self.contract.write_paths:
                    path = local_path(self.path, name)
                    if path.exists() and b'<<<<<<< ' in path.read_bytes() and b'>>>>>>> ' in path.read_bytes():
                        raise ValueError('Resolve conflict markers before checkpointing')
            paths = [p for p in self.contract.write_paths if p in files or p in baseline]
            if paths: git(self.path, 'add', '--all', '--force', '--', *paths)
            if state['phase'] == 'conflict':
                state['integration'][-1]['status'] = 'resolved_by_task'
                state['phase'] = 'editing'; state.pop('resolution_task', None)
                pending = state.pop('pending_candidates', [])
                for index, key in enumerate(pending):
                    identity, prior = self.service._load(key)
                    if (prior['phase'] != 'frozen' or identity['generation'] != self.identity['generation'] or
                            identity['research_id'] != self.identity['research_id']):
                        state['phase'] = 'blocked'; self.save(state)
                        raise PermissionError('Pending integration candidate authorization changed')
                    candidate = CodeCandidate.model_validate(prior['candidate'])
                    if candidate.baseline != self.contract.baseline or candidate.repository_id != self.contract.repository_id:
                        state['phase'] = 'blocked'; self.save(state)
                        raise ValueError('Pending candidate baseline mismatch')
                    patch = git(self.path, 'diff', '--binary', '--no-ext-diff', '--no-textconv',
                                candidate.baseline, candidate.commit, '--').stdout
                    result = git(self.path, 'apply', '--3way', '--index', data=patch, check=False) if patch else None
                    state['integration'].append({'workspace_id': key, 'commit': candidate.commit,
                        'status': 'applied' if result is None or result.returncode == 0 else 'conflict'})
                    if result is not None and result.returncode:
                        state['phase'] = 'conflict'; state['pending_candidates'] = pending[index+1:]
                        state['resolution_task'] = {'task_id': self.identity['task_id'],
                            'spec_version': self.identity['spec_version'], 'required_candidates': list(self.contract.candidate_workspaces),
                            'reason': 'Resolve remaining integration conflict'}
                        self.save(state)
                        return {'status': 'conflict', 'resolution_task': state['resolution_task']}
                self.save(state)
                files = self.manifest()
            tree = output(self.path, 'write-tree')
            parent = output(self.path, 'rev-parse', 'HEAD')
            if tree != output(self.path, 'rev-parse', 'HEAD^{tree}'):
                commit = output(self.path, 'commit-tree', tree, '-p', parent, data=b'Controlled code checkpoint\n')
                git(self.path, 'update-ref', 'HEAD', commit, parent)
            return {'commit': output(self.path, 'rev-parse', 'HEAD'), 'tree': tree,
                    'content_sha256': sha(dumps(files)), 'files': files}

    async def test(self, name):
        state = self.state()
        if state['phase'] not in ('editing', 'conflict') or self.key in self.service.active: raise PermissionError('Workspace busy or frozen')
        if name not in self.contract.test_profiles: raise PermissionError('Test profile not granted')
        snapshot = self.checkpoint()
        if snapshot.get('status') == 'conflict': return snapshot
        profile = self.service.profiles[name]
        if self.service.profile_hash(name) != self.contract.test_profile_hashes[name]:
            raise ValueError('Frozen trusted test profile changed')
        self.service.active.add(self.key)
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix='test-', dir=self.service.root) as temp:
                exported = Path(temp) / 'source'; exported.mkdir()
                for file in snapshot['files']:
                    target = exported / file; target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(local_path(self.path, file).read_bytes())
                env = {**ENV, 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                       'CODE_SOURCE': str(exported), 'TMPDIR': temp}
                with tempfile.TemporaryFile() as log:
                    process = await asyncio.create_subprocess_exec(*profile.argv, cwd=temp, env=env,
                        stdout=log, stderr=log, start_new_session=True)
                    try:
                        await asyncio.wait_for(process.wait(), profile.timeout_seconds)
                    except BaseException:
                        os.killpg(process.pid, signal.SIGKILL); await process.wait(); raise
                    self.state()
                    if self.manifest() != snapshot['files']: raise ValueError('Code changed during test')
                    exported_files = {p.relative_to(exported).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in exported.rglob('*') if p.is_file() and not p.is_symlink()}
                    if exported_files != snapshot['files'] or any(p.is_symlink() for p in exported.rglob('*')):
                        raise ValueError('Trusted validator mutated source under test')
                    log.seek(0); text = log.read(1_000_001)
                    if len(text) > 1_000_000: raise ValueError('Test output exceeds admitted limit')
                result = {'profile': name, 'argv': list(profile.argv), 'environment_id': profile.environment_id,
                          'profile_sha256': self.contract.test_profile_hashes[name],
                          'content_sha256': snapshot['content_sha256'], 'tree': snapshot['tree'],
                          'exit_code': process.returncode, 'log': text.decode(errors='replace')}
                state = self.state(); state['tests'] = [t for t in state['tests'] if t['profile'] != name] + [result]
                self.save(state)
                return result
        finally: self.service.active.discard(self.key)

    def freeze(self):
        self.service.bind_contract(self.contract)
        state = self.state()
        if state['phase'] == 'frozen': return CodeCandidate.model_validate(state['candidate'])
        snapshot = self.checkpoint()
        if snapshot.get('status') == 'conflict': raise ValueError('Integration requires conflict resolution')
        state = self.state()
        tests = tuple(t for t in state['tests'] if t['content_sha256'] == snapshot['content_sha256'] and t['tree'] == snapshot['tree'] and t['exit_code'] == 0)
        if {t['profile'] for t in tests} != set(self.contract.test_profiles):
            raise ValueError('Every approved test must pass on the delivered snapshot')
        self.validate()
        candidate = CodeCandidate(workspace_id=self.key, repository_id=self.contract.repository_id,
            baseline=self.contract.baseline, tests=tests, **snapshot)
        state['phase'] = 'frozen'; state['candidate'] = candidate.model_dump(mode='json'); self.save(state)
        return candidate

    def candidate_patch(self, candidate):
        self.verify_candidate(candidate.model_dump(mode='json'))
        return git(self.path, 'diff', '--binary', '--no-ext-diff', '--no-textconv',
                   candidate.baseline, candidate.commit, '--').stdout.decode()

    def resolve_candidate(self, reference):
        state = self.state()
        if state['phase'] != 'frozen' or reference != self.key + ':' + state['candidate']['commit']:
            raise ValueError('Unknown frozen candidate reference')
        return self.verify_candidate(state['candidate'])

    def verify_candidate(self, value):
        state = self.state()
        candidate = CodeCandidate.model_validate(value)
        if state['phase'] != 'frozen' or state['candidate'] != candidate.model_dump(mode='json'):
            raise ValueError('Candidate is not the service-frozen snapshot')
        if candidate.files != self.manifest() or output(self.path, 'rev-parse', 'HEAD') != candidate.commit:
            raise ValueError('Frozen code was changed')
        return candidate

    def archive(self):
        if self.key in self.service.active or self.key in self.service.writers:
            raise PermissionError('Active code workspace cannot be archived')
        snapshot = self.checkpoint() if self.state()['phase'] == 'editing' else self.state()['candidate']
        state = self.state(); state['phase'] = 'archived'; state['archive'] = snapshot; self.save(state)
        return snapshot
