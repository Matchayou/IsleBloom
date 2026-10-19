"""Manual method and workflow asset commands."""
import argparse
import asyncio
import json
from pathlib import Path
from .research_cli import setup
from .app.api import serial


async def run(args):
    database,research,tasks=setup(args)
    try:
        methods=tasks.methods
        if args.action=='list': result=methods.catalog()
        elif args.action=='instance': result=methods.instance(args.id)
        elif args.action=='propose': result=await methods.propose(args.id)
        else:
            payload=json.loads(Path(args.file).read_text())
            if args.action=='save': result=methods.save(payload)
            elif args.action=='publish': result=methods.publish(payload['category'],payload['asset_id'],
                payload['version'],payload['expected'],payload['actor'],payload['reason'])
            else: result=await methods.instantiate(**payload)
        print(json.dumps(serial(result),ensure_ascii=False,indent=2))
    finally: database.close()


def main():
    parser=argparse.ArgumentParser(description='Stage 8 method, routing policy and research workflow versions')
    from .cli_options import add_service_options
    add_service_options(parser)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('list')
    for action in ('save','publish','compile'): sub.add_parser(action).add_argument('file')
    for action in ('instance','propose'): sub.add_parser(action).add_argument('id')
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__': main()
