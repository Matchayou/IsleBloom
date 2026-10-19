"""Explicit operator repair of this workspace's incomplete specialist identities.

Never called by application startup or repository reads. Run --apply only after
stopping the workspace service. Preserves historical inputs and outputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from agent_core.agent_identity import AgentIdentity
from agent_core.tasks.models import TaskSpec, TaskRun
from agent_core.context.mounts import ContextSnapshot
from agent_core.collaboration.models import ContextPacket, ExpertSession
from agent_core.contracts import RunLimits


def build_plan(rows):
    specs = {(d['id'],d['version']):d for d in rows['task_specs']}
    runs = {d['id']:d for d in rows['task_runs']}
    records = {d['run_id']:d for d in rows['runs']}
    snapshots = {d['run_id']:d for d in rows['context_snapshots']}
    packets = {d['id']:d for d in rows['collaboration_packets']}
    sessions = {d['id']:d for d in rows['collaboration_sessions']}
    changes = {}
    def add(table, key, old, additions, model):
        new = {**old, **additions}
        model.model_validate(new)
        identity = (table,tuple(key.items()))
        if identity in changes and changes[identity]['new'] != new:
            raise ValueError('Conflicting repair evidence')
        if old != new: changes[identity] = dict(table=table,key=key,old=old,new=new)
    for spec in specs.values():
        if spec.get('agent') is not None: continue
        linked = [r for r in runs.values() if (r['task_id'],r['spec_version'])==(spec['id'],spec['version'])]
        if not linked: raise ValueError('Missing actual execution identity evidence')
        for run in linked:
            record = records[run['id']]
            if record['request']['agent_id'] != spec['expert_id']:
                raise ValueError('Actual execution does not match the specialist')
            if RunLimits.model_validate(record['request']['limits']) != RunLimits.model_validate(spec['limits']):
                raise ValueError('Recorded execution and task limits disagree')
        agent = AgentIdentity(mode='specialist',id=spec['expert_id']).model_dump()
        add('task_specs',{'task_id':spec['id'],'version':spec['version']},spec,{'agent':agent},TaskSpec)
        for run in linked:
            if run.get('agent') not in (None,agent): raise ValueError('Run identity changed')
            add('task_runs',{'id':run['id']},run,{'agent':agent},TaskRun)
            snapshot = snapshots[run['id']]
            if snapshot.get('agent') not in (None,agent): raise ValueError('Snapshot identity changed')
            add('context_snapshots',{'run_id':run['id']},snapshot,{'agent':agent},ContextSnapshot)
        session = sessions[spec['expert_session_id']]
        if session['expert_id'] != spec['expert_id'] or session['research_id'] != spec['research_id']:
            raise ValueError('Expert session does not match')
        if session.get('agent') not in (None,agent): raise ValueError('Session identity changed')
        add('collaboration_sessions',{'id':session['id']},session,{'agent':agent},ExpertSession)
        packet = packets[spec['context_packet_id']]
        if packet['research_id'] != spec['research_id']:
            raise ValueError('Handoff belongs to another research')
        if packet.get('agent') not in (None,agent): raise ValueError('Handoff identity changed')
        # Historical project metadata was not recorded. Empty means unknown;
        # never insert today's project state into a historical handoff.
        additions = {'agent':agent}
        if 'project' not in packet: additions['project'] = {}
        if 'limits' not in packet: additions['limits'] = spec['limits']
        add('collaboration_packets',{'id':packet['id']},packet,additions,ContextPacket)
    return list(changes.values())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup-dir',default='.controlled/repairs')
    args=parser.parse_args()
    engine=create_engine(os.environ['DATABASE_URL'],hide_parameters=True)
    with engine.begin() as c:
        if c.scalar(text('SELECT count(*) FROM controlled_workspaces WHERE writer IS NOT NULL')):
            raise ValueError('Active controlled execution; stop before repair')
        rows={t:list(c.execute(text('SELECT payload FROM '+t+' FOR UPDATE')).scalars()) for t in (
            'task_specs','task_runs','context_snapshots','collaboration_packets','collaboration_sessions')}
        rows['runs']=[{'run_id':r.run_id,'request':r.request} for r in c.execute(text('SELECT run_id,request FROM runs'))]
        plan=build_plan(rows)
        counts={t:sum(p['table']==t for p in plan) for t in rows if t!='runs'}
        backup=None
        if args.apply and plan:
            root=Path(args.backup_dir);root.mkdir(parents=True,exist_ok=True,mode=0o700)
            backup=root/('agent-record-repair-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
            with os.fdopen(os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
                json.dump(plan,f,ensure_ascii=False)
            for change in plan:
                where=' AND '.join(k+'=:'+k for k in change['key'])
                params={**change['key'],'new':json.dumps(change['new']),'old':json.dumps(change['old'])}
                result=c.execute(text('UPDATE '+change['table']+' SET payload=CAST(:new AS jsonb) WHERE '+where+' AND payload=CAST(:old AS jsonb)'),params)
                if result.rowcount!=1: raise ValueError('Record changed during repair')
        print(json.dumps({'applied':args.apply,'changes':counts,'backup':str(backup) if backup else None,
            'historical_input_and_output_unchanged':True}))
    engine.dispose()

if __name__=='__main__':main()
