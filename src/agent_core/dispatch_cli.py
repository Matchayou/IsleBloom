"""Read-only historical dispatch groups; execution uses controlled task services."""
import argparse
import json
import os
from .execution.ledger import Ledger
from .orchestration import collect
from .orchestration.router import DIMENSIONS


def display(results):
    print(results.model_dump_json(), flush=True)
    for item in results.items:
        label = DIMENSIONS.get(item.key, item.key)
        if item.status == 'completed':
            print(f'{label} [{item.run_id}] 已保存：{item.result.output}', flush=True)
        else:
            print(f'{label} [{item.run_id}] 缺失：{item.status} / {item.missing_reason}', flush=True)
    return 0 if results.complete else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('show').add_argument('group_id')
    args = parser.parse_args()
    from .adapters.postgres import PostgresDatabase
    database = PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        ledger = Ledger(database)
        result = collect(ledger, args.group_id)
        group = ledger.get_dispatch(args.group_id)
        print(json.dumps({'dispatch_group': group.model_dump(mode='json'),
            'runs': [record.model_dump(mode='json') for item in group.plan.items
                     if (record := ledger.get(item.run_id)) is not None],
            'events': [event.model_dump(mode='json') for item in group.plan.items
                       for event in ledger.events(item.run_id)]}, ensure_ascii=False), flush=True)
        code = display(result)
    finally:
        database.close()
    raise SystemExit(code)


if __name__ == '__main__':
    main()
