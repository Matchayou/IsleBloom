"""Coordinator-only PostgreSQL reservations and fenced workspace ownership.

Uses the application's existing transaction/host lock. No SQLite substitute and
no reconnect, lease expiry or age-based takeover.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import Column, Integer, String, DateTime, MetaData, Table, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from ..execution.ports import Conflict


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class ControlledLedger:
    def __init__(self, database):
        if database.engine.dialect.name != 'postgresql':
            raise ValueError('Controlled coordinator requires PostgreSQL')
        self.database = database
        meta = MetaData()
        self.workspaces = Table('controlled_workspaces', meta,
            Column('id', String, primary_key=True), Column('epoch', Integer, nullable=False),
            Column('writer', String), Column('owner', String))
        self.runs = Table('controlled_runs', meta,
            Column('id', String, primary_key=True), Column('binding', JSONB, nullable=False),
            Column('models', Integer, nullable=False), Column('tools', Integer, nullable=False),
            Column('deadline', DateTime(timezone=True), nullable=False))
        self.cancellations = Table('controlled_cancellations', meta,
            Column('task_id', String, primary_key=True), Column('spec_version', Integer, primary_key=True))
        self.operations = Table('controlled_operations', meta,
            Column('run_id', String, primary_key=True), Column('id', String, primary_key=True),
            Column('kind', String, nullable=False), Column('request_hash', String, nullable=False),
            Column('usage', JSONB), Column('status', String, nullable=False))
        self.phases = Table('controlled_progress', meta,
            Column('run_id', String, primary_key=True), Column('state', JSONB, nullable=False))
        self.manifests = Table('context_manifests', meta,
            Column('run_id', String, primary_key=True), Column('operation_id', String, primary_key=True),
            Column('state', String, nullable=False), Column('manifest', JSONB, nullable=False))
        self.reads = Table('context_reads', meta,
            Column('run_id', String, primary_key=True), Column('id', String, primary_key=True),
            Column('page', JSONB, nullable=False))
        self.compactions = Table('context_compactions', meta,
            Column('run_id', String, primary_key=True), Column('id', String, primary_key=True),
            Column('stream', String, nullable=False), Column('attempt', JSONB, nullable=False))
        self.compaction_heads = Table('context_compaction_heads', meta,
            Column('workspace_id', String, primary_key=True), Column('stream', String, primary_key=True),
            Column('head', JSONB, nullable=False))
        self.checkpoints = Table('controlled_checkpoints', meta,
            Column('run_id', String, primary_key=True), Column('key', String, primary_key=True),
            Column('artifact', String, nullable=False))
        with database.transaction(): meta.create_all(database.connection)

    def input_observation(self, run_id):
        """Latest measured task request from this run; summary usage is unrelated."""
        t = self.manifests
        with self.database.transaction():
            rows = self.database.connection.execute(select(t.c.operation_id, t.c.manifest).where(
                t.c.run_id == run_id, t.c.state == 'responded',
                t.c.manifest['purpose'].astext == 'task')).all()
        if not rows: return None
        def order(row):
            parts = row.operation_id.split(':')
            return (int(parts[1]), int(parts[3]) if len(parts) == 4 else 0)
        latest = max(rows, key=order).manifest
        return {'public_input': latest['public_input'], 'input_tokens': latest['usage']['input_tokens'],
            'response_input_items':latest.get('response_input_items',[]),
            'output_tokens':latest['usage']['output_tokens']}

    def task_model_durations(self, run_id):
        with self.database.transaction():
            values = self.database.connection.scalars(select(self.manifests.c.manifest).where(
                self.manifests.c.run_id == run_id, self.manifests.c.state == 'responded'))
            return tuple(m['duration_seconds'] for m in values if m.get('purpose') == 'task'
                and isinstance(m.get('duration_seconds'), (int,float)))

    def compaction_head(self, workspace_id, stream):
        with self.database.transaction():
            return self.database.connection.scalar(select(self.compaction_heads.c.head).where(
                self.compaction_heads.c.workspace_id==workspace_id,self.compaction_heads.c.stream==stream))

    def compaction_attempt(self, run_id, attempt_id):
        with self.database.transaction():
            return self.database.connection.scalar(select(self.compactions.c.attempt).where(
                self.compactions.c.run_id==run_id,self.compactions.c.id==attempt_id))

    def save_compaction(self, workspace, token, epoch, run_id, stream, attempt, head=None):
        with self.database.transaction():
            c=self.database.connection
            self.check_in(c,workspace,token,epoch)
            c.execute(insert(self.compactions).values(run_id=run_id,id=attempt['id'],stream=stream,attempt=attempt)
                .on_conflict_do_update(index_elements=['run_id','id'],set_={'attempt':attempt}))
            if head is not None:
                if attempt['state']!='succeeded':raise ValueError('Only validated summary may advance coverage')
                c.execute(insert(self.compaction_heads).values(workspace_id=workspace,stream=stream,head=head)
                    .on_conflict_do_update(index_elements=['workspace_id','stream'],set_={'head':head}))

    def cancel_task(self, task_id, spec_version):
        with self.database.transaction():
            self.database.connection.execute(insert(self.cancellations).values(
                task_id=task_id, spec_version=spec_version).on_conflict_do_nothing())

    def check_cancelled(self, task_id, spec_version):
        with self.database.transaction():
            row = self.database.connection.execute(select(self.cancellations).where(
                self.cancellations.c.task_id == task_id,
                self.cancellations.c.spec_version == spec_version)).first()
            if row is not None: raise Conflict('Controlled task version was cancelled')

    def acquire(self, workspace, binding, limits, owner):
        w, r = self.workspaces, self.runs
        with self.database.transaction():
            c = self.database.connection
            c.execute(insert(w).values(id=workspace, epoch=0).on_conflict_do_nothing())
            row = c.execute(select(w).where(w.c.id == workspace).with_for_update()).mappings().one()
            # PostgresDatabase owns both the OS-user lock and database advisory
            # lock for its lifetime. A new process holding them proves the old
            # process stopped; another engine in this process cannot take over.
            if row['writer'] is not None and row['owner'] == owner:
                raise Conflict('Private workspace already has a writer; verified stop required')
            if owner != self.database.process_id:
                raise Conflict('Writer must belong to the exclusive database process')
            old = c.execute(select(r).where(r.c.id == binding['run_id']).with_for_update()).mappings().one_or_none()
            if old is None:
                c.execute(insert(r).values(id=binding['run_id'], binding=binding, models=0, tools=0,
                    deadline=datetime.now(timezone.utc) + timedelta(seconds=limits.timeout_seconds)))
            elif old['binding'] != binding:
                raise Conflict('Controlled run binding changed')
            c.execute(insert(self.phases).values(run_id=binding['run_id'], state={
                'phase':'researching','reason':'admitted','known':[],'repeat_streak':0,'transitions':['researching']})
                .on_conflict_do_nothing())
            token, epoch = uuid4().hex, row['epoch'] + 1
            c.execute(update(w).where(w.c.id == workspace).values(epoch=epoch, writer=token, owner=owner))
        return token, epoch

    def check_in(self, c, workspace, token, epoch):
        w = self.workspaces
        row = c.execute(select(w).where(w.c.id == workspace).with_for_update()).mappings().one()
        if (row['writer'], row['epoch']) != (token, epoch): raise Conflict('Stale controlled writer')

    def check_writer(self, workspace, token, epoch):
        with self.database.transaction():
            self.check_in(self.database.connection, workspace, token, epoch)

    def reserve(self, workspace, token, epoch, run_id, frame, limits):
        with self.database.transaction():
            c = self.database.connection
            self.check_in(c, workspace, token, epoch)
            row = c.execute(select(self.runs).where(self.runs.c.id == run_id).with_for_update()).mappings().one()
            if frame.operation != 'result' and datetime.now(timezone.utc) >= row['deadline']:
                raise Conflict('Persistent controlled deadline exceeded')
            # An unresolved operation is never silently retried under the same ID.
            previous = c.execute(select(self.operations).where(self.operations.c.run_id == run_id,
                self.operations.c.id == frame.operation_id)).first()
            if previous is not None: raise Conflict('Duplicate controlled operation')
            field = 'models' if frame.operation == 'model' else 'tools' if frame.operation in ('tool', 'private') else None
            if field:
                maximum = limits.max_turns if field == 'models' else limits.max_calls
                if maximum is not None and row[field] >= maximum: raise Conflict('Persistent controlled budget exhausted')
                c.execute(update(self.runs).where(self.runs.c.id == run_id).values({field: row[field] + 1}))
            c.execute(insert(self.operations).values(run_id=run_id, id=frame.operation_id,
                kind=frame.operation, request_hash=digest(frame.payload), status='unconfirmed'))

    def settle(self, workspace, token, epoch, run_id, operation_id, usage=None, artifact=None, code_artifact=None):
        with self.database.transaction():
            c = self.database.connection
            self.check_in(c, workspace, token, epoch)
            c.execute(update(self.operations).where(self.operations.c.run_id == run_id,
                self.operations.c.id == operation_id).values(status='confirmed', usage=usage))
            if artifact is not None:
                c.execute(insert(self.checkpoints).values(run_id=run_id,key='receipt:'+operation_id,artifact=artifact))
            if code_artifact is not None:
                c.execute(insert(self.checkpoints).values(run_id=run_id,key='code',artifact=code_artifact)
                    .on_conflict_do_update(index_elements=['run_id','key'],set_={'artifact':code_artifact}))

    def checkpoint(self, run_id, key):
        with self.database.transaction():
            return self.database.connection.scalar(select(self.checkpoints.c.artifact).where(
                self.checkpoints.c.run_id==run_id,self.checkpoints.c.key==key))

    def save_checkpoint(self, workspace, token, epoch, run_id, key, artifact):
        with self.database.transaction():
            c=self.database.connection
            self.check_in(c,workspace,token,epoch)
            c.execute(insert(self.checkpoints).values(run_id=run_id,key=key,artifact=artifact)
                .on_conflict_do_update(index_elements=['run_id','key'],set_={'artifact':artifact}))

    def operation(self, run_id, operation_id):
        with self.database.transaction():
            row=self.database.connection.execute(select(self.operations).where(
                self.operations.c.run_id==run_id,self.operations.c.id==operation_id)).mappings().one_or_none()
            return dict(row) if row else None

    def operation_history(self, run_id):
        with self.database.transaction():
            return [dict(row) for row in self.database.connection.execute(select(self.operations).where(
                self.operations.c.run_id==run_id)).mappings()]

    def model_measurements(self, run_id):
        """Public metrics only; never expose provider bodies or private context."""
        with self.database.transaction():
            rows = self.database.connection.execute(select(self.manifests).where(
                self.manifests.c.run_id == run_id)).mappings().all()
        def order(row):
            return tuple((0,int(p)) if p.isdigit() else (1,p) for p in row['operation_id'].split(':'))
        return [dict(operation_id=row['operation_id'], state=row['state'],
            **{key:row['manifest'].get(key) for key in ('purpose','duration_seconds','usage')})
            for row in sorted(rows, key=order)]

    def usage(self, run_id):
        from ..contracts import Usage
        with self.database.transaction():
            rows = self.database.connection.execute(select(self.operations).where(
                self.operations.c.run_id == run_id, self.operations.c.kind == 'model')).mappings().all()
        total = Usage()
        for row in rows:
            total = total.plus(Usage.model_validate(row['usage']) if row['usage'] else Usage(requests=1, complete=False))
        return total

    def plan_usage(self, node_ids, *, excluding=None):
        from ..contracts import Usage
        from sqlalchemy import and_, not_
        with self.database.transaction():
            query = select(self.operations).join(self.runs,
                self.runs.c.id==self.operations.c.run_id).where(
                self.runs.c.binding['session_id'].astext.in_(node_ids),
                self.operations.c.kind=='model')
            if excluding:
                query = query.where(not_(and_(self.operations.c.run_id==excluding[0],self.operations.c.id==excluding[1])))
            rows = self.database.connection.execute(query).mappings().all()
        total = Usage()
        for row in rows:
            total = total.plus(Usage.model_validate(row['usage']) if row['usage'] else Usage(requests=1,complete=False))
        return total

    def binding(self, run_id):
        with self.database.transaction():
            return self.database.connection.scalar(select(self.runs.c.binding).where(self.runs.c.id == run_id))

    def writer(self, workspace):
        with self.database.transaction():
            return self.database.connection.execute(select(self.workspaces).where(
                self.workspaces.c.id == workspace)).mappings().one_or_none()

    def release(self, workspace, token, epoch):
        # Caller must first finish/cancel and await its trusted SDK execution.
        with self.database.transaction():
            c = self.database.connection
            self.check_in(c, workspace, token, epoch)
            c.execute(update(self.workspaces).where(self.workspaces.c.id == workspace)
                      .values(writer=None, owner=None))

    def save_manifest(self, run_id, operation_id, state, manifest):
        with self.database.transaction():
            self.database.connection.execute(insert(self.manifests).values(
                run_id=run_id, operation_id=operation_id, state=state, manifest=manifest)
                .on_conflict_do_update(index_elements=['run_id', 'operation_id'],
                    set_={'state':state, 'manifest':manifest}))

    def record_read(self, run_id, page):
        with self.database.transaction():
            self.database.connection.execute(insert(self.reads).values(
                run_id=run_id, id=digest(page), page=page).on_conflict_do_nothing())

    def run_budget(self, run_id):
        with self.database.transaction():
            return dict(self.database.connection.execute(select(self.runs).where(self.runs.c.id==run_id)).mappings().one())

    def progress(self, run_id):
        with self.database.transaction():
            return self.database.connection.scalar(select(self.phases.c.state).where(self.phases.c.run_id==run_id))

    def set_phase(self, run_id, phase, reason):
        with self.database.transaction():
            c=self.database.connection
            old=c.execute(select(self.phases.c.state).where(self.phases.c.run_id==run_id).with_for_update()).scalar_one()
            allowed={'researching':{'finalizing','delivered','incomplete'},'finalizing':{'delivered','incomplete'},'delivered':set(),'incomplete':set()}
            if old['phase']==phase:return old
            if phase not in allowed[old['phase']]:raise Conflict('Invalid execution phase transition')
            value={**old,'phase':phase,'reason':reason,'transitions':[*old['transitions'],phase]}
            if phase=='finalizing':value['finalization_reason']=reason
            c.execute(update(self.phases).where(self.phases.c.run_id==run_id).values(state=value))
            return value

    def record_progress(self, run_id, fingerprint, *, waiting=False):
        with self.database.transaction():
            c=self.database.connection
            old=c.execute(select(self.phases.c.state).where(self.phases.c.run_id==run_id).with_for_update()).scalar_one()
            repeated=fingerprint in old['known'] and not waiting
            known=old['known'] if fingerprint in old['known'] else [*old['known'],fingerprint]
            streak=old['repeat_streak']+1 if repeated else 0
            value={**old,'known':known,'repeat_streak':streak}
            if streak>=3 and old['phase']=='researching':
                value.update(phase='finalizing',reason='no_new_information',finalization_reason='no_new_information',transitions=[*old['transitions'],'finalizing'])
            c.execute(update(self.phases).where(self.phases.c.run_id==run_id).values(state=value))
            return value

    def reject_tool(self, workspace, token, epoch, run_id, frame, artifact=None):
        with self.database.transaction():
            c=self.database.connection
            self.check_in(c,workspace,token,epoch)
            c.execute(insert(self.operations).values(run_id=run_id,id=frame.operation_id,
                kind=frame.operation,request_hash=digest(frame.payload),status='denied'))
            if artifact is not None:
                c.execute(insert(self.checkpoints).values(run_id=run_id,key='receipt:'+frame.operation_id,artifact=artifact))
