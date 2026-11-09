#!/usr/bin/env python3
"""Exact cross-image result hashes; independent of the timed benchmark."""
import json
from pathlib import Path
import numpy as np
import _factorpool_native as native
from factorpool.reporting.diagnostics import build
from benchmark_core import inputs, result_hash

out={}
for kind in ('dense','sparse'):
    f,p,m,dates,pdates=inputs(47,17,20261109,kind)
    if kind=='sparse':
        # Mask-dependent ranks, signed zero, invalid prices and nonfinite input.
        f[1][:7]=[-0.0,0.0,1.,1.,1.,None,float('inf')]
        p[3][:7]=[0.,None,12.,12.,float('inf'),-1.,11.]
        m[2][0]=float('nan')
    for groups in (1,2,7,30):
        for ddof in (0,1):
            analysis=dict(groups=groups,horizons=[1,2,5,20,252],decay_steps=3,icir_ddof=ddof,min_ic_pairs=3)
            if hasattr(native,'prepare_research'):
                prepared=native.prepare_research(f,p,m)
                primary=native.analyze(prepared,None,groups,3,ddof)
                report=build(prepared,None,None,dates,pdates,analysis,primary=primary)
            else:
                primary=native.analyze(f,p,groups,3,ddof,m)
                report=build(f,p,m,dates,pdates,analysis)
            out[f'{kind}-G{groups}-ddof{ddof}']=result_hash(dict(primary=primary,diagnostics=report))
# Direct legacy entrypoints must also preserve unusual but accepted inputs.
for name,f,p,m in [
    ('empty',[],[],[]),
    ('zero-columns',[[],[]],[[],[],[],[]],[[],[]]),
    ('overflow',[[1.,2.,3.]],[[1.]*3,[1e-300,1e-300,1e-300],[1e300,1e200,1e100]],[[1.]*3]),
    ('underflow',[[0.,0.,0.]],[[1.]*3,[1e300]*3,[1e-300]*3],[[1.]*3]),
]:
    result=dict(primary=native.analyze(f,p,3,3,1,m),
                portfolios=native.portfolio_report(f,p,m,3,1,252),
                decay=native.ic_decay(f,p,m,1,3,3))
    out[name]=result_hash(result)
import sys
Path('/bench/output/'+sys.argv[1]+'.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(dict(cases=len(out),combined_sha256=result_hash(out))))
