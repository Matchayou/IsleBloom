#!/bin/sh
set -eu
full_port=${PGPORT:-55473}
full_user=$(id -un)
full_tag=$(date +%Y%m%d_%H%M%S)_$$
full_evidence=docs/full-regression-evidence
mkdir -p "$full_evidence"
pg_isready -h 127.0.0.1 -p "$full_port"
.venv/bin/python -c 'import psycopg, sqlalchemy, agents, dbos, PIL, pypdfium2'
test -s .stage3/tessdata/eng.traineddata
test -s .stage3/tessdata/chi_sim.traineddata
for full_db in "general_$full_tag" "second_$full_tag" "collaboration_$full_tag"; do
    createdb -h 127.0.0.1 -p "$full_port" "$full_db"
done
for full_stage in 1 2 3 4 5 6 7; do
    createdb -h 127.0.0.1 -p "$full_port" "ce${full_stage}_full_$full_tag"
done
export TEST_DATABASE_URL="postgresql+psycopg://$full_user@127.0.0.1:$full_port/general_$full_tag"
export SECOND_TEST_DATABASE_URL="postgresql+psycopg://$full_user@127.0.0.1:$full_port/second_$full_tag"
export COLLABORATION_TEST_DATABASE_URL="postgresql+psycopg://$full_user@127.0.0.1:$full_port/collaboration_$full_tag"
export COLLABORATION_TEST_BLOBS="/private/tmp/full-collaboration-$full_tag"
export QUALITY_TEST_DATABASE_ADMIN_URL="postgresql+psycopg://$full_user@127.0.0.1:$full_port/postgres"
for full_stage in 1 2 3 4 5 6 7; do
    export "CE${full_stage}_TEST_DATABASE_URL=postgresql+psycopg://$full_user@127.0.0.1:$full_port/ce${full_stage}_full_$full_tag"
done
export ACCEPTANCE_EVIDENCE_DIR="$full_evidence/contracts"
printf 'Fresh full regression databases: %s\n' "$full_tag"
.venv/bin/python -m pytest tests -q -rs --tb=short --junitxml="$full_evidence/results.xml"
.venv/bin/python - <<'PY'
from xml.etree import ElementTree
root = ElementTree.parse('docs/full-regression-evidence/results.xml').getroot()
counts = {key:sum(int(s.get(key, 0)) for s in root.iter('testsuite'))
          for key in ('tests','failures','errors','skipped')}
print(counts)
assert counts['failures'] == counts['errors'] == counts['skipped'] == 0, counts
PY
