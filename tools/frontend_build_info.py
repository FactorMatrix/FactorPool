#!/usr/bin/env python3
"""Fingerprint UI artifacts independently from the backend release."""
import argparse
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
paths = [p for p in (root / 'frontend/src').rglob('*') if p.is_file()]
paths += [root / p for p in ('frontend/index.html', 'frontend/package.json',
          'frontend/package-lock.json', 'frontend/vite.config.js',
          'tools/docker/Dockerfile.frontend', 'tools/docker/nginx.conf',
          'tools/frontend_build_info.py')]
digest = hashlib.sha256()
for path in sorted(paths):
    digest.update(str(path.relative_to(root)).encode() + b'\0' + path.read_bytes() + b'\0')
info = {'ui_contract': 'brain-workbench-1', 'api_report_version': 3,
        'frontend_source_sha256': digest.hexdigest(),
        'backend_build': json.loads((root / 'tools/docker/build-info.json').read_text())['factorpool_source_sha256']}
target = root / 'tools/docker/frontend-build-info.json'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
if args.check:
    if not target.exists() or json.loads(target.read_text()) != info:
        raise SystemExit('UI build manifest is stale; run tools/frontend_build_info.py')
else:
    target.write_text(json.dumps(info, indent=2) + '\n')
print('Frontend source SHA256: ' + digest.hexdigest())
