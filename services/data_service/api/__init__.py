"""Authenticated internal gRPC endpoints and bounded original-matrix tiles."""
from collections import OrderedDict
import hashlib
import hmac
import json
import os
import threading
import time
import uuid

import grpc

from factorpool.data_client.contract import (FIELDS, market_field, decode_tile, encode_tile, json_bytes, metadata_key,
    pack_metadata, tile_key, tile_width, pack_value_page, unpack_value_page, axis_digest, field_dtype, FIELD_BLOCK_FORMAT)
from factorpool.data_client.generated import data_pb2 as pb, data_pb2_grpc
from ..postgres import transaction
from ..postgres.market import PostgresMarketSource, MarketSnapshotNotFound
from ..postgres.values import ValueStore
from ..prepared_matrices import PreparedMatrices
from ..redis_cache import Cache


def endpoint(method):
    def call(self, request, context):
        self.authenticate(context)
        try:
            return method(self, request, context)
        except MarketSnapshotNotFound as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except (ValueError, TypeError, KeyError) as exc:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
        except Exception:
            context.abort(grpc.StatusCode.UNAVAILABLE, "Data service storage operation failed")
    return call


class DataService(data_pb2_grpc.DataServiceServicer):
    def __init__(self, *, source=None, cache=None, values=None, prepared=None):
        self.source = source or PostgresMarketSource()
        self.cache = cache or Cache()
        self.values = values or ValueStore()
        self.prepared = prepared or PreparedMatrices()
        self.token = os.environ.get("FACTORPOOL_INTERNAL_TOKEN", "")
        self.max_chunk = int(os.environ.get("FACTORPOOL_CACHE_CHUNK_MAX_BYTES", "4194304"))
        self.max_batch = int(os.environ.get("FACTORPOOL_CACHE_BATCH_MAX_BYTES", "33554432"))
        if not 8192 <= self.max_chunk <= 16 * 1024 * 1024 or not self.max_chunk <= self.max_batch <= 128 * 1024 * 1024:
            raise ValueError("Invalid data service chunk/batch bounds")
        self._metadata = OrderedDict()
        self._metadata_lock = threading.Lock()
        self._locks = [threading.Lock() for _ in range(64)]
        self.stats = dict(pg_block_reads=0, decoded_bytes=0, cached_tiles=0, cache_hits=0, requests=0,
                          prepared_tiles=0, cache_encode_seconds=0.)

    def authenticate(self, context):
        provided = dict(context.invocation_metadata()).get("x-factorpool-internal-token", "")
        if not self.token or not hmac.compare_digest(self.token, provided):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Internal service token required")

    def snapshot(self, identity):
        # Published snapshots are immutable. This small, bounded index is not a
        # matrix cache and survives Redis degradation without repeated SQL.
        if identity:
            with self._metadata_lock:
                found = self._metadata.get(identity)
                if found is not None:
                    self._metadata.move_to_end(identity)
                    self.cache.store([(metadata_key(found["snapshot_id"], found["cache"]), pack_metadata(found))])
                    return found
        found = self.source.snapshot(identity or None)
        found["_axes_digest"] = axis_digest(found)
        self.cache.store([(metadata_key(found["snapshot_id"], found["cache"]), pack_metadata(found))])
        with self._metadata_lock:
            self._metadata[found["snapshot_id"]] = found
            while len(self._metadata) > 128:
                self._metadata.popitem(last=False)
        return found

    @endpoint
    def Snapshot(self, request, context):
        return pb.JsonReply(json=json_bytes(self.snapshot(request.snapshot_id)))

    def _cached_tile(self, snapshot, block, field, start):
        key = tile_key(snapshot, block, field, start)
        payload = self.cache.call("get", key)
        if payload is not None:
            try:
                decode_tile(payload, snapshot, block, field, start, axes=snapshot.get("_axes_digest"))
                self.stats["cache_hits"] += 1
                return payload
            except (ValueError, TypeError, KeyError):
                self.cache.call("delete", key)
        return None

    def _fill(self, snapshot, block, requested):
        lock_key = f"fp:fill:{snapshot['snapshot_id']}:{block['start_index']}"
        local = self._locks[int(hashlib.sha256(lock_key.encode()).hexdigest()[:8], 16) % len(self._locks)]
        locked = local.acquire(timeout=.5)
        token, acquired = uuid.uuid4().hex, False
        try:
            found = {key: self._cached_tile(snapshot, block, field, start) for key, field, start in requested}
            if all(value is not None for value in found.values()):
                return found
            acquired = bool(self.cache.call("set", lock_key, token, nx=True, ex=10))
            if not acquired and self.cache.enabled and not self.cache.failed_until > time.monotonic():
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    time.sleep(.02)
                    found = {key: self._cached_tile(snapshot, block, field, start) for key, field, start in requested}
                    if all(value is not None for value in found.values()):
                        return found
                    # Redis failure exits the bounded wait immediately.
                    if self.cache.failed_until > time.monotonic():
                        break
            result = {key: value for key, value in found.items() if value is not None}
            missing = [(key, field, start) for key, field, start in requested if key not in result]
            columns = self.prepared.tiles(snapshot, block, [(field, start) for _, field, start in missing])
            prepared = []
            for key, field, start in missing:
                values = columns.get((field, start))
                if values is not None:
                    encoded = encode_tile(snapshot, block, field, start, values, axes=snapshot.get("_axes_digest"))
                    result[key] = encoded
                    prepared.append((key, encoded))
                    self.stats["prepared_tiles"] += 1
            self.cache.store(prepared)
            missing = [(key, field, start) for key, field, start in missing if key not in result]
            if not missing:
                return result
            fields = tuple(dict.fromkeys(field for _, field, _ in missing))
            if hasattr(self.source, "read_fields"):
                projected = self.source.read_fields(snapshot, block, fields)
            else:
                # In-memory fixture/legacy adapters can expose the old full block.
                original = self.source.original_block(snapshot, block)
                projected = {field: market_field(original, field) for field in fields}
            self.stats["pg_block_reads"] += 1
            self.stats["decoded_bytes"] += sum(value.nbytes for value in projected.values())
            width = tile_width(snapshot, block)
            def column(field, start):
                return projected[field][:, start:start+width]
            def entries():
                for key, field, start in missing:
                    started = time.perf_counter()
                    encoded = encode_tile(snapshot, block, field, start, column(field, start), axes=snapshot.get("_axes_digest"))
                    self.stats["cache_encode_seconds"] += time.perf_counter() - started
                    result[key] = encoded
                    self.stats["cached_tiles"] += 1
                    yield key, encoded
            # Fill only this explicitly requested field/date/symbol region.
            # Pipeline buffers are bounded independently of its decoded size.
            self.cache.store(entries())
            # Disabled Redis must still consume the generator to return blocks.
            if any(key not in result for key, _, _ in missing):
                for key, field, start in missing:
                    result[key] = encode_tile(snapshot, block, field, start,
                        column(field, start), axes=snapshot.get("_axes_digest"))
            return result
        finally:
            if acquired:
                self.cache.call("eval", "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end", 1, lock_key, token)
            if locked:
                local.release()

    def MarketBlocks(self, request, context):
        self.authenticate(context)
        try:
            if not 1 <= len(request.tiles) <= 8192:
                raise ValueError("Invalid market tile batch size")
            snapshot = self.snapshot(request.snapshot_id)
            if request.cache_layout:
                from factorpool.data_client.contract import CACHE_VERSION
                layout = json.loads(request.cache_layout)
                if (layout.get("version") != CACHE_VERSION or layout.get("codec") not in ("raw", "zlib", "lz4")
                        or type(layout.get("chunk_max_bytes")) is not int or not 8192 <= layout["chunk_max_bytes"] <= 16*1024*1024
                        or type(layout.get("tile_symbols")) is not int or not 1 <= layout["tile_symbols"] <= 65536):
                    raise ValueError("Unsupported requested cache layout")
                snapshot = dict(snapshot, cache=layout)
            blocks = {block["start_index"]: block for block in snapshot["blocks"]}
            grouped, estimate, seen = OrderedDict(), 0, set()
            for tile in request.tiles:
                block = blocks.get(tile.start_index)
                if block is None:
                    raise ValueError("Requested block is absent from snapshot")
                key = tile_key(snapshot, block, tile.field, tile.symbol_start)
                if key in seen:
                    raise ValueError("Duplicate requested market tile")
                seen.add(key)
                width = tile_width(snapshot, block)
                estimate += (block["row_count"] * min(width, len(snapshot["symbols"])-tile.symbol_start)
                             * field_dtype(snapshot, tile.field).itemsize + 4096)
                if estimate > self.max_batch:
                    raise ValueError("Market request exceeds bounded batch bytes")
                grouped.setdefault(tile.start_index, []).append((key, tile.field, tile.symbol_start))
            self.stats["requests"] += 1
            for start, requested in grouped.items():
                if not context.is_active():
                    return
                payloads = self._fill(snapshot, blocks[start], requested)
                for key, _, _ in requested:
                    yield pb.BinaryBlock(key=key, payload=payloads[key])
        except (ValueError, TypeError, KeyError) as exc:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
        except Exception:
            context.abort(grpc.StatusCode.UNAVAILABLE, "Data service storage operation failed")

    @endpoint
    def StageValues(self, request, context):
        return self.values.stage(request, self.max_chunk)

    @endpoint
    def FinalizeValues(self, request, context):
        return self.values.finalize(request)

    @endpoint
    def VerifyPublication(self, request, context):
        return self.values.verify(request)

    @endpoint
    def Values(self, request, context):
        task = str(uuid.UUID(request.task_id))
        if request.offset < 0 or not 1 <= request.limit <= 1000:
            raise ValueError("Invalid pagination")
        version = self.values.visible_version(task)
        key = f"fp:values:v1:{task}:{version}:{request.offset}:{request.limit}"
        cached = self.cache.call("get", key)
        if cached is not None:
            try:
                from google.protobuf.message import DecodeError
                raw = unpack_value_page(cached, task, version, request.offset, request.limit)
                rows = pb.ValuesPayload.FromString(raw).rows
                if len(rows) > request.limit:
                    raise ValueError("Factor-value cached page exceeds row limit")
                return pb.JsonReply(json=json_bytes([dict(trading_date=row.date, symbol=row.symbol,
                    value=row.value if row.has_value else None, valid=row.valid, ready=row.ready, reason=row.reason,
                    forward_return=row.forward_return if row.has_forward_return else None,
                    factor_group=row.group if row.has_group else None) for row in rows]))
            except (ValueError, TypeError, KeyError, DecodeError):
                self.cache.call("delete", key)
        rows = self.values.page(task, request.offset, request.limit)
        typed = pb.ValuesPayload(rows=[pb.FactorValue(date=row["trading_date"], symbol=row["symbol"],
            value=row["value"] if row["value"] is not None else 0, has_value=row["value"] is not None, valid=row["valid"], ready=row["ready"],
            reason=row["reason"], forward_return=row["forward_return"] if row["forward_return"] is not None else 0,
            has_forward_return=row["forward_return"] is not None, group=row["factor_group"] or 0,
            has_group=row["factor_group"] is not None) for row in rows]).SerializeToString(deterministic=True)
        # Small response pages have typed binary numeric cells. The envelope is
        # shared with no task state and is cached only after visibility checks.
        self.cache.store([(key, pack_value_page(task, version, request.offset, request.limit, typed))])
        return pb.JsonReply(json=json_bytes(rows))

    @endpoint
    def Health(self, request, context):
        with transaction(readonly=True) as conn:
            conn.execute("SELECT 1").fetchone()
        redis_ok = bool(self.cache.call("ping")) if self.cache.enabled else None
        return pb.JsonReply(json=json_bytes(dict(status="UP", postgres="UP", redis="UP" if redis_ok else "DOWN" if self.cache.enabled else "DISABLED",
            degraded=self.cache.enabled and not redis_ok, stats=dict(self.stats), postgres_stats=dict(self.source.stats),
            prepared_stats=dict(self.prepared.stats), redis_errors=self.cache.errors)))
