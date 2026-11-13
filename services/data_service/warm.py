"""Explicit snapshot/range/field cache warmup; never imports or updates quotes."""
import argparse
from datetime import date
import json

from factorpool.data_client import DataClient
from factorpool.data_client.contract import FIELDS, tile_width


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot-id', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--fields', nargs='+', required=True, choices=FIELDS)
    parser.add_argument('--symbols', nargs='+')
    args = parser.parse_args()
    first, last = date.fromisoformat(args.start).isoformat(), date.fromisoformat(args.end).isoformat()
    if first > last:
        parser.error('start must not follow end')
    count = 0
    with DataClient() as client:
        snapshot = client.snapshot(args.snapshot_id)
        lookup = {symbol: n for n, symbol in enumerate(snapshot['symbols'])}
        columns = sorted(lookup[symbol] for symbol in args.symbols) if args.symbols else list(range(len(lookup)))
        for block in snapshot['blocks']:
            if snapshot['dates'][block['start_index']] > last or snapshot['dates'][block['start_index']+block['row_count']-1] < first:
                continue
            width = tile_width(snapshot, block)
            starts = sorted({column // width * width for column in columns})
            batch, size = [], 0
            for field in sorted(set(args.fields)):
                for start in starts:
                    amount = block['row_count'] * min(width, len(lookup)-start) * 8 + 4096
                    if batch and size + amount > client.batch_bytes:
                        client._tiles(snapshot, batch)
                        count += len(batch)
                        batch, size = [], 0
                    batch.append((block, field, start))
                    size += amount
            if batch:
                client._tiles(snapshot, batch)
                count += len(batch)
        print(json.dumps(dict(snapshot_id=args.snapshot_id, tiles=count, stats=client.stats)))


if __name__ == '__main__':
    main()
