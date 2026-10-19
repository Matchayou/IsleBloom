import asyncio
import os
import sys

from agent_core.adapters.postgres import PostgresDatabase
from legacy_composition import build_orchestration


class Waiting:
    def __init__(self):
        self.count = 0

    async def execute(self, *args):
        self.count += 1
        if self.count == 3:
            print('READY_TO_KILL', flush=True)
        await asyncio.Event().wait()


async def main():
    db = PostgresDatabase(os.environ['TEST_DATABASE_URL'])
    try:
        router, dispatcher = build_orchestration(db, Waiting())
        plan = router.route('比较成本、速度与限制', mode='compare', group_id=sys.argv[1]).plan
        await dispatcher.dispatch(plan)
    finally:
        db.close()


asyncio.run(main())
