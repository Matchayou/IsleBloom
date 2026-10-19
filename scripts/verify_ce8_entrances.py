"""Rerun the four-entrance live scenario against CE8 code and a fresh database."""
import argparse
import asyncio
import getpass
import os
from verify_ce2_live import run

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
args = parser.parse_args()
args.allow_paid_model = True
args.model = 'deepseek-flash'
args.base_url = 'https://api.deepseek.com'
os.environ['OPENAI_API_KEY'] = getpass.getpass('DeepSeek credential (hidden): ')
asyncio.run(run(args))
