#!/usr/bin/env python3
"""Controlled matrix research benchmark; no market/state mounts or network."""
import argparse
import cProfile
from datetime import date, timedelta
import gc
import hashlib
import json
import os
import platform
import resource
import time
from pathlib import Path

import numpy as np
import _factorpool_native as native
from factorpool.reporting.diagnostics import build


def inputs(t, n, seed, kind):
    rng = np.random.default_rng(seed)
    f = rng.normal(size=(t, n))
    returns = np.expm1(rng.normal(0, .01, size=(t + 201, n)))
    m = np.ones((t, n))
    if kind == 'sparse':
        f = np.round(f * 3) / 3  # ties and valid zero
        f[rng.random(f.shape) < .06] = np.nan
        returns[rng.random(returns.shape) < .015] = np.nan
        m[rng.random(m.shape) < .08] = 0
        f[::37] = 0  # constant sections
    # Production and benchmark both pass C-contiguous float64 arrays. Native
    # ownership/copying remains inside the measured prepare stage.
    dates = [(date(2015, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(returns))]
    return f, returns, m, dates[:t], dates


def result_hash(obj):
    h = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, separators=(',', ':'), allow_nan=False).iterencode(obj):
        h.update(chunk.encode())
    return h.hexdigest()


def execute(data, analysis):
    factors, returns, members, dates, return_dates = data
    checkpoint = time.perf_counter()
    prepared = native.prepare_research(factors, returns, members)
    primary = native.analyze(prepared, None, analysis['groups'], 3, 1)
    diagnostic = build(prepared, None, None, dates, return_dates, analysis, primary=primary)
    elapsed = time.perf_counter() - checkpoint
    if diagnostic['version'] != 3:
        raise ValueError('This benchmark requires the returns-matrix-v3 kernel; old open-price images have a different numerical contract')
    return dict(primary=primary, diagnostics=diagnostic), elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--case', choices=['small', 'medium', 'limit'], default='medium')
    parser.add_argument('--kind', choices=['dense', 'sparse'], default='sparse')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args()
    t, n = {'small': (128, 64), 'medium': (504, 512), 'limit': (1000, 2000)}[args.case]
    data = inputs(t, n, 20261109, args.kind)
    analysis = dict(groups=5, horizons=[1, 5, 20], decay_steps=10)
    timings, hashes = [], []
    for _ in range(args.repeat):
        gc.collect()
        result, elapsed = execute(data, analysis)
        timings.append(elapsed)
        # Full, exact result verification is outside compute timing.
        hashes.append(result_hash(result))
        del result
    assert len(set(hashes)) == 1, 'Nondeterministic full result'
    if args.profile:
        profiler = cProfile.Profile();profiler.enable()
        result, _ = execute(data, analysis)
        profiler.disable();del result
        profiler.dump_stats('/bench/output/' + args.label + '.prof')
    report = dict(label=args.label, case=args.case, kind=args.kind, dates=t, symbols=n,
                  cells=t*n, contract="returns-matrix-v3", seed=20261109, analysis=analysis, seconds=timings, median_seconds=float(np.median(timings)),
                  full_result_sha256=hashes[0], python=platform.python_version(), numpy=np.__version__,
                  native_file=native.__file__, native_sha256=hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
                  shared_library_sha256=hashlib.sha256(Path('/opt/factorpool/shared-build/native/abi/libfactortrader_c.so').read_bytes()).hexdigest(),
                  cpu_max=Path('/sys/fs/cgroup/cpu.max').read_text().strip(), affinity=sorted(os.sched_getaffinity(0)),
                  max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  scope='owned contiguous return-matrix prepare + primary + complete diagnostics including daily portfolios; synthetic input generation and canonical serialization outside timing; no I/O benchmark; not comparable to historical open-price-v2 timings',
                  build=json.loads(Path('/opt/factorpool/build-info.json').read_text()))
    Path('/bench/output/' + args.label + '.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
