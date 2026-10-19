import copy
import importlib.util
from pathlib import Path
import pytest

module_spec=importlib.util.spec_from_file_location('repair',Path(__file__).parents[1]/'scripts/repair_workspace_agent_records.py')
repair=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(repair)


def fixture():
    limits={'max_turns':4,'timeout_seconds':180,'max_calls':8}
    spec={'id':'task','version':1,'research_id':'research','expert_session_id':'session','context_packet_id':'packet',
        'user_request':'Original request','goal':'Original goal','kind':'synthesize','expert_id':'expert_synthesize',
        'method_version':'method','method_digest':'digest','model':'model','acceptance':[],'limits':limits}
    run={'id':'run','task_id':'task','spec_version':1,'context_snapshot_id':'snapshot','scope_id':'scope','scope_version':1,
        'model':'model','method_version':'method','method_digest':'digest','research_id':'research'}
    snapshot={'id':'snapshot','run_id':'run','scope_id':'scope','scope_version':1,'scope':{'id':'scope','owner':'local','version':1},
        'mounts':[],'input':{'messages':[{'role':'user','content':'FROZEN ORIGINAL BODY'}],'external_context':{}}}
    packet={'id':'packet','research_id':'research','sender_node_id':'node','sender_chat_revision':1,
        'user_message_id':'message','user_instruction':'Original request','manager_instruction':'Original goal','delivery_requirements':'Report'}
    session={'id':'session','research_id':'research','expert_id':'expert_synthesize','method_version':'method'}
    return {'task_specs':[spec],'task_runs':[run],'context_snapshots':[snapshot],
        'collaboration_packets':[packet],'collaboration_sessions':[session],
        'runs':[{'run_id':'run','request':{'agent_id':'expert_synthesize','limits':limits}}]}


def test_plan_preserves_bodies_and_is_idempotent():
    rows=fixture();before=copy.deepcopy(rows)
    plan=repair.build_plan(rows)
    assert rows==before and len(plan)==5
    for p in plan:
        assert set(p['new'])-set(p['old'])<=({'agent','project','limits'})
        if p['table']=='context_snapshots':assert p['new']['input']==p['old']['input']
        rows[p['table']]=[p['new']]
    assert repair.build_plan(rows)==[]


@pytest.mark.parametrize('kind',['identity','limits','session'])
def test_disagreement_aborts_repair(kind):
    rows=fixture()
    if kind=='identity':rows['runs'][0]['request']['agent_id']='other'
    elif kind=='limits':rows['runs'][0]['request']['limits']={**rows['runs'][0]['request']['limits'],'max_turns':5}
    else:rows['collaboration_sessions'][0]['research_id']='foreign'
    with pytest.raises(ValueError):repair.build_plan(rows)
