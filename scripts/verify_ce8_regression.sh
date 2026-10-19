#!/bin/sh
set -eu
ce8_tag=${CE8_RUN_TAG:-$(date +%Y%m%d_%H%M%S)}
for ce8_stage in 1 2 3 4 5 6; do
    /opt/homebrew/bin/createdb -h 127.0.0.1 -p 55462 "ce${ce8_stage}_ce8_${ce8_tag}"
done
export CE1_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce1_ce8_${ce8_tag}
export CE2_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce2_ce8_${ce8_tag}
export CE3_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce3_ce8_${ce8_tag}
export CE4_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce4_ce8_${ce8_tag}
export CE5_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce5_ce8_${ce8_tag}
export CE6_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce6_ce8_${ce8_tag}
export TEST_DATABASE_URL="$CE5_TEST_DATABASE_URL"
export COLLABORATION_TEST_DATABASE_URL="$CE2_TEST_DATABASE_URL"
export COLLABORATION_TEST_BLOBS=/private/tmp/ce8-regression-${ce8_tag}-blobs
export CE7_TEST_DATABASE_URL="$CE6_TEST_DATABASE_URL"
export ACCEPTANCE_EVIDENCE_DIR=docs/ce8-evidence/contracts
printf "CE8 fresh database run: %s\n" "$ce8_tag"
.venv/bin/python -m pytest tests -q -rs
