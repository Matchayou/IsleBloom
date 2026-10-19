"""Reviewable Stage 10 candidate commands using the product database."""
import argparse
import asyncio
import json
from pathlib import Path
from .research_cli import setup
from .app.api import serial


async def run(args):
    database,_,tasks=setup(args)
    try:
        service=tasks.evolution
        if args.action=='list': result=service.list_candidates(args.research_id)
        elif args.action=='compare': result=service.compare(args.id)
        elif args.action=='candidate': result=service.candidate(args.id)
        else:
            data=json.loads(Path(args.file).read_text())
            if args.action=='propose': result=await service.propose(**data)
            elif args.action=='run': result=await service.run_preview(**data)
            elif args.action=='assess': result=service.assess(**data)
            elif args.action=='publish': result=service.publish(**data)
            else: result=service.rollback(**data)
        print(json.dumps(serial(result),ensure_ascii=False,indent=2))
    finally: database.close()


def main():
    parser=argparse.ArgumentParser(description='Stage 10 isolated method candidate evaluation')
    from .cli_options import add_service_options
    add_service_options(parser)
    sub=parser.add_subparsers(dest='action',required=True)
    for action in ('propose','run','assess','publish','rollback'):
        sub.add_parser(action).add_argument('file')
    for action in ('candidate','compare'):
        sub.add_parser(action).add_argument('id')
    sub.add_parser('list').add_argument('research_id')
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__': main()
