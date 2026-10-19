"""Removed execution commands cannot reopen ungoverned product paths."""
import subprocess
import sys
import json
import os
from pathlib import Path
import pytest


@pytest.mark.parametrize('module,args', [
    ('research_cli', ['--engine', 'scripted', 'create', 'x']),
    ('research_cli', ['--storage', 'memory', 'create', 'x']),
    ('tasks_cli', ['--engine', 'openai', 'run', 'x']),
    ('records_cli', ['--conversation', 'x', 'send', 'x']),
    ('dispatch_cli', ['compare', 'x']),
    ('resources_cli', ['--scope', 'x', 'run', 'x']),
    ('cli', []),
])
def test_removed_execution_paths_reject_before_database_or_model(module, args):
    result = subprocess.run([sys.executable, '-m', 'agent_core.'+module, *args],
        capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert 'DATABASE_URL' not in result.stderr
    assert 'api_key' not in result.stderr


@pytest.mark.parametrize('module', ['research_cli', 'tasks_cli', 'methods_cli', 'evolution_cli', 'evaluation_cli'])
def test_product_commands_expose_single_controlled_composition(module):
    result = subprocess.run([sys.executable, '-m', 'agent_core.'+module, '--help'],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert '--private' in result.stdout and '--blobs' in result.stdout
    assert '--engine' not in result.stdout and '--storage' not in result.stdout


def test_operator_commands_reopen_postgres_without_model_credentials(tmp_path):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Real PostgreSQL required')
    env = {k: v for k, v in os.environ.items() if k != 'OPENAI_API_KEY'}
    env['DATABASE_URL'] = url
    def invoke(module, *args):
        result = subprocess.run([sys.executable, '-m', 'agent_core.'+module,
            '--blobs', str(tmp_path/'blobs'), '--private', str(tmp_path/'private'), *args],
            env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr + result.stdout
        return json.loads(result.stdout)
    research = invoke('research_cli', 'create', 'CE8 operator CLI fixture')
    assert invoke('research_cli', 'show', research['id'])
    spec = invoke('tasks_cli', 'create', research['id'], 'synthesize', 'No execution requested', '--model', 'fixture')
    assert invoke('tasks_cli', 'show', spec['id'])['spec']['id'] == spec['id']
    assert invoke('methods_cli', 'list')
    assert invoke('evolution_cli', 'list', research['id']) == []
    assert invoke('evaluation_cli', 'list-feedback', research['id']) == []


def test_legacy_history_execution_is_not_in_installed_product():
    from agent_core import composition, conversation
    from agent_core.execution.service import ExecutionService
    assert not hasattr(ExecutionService, 'send')
    assert not hasattr(conversation, 'ConversationService')
    assert not any(hasattr(composition, name) for name in
        ('build', 'build_runner', 'build_execution', 'build_orchestration', 'build_scoped_execution'))
    root = Path(__file__).resolve().parents[1]
    for base in ('src', 'scripts', 'evals'):
        for path in (root/base).rglob('*.py'):
            text = path.read_text()
            assert 'snapshot.messages + (user,)' not in text
            if 'OpenAIEngine(' in text:
                assert path == root/'src/agent_core/controlled/runner.py'
            if base != 'src':
                assert 'PostgresDatabase.LOCK_PATH' not in text
