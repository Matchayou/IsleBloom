"""Hard guarantees remain enforced with the generic expert instructions."""
import json

import pytest

from agent_core.execution.ports import Conflict
from test_controlled_collaboration import controlled, seed, answer


@pytest.mark.parametrize('fault', ['invented_quote', 'foreign_source', 'context_artifact_as_source', 'missing_claims'])
async def test_harness_rejects_invalid_evidence_without_prompt_warnings(controlled, fault):
    _, _, _, tasks, _, _, provider = controlled
    node, ref, _ = await seed(controlled)
    spec = tasks.create(research_id=node.research_id, kind='synthesize',
        user_request='Report the source count.', required_refs=(ref,), evidence_required=True)
    citation = dict(ref=ref.model_dump(mode='json'), location='line:1',
        quote='confirmed count 7', relation='supports', reasoning='Count in the source.')
    if fault == 'invented_quote': citation['quote'] = 'confirmed count 999'
    if fault == 'foreign_source': citation['ref']['resource_id'] = 'ungranted-resource'
    if fault == 'context_artifact_as_source':
        # The failed live trial copied a working-memory SHA into all three IDs.
        citation['ref'] = dict.fromkeys(('resource_id', 'snapshot_id', 'parse_id'), '8' * 64)
    claims = [] if fault == 'missing_claims' else [dict(statement='Count 7', citations=[citation])]
    attempts = 3
    provider.plans.append([answer({'report':'Count 7.', 'claims':claims})] * attempts)
    receipt = (await tasks.run(spec.id))['receipt']
    result = tasks.ledger.get(receipt.run_id).result
    assert result.error.code == 'evidence_invalid'
    assert receipt.delivery == 'rework' and receipt.artifact is None
    assert len(provider.requests) == attempts
    assert len(result.output_repairs) == attempts
    assert all(not r['tools'] for r in provider.requests[1:])


@pytest.mark.parametrize('metadata', [dict(company='Sample (consolidated)'),
    dict(period='next year (forecast)'), dict(basis='confirmed, count 7')])
async def test_citation_metadata_corrected_before_publication_within_same_run(controlled, metadata):
    _, _, _, tasks, _, _, provider = controlled
    node, ref, _ = await seed(controlled)
    spec = tasks.create(research_id=node.research_id, kind='synthesize',
        user_request='Report the source count.', required_refs=(ref,), evidence_required=True)
    citation = dict(ref=ref.model_dump(mode='json'), location='line:1', quote='confirmed count 7',
        relation='supports', reasoning='The source confirms 7.', fields=metadata)
    draft = {'report':'Count 7.', 'claims':[dict(statement='Count 7', citations=[citation])]}
    async def correct(kwargs):
        assert not kwargs['tools']
        assert 'evidence_invalid' in kwargs['input'][-1]['content']
        assert 'Cited' in kwargs['input'][-1]['content']
        return answer({**draft, 'claims':[dict(statement='Count 7', citations=[{**citation, 'fields':{}}])]})
    provider.plans.append([answer(draft), correct])
    receipt = (await tasks.run(spec.id))['receipt']
    result = tasks.ledger.get(receipt.run_id).result
    assert receipt.artifact is not None and receipt.delivery == 'pending_review'
    assert result.status == 'succeeded' and len(result.output_repairs) == 1
    assert json.loads(result.output_repairs[0].raw_output)['claims'][0]['citations'][0]['fields'].items() >= metadata.items()
    bundle = tasks.research.evidence.read(receipt.result_id)
    assert bundle.evidence[0].quote == 'confirmed count 7'
    assert all(value is None for value in bundle.evidence[0].fields.model_dump().values())
    assert tasks.engine.ledger.run_budget(receipt.run_id)['models'] == 2


async def test_citation_repair_cannot_exceed_original_model_limit(controlled):
    from agent_core.contracts import RunLimits
    _, _, _, tasks, _, _, provider = controlled
    node, ref, _ = await seed(controlled)
    spec = tasks.create(research_id=node.research_id, kind='synthesize', user_request='Report count.',
        required_refs=(ref,), evidence_required=True, limits=RunLimits(max_turns=1))
    provider.plans.append([answer({'report':'Count 7.', 'claims':[dict(statement='Count 7', citations=[dict(
        ref=ref.model_dump(mode='json'), location='line:1', quote='invented quote', relation='supports', reasoning='Invalid.')])]})])
    receipt = (await tasks.run(spec.id))['receipt']
    result = tasks.ledger.get(receipt.run_id).result
    assert result.error.code == 'evidence_invalid' and receipt.artifact is None
    assert len(provider.requests) == len(result.output_repairs) == 1


