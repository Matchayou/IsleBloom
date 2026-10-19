"""Program checks location and scope; support is a separate semantic judgment."""
import re
from difflib import SequenceMatcher
from ..context.scopes import ScopeDenied
from ..execution.ports import Conflict
from .models import Claim, ClaimEvidence, Evidence, EvidenceBundle


class CitationMismatch(ValueError):
    def __init__(self, message, hint):
        super().__init__(message)
        self.hint = hint


class EvidenceService:
    def __init__(self, database, scopes, research):
        self.database, self.scopes, self.research = database, scopes, research

    def validate_output(self, result_id, binding, output):
        """SDK correction preflight; the publication transaction repeats this check."""
        from ..contracts import CoreFailure, RunError
        try:
            self.prepare(result_id, binding, output)
        except (ScopeDenied, Conflict, KeyError) as exc:
            raise CoreFailure(RunError(code='evidence_invalid', origin='app', message=str(exc))) from exc
        except CitationMismatch as exc:
            return ({'type':'evidence_invalid','loc':['claims'],'msg':str(exc),
                'source_excerpt':exc.hint},)
        except ValueError as exc:
            return ({'type':'evidence_invalid', 'loc':['claims'], 'msg':str(exc)},)
        return ()

    def prepare(self, result_id, binding, output):
        drafts = output.get('claims', ())
        if binding.evidence_required and not drafts:
            raise ValueError('Evidence run has no structured claims')
        node = self.research.node(binding.node_id) if binding.node_id else None
        root = self.research.research(node.research_id if node else binding.research_id)
        scope = self.scopes.current(getattr(binding,'scope_id',node.scope_id if node else root.scope_id),
                                    self.research.owner)
        root_scope = self.scopes.current(root.scope_id, self.research.owner)
        claims, evidence, relations = [], [], []
        source_cache = {}
        allowed_refs = (self.research.run_resource_refs(binding)
                        if hasattr(binding, 'chat_revision') else
                        self.research.collaboration.tasks.run_resource_refs(binding)
                        if getattr(self.research, 'collaboration', None) else binding.resource_refs)
        for ci, draft in enumerate(drafts):
            claim_id = f'{result_id}:claim:{ci}'
            claims.append(Claim(id=claim_id, result_id=result_id, statement=draft['statement']))
            for ei, cite in enumerate(draft.get('citations', ())):
                ref = cite['ref']
                from ..resources.snapshots import ResourceRef
                ref = ResourceRef.model_validate(ref)
                if ref not in allowed_refs:
                    # A draft identifier error grants no access. Correct it through
                    # the SDK; revoked scopes below remain terminal access failures.
                    raise ValueError('Citation is outside the fixed run input; copy a complete body ref actually returned by a source tool, or mark the claim evidence gap without a citation')
                if ref not in source_cache:
                    self.scopes.check(scope, ref)
                    self.scopes.check(root_scope, ref)
                    resource, _, parsed = self.scopes.resources.describe(ref)
                    source_cache[ref] = (resource,parsed,self.scopes.read(scope,ref))
                resource,parsed,all_fragments = source_cache[ref]
                if resource.kind == 'discovery':
                    raise ValueError('Search discovery is not readable source evidence')
                location, quote = cite['location'], cite['quote']
                matching = [f for f in all_fragments if _canonical(quote) in _canonical(f.text)]
                if not matching:
                    fragment = next((f for f in all_fragments if f.location == location), None)
                    hint = None
                    if fragment:
                        text = _canonical(fragment.text)
                        match = SequenceMatcher(None,_canonical(quote),text,autojunk=False).find_longest_match()
                        start = max(0,match.b-match.a)
                        hint = {'ref':ref.model_dump(),'location':fragment.location,
                            'text':text[start:start+800],
                            'instruction':'Copy a verbatim substring from this saved excerpt. This is a correction hint, not an accepted citation; preserve punctuation, PDF ligatures and code escaping.'}
                    raise CitationMismatch(f'Citation quote {quote[:160]!r} does not occur in the saved source',hint)
                if location not in {f.location for f in matching}:
                    if len(matching) != 1:
                        raise ValueError('Citation location is ambiguous for the quoted text')
                    location = matching[0].location
                fields = cite.get('fields') or {}
                page = re.match(r'^page:\d+',location)
                nearby = (f for f in parsed.fragments if page is None or
                    f.location == page.group() or f.location.startswith(page.group()+':'))
                body = _canonical('\n'.join(f.text for f in nearby))
                for key, value in fields.items():
                    field_body = (_canonical('\n'.join(f.text for f in parsed.fragments))
                                  if key == 'company' else body)
                    if value is not None and not _field_present(key, value, field_body):
                        raise ValueError(f'Cited {key} {value!r} is not present in the parsed source')
                evidence_id = f'{result_id}:evidence:{ci}:{ei}'
                evidence.append(Evidence(id=evidence_id, result_id=result_id, ref=ref,
                    location=location, quote=quote, fields=fields))
                relations.append(ClaimEvidence(id=f'{result_id}:link:{ci}:{ei}', result_id=result_id,
                    claim_id=claim_id, evidence_id=evidence_id, relation=cite['relation'],
                    reasoning=cite['reasoning']))
            for mi, gap in enumerate(draft.get('missing', ())):
                relations.append(ClaimEvidence(id=f'{result_id}:gap:{ci}:{mi}', result_id=result_id,
                    claim_id=claim_id, relation='missing', reasoning=gap))
        return EvidenceBundle(claims=tuple(claims), evidence=tuple(evidence), relations=tuple(relations))

    def read(self, result_id):
        if result_id.startswith('task-result:'):
            tasks = self.research.chat_tasks
            if tasks is None: raise KeyError(result_id)
            receipt = tasks.receipt(result_id)
            spec = tasks.spec(receipt.task_id,receipt.spec_version)
            root = self.research.research(spec.research_id)
            node = self.research.node(spec.node_id) if spec.node_id else None
            if node and node.research_id != root.id: raise ScopeDenied('Foreign expert branch')
            if node and node.selection == 'excluded': raise ScopeDenied('Expert branch excluded')
            binding = tasks.query('run',receipt.run_id)
            if binding is None: raise KeyError(receipt.run_id)
        else:
            result = self.research.check_result(result_id, allow_excluded=True)
            node = self.research.node(result.node_id)
            root = self.research.research(node.research_id)
        with self.database.transaction() as tx:
            bundle = tx.evidence.read(result_id)
        scope = self.scopes.current(binding.scope_id if result_id.startswith('task-result:') else node.scope_id,
                                    self.research.owner)
        root_scope = self.scopes.current(root.scope_id, self.research.owner)
        for item in bundle.evidence:
            self.scopes.check(scope, item.ref)
            self.scopes.check(root_scope, item.ref)
        return bundle

    def source_scope(self, result_id):
        if result_id.startswith('task-result:'):
            tasks = self.research.chat_tasks
            if tasks is None: raise KeyError(result_id)
            receipt = tasks.receipt(result_id)
            spec = tasks.spec(receipt.task_id,receipt.spec_version)
            self.research.research(spec.research_id)
            binding = tasks.query('run',receipt.run_id)
            if binding is None: raise KeyError(receipt.run_id)
            return self.scopes.current(binding.scope_id,self.research.owner)
        result = self.research.check_result(result_id,allow_excluded=True)
        return self.scopes.current(self.research.node(result.node_id).scope_id,self.research.owner)

    def locate(self, result_id, evidence_id):
        bundle = self.read(result_id)
        item = next((e for e in bundle.evidence if e.id == evidence_id), None)
        if item is None: raise KeyError(evidence_id)
        resource, snapshot, parsed = self.scopes.resources.describe(item.ref)
        fragment = next((f for f in parsed.fragments if f.location == item.location and
            _canonical(item.quote) in _canonical(f.text)), None)
        if fragment is None: raise Conflict('Fixed citation no longer matches the parse')
        return {'evidence':item, 'fragment':fragment, 'source':resource,
                'snapshot':snapshot, 'parse':{'id':parsed.id,'status':parsed.status,'issues':parsed.issues}}

    def review(self, result_id, relation_id, assessment, reason):
        if assessment not in ('supported', 'unsupported', 'uncertain') or not reason.strip():
            raise ValueError('Evidence review needs a judgment and reason')
        bundle = self.read(result_id)
        relation = next((r for r in bundle.relations if r.id == relation_id), None)
        if relation is None: raise KeyError(relation_id)
        if relation.relation == 'missing' and assessment == 'supported':
            raise ValueError('Missing evidence cannot be marked supported')
        with self.database.transaction() as tx:
            return tx.evidence.review(relation_id, assessment, reason)


def _canonical(text):
    return re.sub(r'\s+',' ',text).strip()


def _field_present(key, value, body):
    if _canonical(value).casefold() in body.casefold(): return True
    if key == 'unit':
        unit = re.fullmatch(r'(millions?|billions?) of (?:u\.s\. )?dollars',
                            _canonical(value), re.IGNORECASE)
        if unit:
            scale = unit.group(1).lower().rstrip('s')
            return (re.search(rf'\b{scale}s?\b', body, re.IGNORECASE) is not None and
                    ('$' in body or re.search(r'\bdollars?\b', body, re.IGNORECASE) is not None))
    if key != 'period': return False
    normalized = _canonical(value)
    quarter = re.fullmatch(r'(\d{4})\s*Q([1-4])', normalized, re.IGNORECASE)
    reversed_quarter = re.fullmatch(r'Q([1-4])\s*(\d{4})', normalized, re.IGNORECASE)
    if quarter:
        year, number = quarter.groups()
    elif reversed_quarter:
        number, year = reversed_quarter.groups()
    else:
        return False
    return re.search(rf'\b{number}(?:st|nd|rd|th) quarter {year}\b', body,
                     re.IGNORECASE) is not None
