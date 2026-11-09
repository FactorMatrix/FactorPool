#!/usr/bin/env python3
"""Create only FactorPool-owned local configuration; never overwrite secrets."""
import argparse
from pathlib import Path
import secrets
import subprocess
import json
import hashlib

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser()
p.add_argument('--state-dir',default='/home/quant/dev/FactorPool-data')
p.add_argument('--market-root',default='/home/quant/quant_data/mmap')
p.add_argument('--release',default='20261109-r1')
a=p.parse_args()
state=Path(a.state_dir)
state.mkdir(parents=True,exist_ok=True,mode=0o700)
(state/'tasks').mkdir(exist_ok=True,mode=0o700)
env=root/'.env'
if not env.exists():
    env.write_text(f'FACTORPOOL_DB_PASSWORD={secrets.token_hex(32)}\nFACTORPOOL_STATE_DIR={state}\nFACTORPOOL_MARKET_ROOT={a.market_root}\nFACTORPOOL_RELEASE={a.release}\nFACTORPOOL_PORT=8088\n')
    env.chmod(0o600)
print('FactorPool configuration ready; existing configuration preserved.')
