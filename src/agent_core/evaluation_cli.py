"""Local operator commands for reviewed feedback and offline evaluation."""
import argparse
import json
from pathlib import Path
from .research_cli import setup
from .app.api import serial
from .execution.ports import Conflict


def main():
    parser = argparse.ArgumentParser(description='Stage 7 feedback and offline evaluation')
    from .cli_options import add_service_options
    add_service_options(parser)
    subs = parser.add_subparsers(dest='action',required=True)
    for name in ('submit','adjudicate','freeze','experiment','trial'):
        command = subs.add_parser(name); command.add_argument('file',help='JSON command file')
    for name in ('feedback','case','compare','list-feedback'):
        command = subs.add_parser(name); command.add_argument('id')
    args = parser.parse_args()
    db = None
    try:
        db,_,tasks = setup(args)
        evaluation = tasks.evaluation
        if args.action in ('submit','adjudicate','freeze','experiment','trial'):
            data = json.loads(Path(args.file).read_text())
            action = {'submit':evaluation.submit,'adjudicate':lambda **data:evaluation.adjudicate(data.pop('feedback_id'),**data),
                'freeze':lambda **data:evaluation.freeze(data.pop('feedback_id'),**data),
                'experiment':evaluation.create_experiment,'trial':evaluation.record_trial}[args.action]
            value = action(**data)
        elif args.action == 'feedback': value = evaluation.feedback(args.id)
        elif args.action == 'case': value = evaluation.case(args.id)
        elif args.action == 'compare': value = evaluation.compare(args.id)
        else: value = evaluation.list_feedback(args.id)
        print(json.dumps(serial(value),ensure_ascii=False,indent=2))
    except (KeyError,ValueError,PermissionError,Conflict) as exc:
        parser.exit(1,f'{type(exc).__name__}: {exc}\n')
    finally:
        if db: db.close()


if __name__ == '__main__': main()
