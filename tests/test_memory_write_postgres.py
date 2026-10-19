"""Actual PostgreSQL write rollback, row receipts and fresh connection recovery."""
import os
from uuid import uuid4
import pytest
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_research, build_tasks, build_projects
from agent_core.contracts import RunResult
from agent_core.memories.write_models import WriteMemory
from test_memory_writes import snapshot, admitted


async def test_atomic_memory_revision_and_receipt_survive_reopen(tmp_path):
    url = os.environ.get('CE7_TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated PostgreSQL required')
    owner = 'write-pg:' + uuid4().hex
    def open_services():
        db = PostgresDatabase(url)
        scopes = build_resources(db, tmp_path/'blobs')
        research = build_research(db, scopes, owner=owner)
        tasks = build_tasks(db, scopes, research)
        build_projects(db, research, tasks)
        return db, scopes, research, tasks
    db, scopes, research, tasks = open_services()
    try:
        root = research.create('Actual PostgreSQL writing')
        app = (db, scopes, research, tasks, root, None)
        old = research.memories.create(kind='preference', content='只给简短答案', source='User', confirmed=True)
        original = '纠正：以后先给结论，再解释原因。'
        snap = snapshot(app, original=original)
        args = WriteMemory(action='correct', scope='user', content='先给结论，再解释原因', basis='user_statement',
            quote=original, reason='Correction', target_id=old.id, expected_version=1)
        with admitted(snap, args) as payload: receipt = await research.memories.writes.submit(snap, root.id, args)
        result = RunResult(run_id=snap.run_id, status='succeeded', output='done')
        with pytest.raises(RuntimeError, match='crash'):
            with db.transaction() as tx:
                research.memories.writes.publish_in(tx, snap.run_id, result)
                raise RuntimeError('crash inside publication transaction')
        assert research.memories.select(root.id, (old.id,))[0].version == 1
        assert research.memories.writes.records(snap.run_id)[0].state == 'pending'
    finally: db.close()
    db, scopes, research, tasks = open_services()
    try:
        assert research.memories.writes.replay(snap.run_id, payload['call_id'], payload)['value'] == receipt
        with db.transaction() as tx: research.memories.writes.publish_in(tx, snap.run_id, result)
        with db.transaction() as tx: research.memories.writes.publish_in(tx, snap.run_id, result)
        current = research.memories.select(root.id, (old.id,))[0]
        assert current.version == 2 and current.content == args.content
        records = research.memories.writes.records(snap.run_id)
        assert len(records) == 1 and records[0].state == 'published'
        assert records[0].old_memory['version'] == 1 and records[0].resolution['version'] == 2
    finally: db.close()
