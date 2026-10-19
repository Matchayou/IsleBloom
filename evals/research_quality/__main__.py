"""Run from the repository root: python -m evals.research_quality --help."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from .run import plan, run_batch
from .report import report_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('plan', help='Freeze cases, source hashes and trial order; no model calls')
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--cases', nargs='+', default=['Q01', 'Q02'])
    create.add_argument('--repeats', type=int, default=1)
    create.add_argument('--model', default=os.environ.get('OPENAI_MODEL'))
    create.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1'))
    create.add_argument('--seed', type=int, default=17)
    create.add_argument('--candidate-instructions', type=Path, help='Replace only synthesize method instructions in isolated candidate databases')
    execute = commands.add_parser('run', help='Make live calls against the frozen batch')
    execute.add_argument('batch', type=Path)
    execute.add_argument('--live', action='store_true', required=True)
    report = commands.add_parser('report', help='Grade saved artifacts, prepare blind review packets and summarize')
    report.add_argument('batch', type=Path)
    args = parser.parse_args()
    if args.command == 'plan':
        if not args.model:
            parser.error('--model or OPENAI_MODEL is required')
        result = plan(args.output, args.cases, args.repeats, args.model, args.base_url, args.seed, args.candidate_instructions)
        print(json.dumps(dict(batch=str(args.output), trials=len(result['trials']), protocol_sha256=result['protocol_sha256'])))
    elif args.command == 'run':
        admin = os.environ.get('EVAL_DATABASE_ADMIN_URL')
        if not admin:
            parser.error('EVAL_DATABASE_ADMIN_URL is required; DATABASE_URL is never used')
        try:
            states = asyncio.run(run_batch(args.batch, admin))
        finally:
            report_batch(args.batch)
        if any(state['execution_status'] != 'completed' for state in states):
            raise SystemExit(1)
    else:
        print(json.dumps(report_batch(args.batch), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
