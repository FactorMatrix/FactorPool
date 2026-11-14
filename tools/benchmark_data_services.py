"""Matched read/research benchmark; reads an existing immutable snapshot only."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import resource
import statistics
import tempfile
import time


def digest_payload(payload):
    digest = hashlib.sha256()
    for key in ('symbols', 'dates', 'snapshot_id'):
        if key in payload:
            digest.update(json.dumps(payload[key], sort_keys=True).encode())
    digest.update(payload['returns'].tobytes())
    digest.update(payload['quotes']['values'].tobytes())
    digest.update(json.dumps(payload['frames'], sort_keys=True).encode())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Pinned data request JSON')
    parser.add_argument('--loader', default='factorpool.market_loader')
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--evict-before-each', action='store_true', help='Delete only this snapshot cache in an isolated benchmark Redis')
    parser.add_argument('--only-read', action='store_true')
    args = parser.parse_args()
    data = json.loads(Path(args.config).read_text())
    load = importlib.import_module(args.loader).load
    request = dict(name='service-benchmark',
                   source_code='from factorpool import PythonIndicator\n'
                   'class Factor(PythonIndicator):\n'
                   '    def update(self, input):\n'
                   '        x = self.ts.mean(input.volume, 20)\n'
                   '        self.value = self.cs.rank_unit(-x)\n'
                   '        return self.ts.is_ready(x)\n'
                   'if __name__ == "__main__":\n    Factor.run()\n',
                   workspace_config=dict(
                       base=dict(start_date=data['factor_start'], end_date=data['factor_end'],
                                 frequency=data.get('frequency', '1d')),
                       data={key: value for key, value in data.items()
                             if key not in ('factor_start', 'factor_end', 'frequency')},
                       analysis=dict(groups=5, horizons=[1, 5, 20], decay_steps=3,
                                     entry='signal_close', exit='next_close')))
    from factorpool.runner import run
    reads, totals, hashes, cache_stats = [], [], [], []
    def evict():
        if args.evict_before_each:
            import redis
            client = redis.Redis.from_url(os.environ['FACTORPOOL_REDIS_URL'])
            for pattern in (f"fp:*:{data['snapshot_id']}:*", f"fp:*:metadata:*:{data['snapshot_id']}"):
                batch = []
                for key in client.scan_iter(match=pattern, count=256):
                    batch.append(key)
                    if len(batch) >= 256:
                        client.delete(*batch)
                        batch.clear()
                if batch:
                    client.delete(*batch)
            client.close()
    for _ in range(args.runs):
        evict()
        started = time.perf_counter()
        if args.loader == 'factorpool.market_loader':
            from factorpool.data_client import DataClient
            with DataClient() as client:
                payload = load(data, 20, 60, matrix_only=True, source=client)
                cache_stats.append(dict(client.stats))
        else:
            payload = load(data, 20, 60, matrix_only=True)
        reads.append(time.perf_counter()-started)
        matrix_hash = digest_payload(payload)
        del payload
        if args.only_read:
            hashes.append(dict(matrix=matrix_hash))
            continue
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'request.json').write_text(json.dumps(request))
            evict()
            started = time.perf_counter()
            run(directory)
            totals.append(time.perf_counter()-started)
            result = json.loads((path/'result.json').read_text())
            numerical = {key: value for key, value in result.items() if key != 'metadata'}
            hashes.append(dict(matrix=matrix_hash,
                               values=hashlib.sha256((path/'values.jsonl').read_bytes()).hexdigest(),
                               report=hashlib.sha256(json.dumps(numerical, sort_keys=True).encode()).hexdigest()))
    assert all(value == hashes[0] for value in hashes), hashes
    print(json.dumps(dict(read_seconds=reads, total_seconds=totals,
                          median_read=statistics.median(reads), median_total=statistics.median(totals) if totals else None,
                          hashes=hashes[0], loader=args.loader, cache_enabled=os.environ.get('FACTORPOOL_CACHE_ENABLED'),
                          cache_codec=os.environ.get('FACTORPOOL_CACHE_CODEC', 'raw'),
                          peak_process_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                          cache_stats=cache_stats,
                          snapshot_id=data['snapshot_id'], symbol_count=len(data['universe'])), sort_keys=True))


if __name__ == '__main__':
    main()
