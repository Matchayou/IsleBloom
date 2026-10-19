#!/bin/sh
set -eu
browser_pg_port=${PGPORT:-55473}
browser_user=$(id -un)
browser_tag=$(date +%Y%m%d_%H%M%S)_$$
browser_evidence=docs/full-regression-evidence
mkdir -p "$browser_evidence"
.venv/bin/python - <<'PY'
import socket
for port in (18744,18769):
    with socket.socket() as s:
        s.bind(('127.0.0.1',port))
PY
createdb -h 127.0.0.1 -p "$browser_pg_port" "browser_$browser_tag"
createdb -h 127.0.0.1 -p "$browser_pg_port" "plan_browser_$browser_tag"
(cd frontend && npm run build) > "$browser_evidence/frontend-build.log" 2>&1
DATABASE_URL="postgresql+psycopg://$browser_user@127.0.0.1:$browser_pg_port/browser_$browser_tag" BROWSER_TEST_PORT=18744 \
    .venv/bin/python scripts/verify_project_browser_server.py > "$browser_evidence/project-server.log" 2>&1 &
browser_pid=$!
DATABASE_URL="postgresql+psycopg://$browser_user@127.0.0.1:$browser_pg_port/plan_browser_$browser_tag" BROWSER_PLAN_PORT=18769 \
    .venv/bin/python scripts/verify_plan_browser_server.py > "$browser_evidence/plan-server.log" 2>&1 &
plan_pid=$!
trap 'kill "$browser_pid" "$plan_pid" 2>/dev/null || true' EXIT INT TERM
.venv/bin/python - <<'PY'
import httpx,time
for port in (18744,18769):
    for attempt in range(60):
        try:
            response=httpx.get(f'http://127.0.0.1:{port}/api/projects',timeout=1)
            response.raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(.25)
    else:
        raise RuntimeError(f'Isolated test server did not start: {port}')
PY
(cd frontend && ./node_modules/.bin/playwright test)
