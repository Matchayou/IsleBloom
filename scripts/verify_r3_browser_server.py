"""Disposable browser acceptance server: one save fault and a stoppable expert."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import uvicorn

from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.app.api import create_app
from agent_core.composition import build_resources, build_research, build_tasks
from agent_core.contracts import RunResult


class BrowserFixtureEngine:
    async def execute(self, request, spec, tools, invoke):
        if request.agent_id.startswith('expert_'):
            await asyncio.sleep(20)
            output = {'report': '脚本专家占位，仅用于停止测试', 'fields': {}, 'sources': {}}
        else:
            output = {'report': '保存重试后可继续聊天的脚本回复', 'missing': []}
        return RunResult(run_id=request.run_id, status='succeeded', output=output)


async def main():
    db = MemoryDatabase()
    scopes = build_resources(db, '/tmp/r3-browser-fixture-blobs')
    engine = BrowserFixtureEngine()
    research = build_research(db, scopes, engine)
    root = research.create('R3 浏览器场景二：保存失败与停止')
    scope = scopes.current(root.scope_id, research.owner)
    scopes.ingest(scope, content=Path('tests/fixtures/stage4/company-1.txt').read_bytes(),
                  name='company-1.txt', media_type='text/plain')
    tasks = build_tasks(db, scopes, research, engine)
    original = db.transaction
    fail = [True]

    @contextmanager
    def transaction():
        with original() as tx:
            class ResearchProxy:
                def __getattr__(self, name): return getattr(tx.research, name)
                def publish(self, value, dependencies):
                    tx.research.publish(value, dependencies)
                    if fail[0]:
                        fail[0] = False
                        raise RuntimeError('injected one-time publication failure')
            repos = vars(tx).copy()
            repos['research'] = ResearchProxy()
            yield SimpleNamespace(**repos)

    db.transaction = transaction
    app = create_app(research, 'frontend/dist', tasks)
    await uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8745, log_level='warning')).serve()


if __name__ == '__main__':
    asyncio.run(main())
