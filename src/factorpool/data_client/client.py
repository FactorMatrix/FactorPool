"""Bounded direct Redis bulk reads with authenticated gRPC fallback."""
import hashlib
import json
import math
import os
import time
from pathlib import Path

import numpy as np

from .contract import (FIELDS, SERVED_FIELDS, decode_tile, metadata_key, tile_key, tile_width,
                       unpack_metadata, validate_snapshot, json_bytes, axis_digest)


def _factor_group(value):
    if value is None:
        return None
    if (type(value) not in (int, float) or not -(2**31) <= value <= 2**31 - 1
            or not math.isfinite(value) or type(value) is float and not value.is_integer()):
        raise ValueError("Factor group must be a finite integral int32 or null")
    return int(value)


class DataClient:
    def __init__(self, *, stub=None, cache=None, enabled=None):
        from .generated import data_pb2 as pb
        self.pb = pb
        self.timeout = float(os.environ.get("FACTORPOOL_DATA_TIMEOUT_SECONDS", "30"))
        self.batch_bytes = int(os.environ.get("FACTORPOOL_CACHE_BATCH_MAX_BYTES", "33554432"))
        self.chunk_bytes = int(os.environ.get("FACTORPOOL_CACHE_CHUNK_MAX_BYTES", "4194304"))
        if not 8192 <= self.chunk_bytes <= 16 * 1024 * 1024 or not self.chunk_bytes <= self.batch_bytes <= 128 * 1024 * 1024:
            raise ValueError("Invalid data client chunk/batch bounds")
        self.enabled = (os.environ.get("FACTORPOOL_CACHE_ENABLED", "true").lower() == "true") if enabled is None else enabled
        self.metadata = (("x-factorpool-internal-token", os.environ.get("FACTORPOOL_INTERNAL_TOKEN", "")),)
        self.channel = None
        if stub is None:
            import grpc
            from .generated import data_pb2_grpc
            self.channel = grpc.insecure_channel(os.environ.get("FACTORPOOL_DATA_SERVICE_ADDR", "data-service:50051"),
                options=[("grpc.max_receive_message_length", 20 * 1024 * 1024),
                         ("grpc.max_send_message_length", 20 * 1024 * 1024)])
            stub = data_pb2_grpc.DataServiceStub(self.channel)
        self.stub = stub
        if cache is None and self.enabled:
            import redis
            cache = redis.Redis.from_url(os.environ.get("FACTORPOOL_REDIS_URL", "redis://redis:6379/0"),
                socket_connect_timeout=float(os.environ.get("FACTORPOOL_REDIS_CONNECT_TIMEOUT_SECONDS", ".2")),
                socket_timeout=float(os.environ.get("FACTORPOOL_REDIS_TIMEOUT_SECONDS", ".5")),
                retry_on_timeout=False, retry=redis.retry.Retry(redis.backoff.NoBackoff(), 0), max_connections=int(os.environ.get("FACTORPOOL_REDIS_POOL_SIZE", "8")),
                decode_responses=False)
        self.cache = cache if self.enabled else None
        self.stats = dict(redis_hits=0, redis_misses=0, redis_errors=0, corrupt_blocks=0, grpc_blocks=0, grpc_snapshots=0,
                          redis_seconds=0., grpc_seconds=0.)
        self._cache_failed_until = 0.

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.channel is not None:
            self.channel.close()
        if self.cache is not None and hasattr(self.cache, "close"):
            self.cache.close()

    def _redis(self, method, *args):
        if self.cache is None or time.monotonic() < self._cache_failed_until:
            return None
        started = time.perf_counter()
        try:
            return getattr(self.cache, method)(*args)
        except Exception:
            self.stats["redis_errors"] += 1
            self._cache_failed_until = time.monotonic() + 2
            return None
        finally:
            self.stats["redis_seconds"] += time.perf_counter() - started

    def snapshot(self, snapshot_id=None):
        if snapshot_id:
            cached = self._redis("get", metadata_key(snapshot_id))
            if cached is not None:
                try:
                    return unpack_metadata(cached, snapshot_id)
                except (ValueError, TypeError, KeyError):
                    self.stats["corrupt_blocks"] += 1
                    self._redis("delete", metadata_key(snapshot_id))
        started = time.perf_counter()
        reply = self.stub.Snapshot(self.pb.SnapshotRequest(snapshot_id=snapshot_id or ""),
                                   timeout=self.timeout, metadata=self.metadata)
        self.stats["grpc_snapshots"] += 1
        self.stats["grpc_seconds"] += time.perf_counter() - started
        return validate_snapshot(json.loads(reply.json), snapshot_id)

    def _tiles(self, metadata, requests):
        """One bounded batch; invalid cache entries become authoritative misses."""
        axes = axis_digest(metadata)
        keys = [tile_key(metadata, block, field, start) for block, field, start in requests]
        cached = self._redis("mget", keys) if keys else []
        if cached is None:
            cached = [None] * len(keys)
        if len(cached) != len(keys):
            raise ValueError("Invalid Redis batch response")
        arrays, missing = {}, []
        for request, key, payload in zip(requests, keys, cached):
            if payload is not None:
                try:
                    arrays[key] = decode_tile(payload, metadata, *request, axes=axes)
                    self.stats["redis_hits"] += 1
                    continue
                except (ValueError, TypeError, KeyError):
                    self.stats["corrupt_blocks"] += 1
            self.stats["redis_misses"] += 1
            missing.append((request, key))
        if missing:
            request = self.pb.MarketRequest(snapshot_id=metadata["snapshot_id"], cache_layout=json_bytes(metadata["cache"]), tiles=[
                self.pb.TileRequest(start_index=block["start_index"], field=field, symbol_start=start)
                for (block, field, start), _ in missing])
            expected = {key: arguments for arguments, key in missing}
            started = time.perf_counter()
            for reply in self.stub.MarketBlocks(request, timeout=self.timeout, metadata=self.metadata):
                arguments = expected.pop(reply.key, None)
                if arguments is None:
                    raise ValueError("Unexpected or duplicate data-service market block")
                arrays[reply.key] = decode_tile(reply.payload, metadata, *arguments, axes=axes)
                self.stats["grpc_blocks"] += 1
            self.stats["grpc_seconds"] += time.perf_counter() - started
            if expected:
                raise ValueError("Data service returned incomplete market blocks")
        return arrays

    def selected_blocks(self, metadata, initial, stop, columns, *, fields=None):
        fields = FIELDS if fields is None else tuple(fields)
        if not fields or len(set(fields)) != len(fields) or any(field not in SERVED_FIELDS for field in fields):
            raise ValueError("Invalid requested market fields")
        columns = np.asarray(columns, dtype=np.intp)
        if (columns.ndim != 1 or not len(columns) or np.any(columns < 0)
                or np.any(columns >= len(metadata["symbols"]))):
            raise ValueError("Invalid requested market columns")
        for block in metadata["blocks"]:
            if not block["start_index"] < stop or block["start_index"] + block["row_count"] <= initial:
                continue
            width = tile_width(metadata, block)
            starts = sorted(set((columns // width * width).tolist()))
            locations = {}
            for start in starts:
                positions = np.flatnonzero((columns >= start) & (columns < start + width))
                offsets = columns[positions]-start
                target = (slice(int(positions[0]), int(positions[-1])+1)
                          if len(positions) == 1 or np.all(np.diff(positions) == 1) else positions)
                origin = (slice(int(offsets[0]), int(offsets[-1])+1)
                          if len(offsets) == 1 or np.all(np.diff(offsets) == 1) else offsets)
                locations[start] = target, origin
            result = np.empty((len(fields), block["row_count"], len(columns)), dtype="<f8")
            batch, size = [], 0
            def consume(batch):
                arrays = self._tiles(metadata, batch)
                for descriptor, field, start in batch:
                    target, origin = locations[start]
                    cells = arrays[tile_key(metadata, descriptor, field, start)][:, origin]
                    if isinstance(target, slice):
                        result[fields.index(field), :, target] = cells
                    else:
                        result[fields.index(field), :, target] = cells.T
            for field in fields:
                for start in starts:
                    tile_bytes = block["row_count"] * min(width, len(metadata["symbols"])-start) * 8 + 4096
                    if batch and size + tile_bytes > self.batch_bytes:
                        consume(batch)
                        batch, size = [], 0
                    batch.append((block, field, start))
                    size += tile_bytes
            if batch:
                consume(batch)
            yield block["start_index"], block["row_count"], result

    def upload_values(self, task_id, attempt, lease_token, path, expected_count, *, cancelled=None):
        """Stream bounded columnar chunks, or explicit legacy JSONL test artifacts."""
        from ..result_matrix import AXES_MAGIC, iter_result_chunks
        lease = self.pb.Lease(task_id=str(task_id), attempt=attempt, lease_token=str(lease_token))
        digest, count = hashlib.sha256(), 0
        path = Path(path)
        with path.open('rb') as source:
            matrix = source.read(4) == AXES_MAGIC
        def check():
            if cancelled is not None and cancelled():
                raise InterruptedError("Task cancelled or execution lease lost")
        def chunks():
            nonlocal count
            if matrix:
                # The file SHA authenticates the exact bytes written by runner;
                # the receiving data service performs bounded decompression and
                # full numeric/axis validation before storing each chunk.
                for ordinal, rows, payload, sha in iter_result_chunks(path, max_bytes=self.chunk_bytes,validate_values=False):
                    check()
                    digest.update(bytes.fromhex(sha))
                    count += rows
                    yield self.pb.ValueChunk(lease=lease, ordinal=ordinal, payload=payload,
                                             row_count=rows, sha256=sha)
            else:
                rows, size, ordinal = [], 0, 0
                def chunk(rows, ordinal):
                    payload = self.pb.ValuesPayload(rows=rows).SerializeToString(deterministic=True)
                    sha = hashlib.sha256(payload).hexdigest()
                    digest.update(bytes.fromhex(sha))
                    return self.pb.ValueChunk(lease=lease, ordinal=ordinal, payload=payload,
                                             row_count=len(rows), sha256=sha)
                with path.open() as source:
                    for line in source:
                        check()
                        if len(line) > self.chunk_bytes:
                            raise ValueError("Factor value row exceeds chunk limit")
                        value = json.loads(line)
                        group = _factor_group(value.get("group"))
                        row = self.pb.FactorValue(date=value["date"], symbol=value["symbol"],
                            value=value["value"] if value.get("value") is not None else 0,
                            has_value=value.get("value") is not None, valid=value["valid"], ready=value["ready"],
                            reason=value.get("reason", ""),
                            forward_return=value["forward_return"] if value.get("forward_return") is not None else 0,
                            has_forward_return=value.get("forward_return") is not None,
                            group=group if group is not None else 0, has_group=group is not None)
                        item_size = row.ByteSize() + 8
                        if rows and (size + item_size > self.chunk_bytes or len(rows) >= 1000):
                            yield chunk(rows, ordinal)
                            ordinal += len(rows)
                            rows, size = [], 0
                        rows.append(row)
                        size += item_size
                        count += 1
                    if rows:
                        yield chunk(rows, ordinal)
                    elif count == 0:
                        yield chunk([], 0)
            if count != expected_count:
                raise ValueError("Incomplete factor values")
        def cancellable(method, request):
            if not hasattr(method, "future"):
                return method(request, timeout=max(self.timeout, 300), metadata=self.metadata)
            import grpc
            future = method.future(request, timeout=max(self.timeout, 300), metadata=self.metadata)
            try:
                while True:
                    check()
                    try:
                        return future.result(timeout=.2)
                    except grpc.FutureTimeoutError:
                        continue
            except BaseException:
                future.cancel()
                raise
        check()
        staged = cancellable(self.stub.StageValues, chunks())
        check()
        if count != expected_count or staged.count != expected_count or staged.sha256 != digest.hexdigest():
            raise ValueError("Incomplete data-service staged values")
        request = self.pb.ReceiptRequest(task_id=str(task_id), attempt=attempt, lease_token=str(lease_token),
            expected_count=expected_count, sha256=digest.hexdigest())
        receipt = cancellable(self.stub.FinalizeValues, request)
        if not receipt.valid or receipt.count != expected_count:
            raise ValueError("Data service did not finalize complete factor values")
        return dict(receipt=receipt.receipt, sha256=receipt.sha256, count=receipt.count)
