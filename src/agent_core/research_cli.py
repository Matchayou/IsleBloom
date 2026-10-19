import argparse
import asyncio
import json
import os
from pathlib import Path
from .composition import build_resources, build_evaluation, build_evolution, build_live_services
from .app.api import serial, create_app
from .research.models import NodeCommand
from .research.planning_models import GeneratePlan, PlanDecision, RevisePlan
from .research.projection import project


def setup(args):
    from .adapters.postgres import PostgresDatabase
    database = PostgresDatabase(os.environ['DATABASE_URL'])
    scopes = build_resources(database,args.blobs)
    from .routing.config import load_router
    router = load_router(args.catalog) if args.catalog else None
    service, task_service, _ = build_live_services(database, scopes, private_root=args.private, router=router)
    evaluation = build_evaluation(database, task_service)
    task_service.evaluation = evaluation
    task_service.evolution = build_evolution(database,task_service,evaluation)
    service.reconcile()
    service.planning.recover()
    return database,service,task_service


async def run(args):
    database,service,task_service = setup(args)
    try:
        if args.action == 'create': value = service.create(args.goal,args.id)
        elif args.action == 'show': value = project(service,args.id)
        elif args.action == 'result':
            version,output = service.read_result(args.id)
            value = {'version':version,'output':output,'inputs':service.query('inputs',args.id),
                     'evidence':service.evidence.read(args.id)}
        elif args.action == 'command': value = await service.submit(NodeCommand.model_validate_json(Path(args.file).read_text()))
        elif args.action == 'plan-generate':
            proposal = service.planning.create(GeneratePlan.model_validate_json(Path(args.file).read_text()))
            value = await service.planning.generate(proposal.id)
        elif args.action == 'plan-show': value = service.planning.get(args.id)
        elif args.action == 'plan-revise':
            proposal = service.planning.revise(RevisePlan.model_validate_json(Path(args.file).read_text()))
            value = await service.planning.generate(proposal.id)
        elif args.action == 'plan-confirm':
            proposal = service.planning.confirm(PlanDecision(proposal_id=args.id, expected_revision=args.revision))
            value = await service.run_plan(proposal.id)
        elif args.action=='plan-recover':
            value=service.planning.recovery_proposal(PlanDecision(proposal_id=args.id,expected_revision=args.revision))
        elif args.action in ('plan-reject','plan-cancel'):
            value = await service.planning.decide(PlanDecision(proposal_id=args.id, expected_revision=args.revision), args.action.removeprefix('plan-'))
        elif args.action == 'upload':
            import mimetypes
            research = service.research(args.id)
            scope = service.scopes.current(research.scope_id,service.owner)
            scope,ref = service.scopes.ingest(scope,content=Path(args.file).read_bytes(),name=Path(args.file).name,
                media_type=mimetypes.guess_type(args.file)[0] or 'application/octet-stream')
            value = {'ref':ref,'parse':service.scopes.resources.describe(ref)[2]}
        elif args.action == 'serve':
            import uvicorn
            durable = None
            if args.durable:
                from .durable import DurableResearch
                durable = DurableResearch(task_service, os.environ['DATABASE_URL'])
                await durable.start()
            try:
                server = uvicorn.Server(uvicorn.Config(create_app(service,args.frontend,task_service,durable,task_service.evaluation,task_service.evolution),host='127.0.0.1',port=args.port,log_level='info'))
                await server.serve()
            finally:
                if durable: durable.close()
            return 0
        if args.action.startswith('plan-'): value = service.planning.view(value.id)
        print(json.dumps(serial(value),ensure_ascii=False,indent=2))
        if args.action == 'command' and value.reason: return 1
        return 0
    finally: database.close()


def main():
    parser = argparse.ArgumentParser(description='Stage 4 research commands and local UI')
    from .cli_options import add_service_options
    add_service_options(parser)
    subs = parser.add_subparsers(dest='action',required=True)
    p=subs.add_parser('create');p.add_argument('goal');p.add_argument('--id')
    for name in ('show','result'):
        p=subs.add_parser(name);p.add_argument('id')
    for name in ('command','plan-generate','plan-revise'):
        p=subs.add_parser(name);p.add_argument('file')
    p=subs.add_parser('plan-show');p.add_argument('id')
    for name in ('plan-confirm','plan-reject','plan-cancel','plan-recover'):
        p=subs.add_parser(name);p.add_argument('id');p.add_argument('--revision',type=int,required=True)
    p=subs.add_parser('upload');p.add_argument('id');p.add_argument('file')
    p=subs.add_parser('serve');p.add_argument('--port',type=int,default=8744);p.add_argument('--frontend',default='frontend/dist')
    p.add_argument('--durable',action='store_true',help='Run the Stage 6 DBOS background worker and job API')
    args=parser.parse_args()
    try: raise SystemExit(asyncio.run(run(args)))
    except (KeyError,ValueError,PermissionError) as exc:
        print(type(exc).__name__ + ': ' + str(exc));raise SystemExit(1)


if __name__ == '__main__': main()
