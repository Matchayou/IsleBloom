"""Test-only deterministic seeding for historical record reader contracts."""
import asyncio
import json
import os
import sys
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from legacy_composition import build_execution, build_orchestration, demo_script


async def main():
    kind, *args = sys.argv[1:]
    db = PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        if kind == 'send':
            conversation, text, *run_id = args
            service = build_execution(db, ScriptedEngine(demo_script),
                emit=lambda e: print(json.dumps({'core_event': e.model_dump(mode='json')})), notify=print)
            result = await service.send(conversation, 'assistant', text, run_id=run_id[0] if run_id else None)
            print(result.model_dump_json())
            return 0 if result.status == 'completed' else 1
        group, *fail = args
        base = ScriptedEngine(lambda r: ([], f'{r.agent_id}: deterministic historical fixture'))
        class Engine:
            async def execute(self, request, *rest):
                if fail and request.agent_id == 'expert_speed':
                    raise RuntimeError('Fixture failure')
                return await base.execute(request, *rest)
        router, dispatcher = build_orchestration(db, Engine())
        decision = router.route('compare cost speed limits', mode='compare', group_id=group)
        result = await dispatcher.dispatch(decision.plan)
        print(result.model_dump_json())
        return 0 if result.complete else 1
    finally:
        db.close()


raise SystemExit(asyncio.run(main()))
