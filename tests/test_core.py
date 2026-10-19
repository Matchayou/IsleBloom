import asyncio

import pytest

from agent_core.contracts import *
from agent_core.core import AgentCatalog, CoreRunner
from legacy_composition import build, MultiplyInput
from agent_core.adapters.scripted import ScriptedEngine, Call
from agent_core.adapters.in_memory import InMemoryConversationStore
from legacy_conversation import ConversationService


def request(**kwargs):
    return RunRequest(agent_id="a", input=PreparedInput(messages=(Message(role="user", content="private parent"),)), **kwargs)


def runner(script, tools=(), output_type=None):
    return CoreRunner(ScriptedEngine(script), AgentCatalog([AgentSpec("a", "test", tools=tools, output_type=output_type)]))


async def test_c0_demo_and_followup():
    events = []
    service = build(emit=events.append)
    first = await service.send("c", "assistant", "算出 12×8，再让表达专家把结果说成一句中文")
    second = await service.send("c", "assistant", "刚才结果是多少")
    assert first.status == second.status == "succeeded"
    assert "96" in first.output and "96" in second.output
    assert [e.name for e in events if e.phase == "tool" and e.status == "started"] == ["multiply", "delegate"]
    history = await service.store.load("c")
    assert len(history.messages) == 4 and history.revision == 2
    assert len([e for e in events if e.parent_run_id and e.phase == "run"]) == 2


async def test_tool_sequence_and_replay():
    seen = []
    async def tool(args):
        seen.append(args.a)
        return ToolResult(value=args.a * args.b)
    binding = ToolBinding("mul", "multiply", MultiplyInput, tool)
    r = runner(lambda _: ([Call("mul", {"a": 1, "b": 2}, "A"),
                            Call("mul", {"a": 1, "b": 2}, "A"),
                            Call("mul", {"a": 3, "b": 4}, "B")], "done"), (binding,))
    assert (await r.execute(request())).status == "succeeded"
    assert seen == [1, 3]


async def test_tool_results_report_actual_execution_budget():
    async def tool(args):
        return ToolResult(value={'product': args.a * args.b, 'remaining_tool_calls': 999})
    class Engine:
        async def execute(self, req, spec, tools, invoke):
            for i, remaining in enumerate((1, 0)):
                response = await invoke('mul', str(i), {'a': 2, 'b': 3})
                assert response.value == {'product': 6, 'remaining_tool_calls': remaining}
            return RunResult(run_id=req.run_id, status='succeeded', output='done')
    run = CoreRunner(Engine(), AgentCatalog([AgentSpec('a', 'test',
        tools=(ToolBinding('mul', 'multiply', MultiplyInput, tool),))]))
    result = await run.execute(request(limits=RunLimits(max_calls=2)))
    assert result.status == 'succeeded'


@pytest.mark.parametrize("steps,output,code", [
    ([Call("bad", {}, "a")], "", "unknown_tool"),
    ([RuntimeError("sensitive")], "", "engine_failed"),
    ([], {}, "output_contract"),
])
async def test_failures(steps, output, code):
    result = await runner(lambda _: (steps, output)).execute(request())
    assert result.status == "failed" and result.error.code == code
    assert "sensitive" not in result.model_dump_json()


@pytest.mark.parametrize("args,limit,code", [({"a": 1, "b": 2}, 0, "call_limit"),
                                          ({"a": 1, "b": 2}, 8, "tool_failed")])
async def test_tool_errors(args, limit, code):
    async def broken(_):
        raise RuntimeError("private secret")
    r = runner(lambda _: ([Call("t", args, "x")], "done"),
               (ToolBinding("t", "test", MultiplyInput, broken),))
    result = await r.execute(request(limits=RunLimits(max_calls=limit)))
    assert result.error.code == code
    assert result.output is None


async def test_invalid_arguments_are_recoverable_and_count_toward_budget():
    seen, responses, events = [], [], []
    async def tool(args):
        seen.append(args.a)
        return ToolResult(value=args.a * args.b)
    class Engine:
        async def execute(self, req, spec, tools, invoke):
            bad = await invoke('mul', 'bad', {'a': 'private-invalid', 'b': 2})
            assert await invoke('mul', 'bad', {'a': 'private-invalid', 'b': 2}) == bad
            assert bad.value['executed'] is False
            assert bad.value['issues'][0]['loc'] == ['a']
            assert 'private-invalid' not in bad.model_dump_json()
            responses.append(bad.value)
            await invoke('mul', 'corrected', {'a': 3, 'b': 2})
            return RunResult(run_id=req.run_id, status='succeeded', output='6')
    r = CoreRunner(Engine(), AgentCatalog([AgentSpec('a', 'test',
        tools=(ToolBinding('mul', 'multiply', MultiplyInput, tool),))]), events.append)
    result = await r.execute(request(limits=RunLimits(max_calls=2)))
    assert result.status == 'succeeded' and seen == [3]
    assert responses[0]['remaining_tool_calls'] == 1
    assert [e.status for e in events if e.phase == 'tool'] == ['started', 'failed', 'started', 'completed']
    seen.clear()
    assert (await r.execute(request(limits=RunLimits(max_calls=1)))).error.code == 'call_limit'
    assert seen == []


async def test_output_contract():
    assert (await runner(lambda _: ([], {"a": 1}), output_type=MultiplyInput).execute(request())).error.code == "output_contract"
    assert (await runner(lambda _: ([], {"a": 1, "b": 2}), output_type=MultiplyInput).execute(request())).output == {"a": 1, "b": 2}


async def test_turn_limit():
    r = runner(lambda _: ([Call("x", {}, "x")], "done"))
    assert (await r.execute(request(limits=RunLimits(max_turns=1)))).error.code == "turn_limit"


