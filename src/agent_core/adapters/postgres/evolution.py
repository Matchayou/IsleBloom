from sqlalchemy import Table, Column, String, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from ...execution.ports import Conflict


def define_tables(meta):
    return Table('evolution_records',meta,Column('id',String,primary_key=True),Column('payload',JSONB,nullable=False))


class PostgresEvolution:
    def __init__(self, connection, table): self.c,self.t=connection,table
    def get(self,key): return self.c.scalar(select(self.t.c.payload).where(self.t.c.id==key))
    def list(self): return list(self.c.scalars(select(self.t.c.payload)))
    def add(self,key,value):
        self.c.execute(insert(self.t).values(id=key,payload=value).on_conflict_do_nothing())
        if self.get(key) != value: raise Conflict('Evolution evidence ID reused')
