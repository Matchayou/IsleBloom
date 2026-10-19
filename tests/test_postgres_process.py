import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from uuid import uuid4

import pytest

from agent_core.adapters.postgres import PostgresDatabase
from agent_core.execution.ledger import Ledger
from agent_core.execution.ports import Conflict, SaveUnavailable

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def url():
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        pytest.skip('Requires real PostgreSQL; process persistence NOT verified')
    return value


def cli(url, *args):
    env = dict(os.environ, DATABASE_URL=url)
    if 'send' in args:
        index = args.index('send')
        command = [sys.executable, str(ROOT/'tests/legacy_record_fixture.py'), 'send', args[1], args[index+1]]
        if '--run-id' in args: command.append(args[args.index('--run-id')+1])
    else:
        command = [sys.executable, '-m', 'agent_core.records_cli', *args]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_historical_fixture_close_reopen_and_readonly_cli(url):
    c, r = str(uuid4()), str(uuid4())
    first = cli(url, '--conversation', c, 'send', 'u', '--run-id', r)
    assert first.index('正在生成') < first.index('正在保存') < first.index('已完成')
    saved = json.loads(cli(url, '--conversation', c, 'show').splitlines()[-1])
    assert saved['conversation']['revision'] == 1 and saved['runs'][0]['result']['output']
    assert [e['event']['name'] for e in saved['events'] if e['event']['phase'] == 'tool' and
            e['event']['status'] == 'started'] == ['multiply', 'delegate']
    again = cli(url, '--conversation', c, 'send', 'u', '--run-id', r)
    assert 'core_event' not in again
    assert json.loads(again.splitlines()[-1])['status'] == 'completed'
    cli(url, '--conversation', c, 'send', 'followup')
    after = json.loads(cli(url, '--conversation', c, 'show').splitlines()[-1])
    assert after['conversation']['revision'] == 2 and '96' in after['runs'][-1]['result']['output']


@pytest.mark.parametrize('point', ['running', 'before', 'after'])
def test_sigkill_transaction_and_restart(url, point):
    c, r = str(uuid4()), str(uuid4())
    child = subprocess.Popen([sys.executable, '-u', str(ROOT / 'tests/stage1_crash_worker.py'), point, c, r],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log = []
    try:
        # Read raw bytes to avoid TextIO buffering hiding readiness from select().
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        raw = b''
        while b'READY_TO_KILL\n' not in raw:
            assert selector.select(timeout=15), f'worker did not reach fault point: {raw!r}'
            chunk = os.read(child.stdout.fileno(), 65536)
            assert chunk, raw.decode()
            raw += chunk
        selector.close()
        log.append(raw.decode())
        with pytest.raises(Conflict):
            PostgresDatabase(url)  # alive owner must NOT be marked interrupted
        child.kill()
        child.wait(timeout=10)
        assert child.returncode == -9
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdout.close()
    payload = json.loads(cli(url, '--conversation', c, 'show').splitlines()[-1])
    record = payload['runs'][0]
    if point == 'after':
        assert record['status'] == 'succeeded' and record['result']['output']
        assert payload['conversation']['revision'] == 1
        again = cli(url, '--conversation', c, 'send', 'u', '--run-id', r)
        assert 'core_event' not in again
    else:
        assert record['status'] == 'interrupted' and record['result'] is None
        assert payload['conversation']['revision'] == 0
        replay = subprocess.run([sys.executable, str(ROOT/'tests/legacy_record_fixture.py'), 'send', c, 'u', r],
            env=dict(os.environ, DATABASE_URL=url), text=True, capture_output=True, timeout=20)
        assert replay.returncode == 1 and '"status":"interrupted"' in replay.stdout
        assert 'core_event' not in replay.stdout
    assert payload['events']


def test_database_disconnect_does_not_reconnect_or_fallback(url):
    from sqlalchemy import create_engine, text
    db = PostgresDatabase(url)
    try:
        with db.transaction():
            pid = db.connection.scalar(text('SELECT pg_backend_pid()'))
        other = create_engine(url)
        with other.begin() as conn:
            assert conn.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': pid})
        other.dispose()
        with pytest.raises(Exception):
            Ledger(db).history('any')
        with pytest.raises(SaveUnavailable):
            Ledger(db).history('any')
        with pytest.raises(Conflict):
            PostgresDatabase(url)  # live process still owns local lifecycle lock
    finally:
        db.close()


def test_independent_database_processes_and_url_aliases(url):
    from sqlalchemy.engine import make_url
    second_url = os.environ.get('SECOND_TEST_DATABASE_URL')
    if not second_url:
        pytest.skip('Requires a second isolated PostgreSQL database')
    assert make_url(url).database != make_url(second_url).database
    # Keep an actual separate process alive while another database is opened.
    child = subprocess.Popen([sys.executable, '-u', '-c',
        'import os,sys; from agent_core.adapters.postgres import PostgresDatabase; '
        'db=PostgresDatabase(os.environ["DATABASE_URL"]); print("READY",flush=True); '
        'sys.stdin.readline(); db.close()'],
        env=dict(os.environ, DATABASE_URL=url), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=15), 'owner did not start'
            assert child.stdout.readline().strip() == 'READY'
        independent = PostgresDatabase(second_url)
        try:
            # Same database, different URL spelling/query still means same owner.
            alias = make_url(url).update_query_dict({'application_name':'lock-alias-test'})
            with pytest.raises(Conflict, match='this database'):
                PostgresDatabase(alias)
            with independent.transaction():
                from sqlalchemy import text
                assert independent.connection.scalar(text('SELECT 1')) == 1
        finally:
            independent.close()
        child.stdin.write('\n'); child.stdin.flush()
        assert child.wait(timeout=10) == 0
        reopened = PostgresDatabase(url)
        reopened.close()
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=10)
        child.stdin.close(); child.stdout.close()
