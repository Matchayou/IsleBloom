"""Select optional context without losing user statements or expanding access."""
from .retrieval import rank
from .skills import SkillCatalog
from .projection import sha
from .scopes import ScopeDenied
from ..execution.ports import Conflict


class ContextSelector:
    def __init__(self, skills=None):
        self.skills = skills or SkillCatalog()

    def source_candidates(self, scopes, scope, refs, selected_refs=()):
        sources = []
        for ref in refs:
            scopes.check(scope, ref)
            resource, _, parsed = scopes.resources.describe(ref)
            metadata = scopes.resources.source_metadata(ref)
            value = {'ref': ref.model_dump(mode='json'), 'name': resource.name, 'kind': resource.kind,
                'parse_status': parsed.status, **metadata, 'missing': [i.model_dump() for i in parsed.issues]}
            if ref not in selected_refs:
                value['reading'] = 'Authorized directory entry; read selected bodies through source tools.'
            sources.append({'value': value, 'search_text': resource.name + ' ' + str(metadata) + ' ' +
                ' '.join(f.text for f in parsed.fragments)})
        return sources

    def candidates_for_chat(self, research, node, history, refs):
        root = research.research(node.research_id)
        root_scope = research.scopes.current(root.scope_id, research.owner)
        main = node.display_parent_id is None
        excluded = research._excluded_refs(root.id)
        available = tuple(ref for ref in (root_scope.allowed if main else refs) if ref not in excluded)
        sources = self.source_candidates(research.scopes, root_scope, available, refs)
        for row in sources:
            ref = row['value']['ref']
            if not any(r.model_dump(mode='json') == ref for r in refs):
                row['value']['reading'] = 'Library metadata only. First use read_context with this resource_refs entry to read and select it for this run.'
        results = []
        if main and getattr(research, 'collaboration', None) is not None:
            service = research.collaboration
            for spec in service.tasks.query('list_specs', root.id):
                for run in service.tasks.query('list_runs', spec.id):
                    receipt = service.tasks.query('receipt', f'task-result:{run.id}')
                    if receipt is None: continue
                    try:
                        service.validate_task_metadata(root.id, service.tasks.spec(spec.id, receipt.spec_version))
                        service.tasks.receipt(receipt.result_id)
                        for ref in receipt.resource_refs: research.scopes.check(root_scope, ref)
                    except (ScopeDenied, Conflict, KeyError):
                        continue  # Unavailable bodies must not leak through retrieval metadata.
                    stored = service.tasks.ledger.get(receipt.run_id)
                    if stored is None or stored.result is None: continue
                    output = stored.result.output
                    report = output.get('report', '') if isinstance(output, dict) else str(output)
                    value = {'result_id': receipt.result_id, 'task_id': spec.id,
                        'session_id': spec.expert_session_id, 'goal': spec.goal,
                        'delivery': receipt.delivery, 'preview': report[:320],
                        'instruction': 'Read the selected result_id with read_context before using its conclusions. Delivery status is not adoption.'}
                    results.append({'value': value, 'search_text': spec.goal + ' ' + report})
        memories = [m for m in research.memories.list(root.id) if m.available()] if getattr(research, 'memories', None) else []
        return {'history': history, 'memories': memories, 'sources': sources, 'related_results': results}

    def select(self, query, *, history=(), memories=(), sources=(), results=(), skill_entries=()):
        history = list(history)
        matches = rank(query, [m['content'] for m in history], limit=6)
        # Every user statement remains verbatim, including old corrections. Recent
        # answers supply conversational continuity when a request uses pronouns.
        selected = {i for i, m in enumerate(history) if m['role'] == 'user'}
        selected.update(i for i, _ in matches)
        selected.update(range(max(0, len(history)-3), len(history)))
        catalog = self.skills.freeze() + list(skill_entries)
        skills = self.skills.search(catalog, query, 3)
        source_matches = rank(query, [s['search_text'] for s in sources], limit=6)
        result_matches = rank(query, [r['search_text'] for r in results], limit=4)
        return {'history': [m for i, m in enumerate(history) if i in selected],
            'sources': [sources[i]['value'] for i, _ in source_matches],
            'results': [results[i]['value'] for i, _ in result_matches],
            'skills': [{'name': e['name'], 'description': e['description'], 'sha256': e['sha256'],
                'reason': e['reason'], 'instruction': 'Candidate only; use load_skill after judging relevance.'} for e in skills],
            'catalog': catalog,
            'memory_catalog': [{'id': m.id, 'version': m.version} for m in memories],
            'manifest': {'algorithm': 'sqlite-fts5-bm25', 'query_sha256': sha(query),
                'history_ids': [m.get('id') for i, m in enumerate(history) if i in selected],
                'omitted_history_ids': [m.get('id') for i, m in enumerate(history) if i not in selected],
                'history_reason': 'all_user_statements+recent_3+ranked_6',
                'memory_refs': [], 'memory_algorithm': 'llm-side-query',
                'source_total': len(sources), 'result_total': len(results),
                'source_matches': [{'ref': sources[i]['value']['ref'], 'score': score} for i, score in source_matches],
                'result_matches': [{'result_id': results[i]['value']['result_id'], 'score': score} for i, score in result_matches],
                'skills': [{'name': e['name'], 'sha256': e['sha256'], 'reason': e['reason']} for e in skills]}}
