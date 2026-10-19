"""Application composition adds research guards without changing the Core loop."""
import os
from datetime import datetime, timezone
from dataclasses import replace
from ..execution.ledger import EventSink
from ..execution.service import ExecutionService
from ..context.execution import ScopeExecutionGate, ScopedExecutor
from ..core import AgentCatalog, CoreRunner
from ..context.tool_guard import ScopedTool
from ..contracts import AgentSpec, CoreFailure, ToolResult
from ..research.models import ResearchAnswer
from .commits import ResearchLedger
from .research_web import web_bindings
from ..delivery_files import FILE_INSTRUCTIONS


def build_execution(database, scopes, bindings, research, engine=None):
    async def source_call(args, original, scope):
        binding = research.query('run', scopes.running.get())
        research.validate_binding(binding)
        try:
            value = await original.handler(args)
        except CoreFailure as exc:
            if exc.error.code != 'scope_denied':
                raise
            # Revocation still aborts the run. A mistaken ref/query receives no source
            # content, but the model may correct it using this run's permitted refs.
            scopes.valid(scope)
            research.validate_binding(binding)
            value = ToolResult(value={'status': 'unavailable', 'fragments': [],
                'error': 'Source ref or query is invalid. Use an available body ref, not a search discovery ref; query must be 2–200 characters.',
                'available_sources': [ref.model_dump() for ref in research.run_resource_refs(binding)[:8]],
                'instruction': 'Choose an existing ref or search_context for needed source metadata; do not enumerate the whole library.'})
        research.validate_binding(binding)
        if getattr(research, 'collaboration', None) is not None and (value.value.get('fragments') or (value.value.get('format') == 'json_text' and value.value.get('text'))):
            research.collaboration.record_source_read(binding.run_id, args.ref)
        return value

    agents = []
    for key, scope in bindings.items():
        original = ScopedTool(scopes, scope).read_binding()
        async def guarded(args, original=original, scope=scope):
            return await source_call(args, original, scope)
        if 'read_source' in scope.tools:
            search = ScopedTool(scopes, scope).search_binding()
            async def guarded_search(args, original=search, scope=scope):
                return await source_call(args, original, scope)
            tools = (replace(original, handler=guarded),) + ((replace(search, handler=guarded_search),) if 'search_source' in scope.tools else ())
        else:
            tools = ()
        tools += web_bindings(research, scope)
        if getattr(research, 'collaboration', None) is not None:
            from ..collaboration.tools import manager_bindings
            node = research.node(key)
            if node.display_parent_id is None and not node.plan_proposal_id:
                tools += manager_bindings(research, scope)
        admitted = research.query('run', research.node(key).active_run_id)
        if admitted and admitted.limits.max_calls == 0:
            tools = ()
        elif admitted and admitted.command_id.startswith('resume:'):
            tools = tuple(t for t in tools if t.name in
                ('read_context', 'read_source', 'search_source', 'get_task_status'))
        from .manager_profiles import BASE, PROFILES, ROUTING
        routed = any(t.name == 'select_task_tools' for t in tools)
        instructions = BASE + ROUTING if routed else BASE
        from ..context.capabilities import context_tools, SKILL_POLICY
        snapshot = scopes.repository.snapshot(admitted.run_id) if admitted else None
        if snapshot:
            tools = context_tools(snapshot, research.context.selector, tools, PROFILES if routed else {},
                lambda admitted=admitted: research.validate_binding(admitted),
                memories=research.memories, research_id=research.node(key).research_id,
                enabled=admitted.limits.max_calls > 0, read_only=admitted.command_id.startswith('resume:'))
        if research.node(key).plan_proposal_id:
            proposal = research.planning.get(research.node(key).plan_proposal_id)
            instructions = (f'当前阶段：approved_plan_execution。用户已明确批准提案 {proposal.id} 的版本 {proposal.approved_revision}。'
                '必须执行当前研究步骤并交付其要求的结果。原请求中的“只生成计划”“等待确认”“当前不执行”是规划阶段限制，'
                '本次批准已经解除这些阶段限制；不得再次生成整份计划或请求重复确认。其他研究目标、资料限制、工具权限和预算仍然有效。'
                '遵守冻结目标、步骤指令与交付要求，不自行派发或扩展计划。')
        if not routed and tools:
            instructions += PROFILES['reports'].instructions
        instructions += ('\n交付判定：blocking_missing 只列尚未完成的必需交付，非空将阻断后续步骤。'
            '用户允许保留的未知、无实测数字、证据边界以及已由其他官方来源补足的抓取失败，应在 limitations 和报告中如实说明，不能列为 blocking_missing。'
            '本规则明确输出字段语义，步骤中笼统的“未知标 missing”按此区分。不得隐瞒真正缺失的必需交付。'
            '论文研究优先获取全文；arXiv abs 仅为摘要，可搜索同一论文并使用 PDF full-text discovery ref。'
            '若输入已明确是摘要，直接搜索全文，不浪费工具次数再次验证摘要身份。'
            '恢复提案的 sources 已包含此前保存的正文，优先用 search_source 定位现有正文；已有官方论文和确认文档时不要重新联网搜索。'
            'results包含上游已保存报告和校验过的evidence；优先复用这些已提供的原文引文、ref和location，不必重复检索同一片段。仅为真正的新证据缺口读取来源。'
            '抓取长论文和官方文档时，fetch_web 必须同时提供聚焦 query，立即获取带位置的机制证据；'
            '例如 ReAct 机制查询 interleaved，人工确认机制查询 approval。全文保存不等于已阅读全部内容，引用只用实际返回摘录。')
        instructions += ('\n保持约1500字的步骤报告。claims只列支撑报告核心结论的事实，每条通常一个短摘录足够；'
            '未使用的来源无需堆入声明。优先复制短的完整正文句子；只有任务需要代码细节时引用代码，'
            '严格保留原文标点、PDF排版字符和转义，不手动规范化引文。')
        instructions += FILE_INSTRUCTIONS
        if snapshot and snapshot.selection: instructions += SKILL_POLICY
        def validate_output(output, binding=admitted):
            research.validate_binding(binding)
            return research.evidence.validate_output(f'result:{binding.run_id}', binding, output)
        agents.append(AgentSpec(key,
            '当前 UTC 日期：' + datetime.now(timezone.utc).date().isoformat() + '。' + instructions,
            tools=tools, output_type=ResearchAnswer, strict_output=True,
            tool_profiles=PROFILES if routed else {}, validate_output=validate_output,
            model=research.planning.get(research.node(key).plan_proposal_id).model if research.node(key).plan_proposal_id else os.environ.get('OPENAI_MANAGER_MODEL', 'deepseek-v4-pro')))
    catalog = AgentCatalog(agents)
    ledger = ResearchLedger(database, research.evidence)
    events = EventSink(ledger)
    if engine is None: raise ValueError('Research execution engine is not configured')
    runner = CoreRunner(engine, catalog, events)
    gate = ScopeExecutionGate(runner, scopes)
    class ResearchGate:
        async def execute(self, request):
            binding = research.query('run', request.run_id)
            research.validate_binding(binding)
            root = research.research(research.node(binding.node_id).research_id)
            root_scope = scopes.current(root.scope_id, research.owner)
            watch = request.run_id + ':library'
            with scopes.track(watch, root_scope):
                return await gate.execute(request)
    def validate(commit): return research.validate_commit(gate.validate_commit(commit))
    execution = ExecutionService(ResearchGate(), ledger, events, validate_commit=validate)
    class ResearchExecutor(ScopedExecutor):
        async def execute(self, request):
            previous = ledger.get(request.run_id)
            if previous and previous.status == 'succeeded':
                research.check_result(f'result:{request.run_id}')
            return await super().execute(request)

        def retry_save(self, run_id):
            value = super().retry_save(run_id)
            if value.result and value.result.status == 'succeeded':
                research.check_result(f'result:{run_id}')
            return value
    return ResearchExecutor(execution, gate, bindings), catalog
