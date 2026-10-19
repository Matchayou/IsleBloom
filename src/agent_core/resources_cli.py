"""Stage 3 CLI: persistent materials, clean scopes and independently recorded runs."""
import argparse
import asyncio
import json
import mimetypes
import os
import sys
from pathlib import Path

from .composition import build_resources
from .context.network import NetworkTools, SearchInput, FetchInput
from .adapters.web_sources import HttpSources
from .adapters.parsing import DocumentParser
from .resources.snapshots import ResourceRef


def ref_arg(value):
    values = value.split(':')
    if len(values) != 3:
        raise argparse.ArgumentTypeError('REF is resource_id:snapshot_id:parse_id')
    return ResourceRef(resource_id=values[0], snapshot_id=values[1], parse_id=values[2])


def encode(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    raise TypeError(type(value).__name__)


def output(value):
    print(json.dumps(value, default=encode, ensure_ascii=False, indent=2))


async def run(args):
    from .adapters.postgres import PostgresDatabase
    database = PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        parser = DocumentParser(tessdata=args.tessdata, languages=args.languages)
        scopes = build_resources(database, args.blobs, parser)
        scopes.resources.observe_parse = lambda p: print(json.dumps({'parse_id': p.id, 'status': p.status,
            'phase': p.phase, 'ocr_pages': p.ocr_pages}, ensure_ascii=False), file=sys.stderr)
        if args.command == 'create':
            scope = scopes.create(args.owner, web_urls=tuple(args.url),
                tools=('read_source', 'search_sources', 'fetch_source', 'write_note'))
            output(scope)
            return 0
        scope = scopes.current(args.scope, args.owner)
        if args.command == 'upload':
            path = Path(args.path)
            media = args.media_type or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            scope, ref = scopes.ingest(scope, content=path.read_bytes(), name=path.name, media_type=media)
            output({'scope': scope, 'ref': ref, 'parse': scopes.resources.describe(ref)[2]})
        elif args.command == 'select':
            output(scopes.select(scope, args.ref, web_urls=args.url))
        elif args.command == 'revoke':
            output(scopes.revoke(scope, args.ref))
        elif args.command == 'read':
            output(scopes.read(scope, args.ref))
        elif args.command in ('search', 'fetch'):
            network = NetworkTools(scopes, scope, HttpSources())
            result = await network.search(SearchInput(query=args.query)) if args.command == 'search' else await network.fetch(FetchInput(url=args.url))
            output(result)
        elif args.command == 'show':
            entries = scopes.resources.repository.ingestions(scope.id)
            versions = []
            for ref in scope.grants:
                _, snap, parsed = scopes.resources.describe(ref)
                try:
                    scopes.mount(scope, ref)
                    blocked = None
                except (PermissionError, ValueError):
                    blocked = 'Not authorized, not a body, or parsing incomplete'
                versions.append({'ref': ref, 'snapshot': snap, 'parse': parsed,
                                 'selected': ref in scope.allowed + scope.inherited, 'blocked': blocked})
            output({'scope': scope, 'ingestions': entries, 'versions': versions,
                    'mountable': [v['ref'] for v in versions if v['blocked'] is None]})
        elif args.command == 'run-show':
            from .execution.ledger import Ledger
            snapshot = scopes.repository.snapshot(args.run_id)
            if snapshot.scope_id != scope.id:
                raise PermissionError('Run belongs to another scope')
            if snapshot.scope_version != scope.version:
                raise PermissionError('Old run is not readable under changed scope')
            for mount in snapshot.mounts:
                scopes.check(scope, mount.ref)
            output({'context': snapshot, 'run': Ledger(database).get(args.run_id), 'events': Ledger(database).events(args.run_id)})
        return 0
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', default='local-user')
    parser.add_argument('--scope')
    parser.add_argument('--blobs', default='.stage3/blobs')
    parser.add_argument('--tessdata', default=os.environ.get('RESEARCH_TESSDATA'))
    parser.add_argument('--languages', default=os.environ.get('RESEARCH_OCR_LANGUAGES', 'eng'))
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create'); create.add_argument('--url', action='append', default=[])
    upload = sub.add_parser('upload'); upload.add_argument('path'); upload.add_argument('--media-type')
    select = sub.add_parser('select'); select.add_argument('ref', type=ref_arg, nargs='*'); select.add_argument('--url', action='append', default=[])
    for name in ('revoke', 'read'):
        command = sub.add_parser(name); command.add_argument('ref', type=ref_arg)
    sub.add_parser('show')
    search = sub.add_parser('search'); search.add_argument('query')
    fetch = sub.add_parser('fetch'); fetch.add_argument('url')
    show = sub.add_parser('run-show'); show.add_argument('run_id')
    args = parser.parse_args()
    if args.command != 'create' and not args.scope:
        parser.error('--scope required')
    try:
        return asyncio.run(run(args))
    except (PermissionError, ValueError, KeyError) as exc:
        print(f'{type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
