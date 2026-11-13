"""Independent matrix expectations across cache faults and bounded cancellation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import grpc
import numpy as np

from factorpool.data_client import DataClient
from factorpool.data_client.contract import (CACHE_VERSION, FIELDS, FORMAT, PREFIX,
    decode_tile, encode_tile, json_bytes, metadata_key, pack_block, pack_metadata, tile_key)
from factorpool.data_client.generated import data_pb2 as pb
from services.data_service.api import DataService
from services.data_service.redis_cache import Cache


class MemoryRedis:
    def __init__(self):
        self.data = {}
        self.calls = []
        self.offline = False
        self.pipeline_failures = 0
        self.lock = threading.Lock()

    def check(self, action):
        self.calls.append(action)
        if self.offline:
            raise OSError("isolated Redis outage")

    def get(self, key):
        self.check("get")
        return self.data.get(key)

    def mget(self, keys):
        self.check("mget")
        return [self.data.get(key) for key in keys]

    def set(self, key, value, **kwargs):
        self.check("set")
        with self.lock:
            if kwargs.get("nx") and key in self.data:
                return False
            self.data[key] = value
            return True

    def delete(self, key):
        self.check("delete")
        return self.data.pop(key, None)

    def eval(self, script, count, key, value):
        self.check("eval")
        with self.lock:
            if self.data.get(key) == value:
                del self.data[key]
                return 1
            return 0

    def pipeline(self, **kwargs):
        owner = self

        class Pipeline:
            def __init__(self):
                self.entries = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def set(self, key, value, **kwargs):
                self.entries.append((key, value))

            def execute(self):
                owner.check("pipeline")
                if owner.pipeline_failures:
                    owner.pipeline_failures -= 1
                    raise OSError("isolated pipeline failure")
                owner.data.update(self.entries)

        return Pipeline()


def fixture(identity="cache-test", multiplier=1):
    values = np.arange(len(FIELDS)*4*3, dtype=np.float64).reshape(len(FIELDS), 4, 3) * multiplier
    values[3, 1, 1] = np.nan
    blocks = [dict(start_index=start, row_count=2, sha256=pack_block(values[:, start:start+2])[1])
              for start in (0, 2)]
    metadata = dict(snapshot_id=identity, manifest={"format": FORMAT}, fields=list(FIELDS),
                    symbols=["A", "B", "C"], dates=["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"],
                    blocks=blocks, cache=dict(version=CACHE_VERSION, codec="raw", chunk_max_bytes=8192, tile_symbols=2))
    return metadata, values


class MatrixStub:
    def __init__(self, metadata, values, cache):
        self.metadata, self.values, self.cache = metadata, values, cache
        self.snapshot_calls = 0
        self.block_calls = 0

    def Snapshot(self, request, **kwargs):
        self.snapshot_calls += 1
        self.cache.data[metadata_key(self.metadata["snapshot_id"])] = pack_metadata(self.metadata)
        return pb.JsonReply(json=json_bytes(self.metadata))

    def MarketBlocks(self, request, **kwargs):
        self.block_calls += 1
        metadata = deepcopy(self.metadata)
        if getattr(request, "cache_layout", b""):
            metadata["cache"] = json.loads(request.cache_layout)
        for tile in request.tiles:
            block = next(row for row in metadata["blocks"] if row["start_index"] == tile.start_index)
            width = min(metadata["cache"]["tile_symbols"], metadata["cache"]["chunk_max_bytes"] // (block["row_count"] * 8))
            selected = self.values[FIELDS.index(tile.field), block["start_index"]:block["start_index"]+block["row_count"],
                                   tile.symbol_start:tile.symbol_start+width]
            key = tile_key(metadata, block, tile.field, tile.symbol_start)
            payload = encode_tile(metadata, block, tile.field, tile.symbol_start, selected)
            self.cache.data[key] = payload
            yield pb.BinaryBlock(key=key, payload=payload)


class CacheFaultTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {"FACTORPOOL_CACHE_ENABLED": "true",
            "FACTORPOOL_CACHE_CHUNK_MAX_BYTES": "8192", "FACTORPOOL_CACHE_BATCH_MAX_BYTES": "32768",
            "FACTORPOOL_CACHE_TILE_SYMBOLS": "2", "FACTORPOOL_CACHE_CODEC": "raw",
            "FACTORPOOL_INTERNAL_TOKEN": "isolated-tests-service-token-12345"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def load(self, client, metadata):
        blocks = list(client.selected_blocks(metadata, 0, 4, np.array([2, 0])))
        return np.concatenate([row[2] for row in blocks], axis=1)

    def test_cold_hot_expired_and_redis_outage_match_independent_cells(self):
        metadata, values = fixture()
        redis = MemoryRedis()
        stub = MatrixStub(metadata, values, redis)
        client = DataClient(stub=stub, cache=redis)
        pinned = client.snapshot(metadata["snapshot_id"])
        expected = values[:, :, [2, 0]]
        np.testing.assert_array_equal(self.load(client, pinned), expected)
        self.assertGreater(stub.block_calls, 0)
        first_calls = stub.block_calls
        np.testing.assert_array_equal(self.load(client, pinned), expected)
        self.assertEqual(stub.block_calls, first_calls)
        self.assertGreater(client.stats["redis_hits"], 0)
        key = tile_key(pinned, pinned["blocks"][0], "close", 0)
        del redis.data[key]
        np.testing.assert_array_equal(self.load(client, pinned), expected)
        self.assertEqual(stub.block_calls, first_calls + 1)
        redis.offline = True
        np.testing.assert_array_equal(self.load(client, pinned), expected)
        self.assertEqual(client.stats["redis_errors"], 1)
        self.assertGreater(client.stats["grpc_blocks"], 0)

    def test_corrupt_header_axes_and_payload_become_misses(self):
        for corruption in ("wrong_type", "axes", "length"):
            with self.subTest(corruption=corruption):
                metadata, values = fixture()
                redis = MemoryRedis()
                stub = MatrixStub(metadata, values, redis)
                client = DataClient(stub=stub, cache=redis)
                pinned = client.snapshot(metadata["snapshot_id"])
                self.load(client, pinned)
                key = tile_key(pinned, pinned["blocks"][0], "close", 0)
                payload = redis.data[key]
                if corruption == "wrong_type":
                    redis.data[key] = PREFIX + struct.pack("!I", 2) + b"[]"
                elif corruption == "length":
                    redis.data[key] = payload[:-1]
                else:
                    size = struct.unpack("!I", payload[4:8])[0]
                    header = json.loads(payload[8:8+size])
                    header["axes"] = "0"*64
                    changed = json_bytes(header)
                    redis.data[key] = PREFIX + struct.pack("!I", len(changed)) + changed + payload[8+size:]
                before = stub.block_calls
                np.testing.assert_array_equal(self.load(client, pinned), values[:, :, [2, 0]])
                self.assertEqual(stub.block_calls, before + 1)
                self.assertEqual(client.stats["corrupt_blocks"], 1)

    def test_metadata_wrong_shape_or_snapshot_id_refetches(self):
        for corrupted in ([], {"snapshot_id": "another"}, {"snapshot_id": "cache-test", "manifest": None}):
            metadata, values = fixture()
            redis = MemoryRedis()
            redis.data[metadata_key("cache-test")] = pack_metadata(corrupted)
            stub = MatrixStub(metadata, values, redis)
            pinned = DataClient(stub=stub, cache=redis).snapshot("cache-test")
            self.assertEqual(pinned["snapshot_id"], "cache-test")
            self.assertEqual(stub.snapshot_calls, 1)

    def test_snapshots_share_redis_without_cell_contamination(self):
        redis = MemoryRedis()
        for identity, multiplier in (("old-snapshot", 1), ("new-snapshot", 3), ("old-snapshot", 1)):
            metadata, values = fixture(identity, multiplier)
            stub = MatrixStub(metadata, values, redis)
            client = DataClient(stub=stub, cache=redis)
            pinned = client.snapshot(identity)
            np.testing.assert_array_equal(self.load(client, pinned), values[:, :, [2, 0]])
            if identity == "old-snapshot" and multiplier == 1 and stub.snapshot_calls == 0:
                self.assertEqual(stub.block_calls, 0)

    def test_pipeline_failure_opens_circuit_and_still_consumes_entries(self):
        redis = MemoryRedis()
        redis.pipeline_failures = 10
        consumed = []
        with patch.dict("os.environ", {"FACTORPOOL_CACHE_BATCH_MAX_BYTES": "8192"}):
            cache = Cache(redis)

        def entries():
            for number in range(4):
                consumed.append(number)
                yield f"test-{number}", b"x" * 5000

        cache.store(entries())
        self.assertEqual(consumed, [0, 1, 2, 3])
        self.assertEqual(redis.calls.count("pipeline"), 1)
        self.assertEqual(cache.errors, 1)

    def test_client_and_service_disable_hidden_redis_retries(self):
        with patch("redis.Redis.from_url", return_value=MemoryRedis()) as construct:
            DataClient(stub=object())
            Cache()
        self.assertEqual(len(construct.call_args_list), 2)
        for call in construct.call_args_list:
            self.assertEqual(call.kwargs["retry"].get_retries(), 0)

    def test_concurrent_first_fill_decodes_original_block_once(self):
        metadata, values = fixture()

        class Source:
            stats = {}

            def __init__(self):
                self.calls = 0

            def original_block(self, snapshot, block):
                self.calls += 1
                time.sleep(.02)
                return values[:, :2]

        source, redis = Source(), MemoryRedis()
        service = DataService(source=source, cache=Cache(redis), values=object())
        block = metadata["blocks"][0]
        key = tile_key(metadata, block, "close", 0)
        request = [(key, "close", 0)]
        with ThreadPoolExecutor(max_workers=6) as executor:
            replies = list(executor.map(lambda _: service._fill(metadata, block, request), range(6)))
        self.assertEqual(source.calls, 1)
        for reply in replies:
            np.testing.assert_array_equal(decode_tile(reply[key], metadata, block, "close", 0), values[3, :2, :2])


class PendingFuture:
    def __init__(self):
        self.cancelled = False

    def result(self, timeout):
        time.sleep(min(timeout, .01))
        raise grpc.FutureTimeoutError()

    def cancel(self):
        self.cancelled = True


class PendingMethod:
    def __init__(self, started=None):
        self.pending = PendingFuture()
        self.started = started

    def future(self, *args, **kwargs):
        if self.started:
            self.started.set()
        return self.pending


class UploadCancellationTests(unittest.TestCase):
    def value_file(self, directory):
        path = Path(directory) / "values.jsonl"
        path.write_text(json.dumps(dict(date="2026-01-05", symbol="A", value=1.0,
                                       valid=True, ready=True, reason="")) + "\n")
        return path

    def test_stalled_stage_future_cancels_without_waiting_rpc_deadline(self):
        class Stub:
            StageValues = PendingMethod()

        calls = []

        def cancelled():
            calls.append(1)
            return len(calls) >= 3

        with tempfile.TemporaryDirectory() as tmp:
            client = DataClient(stub=Stub(), enabled=False)
            started = time.monotonic()
            with self.assertRaises(InterruptedError):
                client.upload_values("12345678-1234-1234-1234-123456789abc", 1,
                    "87654321-4321-4321-4321-cba987654321", self.value_file(tmp), 1, cancelled=cancelled)
        self.assertTrue(Stub.StageValues.pending.cancelled)
        self.assertLess(time.monotonic() - started, 1)

    def test_stalled_finalize_future_cancels_after_complete_stage(self):
        started = threading.Event()

        class Stub:
            FinalizeValues = PendingMethod(started)

            def StageValues(self, chunks, **kwargs):
                count, digest = 0, hashlib.sha256()
                for chunk in chunks:
                    count += chunk.row_count
                    digest.update(bytes.fromhex(chunk.sha256))
                return pb.ReceiptReply(valid=True, count=count, sha256=digest.hexdigest())

        with tempfile.TemporaryDirectory() as tmp:
            client = DataClient(stub=Stub(), enabled=False)
            before = time.monotonic()
            with self.assertRaises(InterruptedError):
                client.upload_values("12345678-1234-1234-1234-123456789abc", 1,
                    "87654321-4321-4321-4321-cba987654321", self.value_file(tmp), 1, cancelled=started.is_set)
        self.assertTrue(Stub.FinalizeValues.pending.cancelled)
        self.assertLess(time.monotonic() - before, 1)


if __name__ == "__main__":
    unittest.main()
