"""Acceptance aid: compare report UUID references with pre-delivery observable input."""
import json,re,sys
from pathlib import Path
root=Path(sys.argv[1]);live=json.loads((root/'live.json').read_text())
report=json.dumps(live['result']['output'],ensure_ascii=False)
# Task input, fixed memory and actual provider input precede final output. A report
# cannot mint a new source identifier merely by presenting a plausible UUID.
known=json.dumps({'spec':live['spec'],'summaries':live['summaries'],
    'inputs':[m['manifest']['public_input'] for m in live['manifests']]},ensure_ascii=False)
pattern=r'(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])'
unknown=sorted(set(re.findall(pattern,report))-set(re.findall(pattern,known)))
value={'check':'report_identifier_fidelity','unknown_uuid_references':unknown,'passed':not unknown,
       'limitation':'Exact identifier check only; semantic review still required.'}
(root/'identifier-review.json').write_text(json.dumps(value,indent=2));print(json.dumps(value))
raise SystemExit(bool(unknown))
