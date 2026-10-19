from sqlalchemy import Table, Column, String, Integer, select, update, insert
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from ...execution.ports import Conflict
from ...methods.models import AssetVersion


def define_tables(meta):
    return {
        'versions':Table('method_asset_versions',meta,Column('category',String,primary_key=True),Column('asset_id',String,primary_key=True),Column('version',String,primary_key=True),Column('payload',JSONB,nullable=False)),
        'heads':Table('method_asset_heads',meta,Column('category',String,primary_key=True),Column('asset_id',String,primary_key=True),Column('version',String,nullable=False)),
        'releases':Table('method_asset_releases',meta,Column('id',Integer,primary_key=True,autoincrement=True),Column('payload',JSONB,nullable=False)),
        'instances':Table('method_workflow_instances',meta,Column('id',String,primary_key=True),Column('payload',JSONB,nullable=False)),
    }


class PostgresMethods:
    def __init__(self, c, tables): self.c,self.t = c,tables
    def get(self, category, asset_id, version):
        t=self.t['versions'];v=self.c.scalar(select(t.c.payload).where(t.c.category==category,t.c.asset_id==asset_id,t.c.version==version))
        return AssetVersion.model_validate(v) if v else None
    def list(self): return [AssetVersion.model_validate(v) for v in self.c.scalars(select(self.t['versions'].c.payload))]
    def head(self, category, asset_id):
        t=self.t['heads'];return self.c.scalar(select(t.c.version).where(t.c.category==category,t.c.asset_id==asset_id))
    def add(self, value):
        self.c.execute(pg_insert(self.t['versions']).values(category=value.category,asset_id=value.asset_id,version=value.version,payload=value.model_dump(mode='json')).on_conflict_do_nothing())
        if self.get(value.category,value.asset_id,value.version) != value: raise Conflict('Immutable version already exists')
    def activate(self, category, asset_id, version, expected, actor, reason):
        t=self.t['heads']
        if expected is None:
            result=self.c.execute(pg_insert(t).values(category=category,asset_id=asset_id,version=version).on_conflict_do_nothing().returning(t.c.version))
        else:
            result=self.c.execute(update(t).where(t.c.category==category,t.c.asset_id==asset_id,t.c.version==expected).values(version=version).returning(t.c.version))
        if result.scalar_one_or_none() is None: raise Conflict('Active version changed; refresh before publishing')
        self.c.execute(insert(self.t['releases']).values(payload=dict(category=category,asset_id=asset_id,version=version,previous=expected,actor=actor,reason=reason)))
    def history(self): return list(self.c.scalars(select(self.t['releases'].c.payload).order_by(self.t['releases'].c.id)))
    def instance(self, key):
        t=self.t['instances'];return self.c.scalar(select(t.c.payload).where(t.c.id==key))
    def add_instance(self,value):
        self.c.execute(pg_insert(self.t['instances']).values(id=value['id'],payload=value).on_conflict_do_nothing())
        if self.instance(value['id']) != value: raise Conflict('Instance ID reused')
