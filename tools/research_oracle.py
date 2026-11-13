#!/usr/bin/env python3
"""Exact same-contract cross-image hashes, independent of timed runs."""
import json
from pathlib import Path
import numpy as np
import _factorpool_native as native
from factorpool.reporting.diagnostics import build
from benchmark_core import inputs, result_hash

out = {}
for kind in ('dense', 'sparse'):
    factors, returns, members, dates, return_dates = inputs(47, 17, 20261109, kind)
    if kind == 'sparse':
        # Mask-dependent ranks, signed zero, total loss and unavailable returns.
        factors[1, :7] = [-0., 0., 1., 1., 1., np.nan, np.inf]
        returns[3, :7] = [0., np.nan, .12, .12, np.inf, -1., -2.]
        members[2, 0] = np.nan
    for groups in (1, 2, 7, 30):
        for ddof in (0, 1):
            analysis = dict(groups=groups, horizons=[1, 2, 5, 20, 252], decay_steps=3,
                            icir_ddof=ddof, min_ic_pairs=3)
            prepared = native.prepare_research(factors, returns, members)
            primary = native.analyze(prepared, None, groups, 3, ddof)
            report = build(prepared, None, None, dates, return_dates, analysis, primary=primary)
            assert report['version'] == 3, 'Use a returns-matrix-v3 image'
            out[f'{kind}-G{groups}-ddof{ddof}'] = result_hash(dict(primary=primary, diagnostics=report))
for name, factors, returns, members in [
    ('empty', [], [], []),
    ('zero-columns', [[], []], [[], [], [], []], [[], []]),
    ('overflow', [[1., 2., 3.]], [[0.]*3, [1e308]*3, [1e308]*3], [[1.]*3]),
    ('underflow', [[0., 0., 0.]], [[0.]*3, [-1.+1e-15]*3, [-1.+1e-15]*3], [[1.]*3]),
]:
    result = dict(primary=native.analyze(factors, returns, 3, 3, 1, members, 2),
                  portfolios=native.portfolio_report(factors, returns, members, 3, 2, 252),
                  decay=native.ic_decay(factors, returns, members, 2, 3, 3))
    out[name] = result_hash(result)
import sys
Path('/bench/output/'+sys.argv[1]+'.json').write_text(json.dumps(out, indent=2)+'\n')
print(json.dumps(dict(contract='returns-matrix-v3', cases=len(out), combined_sha256=result_hash(out))))
