#!/usr/bin/env python3
"""Pin source and environment identities without reading runtime configuration."""
from pathlib import Path
import subprocess
import hashlib
import json
root=Path(__file__).resolve().parents[1]
paths=[]
for name in ('src','backend/src','frontend/src','tests','tools','services','shared/contracts'):
    paths.extend(p for p in (root/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name not in ('build-info.json', 'frontend-build-info.json'))
for name in ('pyproject.toml','backend/pom.xml','frontend/package.json','frontend/package-lock.json','frontend/vite.config.js','compose.yaml','compose.test.yaml','compose.benchmark.yaml','compose.services-test.yaml'):
    paths.append(root/name)
digest=hashlib.sha256()
for path in sorted(paths):
    digest.update(str(path.relative_to(root)).encode()+b'\0'+path.read_bytes()+b'\0')
commit=subprocess.check_output(['git','-C',str(root/'shared/factortrader'),'rev-parse','HEAD'],text=True).strip()
environment_id=subprocess.check_output(['docker','image','inspect','factortrader/platform:20261105-factortrader-48804c66-r1','--format','{{.Id}}'],text=True).strip()
info=dict(environment_image_id=environment_id,factortrader_commit=commit,lean_reference='f49bb9623a3e8782bf22bbd2cb51f691058cfdc3',environment_image='factortrader/platform:20261105-factortrader-48804c66-r1',
          python='3.11.16',operator_contract='pool-indicator-2',public_operator_api='pool-single-entry-v1',shared_primitive_contract='ft-pool-1',research_kernel='return-matrix-v3',factorpool_source_sha256=digest.hexdigest())
(root/'tools/docker/build-info.json').write_text(json.dumps(info,indent=2)+'\n')
print('Pinned FactorTrader '+commit[:8]+'; FactorPool source '+digest.hexdigest())
