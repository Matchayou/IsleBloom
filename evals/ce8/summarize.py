"""Read saved evidence only; semantic scores require independent report review."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

p = argparse.ArgumentParser()
p.add_argument('round_dir', type=Path)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
rows = []
for file in sorted(a.round_dir.glob('*/result.json')):
    d = json.loads(file.read_text())
    requests = []
    saved_notes = []
    for index in file.parent.glob('private/*/index.sqlite'):
        connection = sqlite3.connect(f'file:{index}?mode=ro', uri=True)
        try:
            for name, digest in connection.execute("SELECT name,digest FROM notes WHERE name LIKE 'transcript_%_request'"):
                raw = (index.parent/digest).read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError('Transcript hash mismatch')
                frame = json.loads(raw)
                if frame['operation'] in ('tool', 'private'):
                    value = frame['payload']
                    response_row = connection.execute('SELECT digest FROM notes WHERE name=?',
                        (name.removesuffix('_request')+'_response',)).fetchone()
                    response = json.loads((index.parent/response_row[0]).read_text()) if response_row else None
                    requests.append({'operation_id': frame['operation_id'], 'name': value['name'],
                                     'arguments': json.loads(value['arguments']), 'response': response})
                    if value['name'] == 'write_private_note':
                        args = json.loads(value['arguments'])
                        saved = connection.execute('SELECT digest FROM notes WHERE name=?', (args['name'],)).fetchone()
                        if saved and (index.parent/saved[0]).read_text() == args['text']:
                            saved_notes.append({'name': args['name'], 'sha256': saved[0], 'text': args['text']})
        finally:
            connection.close()
    reads = [r for r in requests if r['name'] in ('read_source', 'search_source', 'read_context_page', 'read_private_note')]
    counter = Counter(json.dumps([r['name'], r['arguments']], sort_keys=True) for r in reads)
    required = {json.dumps(ref, sort_keys=True) for ref in d.get('receipt', {}).get('resource_refs', [])}
    read_refs = {json.dumps(r['arguments']['ref'], sort_keys=True) for r in reads if r['name'] == 'read_source'}
    confirmed_refs = {json.dumps(r['response']['value']['ref'], sort_keys=True) for r in reads
        if r['name'] == 'read_source' and r.get('response') and 'ref' in r['response'].get('value', {})}
    manifests = [m['manifest'] for m in d.get('manifests', [])]
    usage = d.get('result', {}).get('usage', {})
    row = {'case': d['case'], 'arm': d['arm'], 'status': d['status'],
        'delivery': d.get('receipt', {}).get('delivery'),
        'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens'),
        'usage_complete': usage.get('complete'),
        'unconfirmed_model_calls': sum(o['kind'] == 'model' and o['status'] != 'confirmed' for o in d.get('operations', [])),
        'ledger_model_usage_complete': all(o['status'] == 'confirmed' and o.get('usage', {}).get('complete')
            for o in d.get('operations', []) if o['kind'] == 'model'),
        'cached_input_tokens': sum((m.get('usage') or {}).get('cached_input_tokens', 0) or 0 for m in manifests),
        'wire_input_bytes': sum(len(m.get('wire_text', '').encode()) for m in manifests),
        'model_calls': d.get('budget', {}).get('models'), 'tool_calls': d.get('budget', {}).get('tools'),
        'summary_calls': sum(m['purpose'] == 'summary' for m in manifests),
        'summaries_succeeded': sum(c['attempt']['state'] == 'succeeded' for c in d.get('compactions', [])),
        'has_business_projection': any(m.get('business_excluded') for m in manifests),
        'repeated_reads': sum(n-1 for n in counter.values()),
        'required_fixed_sources_requested': bool(required) and required <= read_refs,
        'required_fixed_sources_returned': bool(required) and required <= confirmed_refs,
        'private_note_saved': bool(saved_notes), 'private_notes': saved_notes,
        'foreign_fixed_source_requested': bool(read_refs - required),
        'seconds': d['latency_seconds'], 'tool_requests': requests,
        'evidence': str(file), 'fixture_sha256': d['fixture_sha256'],
        'preregistration_sha256': d['preregistration_sha256'],
        'method_digest': d.get('budget', {}).get('binding', {}).get('method_digest'),
        'semantic_review': 'pending'}
    rows.append(row)
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
print(json.dumps([{k: v for k, v in row.items() if k not in ('tool_requests', 'fixture_sha256', 'preregistration_sha256', 'method_digest')} for row in rows], ensure_ascii=False, indent=2))
