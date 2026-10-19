def build_resources(database, blob_root, parser=None):
    from .resources import ResourceService
    from .context import ScopeService
    from .adapters.filesystem import FileBlobStore
    from .adapters.parsing import DocumentParser
    from .adapters.memory_ledger import MemoryDatabase
    if isinstance(database, MemoryDatabase):
        from .adapters.memory_resources import MemoryResources
        from .adapters.memory_context import MemoryContext
        resources, contexts = MemoryResources(), MemoryContext()
    else:
        from .adapters.postgres.resources import PostgresResources
        from .adapters.postgres.context import PostgresContext
        resources, contexts = PostgresResources(database), PostgresContext(database)
    return ScopeService(contexts, ResourceService(resources, FileBlobStore(blob_root), parser or DocumentParser()))


def build_research(database, scopes, engine=None, owner='local'):
    from .research.service import ResearchService
    from .evidence import EvidenceService
    from .adapters.memory_ledger import MemoryDatabase
    if scopes is None:
        raise ValueError('Research unavailable without ScopeService')
    if isinstance(database, MemoryDatabase):
        from .adapters.memory_research import MemoryResearch
        from .adapters.memory_evidence import MemoryEvidence
        if 'research' not in database.repositories:
            database.repositories['research'] = MemoryResearch()
        if 'evidence' not in database.repositories:
            database.repositories['evidence'] = MemoryEvidence()
    else:
        from sqlalchemy import MetaData
        from .adapters.postgres.research import PostgresResearch, define_tables
        from .adapters.postgres.evidence import PostgresEvidence, define_tables as evidence_tables
        meta = MetaData()
        tables = define_tables(meta)
        ev_tables = evidence_tables(meta)
        with database.transaction(): meta.create_all(database.connection)
        database.repositories['research'] = lambda c: PostgresResearch(c, tables)
        database.repositories['evidence'] = lambda c: PostgresEvidence(c, ev_tables)
    from .app.research_execution import build_execution as factory
    service = ResearchService(database, scopes, lambda bindings, service: factory(database, scopes, bindings, service, service.engine), owner)
    service.engine = engine
    service.evidence = EvidenceService(database, scopes, service)
    from .evidence.search import SearchService
    service.search = SearchService(service)
    from .research.plan_service import PlanService
    service.planning = PlanService(service)
    from .context.assembler import ContextAssembler
    service.context = ContextAssembler()
    service.memories = build_memories(database, service)
    return service


def build_memories(database, research):
    from .adapters.memory_ledger import MemoryDatabase
    from .memories.service import MemoryService
    if 'memories' not in database.repositories and isinstance(database, MemoryDatabase):
        from .adapters.memory_memories import MemoryMemories
        database.repositories['memories'] = MemoryMemories()
    elif 'memories' not in database.repositories:
        from sqlalchemy import MetaData
        from .adapters.postgres.memories import PostgresMemories, define_tables
        meta = MetaData(); table = define_tables(meta)
        with database.transaction(): meta.create_all(database.connection)
        database.repositories['memories'] = lambda c: PostgresMemories(c, table)
    memories = MemoryService(database, research.owner, research)
    from .memories.writing import MemoryWriter
    memories.writes = MemoryWriter(memories)
    from .memories.capture import MemoryCapturer
    memories.capture = MemoryCapturer(memories)
    return memories


def build_tasks(database, scopes, research, engine=None, router=None):
    """Tasks share the existing database, scope and research services."""
    from .tasks import TaskService
    from .adapters.memory_ledger import MemoryDatabase
    if isinstance(database, MemoryDatabase):
        from .adapters.memory_tasks import MemoryTasks
        database.repositories['tasks'] = MemoryTasks()
    else:
        from sqlalchemy import MetaData
        from .adapters.postgres.tasks import PostgresTasks, define_tables
        meta = MetaData()
        tables = define_tables(meta)
        with database.transaction(): meta.create_all(database.connection)
        database.repositories['tasks'] = lambda c: PostgresTasks(c, tables)
    methods = build_methods(database, research)
    memories = research.memories
    service = TaskService(database, scopes, research, engine, router, methods=methods, memories=memories)
    research.chat_tasks = service
    service.learning = build_learning(database, service)
    research.learning = service.learning
    research.context.learning = service.learning
    return service


def build_projects(database, research, tasks):
    from .projects import ProjectService
    from .adapters.memory_ledger import MemoryDatabase
    if 'projects' not in database.repositories:
        if isinstance(database, MemoryDatabase):
            from .adapters.memory_projects import MemoryProjects
            database.repositories['projects'] = MemoryProjects()
        else:
            from sqlalchemy import MetaData
            from .adapters.postgres.projects import PostgresProjects, define_tables
            meta = MetaData()
            tables = define_tables(meta)
            with database.transaction(): meta.create_all(database.connection)
            database.repositories['projects'] = lambda c: PostgresProjects(c, tables)
    projects = ProjectService(database, research, tasks)
    build_collaboration(database, research, tasks, projects)
    return projects


