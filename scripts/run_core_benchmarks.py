#!/usr/bin/env python3
"""Run serial, same-budget A/B; compare every complete result hash."""
import argparse
import json
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline', default='factorpool/backend:20261109-r2')
parser.add_argument('--candidate', required=True)
parser.add_argument('--repeat', type=int, default=3)
parser.add_argument('--cpu', default='0')
args = parser.parse_args()
output = root / 'build/performance'
output.mkdir(parents=True, exist_ok=True)
results = []
for case in ('small', 'medium', 'limit'):
    for kind in ('dense', 'sparse'):
        pair = {}
        for name, image in [('baseline', args.baseline), ('candidate', args.candidate)]:
            label = f'{name}-{case}-{kind}-final'
            env = dict(os.environ, FACTORPOOL_BENCH_IMAGE=image, FACTORPOOL_BENCH_CPU=args.cpu)
            command = ['docker', 'compose', '-f', 'compose.benchmark.yaml', 'run', '--rm',
                       'benchmark', '--label', label, '--case', case, '--kind', kind,
                       '--repeat', str(args.repeat)]
            with (output / (label + '.log')).open('w') as log:
                subprocess.run(command, cwd=root, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)
            pair[name] = json.loads((output / (label + '.json')).read_text())
            print(f"{label}: {pair[name]['median_seconds']:.6f}s; RSS {pair[name]['max_rss_kib']} KiB", flush=True)
        assert pair['baseline']['full_result_sha256'] == pair['candidate']['full_result_sha256'], 'Full result differs'
        assert pair['baseline']['affinity'] == pair['candidate']['affinity']
        assert pair['baseline']['cpu_max'] == pair['candidate']['cpu_max']
        pair['speedup'] = pair['baseline']['median_seconds'] / pair['candidate']['median_seconds']
        pair['baseline_image'] = args.baseline
        pair['candidate_image'] = args.candidate
        results.append(pair)
        (output / 'comparison.json').write_text(json.dumps(results, indent=2)+'\n')
        print(f"{case}/{kind}: exact output match, {pair['speedup']:.3f}x", flush=True)
