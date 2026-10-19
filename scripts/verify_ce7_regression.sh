#!/bin/sh
set -eu
ce7_tag=${CE7_RUN_TAG:-$(date +%Y%m%d_%H%M%S)}
for ce7_stage in 1 2 3 4 5 6; do
    /opt/homebrew/bin/createdb -h 127.0.0.1 -p 55462 "ce${ce7_stage}_ce7_${ce7_tag}"
done
export CE1_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce1_ce7_${ce7_tag}
export CE2_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce2_ce7_${ce7_tag}
export CE3_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce3_ce7_${ce7_tag}
export CE4_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce4_ce7_${ce7_tag}
export CE5_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce5_ce7_${ce7_tag}
export CE6_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce6_ce7_${ce7_tag}
export TEST_DATABASE_URL="$CE5_TEST_DATABASE_URL"
export COLLABORATION_TEST_DATABASE_URL="$CE2_TEST_DATABASE_URL"
export COLLABORATION_TEST_BLOBS=/private/tmp/ce7-regression-${ce7_tag}-blobs
export CE7_TEST_DATABASE_URL="$CE6_TEST_DATABASE_URL"
printf "CE7 fresh database run: %s\n" "$ce7_tag"
.venv/bin/python -m pytest tests/test_durable.py tests/test_durable_process.py tests/test_checkpoints.py tests/test_checkpoint_process.py tests/test_checkpoint_sdk.py tests/test_compaction.py tests/test_compaction_controlled.py tests/test_coding.py tests/test_coding_controlled.py tests/test_controlled.py tests/test_controlled_external.py tests/test_controlled_collaboration.py tests/test_controlled_delivery.py tests/test_context_projection.py tests/test_sdk_contract.py tests/test_tasks.py tests/test_methods.py tests/test_routing.py tests/test_collaboration.py tests/test_report_delivery.py tests/test_report_information.py tests/test_information_requests.py tests/test_workspace_api.py -q
