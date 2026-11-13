"""Measure raw/zlib contract codecs and actual Redis transfer on one pinned tile."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
import uuid

import numpy as np

from factorpool.data_client import DataClient
from factorpool.data_client.contract import FIELDS, axis_digest, decode_tile, encode_tile, tile_width


def sample(operation, iterations):
    durations = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        durations.append(time.perf_counter() - started)
    return dict(median=statistics.median(durations), minimum=min(durations), samples=durations)


def benchmark(metadata, block, values, field, redis, iterations, batch):
    """Keys are invocation-local, short-lived and deleted without global invalidation."""
    raw_sha = hashlib.sha256(np.ascontiguousarray(values, dtype="<f8").tobytes()).hexdigest()
    axes = axis_digest(metadata)
    reports = []
    prefix = f"fp:benchmark:codec:{uuid.uuid4().hex}"
    keys = [f"{prefix}:{index}" for index in range(batch)]
    try:
        for codec in ("raw", "zlib"):
            pinned = deepcopy(metadata)
            pinned["cache"]["codec"] = codec
            pinned["cache"]["tile_symbols"] = values.shape[1]
            payload = encode_tile(pinned, block, field, 0, values, axes=axes)
            if len(payload) * batch > 128 * 1024 * 1024:
                raise ValueError("Codec benchmark batch exceeds 128 MiB")

            def decode():
                decoded = decode_tile(payload, pinned, block, field, 0, axes=axes)
                if hashlib.sha256(decoded.tobytes()).hexdigest() != raw_sha:
                    raise ValueError("Codec changed numeric bytes")

            def write(encoded=payload):
                with redis.pipeline(transaction=False) as pipe:
                    for key in keys:
                        pipe.set(key, encoded, ex=120)
                    pipe.execute()

            def read():
                received = redis.mget(keys)
                if len(received) != batch or any(value != payload for value in received):
                    raise ValueError("Redis benchmark returned missing or changed bytes")

            def round_trip():
                encoded = encode_tile(pinned, block, field, 0, values, axes=axes)
                write(encoded)
                for received in redis.mget(keys):
                    if received is None:
                        raise ValueError("Redis evicted benchmark key during round trip")
                    decoded = decode_tile(received, pinned, block, field, 0, axes=axes)
                    if hashlib.sha256(decoded.tobytes()).hexdigest() != raw_sha:
                        raise ValueError("Redis codec round trip changed numeric bytes")

            decode()
            write()
            read()
            reports.append(dict(codec=codec, raw_bytes=values.nbytes, encoded_bytes=len(payload),
                encoded_to_raw=len(payload) / values.nbytes, batch=batch,
                encode_seconds=sample(lambda: encode_tile(pinned, block, field, 0, values, axes=axes), iterations),
                decode_seconds=sample(decode, iterations),
                redis_pipeline_set_seconds=sample(write, iterations),
                redis_mget_seconds=sample(read, iterations),
                encode_transfer_decode_seconds=sample(round_trip, iterations)))
    finally:
        redis.delete(*keys)
    return dict(snapshot_id=metadata["snapshot_id"], block_start=block["start_index"],
                field=field, shape=list(values.shape), dtype="<f8", numeric_sha256=raw_sha,
                finite_fraction=float(np.count_nonzero(np.isfinite(values)) / values.size),
                iterations=iterations, results=reports)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", help="Immutable snapshot id; omitted selects latest once")
    parser.add_argument("--date", required=True, help="Select the original date block containing/preceding this date")
    parser.add_argument("--field", choices=FIELDS, default="close")
    parser.add_argument("--tile-symbols", type=int, default=1024)
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100 or not 1 <= args.batch <= 128 or args.tile_symbols < 1:
        parser.error("Invalid iterations, batch or tile width")
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    redis = Redis.from_url(os.getenv("FACTORPOOL_REDIS_URL", "redis://redis:6379/0"),
                           socket_connect_timeout=.5, socket_timeout=2,
                           retry=Retry(NoBackoff(), 0), decode_responses=False)
    try:
        with DataClient() as source:
            metadata = source.snapshot(args.snapshot)
            position = int(np.searchsorted(metadata["dates"], args.date, side="right")) - 1
            if position < 0:
                raise ValueError("Requested sample date precedes snapshot")
            block = next(item for item in metadata["blocks"]
                         if item["start_index"] <= position < item["start_index"] + item["row_count"])
            target = deepcopy(metadata)
            target["cache"]["tile_symbols"] = args.tile_symbols
            width = min(len(metadata["symbols"]), tile_width(target, block))
            _, _, matrix = next(source.selected_blocks(metadata, block["start_index"],
                                block["start_index"] + block["row_count"], np.arange(width)))
            values = np.ascontiguousarray(matrix[FIELDS.index(args.field)], dtype="<f8")
        result = benchmark(metadata, block, values, args.field, redis, args.iterations, args.batch)
    finally:
        redis.close()
    serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n")
    print(serialized)


if __name__ == "__main__":
    main()