async def test_source_fact_and_history_only_constraint_have_distinct_evidence(controlled):
    _, _, _, tasks, _, _, provider = controlled
    node, ref, _ = await seed(controlled)
    spec = tasks.create(research_id=node.research_id, kind='synthesize',
        user_request='Report the source count. Earlier conversation says the replacement draft is unapproved.',
        required_refs=(ref,), evidence_required=True)
    provider.plans.append([answer({'report':'Source confirms count 7. Conversation history says the replacement draft is unapproved; its original approval record is unavailable.',
        'claims':[
            dict(statement='Count 7', citations=[dict(ref=ref.model_dump(mode='json'),
                location='line:1', quote='confirmed count 7', relation='supports', reasoning='Source count.')]),
            dict(statement='The replacement draft is unapproved according to conversation history.',
                missing=['Conversation history only; no original approval record was supplied.'])]})])
    receipt = (await tasks.run(spec.id))['receipt']
    assert receipt.delivery == 'pending_review' and receipt.artifact is not None
    assert tasks.ledger.get(receipt.run_id).result.status == 'succeeded'
    assert all(check.status == 'pending' for check in receipt.semantic_checks)


async def test_harness_owns_contract_and_review_status(controlled):
    _, _, _, tasks, _, _, provider = controlled
    node, ref, _ = await seed(controlled)
    spec = tasks.create(research_id=node.research_id, kind='extract',
        user_request='Extract the required fields.', required_refs=(ref,))
    async def respond(kwargs):
        # Field names come from the admitted task contract, not the role prompt.
        context = json.dumps(kwargs['input'])
        assert all(field in context for field in spec.required_fields)
        return answer({'report':'Extracted the available information.',
            'fields':{'company':'Sample','period':'2026','operating_cash_flow':'7',
                      'capital_expenditure':'1','unit':'units'},
            'sources':{'operating_cash_flow':'line:1','capital_expenditure':'line:1','unit':'line:1'},
            'self_checks':{str(i.number):'passed' for i in spec.acceptance}})
    provider.plans.append([respond])
    receipt = (await tasks.run(spec.id))['receipt']
    assert receipt.delivery == 'pending_review'
    assert all(check.status == 'pending' for check in receipt.semantic_checks)
    with pytest.raises(Conflict): tasks.adopt(receipt.result_id)


def test_startup_preserves_published_methods_even_when_default_version_matches():
    from types import SimpleNamespace
    from agent_core.adapters.memory_ledger import MemoryDatabase
    from agent_core.adapters.memory_methods import MemoryMethods
    from agent_core.composition import build_methods
    from agent_core.methods.models import AssetVersion
    from agent_core.methods.defaults import EXPERT_METHODS
    db = MemoryDatabase()
    db.repositories['methods'] = MemoryMethods()
    kind, version, prompt = EXPERT_METHODS['expert_synthesize']
    published = AssetVersion(category='method', asset_id='expert_synthesize', version=version,
        description='Published synthesis role', body={'kind':kind,'instructions':prompt},
        author='operator', evidence='Reviewed publication', sanitized=True)
    with db.transaction() as tx:
        tx.methods.add(published)
        tx.methods.activate('method',published.asset_id,version,None,'operator','Publish')
    methods = build_methods(db, SimpleNamespace())
    assert methods.resolve('method',published.asset_id) == published
    build_methods(db, SimpleNamespace())
    assert methods.resolve('method',published.asset_id) == published
    assert len([r for r in methods.query('history') if r['asset_id']==published.asset_id]) == 1
    db.close()


async def test_invalid_expert_kind_is_corrected_before_catalog_query(controlled):
    from uuid import uuid4
    from agent_core.research.models import NodeCommand
    from test_controlled_collaboration import call
    _, _, research, _, _, _, provider = controlled
    node, _, chat = await seed(controlled)
    async def correct(kwargs):
        result = json.loads(kwargs['input'][-1]['output'])['value']
        assert result['status'] == 'invalid_input' and result['executed'] is False
        assert result['issues'][0]['type'] == 'literal_error'
        return call('find_experts', kind='synthesize')
    async def finish(kwargs):
        result = json.loads(kwargs['input'][-1]['output'])['value']
        assert any(row['expert_id']=='expert_synthesize' for row in result['experts'])
        return answer({'report':'Found a synthesis expert.'})
    provider.plans.append([call('select_task_tools',profile='experts'),
        call('find_experts',kind='null'),correct,finish])
    command = await research.submit(NodeCommand(command_id=uuid4().hex,
        research_id=node.research_id,node_id=node.id,kind='chat_reply',chat_revision=chat.revision))
    assert research.ledger.get(command.run_id).status == 'succeeded'
