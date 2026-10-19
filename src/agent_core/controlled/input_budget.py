"""Conservative incremental budget anchored to one persisted provider measurement."""
from difflib import SequenceMatcher
from ..context.projection import dumps, sha


def byte_bound(value):
    return len(dumps(value).encode())


def header(value):
    # A deadline contributes no model context. Other changed header fields are
    # charged in full rather than assigning them the old measured token count.
    return {k: ({s:v for s,v in value[k].items() if s != 'timeout'} if k == 'settings' else value[k])
            for k in ('model', 'instructions', 'tools', 'output_schema', 'settings')}


def estimate_input(value, observation=None):
    full = byte_bound(value)
    result = {'tokens': full, 'method': 'utf8_upper_bound', 'utf8_upper_bound': full}
    if observation is None: return result
    previous, measured = observation['public_input'], observation['input_tokens']
    current, prior = value['input'], previous['input']
    if (not isinstance(measured, int) or isinstance(measured, bool) or measured <= 0
            or not isinstance(current, list) or not isinstance(prior, list)
            or previous['model'] != value['model']):
        return result
    # Match only complete, byte-identical items in order. A changed summary,
    # tool result, instruction or finalization header is charged anew. Nothing
    # is subtracted for removed/replaced history, so the old measured request
    # still overpays for its discarded material. This also works after compaction
    # and checkpoint recovery without trusting a token/character ratio.
    before, after = [dumps(i) for i in prior], [dumps(i) for i in current]
    matches = SequenceMatcher(a=before, b=after, autojunk=False).get_matching_blocks()
    retained = {i for block in matches for i in range(block.b, block.b + block.size)}
    if not retained: return result
    added = [item for i,item in enumerate(current) if i not in retained]
    generated = observation.get('response_input_items',[])
    output_tokens = observation.get('output_tokens',0)
    from collections import Counter
    known = Counter(dumps(item) for item in generated)
    matched,unmeasured = [],[]
    for item in added:
        key = dumps(item)
        if known[key]>0:
            matched.append(item);known[key]-=1
        else:unmeasured.append(item)
    generated_bound = 0
    if matched and isinstance(output_tokens,int) and not isinstance(output_tokens,bool) and output_tokens>0:
        # Same-model output replay uses its confirmed generation count once,
        # even if only part is retained. IDs, encrypted payloads and framing
        # remain charged by bytes; edited or unrecognized items gain no credit.
        generated_bound = output_tokens + byte_bound([
            {k:v for k,v in item.items() if k not in ('content','summary','arguments')} for item in matched])
        added = unmeasured
    changed_header = header(value) != header(previous)
    # Pay for serialization plus item/boundary framing. The request's separate
    # safety margin and output reserve remain in place at both budget checks.
    delta = byte_bound(added) + generated_bound + 16 * len(current) + 64
    if changed_header: delta += byte_bound(header(value))
    bound = measured + delta
    if bound >= full: return result
    return {'tokens': bound, 'method': 'measured_input_plus_utf8_delta',
        'utf8_upper_bound': full, 'baseline_input_tokens': measured,
        'baseline_input_sha256': sha(dumps(previous)), 'unchanged_items': len(retained),
        'changed_header_charged': changed_header, 'delta_upper_bound': delta}
