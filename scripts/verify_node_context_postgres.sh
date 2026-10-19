#!/bin/sh
set -eu
node_pg_port=${PGPORT:-55473}
node_pg_user=$(id -un)
node_tag=$(date +%Y%m%d_%H%M%S)_$$
node_evidence=docs/node-context-evidence
mkdir -p "$node_evidence"
pg_isready -h 127.0.0.1 -p "$node_pg_port"
.venv/bin/python -c 'import psycopg, sqlalchemy, agents, dbos; print("PostgreSQL driver, SQLAlchemy, Agents SDK and DBOS imports OK")'
for node_database in "node_context_$node_tag" "ce2_node_context_$node_tag" "node_second_$node_tag"; do
    createdb -h 127.0.0.1 -p "$node_pg_port" "$node_database"
done
export TEST_DATABASE_URL="postgresql+psycopg://$node_pg_user@127.0.0.1:$node_pg_port/node_context_$node_tag"
export CE2_TEST_DATABASE_URL="postgresql+psycopg://$node_pg_user@127.0.0.1:$node_pg_port/ce2_node_context_$node_tag"
export SECOND_TEST_DATABASE_URL="postgresql+psycopg://$node_pg_user@127.0.0.1:$node_pg_port/node_second_$node_tag"
.venv/bin/python -m pytest -q -rs --tb=short --junitxml="$node_evidence/results.xml" \
    tests/test_node_dynamic_context.py tests/test_node_controlled_postgres.py \
    tests/test_research.py tests/test_context_layer_2.py tests/test_context_layer_3.py \
    tests/test_context_layer_4.py tests/test_dynamic_context.py tests/test_planning.py \
    tests/test_controlled_collaboration.py tests/test_postgres_process.py
.venv/bin/python - <<'PY'
from pathlib import Path
from xml.etree import ElementTree
root = ElementTree.parse(Path('docs/node-context-evidence/results.xml')).getroot()
counts = {key: sum(int(s.get(key, 0)) for s in root.iter('testsuite'))
          for key in ('tests', 'failures', 'errors', 'skipped')}
print(counts)
assert counts['failures'] == counts['errors'] == counts['skipped'] == 0, counts
PY
