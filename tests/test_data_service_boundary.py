"""Real gRPC transport, shared binary blocks and direct client cache hits."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import threading
import unittest
from unittest.mock import patch

import grpc
import numpy as np

from factorpool.data_client import DataClient
from factorpool.data_client.contract import (CACHE_VERSION, FIELDS, FORMAT, pack_block, metadata_key,
                                             tile_key, validate_snapshot)
from factorpool.data_client.generated import data_pb2 as pb, data_pb2_grpc
from factorpool.market_loader import load
from services.data_service.api import DataService
from services.data_service.redis_cache import Cache


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()
        self.online = True
        self.gets = self.mgets = self.writes = 0

    def _check(self):
        if not self.online:
            raise ConnectionError('isolated cache outage')

    def get(self, key):
        self._check()
        with self.lock:
            self.gets += 1
            return self.values.get(key)

    def mget(self, keys):
        self._check()
        with self.lock:
            self.mgets += 1
            return [self.values.get(key) for key in keys]

    def set(self, key, value, nx=False, ex=None):
        self._check()
        with self.lock:
            if nx and key in self.values:
                return False
            self.values[key] = value
            self.writes += 1
            return True

    def delete(self, key):
        self._check()
        with self.lock:
            return self.values.pop(key, None) is not None

    def eval(self, script, count, key, token):
        with self.lock:
            if self.values.get(key) == token:
                self.values.pop(key)
                return 1
            return 0

    def pipeline(self, transaction=False):
        return MemoryPipeline(self)

    def ping(self):
        self._check()
        return True


class MemoryPipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set(self, key, value, **options):
        self.commands.append((key, value, options))

    def execute(self):
        for key, value, options in self.commands:
            self.client.set(key, value, **options)


class OriginalSource:
    def __init__(self, snapshot='original', codec='raw'):
        self.identity = snapshot
        self.matrix = np.arange(len(FIELDS)*9*4, dtype='<f8').reshape(len(FIELDS), 9, 4)
        self.matrix[8] = 1
        self.matrix[8, 1:4, 1] = 0
        self.matrix[:7, 3, 2] = np.nan
        self.matrix[7] = np.arange(36).reshape(9, 4)/10000
        self.metadata = dict(snapshot_id=snapshot, manifest=dict(format=FORMAT, instrument_lifetimes=[[20200101,20990101]]*4),
            symbols=['A','B','C','D'], dates=[f'2024-01-{n:02d}' for n in range(1,10)], fields=list(FIELDS),
            cache=dict(version=CACHE_VERSION, codec=codec, tile_symbols=2, chunk_max_bytes=8192),
            blocks=[dict(start_index=0,row_count=9,sha256=pack_block(self.matrix)[1])])
        self.stats = dict(pg_blocks=0, pg_snapshots=0)
        self.lock = threading.Lock()

    def snapshot(self, identity):
        if identity and identity != self.identity:
            from services.data_service.postgres.market import MarketSnapshotNotFound
            raise MarketSnapshotNotFound('no such snapshot')
        self.stats['pg_snapshots'] += 1
        return validate_snapshot(deepcopy(self.metadata), identity)

    def original_block(self, snapshot, block):
        with self.lock:
            self.stats['pg_blocks'] += 1
        return self.matrix.copy()


class GrpcBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, FACTORPOOL_INTERNAL_TOKEN='isolated-only',
            FACTORPOOL_CACHE_ENABLED='true', FACTORPOOL_CACHE_CHUNK_MAX_BYTES='8192',
            FACTORPOOL_CACHE_BATCH_MAX_BYTES='33554432', FACTORPOOL_CACHE_TILE_SYMBOLS='2')
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.cache = MemoryRedis()
        self.source = OriginalSource()
        self.service = DataService(source=self.source, cache=Cache(self.cache))
        self.server = grpc.server(ThreadPoolExecutor(max_workers=8))
        data_pb2_grpc.add_DataServiceServicer_to_server(self.service, self.server)
        self.port = self.server.add_insecure_port('127.0.0.1:0')
        self.server.start()
        self.addCleanup(lambda: self.server.stop(0).wait())
        self.clients = []
        self.addCleanup(lambda: [client.close() for client in self.clients])

    def client(self):
        channel = grpc.insecure_channel(f'127.0.0.1:{self.port}')
        client = DataClient(stub=data_pb2_grpc.DataServiceStub(channel), cache=self.cache)
        client.channel = channel
        self.clients.append(client)
        return client

    def test_second_read_is_direct_redis_and_matches_independent_matrix(self):
        client = self.client()
        metadata = client.snapshot('original')
        first = list(client.selected_blocks(metadata,0,9,np.array([3,0])))
        np.testing.assert_array_equal(first[0][2], self.source.matrix[:,:,[3,0]])
        self.assertEqual(self.source.stats['pg_blocks'], 1)
        hot = self.client()
        with patch.object(hot.stub, 'Snapshot', side_effect=AssertionError('hot metadata proxied')), \
             patch.object(hot.stub, 'MarketBlocks', side_effect=AssertionError('hot matrix proxied')):
            second = list(hot.selected_blocks(hot.snapshot('original'),0,9,np.array([0,3])))
        np.testing.assert_array_equal(second[0][2], self.source.matrix[:,:,[0,3]])
        self.assertEqual(hot.stats['grpc_blocks'], 0)
        self.assertEqual(hot.stats['redis_hits'], 22)
        self.assertGreater(self.cache.mgets, 0)

    def test_bad_cache_header_is_scoped_repaired_and_redis_outage_falls_back(self):
        client = self.client()
        metadata = client.snapshot('original')
        expected = list(client.selected_blocks(metadata,0,9,np.array([0])))
        key = tile_key(metadata,metadata['blocks'][0],'close',0)
        self.cache.values[key] = b'FPC2\x00\x00\x00\x02[]'
        actual = list(client.selected_blocks(metadata,0,9,np.array([0])))
        np.testing.assert_array_equal(actual[0][2], expected[0][2])
        self.assertEqual(client.stats['corrupt_blocks'],1)
        self.cache.online = False
        outage = list(client.selected_blocks(metadata,0,9,np.array([0])))
        np.testing.assert_array_equal(outage[0][2], expected[0][2])
        self.assertEqual(client.stats['redis_errors'],1)

    def test_concurrent_first_access_fills_once_and_keeps_axes(self):
        clients = [self.client() for _ in range(6)]
        metadata = clients[0].snapshot('original')
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda client: list(client.selected_blocks(metadata,0,9,np.array([2,1]))),clients))
        for actual in results:
            np.testing.assert_array_equal(actual[0][2],self.source.matrix[:,:,[2,1]])
        self.assertEqual(self.source.stats['pg_blocks'],1)

    def test_cache_layout_change_does_not_invalidate_old_snapshot_reads(self):
        client = self.client()
        old = client.snapshot('original')
        self.service._metadata.clear()
        self.source.metadata['cache']['tile_symbols'] = 4
        self.source.metadata['cache']['codec'] = 'zlib'
        actual = list(client.selected_blocks(old,0,9,np.array([1,3])))
        np.testing.assert_array_equal(actual[0][2], self.source.matrix[:,:,[1,3]])

    def test_metadata_corruption_repairs_only_its_key(self):
        client = self.client()
        client.snapshot('original')
        key = metadata_key('original')
        self.cache.values[key] = b'badmetadata'
        self.cache.values['unrelated'] = b'preserve'
        actual = client.snapshot('original')
        self.assertEqual(actual['snapshot_id'],'original')
        self.assertNotEqual(self.cache.values[key],b'badmetadata')
        self.assertEqual(self.cache.values['unrelated'],b'preserve')

    def test_missing_snapshot_auth_and_oversized_batch_fail_explicitly(self):
        client = self.client()
        with self.assertRaises(grpc.RpcError) as missing:
            client.snapshot('missing')
        self.assertEqual(missing.exception.code(),grpc.StatusCode.NOT_FOUND)
        with self.assertRaises(grpc.RpcError) as auth:
            client.stub.Snapshot(pb.SnapshotRequest(),timeout=2)
        self.assertEqual(auth.exception.code(),grpc.StatusCode.UNAUTHENTICATED)
        with self.assertRaises(grpc.RpcError) as duplicate:
            list(client.stub.MarketBlocks(pb.MarketRequest(snapshot_id='original',tiles=[
                pb.TileRequest(start_index=0,field='close',symbol_start=0)]*2),
                timeout=2,metadata=client.metadata))
        self.assertEqual(duplicate.exception.code(),grpc.StatusCode.FAILED_PRECONDITION)

    def test_full_loader_keeps_warmup_research_and_future_axes(self):
        payload = load(dict(snapshot_id='original',factor_start='2024-01-03',factor_end='2024-01-07',universe=['D','A']),
                       3,2,source=self.client(),matrix_only=True)
        self.assertEqual(payload['symbols'],['A','D'])
        self.assertEqual(payload['quotes']['dates'],self.source.metadata['dates'][:7])
        np.testing.assert_array_equal(payload['returns'],self.source.matrix[7,:,[0,3]].T)
        np.testing.assert_array_equal(payload['quotes']['values'],self.source.matrix[:,:7,[0,3]])
        self.assertTrue(all(not frame['symbols'] for frame in payload['frames'][7:]))
