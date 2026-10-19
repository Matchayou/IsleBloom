import ast
from pathlib import Path

from agent_core.core import AgentCatalog
from agent_core.contracts import AgentSpec
import pytest


def test_core_has_no_sdk_or_future_module_dependency():
    root = Path(__file__).resolve().parents[1] / "src" / "agent_core"
    banned = {"agents", "openai", "research", "resources", "memory", "evidence", "skills", "durability", "feedback", "execution", "sqlalchemy", "psycopg"}
    for filename in ("contracts.py", "core.py", "conversation.py"):
        for node in ast.walk(ast.parse((root / filename).read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(set(module.split(".")) & banned for module in modules)


@pytest.mark.parametrize("specs", [[AgentSpec("a", ""), AgentSpec("a", "")],
                                   [AgentSpec("a", "", delegates=("missing",))]])
def test_invalid_catalog_rejected(specs):
    with pytest.raises(ValueError):
        AgentCatalog(specs)


def test_execution_uses_ports_not_sdk_or_adapters():
    root = Path(__file__).resolve().parents[1] / "src" / "agent_core" / "execution"
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not set((node.module or "").split(".")) & {"agents", "adapters", "sqlalchemy", "psycopg"}


def test_orchestration_has_no_sdk_storage_or_session_internals():
    root = Path(__file__).resolve().parents[1] / 'src' / 'agent_core' / 'orchestration'
    for path in root.glob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not set((node.module or '').split('.')) & {'agents', 'adapters', 'sqlalchemy', 'psycopg', 'conversation'}
            if isinstance(node, ast.Attribute):
                assert node.attr not in {'_specs', 'new_items', 'session', 'conversations', 'connection', 'transaction'}


def test_scope_and_resource_services_do_not_import_sdk_or_storage_adapters():
    root = Path(__file__).resolve().parents[1] / 'src' / 'agent_core'
    for module in ('context', 'resources'):
        for path in (root / module).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    assert not set((node.module or '').split('.')) & {'adapters', 'agents', 'sqlalchemy', 'psycopg'}


def test_research_uses_public_services_not_foreign_tables_or_sdk():
    root=Path(__file__).resolve().parents[1]/'src/agent_core/research'
    for path in root.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.ImportFrom):
                assert not set((node.module or '').split('.')) & {'agents','adapters','sqlalchemy','psycopg'}
            if isinstance(node,ast.Attribute):
                assert node.attr not in {'connection','run_tables','new_items','_specs','session','pending'}
