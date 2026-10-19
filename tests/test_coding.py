"""Real local Git and trusted process tests; these are not live model evidence."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
from agent_core.coding.git import git, output
from agent_core.coding.models import CodingContract
from agent_core.coding.workspace import CodingService, TestProfile as ApprovedTestProfile

@pytest.fixture
def coding(tmp_path):
    repo=tmp_path/'source'; repo.mkdir();git(repo,'init')
    (repo/'a.py').write_text('ANSWER = 1\n');(repo/'b.py').write_text('ANSWER = 2\n')
    git(repo,'add','.');git(repo,'commit','-m','fixture')
    validator=tmp_path/'trusted.py'
    validator.write_text("import ast,os,pathlib\np=pathlib.Path(os.environ['CODE_SOURCE'])\nfor f in p.glob('*.py'): ast.parse(f.read_text())\nassert not (p/'.git').exists()\nprint('All Python files parsed without executing repository code')\n")
    profile=ApprovedTestProfile('syntax',(sys.executable,'-I',str(validator)),'python-ast-v1')
    service=CodingService(tmp_path/'managed',{'demo':repo},[profile])
    baseline=output(repo,'rev-parse','HEAD')
    def admit(task='one', paths=('a.py','new.py'), validate=lambda:None):
        contract=CodingContract(repository_id='demo',baseline=baseline,
            read_paths=('a.py','b.py'),write_paths=paths,test_profiles=('syntax',))
        return service.admit(SimpleNamespace(id=task,version=1,research_id='research',coding=service.bind_contract(contract)),'g1',validate)
    yield service,admit,repo
    service.close()

async def test_two_worktrees_freeze_restart_and_new_files(coding):
    service,admit,source=coding
    one,two=admit(),admit('two',('b.py',))
    one.write('a.py','ANSWER = 3\n');one.write('new.py','NEW = True\n')
    two.write('b.py','ANSWER = 4\n')
    await asyncio.gather(one.test('syntax'),two.test('syntax'))
    a,b=one.freeze(),two.freeze()
    assert a.commit!=b.commit and 'new.py' in a.files
    assert one.read('b.py')=='ANSWER = 2\n' and two.read('a.py')=='ANSWER = 1\n'
    assert (source/'a.py').read_text()=='ANSWER = 1\n'
    assert admit().freeze()==a
    with pytest.raises(PermissionError):one.write('a.py','bad')
    assert one.verify_candidate(a.model_dump())==a

@pytest.mark.parametrize('path',['../a','/etc/passwd','.git/config','a/../../b','.env','.ssh/id_rsa','a.pem','a//b','node_modules/a'])
def test_unsafe_paths_denied(coding,path):
    _,admit,_=coding;work=admit()
    with pytest.raises((ValueError,PermissionError)):work.read(path)
    with pytest.raises((ValueError,PermissionError)):work.write(path,'x')

async def test_stale_test_forged_candidate_links_and_tampering(coding,tmp_path):
    _,admit,_=coding;work=admit()
    await work.test('syntax');work.write('a.py','ANSWER = 9\n')
    with pytest.raises(ValueError,match='Every approved'):work.freeze()
    work.path.joinpath('new.py').symlink_to(tmp_path/'secret')
    with pytest.raises(ValueError,match='Links'):work.checkpoint()
    work.path.joinpath('new.py').unlink()
    work.path.joinpath('b.py').write_text('ANSWER = 99\n')
    with pytest.raises(PermissionError,match='outside write'):work.checkpoint()

async def test_revoke_preserves_uncommitted_but_blocks_access(coding):
    _,admit,_=coding;revoked=False
    def check():
        if revoked:raise PermissionError('revoked')
    work=admit(validate=check);work.write('new.py','SAVED = True\n')
    revoked=True
    with pytest.raises(PermissionError):work.read('new.py')
    with pytest.raises(PermissionError):work.freeze()
    assert (work.path/'new.py').read_text()=='SAVED = True\n'

async def test_failed_test_never_freezes_and_command_not_model_controlled(coding):
    _,admit,_=coding;work=admit();work.write('a.py','invalid python !')
    result=await work.test('syntax');assert result['exit_code']!=0
    with pytest.raises(ValueError):work.freeze()
    with pytest.raises(PermissionError):await work.test('sh -c env')
    with pytest.raises(ValueError):work.read('a.py','refs/heads/other')

async def test_integration_retests_combination_and_conflict_is_resolution_task(coding):
    service,admit,_=coding
    one,two=admit(),admit('two',('b.py',))
    one.write('a.py','ANSWER = 3\n');two.write('b.py','ANSWER = 4\n')
    await one.test('syntax');await two.test('syntax');a,b=one.freeze(),two.freeze()
    contract=one.contract.model_copy(update={'write_paths':('a.py','b.py','new.py'),
        'candidate_workspaces':(a.workspace_id,b.workspace_id)})
    spec=SimpleNamespace(id='integration',version=1,research_id='research',coding=contract)
    combined=service.admit(spec,'g1',lambda:None)
    assert combined.read('a.py')=='ANSWER = 3\n' and combined.read('b.py')=='ANSWER = 4\n'
    with pytest.raises(ValueError,match='Every approved'):combined.freeze()
    await combined.test('syntax');assert combined.freeze().tree not in (a.tree,b.tree)
    conflict=admit('conflicting');conflict.write('a.py','ANSWER = 99\n')
    await conflict.test('syntax');c=conflict.freeze()
    conflict_contract=contract.model_copy(update={'candidate_workspaces':(a.workspace_id,c.workspace_id)})
    resolved=service.admit(SimpleNamespace(id='resolve-conflict',version=1,research_id='research',coding=conflict_contract),'g1',lambda:None)
    assert resolved.status()['phase']=='conflict'
    assert resolved.status()['resolution_task']['task_id']=='resolve-conflict'
    with pytest.raises(ValueError,match='conflict markers'):resolved.freeze()
    resolved.write('a.py','ANSWER = 102\n')
    await resolved.test('syntax')
    assert resolved.freeze().tree not in (a.tree,c.tree)

async def test_archive_keeps_unsaved_work(coding):
    _,admit,_=coding;work=admit();work.write('new.py','PRESERVED = True\n')
    snapshot=work.archive()
    assert 'new.py' in snapshot['files']
    assert output(work.path,'show',snapshot['commit']+':new.py')=='PRESERVED = True'
    with pytest.raises(PermissionError):work.read('new.py')

async def test_process_kill_keeps_uncommitted_work(coding,tmp_path):
    import subprocess
    service,admit,repo=coding
    original=admit('crash')
    child=tmp_path/'crash.py'
    child.write_text('''import json,signal,sys
from pathlib import Path
from types import SimpleNamespace
from agent_core.coding.workspace import CodingService,TestProfile
from agent_core.coding.models import CodingContract
root,repo,validator,contract,marker=sys.argv[1:]
s=CodingService(root,{'demo':repo},[TestProfile('syntax',(sys.executable,'-I',validator),'python-ast-v1')])
w=s.admit(SimpleNamespace(id='crash',version=1,research_id='research',coding=s.bind_contract(CodingContract.model_validate_json(contract))),'g1',lambda:None)
w.write('new.py','CRASH_SAVED = True\\n')
Path(marker).write_text('saved')
signal.pause()
''')
    marker=tmp_path/'ready'
    process=subprocess.Popen([sys.executable,str(child),str(service.root),str(repo),
        service.profiles['syntax'].argv[2],original.contract.model_dump_json(),str(marker)])
    try:
        for _ in range(100):
            if marker.exists():break
            await asyncio.sleep(.05)
        assert marker.exists()
        process.kill();process.wait(timeout=5)
        restored=admit('crash')
        assert restored.read('new.py')=='CRASH_SAVED = True\n'
        assert 'new.py' in restored.archive()['files']
    finally:
        if process.poll() is None:process.kill();process.wait()

async def test_test_profile_changes_and_frozen_metadata_tamper_rejected(coding):
    service,admit,_=coding;work=admit()
    await work.test('syntax');candidate=work.freeze()
    changed=candidate.model_copy(update={'content_sha256':'0'*64})
    with pytest.raises(ValueError):work.verify_candidate(changed.model_dump())
    with pytest.raises(ValueError):work.resolve_candidate(work.key+':other-branch')
    validator=Path(service.profiles['syntax'].argv[2]);validator.write_text('raise SystemExit(0)')
    with pytest.raises(ValueError,match='profile changed'):service.bind_contract(work.contract)

async def test_invalid_integration_cannot_be_retried_as_empty_workspace(coding):
    service,admit,_=coding;original=admit()
    contract=original.contract.model_copy(update={'candidate_workspaces':('not-authorized',)})
    spec=SimpleNamespace(id='invalid-integration',version=1,research_id='research',coding=contract)
    with pytest.raises(ValueError):service.admit(spec,'g1',lambda:None)
    blocked=service.admit(spec,'g1',lambda:None)
    with pytest.raises(PermissionError):blocked.write('a.py','BYPASS=1')

async def test_source_git_extensions_and_environment_are_not_inherited(coding,tmp_path,monkeypatch):
    service,admit,source=coding
    marker=tmp_path/'hook-ran'
    hooks=tmp_path/'hooks';hooks.mkdir()
    hook=hooks/'post-checkout';hook.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\n');hook.chmod(0o755)
    git(source,'config','core.hooksPath',str(hooks))
    git(source,'config','diff.external',str(hook))
    validator=Path(service.profiles['syntax'].argv[2])
    validator.write_text(validator.read_text()+"\nassert 'OPENAI_API_KEY' not in os.environ\nassert 'DATABASE_URL' not in os.environ\n")
    monkeypatch.setenv('OPENAI_API_KEY','fixture-secret-do-not-inherit')
    monkeypatch.setenv('DATABASE_URL','fixture-secret-do-not-inherit')
    work=admit();work.write('a.py','ANSWER = 5\n');work.diff();await work.test('syntax');work.freeze()
    assert not marker.exists()
    config=output(service.root/'repo-demo','config','--local','--list')
    assert 'diff.external' not in config and 'filter.' not in config and 'core.hookspath' not in config

async def test_hardlinked_files_denied(coding,tmp_path):
    import os
    _,admit,_=coding;work=admit()
    os.link(work.path/'a.py',tmp_path/'external-hardlink')
    with pytest.raises(ValueError,match='Hard links'):work.read('a.py')
    with pytest.raises(ValueError,match='Hard links'):work.write('a.py','new')

async def test_archive_rejects_live_writer(coding):
    _,admit,_=coding;work=admit()
    with work.writer():
        work.write('new.py','ACTIVE = True\n')
        with pytest.raises(PermissionError,match='Active'):work.archive()
        with pytest.raises(PermissionError,match='already has a writer'):
            with work.writer():pass
    assert 'new.py' in work.archive()['files']
