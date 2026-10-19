"""Serial real-model matrix; one application process at a time, no hidden retries."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument('--round', required=True)
p.add_argument('--tag', required=True)
p.add_argument('--skip', action='append', default=[])
a = p.parse_args()
env = dict(os.environ)
env['OPENAI_API_KEY'] = getpass.getpass('DeepSeek credential (hidden): ')
root = Path('.ce8')/a.round
root.mkdir(parents=True, exist_ok=True)
records = []
for case in ('equipment', 'cashflow', 'orders'):
    for arm in ('full', 'projection', 'compaction'):
        name = f'{case}-{arm}'
        if name in a.skip:
            continue
        database = f'ce8_{case}_{arm}_{a.tag}'
        subprocess.run(['/opt/homebrew/bin/createdb', '-h', '127.0.0.1', '-p', '55462', database], check=True)
        env['CE8_TEST_DATABASE_URL'] = f'postgresql+psycopg://chenyouran@127.0.0.1:55462/{database}'
        command = [sys.executable, 'evals/ce8/run.py', '--case', case, '--arm', arm, '--output', str(root/name)]
        print(f'Starting {name}', flush=True)
        with (root/f'{name}.log').open('w') as log:
            try:
                result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=420)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 'timeout'
        records.append({'case': case, 'arm': arm, 'database': database, 'exit_code': code})
        (root/'matrix.json').write_text(json.dumps(records, indent=2))
        print(f'Finished {name}: {code}', flush=True)
raise SystemExit(0 if all(r['exit_code'] == 0 for r in records) else 1)
