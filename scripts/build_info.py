#!/usr/bin/env python3
"""Pin source and environment identities without reading runtime configuration."""
from pathlib import Path
import subprocess
import hashlib
import json
root=Path(__file__).resolve().parents[1]
paths=[]
for name in ('src','research-core-cpp','backend/src','frontend/src','examples','tests'):
    paths.extend(p for p in (root/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
for name in ('backend/pom.xml','frontend/package.json','frontend/package-lock.json','frontend/vite.config.js','compose.yaml','docker/Dockerfile','docker/Dockerfile.frontend','docker/nginx.conf','docs/operators.json'):
    paths.append(root/name)
digest=hashlib.sha256()
for path in sorted(paths):
    digest.update(str(path.relative_to(root)).encode()+b'\0'+path.read_bytes()+b'\0')
commit=subprocess.check_output(['git','-C',str(root/'shared/factortrader'),'rev-parse','HEAD'],text=True).strip()
environment_id=subprocess.check_output(['docker','image','inspect','factortrader/platform:20261105-factortrader-48804c66-r1','--format','{{.Id}}'],text=True).strip()
info=dict(environment_image_id=environment_id,factortrader_commit=commit,lean_reference='f49bb9623a3e8782bf22bbd2cb51f691058cfdc3',environment_image='factortrader/platform:20261105-factortrader-48804c66-r1',
          python='3.11.16',operator_contract='ft-pool-1',research_kernel='matrix-analysis-v1',factorpool_source_sha256=digest.hexdigest())
(root/'docker/build-info.json').write_text(json.dumps(info,indent=2)+'\n')
print('Pinned FactorTrader '+commit[:8]+'; FactorPool source '+digest.hexdigest())
