"""Used only by real process/DB fault tests; never part of the CLI."""
import asyncio
from contextlib import contextmanager
import os
import signal
import sys
from types import SimpleNamespace

from agent_core.adapters.postgres import PostgresDatabase
from legacy_composition import build_execution


def ready():
    print('READY_TO_KILL', flush=True)
    signal.pause()


class CrashDatabase:
    def __init__(self, db, point):
        self.db, self.process_id, self.point = db, db.process_id, point

    @contextmanager
    def transaction(self):
        finishing = False
        with self.db.transaction() as tx:
            def finish(commit):
                nonlocal finishing
                tx.runs.finish(commit)
                finishing = True
                if self.point == 'before':
                    ready()  # all writes executed, transaction not committed
            class RunsProxy:
                def __getattr__(self, name):
                    return finish if name == 'finish' else getattr(tx.runs, name)
            yield SimpleNamespace(conversations=tx.conversations, runs=RunsProxy())
        if finishing and self.point == 'after':
            ready()  # server COMMIT succeeded, application has not received success


class Waiting:
    async def execute(self, *args):
        ready()


async def main():
    point, conversation, run_id = sys.argv[1:]
    db = PostgresDatabase(os.environ['TEST_DATABASE_URL'])
    try:
        service = build_execution(CrashDatabase(db, point), Waiting() if point == 'running' else None,
                                  emit=lambda e: print(e.model_dump_json(), flush=True))
        await service.send(conversation, 'assistant', 'u', run_id=run_id)
    finally:
        db.close()


asyncio.run(main())