class WaitingEngine:
    def __init__(self):
        self.entered = asyncio.Event()
        self.finished = asyncio.Event()
    async def execute(self, *args):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.finished.set()


async def test_timeout_and_cancel():
    engine = WaitingEngine()
    r = CoreRunner(engine, AgentCatalog([AgentSpec("a", "test")]))
    result = await r.execute(request(limits=RunLimits(timeout_seconds=0.01)))
    assert result.error.code == "timeout" and engine.finished.is_set()
    engine = WaitingEngine()
    r.engine = engine
    task = asyncio.create_task(r.execute(request()))
    await engine.entered.wait()
    task.cancel()
    result = await task
    assert result.status == "cancelled" and engine.finished.is_set()


async def test_delegate_isolation_depth_and_authorization():
    def script(req):
        if req.agent_id == "a":
            return [Call("delegate", {"agent_id": "b", "input": "only explicit"}, "d")], lambda r, v: v[0]
        assert req.input.messages == (Message(role="user", content="only explicit"),)
        assert req.parent_run_id == "parent" and req.depth == 1
        return [], "child result"
    engine = ScriptedEngine(script)
    r = CoreRunner(engine, AgentCatalog([AgentSpec("a", "a", delegates=("b",)), AgentSpec("b", "b")]))
    assert (await r.execute(request(run_id="parent"))).output == "child result"
    assert (await r.execute(request(limits=RunLimits(max_depth=0)))).error.code == "depth_limit"
    engine.script = lambda _: ([Call("delegate", {"agent_id": "not-approved", "input": "x"}, "d")], "")
    assert (await r.execute(request())).error.code == "unauthorized_delegate"
    recursive = CoreRunner(ScriptedEngine(lambda _: ([Call("delegate", {"agent_id": "a", "input": "x"}, "d")], "")),
                           AgentCatalog([AgentSpec("a", "a", delegates=("a",))]))
    assert (await recursive.execute(request())).error.code == "depth_limit"


async def test_unknown_agent():
    r = CoreRunner(ScriptedEngine(lambda _: ([], "")), AgentCatalog([]))
    assert (await r.execute(request())).error.code == "unknown_agent"


async def test_conversation_conflict_and_failed_history():
    engine = WaitingEngine()
    service = ConversationService(CoreRunner(engine, AgentCatalog([AgentSpec("a", "a")])), InMemoryConversationStore())
    task = asyncio.create_task(service.send("c", "a", "first"))
    await engine.entered.wait()
    assert (await service.send("c", "a", "second")).error.code == "conversation_conflict"
    task.cancel()
    assert (await task).status == "cancelled"
    assert (await service.store.load("c")).revision == 0
    service.runner = runner(lambda _: ([], "works"))
    assert (await service.send("c", "a", "retry")).status == "succeeded"


async def test_store_revision_idempotency_isolation():
    store = InMemoryConversationStore()
    messages = (Message(role="user", content="u"), Message(role="assistant", content="a"))
    await store.append("c", 0, "r", messages)
    await store.append("c", 0, "r", messages)
    assert (await store.load("c")).revision == 1
    assert (await store.load("other")).messages == ()
    with pytest.raises(CoreFailure):
        await store.append("c", 0, "other", messages)
    with pytest.raises(CoreFailure):
        await store.append("c", 0, "r", messages[:1])


async def test_tool_id_conflict_and_invalid_result():
    async def ok(_):
        return ToolResult(value=1)
    binding = ToolBinding("t", "t", MultiplyInput, ok)
    r = runner(lambda _: ([Call("t", {"a": 1, "b": 2}, "same"), Call("t", {"a": 2, "b": 2}, "same")], ""), (binding,))
    assert (await r.execute(request())).error.code == "tool_call_conflict"
    async def invalid(_):
        return "not structured"
    r = runner(lambda _: ([Call("t", {"a": 1, "b": 2}, "x")], ""), (ToolBinding("t", "t", MultiplyInput, invalid),))
    assert (await r.execute(request())).error.code == "invalid_tool_result"


@pytest.mark.parametrize("cancel", [True, False])
async def test_delegate_cancel_and_parent_deadline(cancel):
    entered, cleaned = asyncio.Event(), asyncio.Event()
    class Engine:
        async def execute(self, req, spec, tools, invoke):
            if req.agent_id == "a":
                await invoke("delegate", "d", {"agent_id": "b", "input": "independent"})
            else:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            return RunResult(run_id=req.run_id, status="succeeded", output="wrong")
    events = []
    r = CoreRunner(Engine(), AgentCatalog([AgentSpec("a", "a", delegates=("b",)), AgentSpec("b", "b")]), events.append)
    task = asyncio.create_task(r.execute(request(limits=RunLimits(timeout_seconds=10 if cancel else 0.02))))
    await entered.wait()
    if cancel:
        task.cancel()
    result = await task
    assert result.error.code == ("cancelled" if cancel else "timeout")
    assert cleaned.is_set()
    assert [e.status for e in events if e.phase == "delegate"] == ["started", "cancelled"]
    assert all(e.tool_call_id == "d" for e in events if e.phase == "delegate")


async def test_distinct_conversations_and_store_failure():
    service = build()
    await service.send("a", "assistant", "demo")
    await service.send("b", "assistant", "demo")
    assert len((await service.store.load("a")).messages) == 2
    assert len((await service.store.load("b")).messages) == 2
    class BrokenStore:
        async def load(self, _):
            raise OSError("secret")
    service.store = BrokenStore()
    assert (await service.send("a", "assistant", "x")).error.code == "store_failed"