def build_collaboration(database, research, tasks, projects):
    from .collaboration import CollaborationService
    from .adapters.memory_ledger import MemoryDatabase
    if 'collaboration' not in database.repositories:
        if isinstance(database, MemoryDatabase):
            from .adapters.memory_collaboration import MemoryCollaboration
            database.repositories['collaboration'] = MemoryCollaboration()
        else:
            from sqlalchemy import MetaData
            from .adapters.postgres.collaboration import PostgresCollaboration, define_tables
            meta = MetaData()
            tables = define_tables(meta)
            with database.transaction(): meta.create_all(database.connection)
            database.repositories['collaboration'] = lambda c: PostgresCollaboration(c, tables)
    service = CollaborationService(database, research, tasks, projects)
    research.collaboration = service
    return service


def build_evaluation(database, tasks):
    from .evaluation import EvaluationService
    from .adapters.memory_ledger import MemoryDatabase
    if isinstance(database, MemoryDatabase):
        from .adapters.memory_evaluation import MemoryEvaluation
        database.repositories['evaluation'] = MemoryEvaluation()
    else:
        from sqlalchemy import MetaData
        from .adapters.postgres.evaluation import PostgresEvaluation, define_tables
        meta = MetaData()
        tables = define_tables(meta)
        with database.transaction(): meta.create_all(database.connection)
        database.repositories['evaluation'] = lambda c: PostgresEvaluation(c, tables)
    return EvaluationService(database, tasks)


def build_evolution(database, tasks, evaluation):
    from .evolution import EvolutionService
    build_evolution_records(database)
    return EvolutionService(database, tasks, evaluation)


def build_evolution_records(database):
    from .adapters.memory_ledger import MemoryDatabase
    if 'evolution' not in database.repositories:
        if isinstance(database, MemoryDatabase):
            from .adapters.memory_evolution import MemoryEvolution
            database.repositories['evolution'] = MemoryEvolution()
        else:
            from sqlalchemy import MetaData
            from .adapters.postgres.evolution import PostgresEvolution, define_tables
            meta = MetaData(); table = define_tables(meta)
            with database.transaction(): meta.create_all(database.connection)
            database.repositories['evolution'] = lambda c: PostgresEvolution(c, table)


def build_learning(database, tasks):
    from .learning.service import DomainLearning
    build_evolution_records(database)
    return DomainLearning(database, tasks)


def build_methods(database, research):
    from .methods.service import MethodService
    from .methods.models import AssetVersion
    from .methods.defaults import EXPERT_METHODS, published_experts
    from .adapters.memory_ledger import MemoryDatabase
    if 'methods' not in database.repositories:
        if isinstance(database, MemoryDatabase):
            from .adapters.memory_methods import MemoryMethods
            database.repositories['methods'] = MemoryMethods()
        else:
            from sqlalchemy import MetaData
            from .adapters.postgres.methods import PostgresMethods, define_tables
            meta=MetaData(); tables=define_tables(meta)
            with database.transaction(): meta.create_all(database.connection)
            database.repositories['methods'] = lambda c: PostgresMethods(c,tables)
    service=MethodService(database,research)
    candidates={v.id:v for v in published_experts()}
    for key,(kind,version,instruction) in EXPERT_METHODS.items():
        # Published registry state is authoritative; defaults only seed new roles.
        if service.query('head', 'method', key) is not None:
            continue
        value=AssetVersion(category='method',asset_id=key,version=version,
            description=candidates[key].description,
            body=dict(kind=kind,instructions=instruction,tools=sorted(candidates[key].tools)),
            author='product-baseline',
            evidence='R1–Stage 7 fixed method baseline; control tests, no claim of semantic improvement',sanitized=True)
        with database.transaction() as tx:
            tx.methods.add(value)
            if tx.methods.head('method',key) is None:
                tx.methods.activate('method',key,version,None,'product-baseline','Initialize the validated execution baseline')
    research.methods=service
    return service


def build_ce1_tasks(database, scopes, research, *, private_root, model_provider=None):
    """CE1 controlled tools with PostgreSQL accounting and logical private storage."""
    from agents.models.openai_provider import OpenAIProvider
    from openai import AsyncOpenAI
    from .controlled.ledger import ControlledLedger
    from .controlled.engine import ControlledEngine
    ledger = ControlledLedger(database)
    tasks = build_tasks(database, scopes, research)
    provider = model_provider or OpenAIProvider(openai_client=AsyncOpenAI(max_retries=0))
    tasks.engine = ControlledEngine(tasks, private_root, ledger, provider)
    tasks.stop_execution = lambda spec: ledger.cancel_task(spec.id, spec.version)
    return tasks


def build_controlled_services(database, scopes, *, private_root, model_provider, router=None, owner='local', coding=None):
    """All live research entrances share the controlled broker and accounting."""
    from .controlled.ledger import ControlledLedger
    from .controlled.engine import ControlledEngine
    research = build_research(database, scopes, owner=owner)
    tasks = build_tasks(database, scopes, research, router=router)
    engine = ControlledEngine(tasks, private_root, ControlledLedger(database), model_provider)
    tasks.coding = coding
    research.engine = tasks.engine = engine
    tasks.stop_execution = lambda spec: engine.ledger.cancel_task(spec.id, spec.version)
    projects = build_projects(database, research, tasks)
    return research, tasks, projects


def build_live_services(database, scopes, *, private_root, router=None, coding=None):
    from .controlled.provider import LiveModelProvider
    return build_controlled_services(database, scopes, private_root=private_root,
        model_provider=LiveModelProvider(), router=router, coding=coding)
