#!/usr/bin/env python3
"""Create only FactorPool-owned local configuration; never overwrite secrets."""
import argparse
from pathlib import Path
import secrets

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser()
p.add_argument('--state-dir',default='/home/quant/colorful-data/FactorPool-data')
p.add_argument('--bundle-root',default='/home/quant/colorful-data/quant_data/bundle')
p.add_argument('--release',default='dev')
a=p.parse_args()
state=Path(a.state_dir)
state.mkdir(parents=True,exist_ok=True,mode=0o700)
env=root/'.env'
if not env.exists():
    env.write_text(f'FACTORPOOL_DB_PASSWORD={secrets.token_hex(32)}\nFACTORPOOL_INTERNAL_TOKEN={secrets.token_hex(32)}\nFACTORPOOL_STATE_DIR={state}\nFACTORPOOL_BUNDLE_ROOT={a.bundle_root}\nFACTORPOOL_RELEASE={a.release}\nFACTORPOOL_PORT=8088\n')
    env.chmod(0o600)
elif not any(line.startswith('FACTORPOOL_INTERNAL_TOKEN=') for line in env.read_text().splitlines()):
    with env.open('a') as stream:
        stream.write(f'\nFACTORPOOL_INTERNAL_TOKEN={secrets.token_hex(32)}\n')
    env.chmod(0o600)
print('FactorPool configuration ready; existing configuration preserved.')
