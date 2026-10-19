#!/bin/sh
set -eu
export CE1_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce1_ce6_regression
export CE2_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce2_ce6_regression
export CE3_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce3_ce6_regression
export CE4_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce4_ce6_regression
export CE5_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce5_ce6_regression
export CE6_TEST_DATABASE_URL=postgresql+psycopg://chenyouran@127.0.0.1:55462/ce6_contract_20261007
export TEST_DATABASE_URL="$CE5_TEST_DATABASE_URL"
export COLLABORATION_TEST_DATABASE_URL="$CE2_TEST_DATABASE_URL"
export COLLABORATION_TEST_BLOB_ROOT=/private/tmp/ce6-regression-blobs
.venv/bin/python -m pytest tests/test_compaction.py tests/test_compaction_controlled.py tests/test_coding.py tests/test_coding_controlled.py tests/test_controlled.py tests/test_controlled_external.py tests/test_controlled_collaboration.py tests/test_controlled_delivery.py tests/test_context_projection.py tests/test_sdk_contract.py tests/test_tasks.py tests/test_methods.py tests/test_routing.py tests/test_collaboration.py tests/test_report_delivery.py tests/test_report_information.py tests/test_information_requests.py tests/test_workspace_api.py -q
