#!/usr/bin/env python3
"""Measure proportional portfolios separately from the complete IC report."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import time

import _factorpool_native as native
from benchmark_core import inputs, result_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--kind', choices=['dense', 'sparse'], default='dense')
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error('repeat must be positive')
    data = inputs(1000, 2000, 20261109, args.kind)
    samples = []
    for _ in range(args.repeat):
        started = time.perf_counter()
        prepared = native.prepare_research(data[0], data[1], data[2])
        checkpoint = time.perf_counter()
        result = {h: native.portfolio_report(prepared, None, None, 5, h, 252)
                  for h in (1, 5, 20)}
        finished = time.perf_counter()
        samples.append(dict(prepare_seconds=checkpoint-started,
                            portfolio_seconds=finished-checkpoint,
                            total_seconds=finished-started,
                            sha256=result_hash(result)))
        del result, prepared
    assert len({sample['sha256'] for sample in samples}) == 1
    report = dict(contract='returns-matrix-v3', cells=2000000, kind=args.kind,
                  horizons=[1, 5, 20], groups=5, samples=samples,
                  native_sha256=hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
                  scope='owned contiguous input, stable factor sorting and complete daily proportional portfolios; excludes IC, Indicator, database and export')
    for stage in ('prepare', 'portfolio', 'total'):
        report[f'median_{stage}_seconds'] = statistics.median(sample[f'{stage}_seconds'] for sample in samples)
    Path('/bench/output', args.label + '.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
