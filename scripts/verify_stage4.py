"""Run and preserve actual cumulative acceptance output; no missing env passes."""
import os
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parents[1]
if not os.environ.get('TEST_DATABASE_URL'):
    print('TEST_DATABASE_URL required');raise SystemExit(2)
with (root/'docs/stage-4-tests.txt').open('w') as stream:
    result=subprocess.run([sys.executable,'-m','pytest','-v'],cwd=root,stdout=stream,stderr=subprocess.STDOUT)
print((root/'docs/stage-4-tests.txt').read_text()[-2500:])
raise SystemExit(result.returncode)
