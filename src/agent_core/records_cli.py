"""Read-only historical conversation records; execution uses agent-research/tasks."""
import argparse
import json
import os
from .execution.ports import Conflict
from .execution.ledger import Ledger


def show(ledger, conversation_id, run_id=None):
    records = [ledger.get(run_id)] if run_id else ledger.runs(conversation_id)
    records = [r for r in records if r is not None]
    if any(r.conversation_id != conversation_id for r in records):
        raise Conflict('Run belongs to a different conversation')
    print(json.dumps({'conversation': ledger.history(conversation_id).model_dump(mode='json'),
                      'runs': [r.model_dump(mode='json') for r in records],
                      'events': [e.model_dump(mode='json') for r in records for e in ledger.events(r.run_id)]},
                     ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--conversation', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('show').add_argument('--run-id')
    args = parser.parse_args()
    from .adapters.postgres import PostgresDatabase
    database = PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        show(Ledger(database), args.conversation, args.run_id)
    finally:
        database.close()


if __name__ == '__main__':
    main()
