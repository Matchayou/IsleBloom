"""Local same-origin HTTP product API; all mutations use public services."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import JSONResponse, FileResponse, Response
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from ..research.models import NodeCommand, ResearchPlan
from ..research.projection import project
from ..research.aggregate import CompanyPeriod, validate_comparison
from ..resources.snapshots import ResourceRef
from ..context.scopes import ScopeDenied
from ..execution.ports import Conflict
from ..execution.service import PublicationError
from ..routing import RouteBlocked
from ..research.chat import ChatService


def serial(value):
    if hasattr(value, 'model_dump'): return value.model_dump(mode='json')
    if isinstance(value, dict): return {k: serial(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [serial(v) for v in value]
    if isinstance(value, datetime): return value.isoformat()
    return value


def parse_summary(parsed):
    return {'id':parsed.id,'status':parsed.status,'phase':parsed.phase,
            'fragment_count':len(parsed.fragments),'issues':parsed.issues,
            'ocr_pages':parsed.ocr_pages,'cached_from':parsed.cached_from}


def create_app(service, frontend=None, task_service=None, durable=None, evaluation=None, evolution=None):
    tasks = set()
    chat = ChatService(service, task_service)
    from .replies import ChatReplies
    replies = ChatReplies(service, chat, tasks)
    planning = service.planning
    planning.durable = durable
    from ..research.planning_models import GeneratePlan, RevisePlan, PlanDecision
    scheduled_plans = set()
    def schedule_plan(p):
        if p.id in scheduled_plans or p.id in planning.active: return
        async def work():
            try:
                if durable: await durable.submit_plan(p.id, planning)
                elif p.status == 'generating': await planning.generate(p.id)
                else: await planning.execute(p.id)
            except Exception as exc:
                current = service.query('proposal',p.id)
                if current.status in ('generating','queued','running'):
                    planning.change(current,status='generation_failed' if current.status=='generating' else 'blocked',reason=type(exc).__name__ + ': ' + str(exc)[:500])
            finally: scheduled_plans.discard(p.id)
        scheduled_plans.add(p.id)
        task = asyncio.create_task(work())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    from ..composition import build_projects
    projects = build_projects(service.database, service, task_service) if task_service else None
    collaboration = service.collaboration if projects else None
    if collaboration: collaboration.durable = durable
    def task_job_id(spec): return f'{spec.id}-v{spec.version}'
    def respond(value, status=200): return JSONResponse(serial(value), status_code=status)
    async def guarded(request):
        try:
            action = request.path_params['action']
            if request.method == 'POST':
                origin = request.headers.get('origin')
                if origin and origin != str(request.base_url).rstrip('/'):
                    return respond({'error':'Cross-origin mutation denied'},403)
            elif action not in ('review_issue','expert_run','run_analysis','project_deliveries','generated_file','plan_proposal','plan_proposals','researches','projects','project_contents','project_files','expert_thread','expert_context','projection','result','resources','resource_original','run','task_activity','task_status','task_artifact','task_receipt','task_route','chat_read','chat_stream','chat_transfers','evidence','citation','original','durable_status','feedback','feedbacks','evaluation_case','evaluation_compare','assets','workflow_instance','memories','memory_captures','memory_writes','evolution_candidates','evolution_candidate','evolution_compare','evolution_preview','domain_trajectories','domain_candidate'):
                return respond({'error':'POST required'},405)
            return await endpoints[action](request)
        except (ScopeDenied, PermissionError): return respond({'error':'Access denied; scope or selected input changed'},403)
        except PublicationError as exc: return respond({'error':exc.outcome.status,'run_id':exc.outcome.run_id},503)
        except RouteBlocked as exc: return respond({'error':exc.record.reason,'route_id':exc.record.id},409)
        except KeyError: return respond({'error':'Not found'},404)
        except (Conflict,ValueError,ValidationError,RuntimeError) as exc: return respond({'error':str(exc)},409)
    async def researches(request):
        if request.method == 'GET': return respond(service.query('list_researches',service.owner))
        data = await request.json()
        return respond(service.create(data['goal'],data.get('id')),201)
    async def project_list(request):
        if projects is None: raise KeyError('projects')
        if request.method == 'GET': return respond(projects.list())
        data = await request.json()
        return respond(projects.create(data['name'],data.get('id')),201)
    async def project_contents(request):
        return respond(projects.contents(request.query_params['project_id']))
    async def project_main_task(request):
        data = await request.json()
        return respond(await projects.create_main_task(data['project_id'],data['title'],data.get('research_id')),201)
    async def project_folder(request):
        data = await request.json()
        return respond(projects.create_folder(data['project_id'],data['name'],data.get('parent_id'),data.get('id')),201)
    async def project_folder_update(request):
        data = await request.json()
        return respond(projects.update_folder(data['id'],data['expected_revision'],name=data.get('name'),
            parent_id=data.get('parent_id'),change_parent='parent_id' in data))
    async def project_arrange(request):
        data = await request.json()
        return respond(projects.arrange(data['project_id'],data['kind'],data['object_id'],data['expected_revision'],
            folder_id=data.get('folder_id'),title=data.get('title')))
    async def project_files(request):
        q = request.query_params
        return respond(projects.file_links(q['project_id'],q.get('research_id'),q.get('node_id'),q.get('task_id')))
    async def project_deliveries(request):
        q = request.query_params
        return respond(projects.deliveries(q['project_id'], q.get('research_id')))
    async def generated_file(request):
        q = request.query_params
        version = service.query('result', q['result_id'])
        node = service.node(version.node_id)
        if projects.main_task(node.research_id).project_id != q['project_id']:
            raise ScopeDenied('Foreign file delivery')
        file = next((f for f in service.result_files(version.id)
                     if f.metadata()['file_id'] == q['file_id']), None)
        if file is None: raise KeyError(q['file_id'])
        metadata = file.metadata()
        disposition = 'attachment' if q.get('download') == '1' else 'inline'
        return Response(file.content.encode('utf-8'), media_type=metadata['media_type'], headers={
            'Content-Disposition': disposition + "; filename*=UTF-8''" + quote(file.name, safe=''),
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "sandbox allow-scripts; default-src 'none'; script-src https: 'unsafe-inline'; style-src https: 'unsafe-inline'; img-src https: data:; font-src https: data:; connect-src https:; frame-src https:; base-uri 'none'; form-action 'none'"})
    async def expert_thread(request):
        return respond(projects.expert_thread(request.query_params['task_id']))
    async def expert_context(request):
        if projects is None: raise KeyError('projects')
        q = request.query_params
        response = respond(projects.expert_context(q['task_id'], q.get('run_id')))
        response.headers['Cache-Control'] = 'no-store'
        return response
    async def review_issue(request):
        if collaboration is None: raise KeyError('collaboration')
        q = request.query_params
        response = respond(collaboration.review.issue_evidence(q['task_id'], int(q['spec_version']), int(q['issue_index'])))
        response.headers['Cache-Control'] = 'no-store'
        return response
    async def expert_discuss(request):
        data = await request.json()
        return respond(await projects.discuss(data['task_id'],data['content'],data['expected_revision'],data.get('message_id')))
    async def expert_run(request):
        if projects is None: raise KeyError('projects')
        q = request.query_params
        response = respond(projects.expert_run(q['task_id'], q['run_id'], q.get('call_id')))
        response.headers['Cache-Control'] = 'no-store'
        return response
    async def expert_revise(request):
        data = await request.json()
        revision = collaboration.revise_task(command_id=data['command_id'],
            task_id=data['task_id'], expected_version=data['expected_version'],
            request=data['request'], mode=data['mode'], notify_manager=True)
        return respond(await collaboration.process_revision(revision.id),201)
    async def projection(request):
        return respond(project(service,request.query_params['research_id']))
    async def command(request):
        c = NodeCommand.model_validate(await request.json())
        # Execute asynchronously in this process; accepted command is persisted before response.
        task = asyncio.create_task(service.submit(c))
        tasks.add(task)
        def finished(t):
            tasks.discard(t)
            if not t.cancelled(): t.exception()  # Expose persisted command/run status, no unhandled task warning.
        task.add_done_callback(finished)
        await asyncio.sleep(0)
        if task.done(): return respond(task.result())
        return respond(service.query('command',c.command_id),202)
    async def result(request):
        version, output = service.read_result(request.query_params['id'])
        return respond({'version':version,'output':output,'inputs':service.query('inputs',version.id),
                        'evidence':service.evidence.read(version.id)})
    async def evidence(request):
        return respond(service.evidence.read(request.query_params['result_id']))
    async def citation(request):
        return respond(service.evidence.locate(request.query_params['result_id'],request.query_params['evidence_id']))
    async def original(request):
        result_id, evidence_id = request.query_params['result_id'], request.query_params['evidence_id']
        located = service.evidence.locate(result_id,evidence_id)
        scope = service.evidence.source_scope(result_id)
        content = service.scopes.original(scope,located['evidence'].ref)
        return Response(content,media_type=located['snapshot'].media_type,
                        headers={'Content-Disposition':'inline','Cache-Control':'no-store'})
    async def evidence_review(request):
        data = await request.json()
        return respond(service.evidence.review(data['result_id'],data['relation_id'],
            data['assessment'],data['reason']))
    async def search(request):
        data = await request.json()
        return respond(await service.search.discover(data['research_id'],data['query']))
    async def fetch_discovery(request):
        data = await request.json()
        value = await service.search.fetch(data['research_id'],data['ref'])
        return respond({**value,'parse':parse_summary(value['parse'])},201)
    async def resources(request):
        r = service.research(request.query_params['research_id'])
        scope = service.scopes.current(r.scope_id,service.owner)
        return respond({'scope':scope,'resources':[{'ref':ref,'resource':service.scopes.resources.describe(ref)[0],
            'parse':parse_summary(service.scopes.resources.describe(ref)[2])} for ref in scope.grants]})
    async def resource_original(request):
        research = service.research(request.query_params['research_id'])
        scope = service.scopes.current(research.scope_id, service.owner)
        ref = ResourceRef.model_validate({key:request.query_params[key]
            for key in ('resource_id','snapshot_id','parse_id')})
        content = service.scopes.original(scope,ref)
        resource, snapshot, _ = service.scopes.resources.describe(ref)
        inline = snapshot.media_type in ('text/plain','text/csv','application/pdf','image/png','image/jpeg','image/gif','image/webp')
        return Response(content, media_type=snapshot.media_type,
                        headers={'Content-Disposition':f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(resource.name, safe='')}",
                                 'Cache-Control':'no-store',
                                 'X-Content-Type-Options':'nosniff'})
    async def upload(request):
        r = service.research(request.query_params['research_id'])
        data = await request.body()
        if len(data) > 25_000_000: return respond({'error':'File exceeds 25 MB'},413)
        scope = service.scopes.current(r.scope_id,service.owner)
        scope, ref = service.scopes.ingest(scope,content=data,name=request.query_params['name'],
            media_type=request.headers.get('content-type','application/octet-stream'))
        return respond({'ref':ref,'parse':parse_summary(service.scopes.resources.describe(ref)[2])},201)
    async def scope(request):
        data = await request.json()
        r = service.research(data['research_id'])
        node = service.node(data['node_id']) if data.get('node_id') else None
        if node and node.research_id != r.id: raise Conflict('Foreign node')
        current = service.scopes.current(node.scope_id if node else r.scope_id, service.owner)
        if data['operation'] == 'revoke':
            value = service.scopes.revoke(current,ResourceRef.model_validate(data['ref']))
        elif data['operation'] == 'select':
            value = service.scopes.select(current,tuple(ResourceRef.model_validate(v) for v in data['refs']))
        else: raise ValueError('Unknown scope operation')
        return respond(value)
    def authorize_node_run(run_id):
        from ..controlled.engine import ControlledEngine
        if isinstance(service.engine, ControlledEngine):
            service.engine.validate_run(run_id)
        binding = service.query('run',run_id)
        if binding is None: raise KeyError(run_id)
        node = service.node(binding.node_id)
        current = service.scopes.current(node.scope_id,service.owner)
        root = service.scopes.current(service.research(node.research_id).scope_id,service.owner)
        for ref in binding.resource_refs:
            service.scopes.check(current,ref)
            service.scopes.check(root,ref)
        for key in binding.result_ids: service.check_result(key, current, allow_excluded=True)
        return binding
    async def run(request):
        run_id = request.query_params['id']
        binding = authorize_node_run(run_id)
        return respond({'binding':binding,'run':service.ledger.get(run_id),'events':service.ledger.events(run_id),
            'context':service.scopes.repository.snapshot(run_id)})
    async def retry_save(request):
        data = await request.json()
        return respond(service.retry_save(data['run_id']))
    async def run_analysis(request):
        from ..execution.analysis import analyze_run
        from ..controlled.engine import ControlledEngine
        research = service.research(request.query_params['research_id'])
        root = service.scopes.current(research.scope_id, service.owner)
        rows, unavailable = [], 0
        def append(run_id, owner, validate, kind, object_id):
            nonlocal unavailable
            try:
                validate()
            except (ScopeDenied, KeyError, Conflict):
                unavailable += 1
                return
            record = owner.ledger.get(run_id)
            if record is None: return
            controlled = owner.engine.ledger if isinstance(owner.engine, ControlledEngine) else None
            row = analyze_run(record, owner.ledger.events(run_id),
                operations=controlled.operation_history(run_id) if controlled else None,
                measurements=controlled.model_measurements(run_id) if controlled else ())
            rows.append(dict(row, kind=kind, object_id=object_id))
        for node in service.query('list_nodes', research.id):
            for binding in service.query('list_runs', node.id):
                append(binding.run_id, service, lambda:authorize_node_run(binding.run_id), 'node', node.id)
        if task_service:
            for spec in task_service.query('list_specs', research.id):
                for binding in task_service.query('list_runs', spec.id):
                    def validate_task():
                        if isinstance(task_service.engine, ControlledEngine):
                            task_service.engine.validate_run(binding.id)
                        snapshot = service.scopes.repository.snapshot(binding.id)
                        service.scopes.valid(snapshot.scope)
                        for ref in task_service.run_resource_refs(binding):
                            service.scopes.check(root, ref)
                            service.scopes.check(snapshot.scope, ref)
                        for message in spec.chat_context:
                            chat._validate_message(spec.research_id, message, None if spec.context_packet_id else spec.node_id)
                    append(binding.id, task_service, validate_task, 'task', spec.id)
        if collaboration and task_service:
            for cycle in collaboration.query('review_loops_for', research.id):
                for review_run_id in collaboration.review.run_ids(cycle):
                    if task_service.ledger.get(review_run_id) is not None:
                        append(review_run_id, task_service, lambda:collaboration.review.visible(cycle.task_id),
                            'task', cycle.reviewer_task_id)
        rows.sort(key=lambda row:(row['created_at'],row['run_id']), reverse=True)
        response = respond({'runs':rows, 'unavailable_runs':unavailable})
        response.headers['Cache-Control'] = 'no-store'
        return response
    async def validate(request):
        data = await request.json()
        return respond(validate_comparison([CompanyPeriod.model_validate(c) for c in data]))
    async def plan_generate(request):
        p = planning.create(GeneratePlan.model_validate(await request.json()))
        if p.status == 'generating': schedule_plan(p)
        return respond(planning.view(p.id), 202)
    async def plan_proposal(request):
        return respond(planning.view(request.query_params['id']))
    async def plan_proposals(request):
        return respond([planning.view(p.id) for p in planning.list(request.query_params['research_id'], request.query_params.get('node_id'))])
    async def plan_revise(request):
        p = planning.revise(RevisePlan.model_validate(await request.json()))
        if p.status == 'generating': schedule_plan(p)
        return respond(planning.view(p.id), 202)
    async def plan_confirm(request):
        p = planning.confirm(PlanDecision.model_validate(await request.json()))
        if p.status == 'queued': schedule_plan(p)
        return respond(planning.view(p.id), 202)
    async def plan_recover(request):
        data = await request.json()
        budget = data.pop('max_total_tokens',None)
        p = planning.recovery_proposal(PlanDecision.model_validate(data),max_total_tokens=budget)
        return respond(planning.view(p.id),201)
    async def plan_reject(request):
        p = await planning.decide(PlanDecision.model_validate(await request.json()), 'reject')
        return respond(planning.view(p.id))
    async def plan_cancel(request):
        p = await planning.decide(PlanDecision.model_validate(await request.json()), 'cancel')
        return respond(planning.view(p.id))
    async def task_activity(request):
        if task_service is None: raise KeyError('tasks')
        research = service.research(request.query_params['research_id'])
        root_scope = service.scopes.current(research.scope_id, service.owner)
        dependencies = collaboration.query('dependencies_for', research.id) if collaboration else ()
        values = []
        cycles = collaboration.query('review_loops_for', research.id) if collaboration else ()
        for spec in task_service.query('list_specs', research.id):
            try:
                for message in spec.chat_context:
                    chat._validate_message(spec.research_id, message, None if spec.context_packet_id else spec.node_id)
            except (ScopeDenied, KeyError):
                continue
            runs = []
            for binding in task_service.query('list_runs', spec.id):
                receipt = task_service.query('receipt', f'task-result:{binding.id}')
                visible = True
                try:
                    from ..controlled.engine import ControlledEngine
                    if isinstance(task_service.engine, ControlledEngine):
                        task_service.engine.validate_run(binding.id)
                    for ref in binding.resource_refs: service.scopes.check(root_scope, ref)
                except (ScopeDenied, KeyError, Conflict): visible = False
                execution = task_service.ledger.get(binding.id)
                runs.append({'binding':binding, 'execution_status':execution.status if execution else 'queued',
                    'receipt':receipt if visible else None,
                    'artifact':receipt.artifact if visible and receipt else None,
                    'execution_phase':{k:v for k,v in (task_service.engine.ledger.progress(binding.id) or {}).items()
                        if k in ('phase','reason','finalization_reason','transitions')} if isinstance(task_service.engine,ControlledEngine) else None,
                    'usage':execution.result.usage if execution and execution.result else None,
                    'output_repairs': execution.result.output_repairs if visible and execution and execution.result else (),
                    'error':execution.result.error if execution and execution.result else None,
                    'started_at':execution.created_at if execution else None,
                    'access': 'available' if visible else 'revoked'})
            runs.sort(key=lambda row:(row['binding'].spec_version,
                row['started_at'].isoformat() if row['started_at'] else ''))
            route = task_service.query('route', spec.route_id) if spec.route_id else None
            route_visible = True
            try:
                for ref in route.resource_refs if route else (): service.scopes.check(root_scope, ref)
            except (ScopeDenied, KeyError): route_visible = False
            job = None
            intent = collaboration.query('intent_for_task', spec.id) if collaboration else None
            if durable:
                try: job = await durable.status('collaboration:' + intent.command_id if intent and spec.version == 1 else task_job_id(spec))
                except KeyError: pass
                except (ScopeDenied, Conflict): job = {'status':'ACCESS_DENIED'}
            visible_dependencies = []
            for edge in dependencies:
                if edge.downstream_task_id != spec.id or edge.downstream_version != spec.version: continue
                try:
                    if edge.result_id:
                        receipt = task_service.receipt(edge.result_id)
                        for ref in receipt.resource_refs: service.scopes.check(root_scope, ref)
                    else:
                        source = task_service.spec(edge.upstream_task_id, edge.upstream_version)
                        projects._fixed_context(source)
                except (ScopeDenied, KeyError, Conflict): continue
                visible_dependencies.append(edge)
            visible_messages = []
            if collaboration and spec.expert_session_id:
                for message in collaboration.query('messages_for', spec.expert_session_id):
                    if message.task_id != spec.id or not message.sender_task_id: continue
                    try:
                        projects._fixed_context(spec)
                        source = task_service.spec(message.sender_task_id)
                        if source.research_id != spec.research_id: continue
                        projects._fixed_context(source)
                    except (ScopeDenied, KeyError, Conflict): continue
                    visible_messages.append(message)
            information_requests = collaboration.information.visible_for(spec.id) if collaboration else []
            cycle = next((v for v in cycles if v.task_id == spec.id or v.reviewer_task_id == spec.id), None)
            public_cycle = None
            if cycle:
                try: public_cycle = collaboration.review.visible(cycle.task_id)
                except (ScopeDenied, KeyError, Conflict): pass
            values.append({'spec':spec, 'route':route if route_visible else None, 'runs':runs,
                'job_status':job['status'] if job else None,
                'dependencies':visible_dependencies,
                'messages':visible_messages,
                'information_requests':information_requests,
                'dispatch_status':intent.status if intent else None,
                'collaboration_role': 'reviewer' if cycle and cycle.reviewer_task_id == spec.id else 'executor',
                'automatic_review': public_cycle})
        return respond(values)
    async def task_status(request):
        return respond(collaboration.task_status(request.query_params['research_id'], request.query_params['task_id']))

    async def task_artifact(request):
        if task_service is None: raise KeyError('tasks')
        return respond(task_service.read_artifact(request.query_params['id']))

    async def task_receipt(request):
        if task_service is None: raise KeyError('tasks')
        receipt = task_service.receipt(request.query_params['id'])
        spec = task_service.spec(receipt.task_id, receipt.spec_version)
        for message in spec.chat_context:
            chat._validate_message(spec.research_id, message, None if spec.context_packet_id else spec.node_id)
        root = service.research(spec.research_id)
        scope = service.scopes.current(root.scope_id, service.owner)
        for ref in receipt.resource_refs: service.scopes.check(scope, ref)
        return respond(receipt)
    async def task_route(request):
        if task_service is None: raise KeyError('tasks')
        route = task_service.query('route', request.query_params['id'])
        if route is None: raise KeyError('route')
        try: spec = task_service.spec(route.task_id, route.spec_version)
        except KeyError: spec = None
        if spec:
            for message in spec.chat_context:
                chat._validate_message(spec.research_id, message, None if spec.context_packet_id else spec.node_id)
        root = service.research(route.research_id)
        scope = service.scopes.current(root.scope_id, service.owner)
        for ref in route.resource_refs: service.scopes.check(scope, ref)
        return respond(route)
    async def task_create(request):
        if task_service is None: raise KeyError('tasks')
        data = await request.json()
        kwargs = dict(research_id=data['research_id'], kind=data['kind'],
            user_request=data['request'], node_id=data.get('node_id'),
            required_refs=tuple(ResourceRef.model_validate(v) for v in data.get('required_refs', ())),
            optional_refs=tuple(ResourceRef.model_validate(v) for v in data.get('optional_refs', ())),
            authorized_refs=tuple(ResourceRef.model_validate(v) for v in data.get('authorized_refs', ())),
            agent_mode=data.get('agent_mode', 'specialist'), role_instructions=data.get('role_instructions', ''),
            required_results=tuple(data.get('required_results', ())),
            memory_ids=tuple(data.get('memory_ids', ())),
            allow_partial_results=data.get('allow_partial_results', False), task_id=data.get('task_id'),
            evidence_required=data.get('evidence_required',False),parent_task_id=data.get('parent_task_id'))
        if data.get('route'):
            value = await task_service.create_routed(**kwargs,
                approved_summary=data['routing_summary'],
                preferred_expert=data.get('expert_id'), preferred_model=data.get('model'),
                budget_usd=data.get('budget_usd'), reroute_reason=data.get('reroute_reason'))
        else:
            value = task_service.create(**kwargs, model=data.get('model'), expert_id=data.get('expert_id'))
        return respond(value,201)
    async def memories(request):
        from ..agent_identity import AgentIdentity
        if task_service is None: raise KeyError('tasks')
        specialist_id = request.query_params.get('specialist_id')
        if specialist_id: task_service.methods.method(specialist_id)
        return respond(task_service.memories.list(request.query_params['research_id'],
            agent=AgentIdentity(mode='specialist', id=specialist_id) if specialist_id else None))
    async def memory_create(request):
        if task_service is None: raise KeyError('tasks')
        from ..memories.models import CreateMemory
        data = CreateMemory.model_validate(await request.json())
        return respond(task_service.memories.create(**data.model_dump()),201)
    async def memory_captures(request):
        if task_service is None: raise KeyError('tasks')
        research_id = request.query_params['research_id']
        task_service.memories.namespaces(research_id)
        capturer = task_service.memories.capture
        records = []
        for job in capturer.records():
            if job.research_id != research_id: continue
            try:
                capturer.validate(job)
                records.append(job)
            except (ScopeDenied, Conflict, KeyError):
                records.append({'id':job.id, 'state':'source_unavailable'})
        return respond(records)
    async def memory_writes(request):
        from ..agent_identity import AgentIdentity
        if task_service is None: raise KeyError('tasks')
        research_id = request.query_params['research_id']
        specialist = request.query_params.get('specialist_id')
        if specialist: task_service.methods.method(specialist)
        namespaces = task_service.memories.namespaces(research_id,
            AgentIdentity(mode='specialist', id=specialist) if specialist else None)
        return respond([w for w in task_service.memories.writes.records(request.query_params.get('run_id'))
            if (w.request.scope, w.scope_id) in namespaces])
    async def memory_update(request):
        if task_service is None: raise KeyError('tasks')
        data = await request.json()
        return respond(task_service.memories.update(data['id'],data['expected_version'],
            **{k:data[k] for k in ('content','source','status','expires_at') if k in data}))
    async def memory_delete(request):
        if task_service is None: raise KeyError('tasks')
        data = await request.json()
        return respond(task_service.memories.delete(data['id'],data['expected_version']))
    async def domain_trajectories(request):
        if task_service is None: raise KeyError('tasks')
        return respond(task_service.learning.visible_trajectories(request.query_params.get('expert_id')))
    async def domain_candidate(request):
        if task_service is None: raise KeyError('tasks')
        return respond(task_service.learning.status(request.query_params['trajectory_id']))
    async def domain_process(request):
        if task_service is None: raise KeyError('tasks')
        return respond(await task_service.learning.process((await request.json())['trajectory_id']))
    async def domain_revoke(request):
        if task_service is None: raise KeyError('tasks')
        data = await request.json()
        return respond(task_service.learning.revoke(data['trajectory_id'], data['reason']))
    async def task_run(request):
        if task_service is None: raise KeyError('tasks')
        task_id = (await request.json())['task_id']
        if task_service.spec(task_id).pending_dependency_task_ids:
            raise Conflict('Task is waiting for reviewed upstream results')
        spec = task_service.spec(task_id)
        if collaboration and spec.node_id and spec.expert_session_id:
            collaboration.review.ensure(task_id)
            async def execute_assigned():
                try: await collaboration.execute_assigned_task(task_id)
                except (ScopeDenied, KeyError, Conflict):
                    cycle = collaboration.review.get(task_id)
                    if cycle and cycle.status in ('executing', 'reviewing', 'revising'):
                        collaboration.review.change(cycle, status='blocked', reason='Authorization or task changed')
            work = asyncio.create_task(execute_assigned())
            tasks.add(work)
            def finished_assigned(t):
                tasks.discard(t)
                if not t.cancelled(): t.exception()
            work.add_done_callback(finished_assigned)
            return respond({'task_id':task_id, 'automatic_review':collaboration.review.visible(task_id)}, 202)
        if durable:
            spec = task_service.spec(task_id)
            return respond(await durable.submit(task_job_id(spec), (task_id,)), 202)
        return respond(await task_service.run(task_id))
    async def task_review(request):
        if task_service is None: raise KeyError('tasks')
        data = await request.json()
        findings = {int(k):(v['status'],v['evidence']) for k,v in data['findings'].items()}
        return respond(task_service.review(data['result_id'],findings,data.get('reviewer','local-user')))
    async def task_adopt(request):
        if task_service is None: raise KeyError('tasks')
        return respond(task_service.adopt((await request.json())['result_id']))
    async def task_retry_save(request):
        if task_service is None: raise KeyError('tasks')
        return respond(task_service.retry_save((await request.json())['run_id']))
    async def task_stop(request):
        if task_service is None: raise KeyError('tasks')
        data = await request.json()
        task_id = data['task_id']
        spec = task_service.spec(task_id)
        if spec.expert_session_id:
            request_id = data['command_id']
            cancellation = collaboration.cancel_task(command_id=request_id,
                task_id=task_id, expected_version=data['expected_version'],
                reason=data['reason'], notify_manager=True)
            return respond(await collaboration.process_cancellation(cancellation.id))
        if durable:
            return respond(await durable.cancel(task_job_id(spec)))
        return respond({'stopping':task_service.stop(task_id)})
    async def feedback(request):
        if evaluation is None: raise KeyError('evaluation')
        if request.method == 'GET': return respond(evaluation.feedback(request.query_params['id']))
        data = await request.json()
        return respond(evaluation.submit(feedback_id=data['feedback_id'], result_id=data['result_id'],
            kind=data['kind'], description=data['description'],
            proposed_correction=data.get('proposed_correction')),201)
    async def feedbacks(request):
        if evaluation is None: raise KeyError('evaluation')
        return respond(evaluation.list_feedback(request.query_params['research_id']))
    async def feedback_adjudicate(request):
        if evaluation is None: raise KeyError('evaluation')
        data = await request.json()
        return respond(evaluation.adjudicate(data['feedback_id'],status=data['status'],
            attribution=data.get('attribution'),finding=data['finding'],
            reviewer=data['reviewer'],acceptance_number=data.get('acceptance_number')))
    async def evaluation_case(request):
        if evaluation is None: raise KeyError('evaluation')
        if request.method == 'GET': return respond(evaluation.case(request.query_params['id']))
        data = await request.json()
        return respond(evaluation.freeze(data['feedback_id'],split=data['split'],
            dimension=data['dimension']),201)
    async def evaluation_experiment(request):
        if evaluation is None: raise KeyError('evaluation')
        data = await request.json()
        return respond(evaluation.create_experiment(experiment_id=data['experiment_id'],
            case_ids=tuple(data['case_ids']),arms=tuple(data['arms']),
            quality_floor=data['quality_floor'],max_total_cost_usd=data['max_total_cost_usd'],
            min_quality_gain=data.get('min_quality_gain',0),
            candidate_pool=tuple(data.get('candidate_pool',())),
            task_split_digest=data.get('task_split_digest')),201)
    async def evaluation_trial(request):
        if evaluation is None: raise KeyError('evaluation')
        return respond(evaluation.record_trial(**await request.json()),201)
    async def evaluation_compare(request):
        if evaluation is None: raise KeyError('evaluation')
        return respond(evaluation.compare(request.query_params['experiment_id']))
    async def evolution_candidates(request):
        if evolution is None: raise KeyError('evolution')
        return respond(evolution.list_candidates(request.query_params['research_id']))
    async def evolution_candidate(request):
        if evolution is None: raise KeyError('evolution')
        return respond(evolution.candidate(request.query_params['id']))
    async def evolution_propose(request):
        if evolution is None: raise KeyError('evolution')
        return respond(await evolution.propose(**await request.json()),201)
    async def evolution_preview(request):
        if evolution is None: raise KeyError('evolution')
        return respond(evolution.preview(request.query_params['id']))
    async def evolution_run(request):
        if evolution is None: raise KeyError('evolution')
        return respond(await evolution.run_preview(**await request.json()),201)
    async def evolution_assess(request):
        if evolution is None: raise KeyError('evolution')
        return respond(evolution.assess(**await request.json()),201)
    async def evolution_compare(request):
        if evolution is None: raise KeyError('evolution')
        return respond(evolution.compare(request.query_params['candidate_id']))
    async def evolution_publish(request):
        if evolution is None: raise KeyError('evolution')
        data=await request.json()
        return respond(evolution.publish(data['candidate_id'],expected=data['expected'],
            user_actor=data['user_actor'],confirmation=data['confirmation']))
    async def evolution_rollback(request):
        if evolution is None: raise KeyError('evolution')
        data=await request.json()
        return respond(evolution.rollback(data['candidate_id'],expected=data['expected'],
            user_actor=data['user_actor'],reason=data['reason']))
    async def durable_submit(request):
        if durable is None: raise KeyError('durable')
        data = await request.json()
        return respond(await durable.submit(data['job_id'], tuple(data['task_ids'])), 202)
    async def durable_status(request):
        if durable is None: raise KeyError('durable')
        return respond(await durable.status(request.query_params['job_id']))
    async def durable_cancel(request):
        if durable is None: raise KeyError('durable')
        return respond(await durable.cancel((await request.json())['job_id']))
    async def chat_read(request):
        value = chat.read(request.query_params['node_id'])
        parent = service.node(value.node_id).display_parent_id
        source = service.query('chat', parent) if parent else None
        return respond({'chat':value, 'upstream_updated': bool(source and value.fork_source_revision is not None
            and source.revision > value.fork_source_revision)})
    async def chat_transfers(request):
        research = service.research(request.query_params['research_id'])
        values = service.query('list_transfers', research.id)
        visible = []
        for value in values:
            try:
                chat.read(value.source_node_id)
                chat.read(value.target_node_id)
            except (ScopeDenied, KeyError): continue
            visible.append(value)
        return respond(visible)
    async def chat_start(request):
        data = await request.json()
        return respond(await chat.start(data['research_id'], data['goal']), 201)
    async def chat_send(request):
        data = await request.json()
        return respond(chat.send(data['node_id'], data['content'], data['expected_revision'], data.get('message_id')))
    async def chat_branch(request):
        data = await request.json()
        return respond(await chat.branch(data['node_id'], data['fork_message_id'], data['goal'],
            data['expected_revision'], data.get('command_id')), 201)
    async def chat_reply(request):
        record = await replies.start(await request.json())
        return respond(record, 202 if record.status == 'accepted' else 200)
    async def chat_stream(request):
        return replies.stream(request.query_params['command_id'], request.query_params['node_id'])
    async def chat_publish(request):
        data = await request.json()
        return respond(chat.publish_expert(data['node_id'], data['result_id'], data['expected_revision']))
    async def chat_transfer(request):
        data = await request.json()
        return respond(chat.transfer(data['source_node_id'], data['source_message_id'],
            data['target_node_id'], data['expected_revision'], data['expected_node_revision'],
            data.get('transfer_id')), 201)
    async def chat_adopt(request):
        return respond(chat.adopt((await request.json())['transfer_id']))
    endpoints = dict(researches=researches,projects=project_list,project_contents=project_contents,
        project_main_task=project_main_task,project_folder=project_folder,project_folder_update=project_folder_update,
        project_arrange=project_arrange,project_files=project_files,project_deliveries=project_deliveries,
        generated_file=generated_file,expert_thread=expert_thread,expert_context=expert_context,expert_run=expert_run,review_issue=review_issue,
        expert_discuss=expert_discuss,expert_revise=expert_revise,
        projection=projection,command=command,result=result,
        evidence=evidence,citation=citation,original=original,evidence_review=evidence_review,
        search=search,fetch_discovery=fetch_discovery,
        resources=resources,resource_original=resource_original,upload=upload,scope=scope,run=run,run_analysis=run_analysis,retry_save=retry_save,validate=validate,
        plan_generate=plan_generate,plan_proposal=plan_proposal,plan_proposals=plan_proposals,
        plan_revise=plan_revise,plan_confirm=plan_confirm,plan_recover=plan_recover,plan_reject=plan_reject,plan_cancel=plan_cancel,
        task_activity=task_activity,task_status=task_status,task_artifact=task_artifact,task_receipt=task_receipt,task_route=task_route,
        task_create=task_create,task_run=task_run,
        memories=memories,memory_captures=memory_captures,memory_writes=memory_writes,memory_create=memory_create,memory_update=memory_update,memory_delete=memory_delete,
        domain_trajectories=domain_trajectories,domain_candidate=domain_candidate,domain_process=domain_process,domain_revoke=domain_revoke,
        task_review=task_review,task_adopt=task_adopt,task_retry_save=task_retry_save,task_stop=task_stop,
        feedback=feedback,feedbacks=feedbacks,feedback_adjudicate=feedback_adjudicate,
        evaluation_case=evaluation_case,evaluation_experiment=evaluation_experiment,
        evaluation_trial=evaluation_trial,evaluation_compare=evaluation_compare,
        evolution_candidates=evolution_candidates,evolution_candidate=evolution_candidate,
        evolution_propose=evolution_propose,evolution_preview=evolution_preview,
        evolution_run=evolution_run,evolution_assess=evolution_assess,
        evolution_compare=evolution_compare,evolution_publish=evolution_publish,
        evolution_rollback=evolution_rollback,
        durable_submit=durable_submit,durable_status=durable_status,durable_cancel=durable_cancel,
        chat_read=chat_read,chat_transfers=chat_transfers,chat_start=chat_start,chat_send=chat_send,
        chat_branch=chat_branch,chat_reply=chat_reply,chat_stream=chat_stream,chat_publish=chat_publish,
        chat_transfer=chat_transfer,chat_adopt=chat_adopt)
    if task_service is not None:
        from .method_api import method_endpoints
        endpoints.update(method_endpoints(task_service.methods,respond))
    routes = [Route('/api/{action}',guarded,methods=['GET','POST'])]
    if frontend and Path(frontend).is_dir(): routes.append(Mount('/',app=StaticFiles(directory=frontend,html=True)))
    @asynccontextmanager
    async def lifespan(app):
        planning.recover()
        async def recover():
            while True:
                for root in service.query('list_researches', service.owner):
                    for p in service.query('list_proposals', root.id):
                        if p.status in ('generating', 'queued', 'running'):
                            schedule_plan(p)
                if collaboration:
                    await collaboration.recover_pending_cancellations()
                    collaboration.recover_waiting()
                    await collaboration.recover_pending()
                    await collaboration.recover_pending_messages()
                    await collaboration.recover_pending_revisions()
                    await collaboration.recover_reviews()
                    await collaboration.resume_manager()
                await asyncio.sleep(2)
        async def learn():
            while True:
                service.memories.writes.drain()
                await service.memories.capture.drain()
                if task_service: await task_service.learning.drain()
                await asyncio.sleep(2)
        learner = asyncio.create_task(learn())
        worker = asyncio.create_task(recover())
        try:
            yield
        finally:
            learner.cancel()
            with suppress(asyncio.CancelledError): await learner
            if worker:
                worker.cancel()
                with suppress(asyncio.CancelledError): await worker
            for task in tuple(tasks): task.cancel()
            if tasks: await asyncio.gather(*tasks, return_exceptions=True)
    return Starlette(routes=routes, lifespan=lifespan)
