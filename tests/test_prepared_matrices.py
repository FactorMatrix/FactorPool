"""Prepared field tables are optional, immutable-source-bound and recoverable."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import grpc
import numpy as np

from factorpool.data_client import DataClient
from factorpool.data_client.contract import (CACHE_VERSION, FIELDS, FORMAT, MEMBERSHIP_FIELD,
    PRESENT_FIELD, SERVED_FIELDS, pack_block, tile_key, validate_snapshot)
from factorpool.data_client.generated import data_pb2_grpc
from services.data_service.api import DataService
from services.data_service.prepared_matrices import PreparedMatrices
from services.data_service.redis_cache import Cache
import test_data_service_boundary as grpc_cases
from market_fixtures import field_snapshot, matrix_blocks


class OriginalBlocks:
    """Independent field values and original source-block identities."""
    def __init__(self, identity='prepared-fixture'):
        self.matrix = np.arange(len(FIELDS)*8*5, dtype='<f8').reshape(len(FIELDS), 8, 5)/7
        self.matrix[7] = np.arange(40, dtype=float).reshape(8, 5)/10000-.002
        self.matrix[7, 2, 4] = np.nan
        self.matrix[8] = 1
        self.matrix[8, :2, 1] = 0
        self.matrix[8, -1, 4] = 0
        self.matrix[:7, 3, 2] = np.nan
        self.matrix[6, 4, 0] = np.nan
        self.matrix[0, 1, 4] = np.inf
        self.matrix[:7, 6, 3] = np.inf
        self.payloads = {}
        blocks = []
        for start in range(0, 8, 3):
            count = min(3, 8-start)
            payload, checksum = pack_block(self.matrix[:, start:start+count])
            self.payloads[start] = payload
            blocks.append(dict(start_index=start, row_count=count, sha256=checksum))
        self.metadata = dict(snapshot_id=identity,
            manifest=dict(format=FORMAT, instrument_lifetimes=[[20200101, 20990101]]*5,
                          return_policy=dict(version='synthetic-fixed-returns')),
            symbols=[f'S{n:03d}' for n in range(5)],
            dates=[(date(2024, 1, 2)+timedelta(days=n)).isoformat() for n in range(8)],
            fields=list(FIELDS), blocks=blocks,
            cache=dict(version=CACHE_VERSION, codec='raw', tile_symbols=2, chunk_max_bytes=8192))
        self.columns = field_snapshot(self.metadata, self.matrix)
        self.reads = []
        self.lock = threading.Lock()
        self.fail_start = None
        self.pause_start = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.stats = dict(pg_snapshots=0, pg_blocks=0)

    def snapshot(self, identity):
        self.stats['pg_snapshots'] += 1
        return validate_snapshot(deepcopy(self.metadata), identity)

    def original_block(self, snapshot, block):
        start = block['start_index']
        if start == self.pause_start:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError('Prepared-cache test synchronization timed out')
        if start == self.fail_start:
            raise RuntimeError('Interrupted original block read')
        with self.lock:
            self.reads.append(start)
            self.stats['pg_blocks'] += 1
        count = block['row_count']
        return self.matrix[:, start:start+count].copy()

    def read_fields(self, snapshot, block, fields):
        self.original_block(snapshot, block)
        start, count = block['start_index'], block['row_count']
        return {field: self.columns[field][start:start+count].copy() for field in fields}


def expected_field(matrix, field):
    if field == PRESENT_FIELD:
        return np.any(np.isfinite(matrix[:7]), axis=0).astype('uint8')
    if field == MEMBERSHIP_FIELD:
        return (matrix[FIELDS.index('member')] > 0).astype('uint8')
    return matrix[FIELDS.index(field)]


class PreparedMatricesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = OriginalBlocks()
        self.snapshot = self.source.snapshot('prepared-fixture')
        self.cache = PreparedMatrices(self.root)
        self.fields = ('return', 'total_turnover', PRESENT_FIELD, MEMBERSHIP_FIELD)

    def prepare(self, start=None, end=None, fields=None, source=None):
        return self.cache.prepare(self.snapshot, source or self.source,
            fields=self.fields if fields is None else fields,
            start=start or self.snapshot['dates'][0], end=end or self.snapshot['dates'][-1])

    def assert_block(self, block, fields=None, cache=None):
        fields = self.fields if fields is None else fields
        requests = [(field, start) for field in fields for start in (0, 2, 4)]
        found = (cache or self.cache).tiles(self.snapshot, block, requests)
        self.assertEqual(set(found), set(requests))
        for field, start in requests:
            expected = expected_field(self.source.matrix, field)
            left = block['start_index']
            expected = expected[left:left+block['row_count'], start:min(5, start+2)]
            np.testing.assert_array_equal(found[field, start], expected)
            self.assertTrue(found[field, start].flags.c_contiguous)
            self.assertEqual(found[field, start].dtype, np.dtype('uint8' if field in (PRESENT_FIELD, MEMBERSHIP_FIELD) else '<f8'))

    def test_absent_cache_is_a_miss_without_creating_storage(self):
        root = self.root/'absent'
        cache = PreparedMatrices(root)
        self.assertEqual(cache.tiles(self.snapshot, self.snapshot['blocks'][0], [('return', 0)]), {})
        self.assertFalse(root.exists())

    def test_all_fields_round_trip_from_one_read_per_original_block_and_reuse(self):
        report = self.prepare(fields=SERVED_FIELDS)
        self.assertIsInstance(report, dict)
        self.assertEqual(self.source.reads, [0, 3, 6])
        for block in self.snapshot['blocks']:
            self.assert_block(block, fields=SERVED_FIELDS)
        paths = list((self.root/self.snapshot['snapshot_id']).glob('*.npy'))
        self.assertEqual({path.name for path in paths}, {field+'.npy' for field in SERVED_FIELDS})
        self.assertTrue((self.root/self.snapshot['snapshot_id']/'manifest.json').is_file())
        self.source.reads.clear()
        self.prepare(fields=SERVED_FIELDS)
        self.assertEqual(self.source.reads, [])
        # Cache data is independent from one reader's returned array object.
        tile = self.cache.tiles(self.snapshot, self.snapshot['blocks'][0], [('return', 0)])['return', 0]
        if tile.flags.writeable:
            tile[0, 0] = 999
        self.assert_block(self.snapshot['blocks'][0])

    def test_partial_date_window_and_extension_preserve_exact_old_coverage(self):
        self.prepare(start=self.snapshot['dates'][1], end=self.snapshot['dates'][4])
        self.assertEqual(self.source.reads, [0, 3])
        self.assert_block(self.snapshot['blocks'][0])
        self.assert_block(self.snapshot['blocks'][1])
        self.assertEqual(self.cache.tiles(self.snapshot, self.snapshot['blocks'][2], [('return', 0)]), {})
        self.source.reads.clear()
        self.prepare(start=self.snapshot['dates'][6], end=self.snapshot['dates'][7])
        self.assertEqual(self.source.reads, [6])
        for block in self.snapshot['blocks']:
            self.assert_block(block)

    def test_source_failure_does_not_publish_partial_new_coverage(self):
        self.prepare(start=self.snapshot['dates'][0], end=self.snapshot['dates'][2])
        folder = self.root/self.snapshot['snapshot_id']
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in folder.iterdir() if path.suffix == '.npy' or path.name == 'manifest.json'}
        self.source.fail_start = 6
        with self.assertRaisesRegex(RuntimeError, 'Interrupted'):
            self.prepare(start=self.snapshot['dates'][3], end=self.snapshot['dates'][7])
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in folder.iterdir() if path.suffix == '.npy' or path.name == 'manifest.json'}
        self.assertEqual(before, after)
        self.assert_block(self.snapshot['blocks'][0])
        self.assertEqual(self.cache.tiles(self.snapshot, self.snapshot['blocks'][1], [('return', 0)]), {})

    def test_corrupted_bytes_and_bad_file_length_are_scoped_misses(self):
        self.prepare()
        first = self.snapshot['blocks'][0]
        self.assert_block(first)  # Warm any per-file checksum-validation state.
        path = self.root/self.snapshot['snapshot_id']/'total_turnover.npy'
        values = np.load(path, mmap_mode='r+')
        values[0, 0] += 123
        values.flush()
        del values
        result = self.cache.tiles(self.snapshot, first, [('total_turnover', 0), ('return', 0)])
        self.assertNotIn(('total_turnover', 0), result)
        np.testing.assert_array_equal(result['return', 0], self.source.matrix[7, :3, :2])
        self.prepare(fields=('total_turnover',))
        self.assert_block(first)
        with path.open('ab') as file:
            file.write(b'unexpected trailer')
        self.assertEqual(self.cache.tiles(self.snapshot, first, [('total_turnover', 0)]), {})
        self.assertIn(('return', 0), self.cache.tiles(self.snapshot, first, [('return', 0)]))

    def test_changed_axes_original_sha_and_npy_header_cannot_be_used(self):
        self.prepare()
        first = self.snapshot['blocks'][0]
        wrong_axes = deepcopy(self.snapshot)
        wrong_axes['symbols'] = [f'X{n:03d}' for n in range(5)]
        self.assertEqual(self.cache.tiles(wrong_axes, wrong_axes['blocks'][0], [('return', 0)]), {})
        wrong_sha = deepcopy(self.snapshot)
        wrong_sha['blocks'][0]['sha256'] = 'f'*64
        self.assertEqual(self.cache.tiles(wrong_sha, wrong_sha['blocks'][0], [('return', 0)]), {})
        path = self.root/self.snapshot['snapshot_id']/'return.npy'
        path.write_bytes(b'not-a-numpy-header')
        self.assertEqual(self.cache.tiles(self.snapshot, first, [('return', 0)]), {})

    def test_malformed_manifest_is_a_miss_and_a_repaired_marker_is_readable(self):
        self.prepare()
        path = self.root/self.snapshot['snapshot_id']/'manifest.json'
        original = path.read_bytes()
        first = self.snapshot['blocks'][0]
        self.assert_block(first)
        for broken in (b'[]', b'null', b'{'):
            path.write_bytes(broken)
            self.assertEqual(self.cache.tiles(self.snapshot, first, [('return', 0)]), {})
        path.write_bytes(original)
        self.assert_block(first)

    def test_concurrent_preparations_keep_both_published_windows(self):
        second = PreparedMatrices(self.root)
        with ThreadPoolExecutor(max_workers=2) as executor:
            jobs = [executor.submit(cache.prepare, self.snapshot, self.source, fields=('return',),
                                    start=self.snapshot['dates'][first], end=self.snapshot['dates'][last])
                    for cache, first, last in ((self.cache, 0, 2), (second, 6, 7))]
            for job in jobs:
                job.result(timeout=10)
        self.assertEqual(sorted(self.source.reads), [0, 6])
        self.assert_block(self.snapshot['blocks'][0], fields=('return',))
        self.assert_block(self.snapshot['blocks'][2], fields=('return',))
        self.assertEqual(len(list((self.root/self.snapshot['snapshot_id']).glob('*.npy'))), 1)

    def test_reader_sees_old_coverage_until_new_prepare_is_published(self):
        self.prepare(start=self.snapshot['dates'][0], end=self.snapshot['dates'][2])
        self.source.pause_start = 6
        with ThreadPoolExecutor(max_workers=1) as executor:
            task = executor.submit(self.prepare, start=self.snapshot['dates'][6], end=self.snapshot['dates'][7])
            try:
                self.assertTrue(self.source.entered.wait(timeout=5))
                self.assert_block(self.snapshot['blocks'][0])
                self.assertEqual(self.cache.tiles(self.snapshot, self.snapshot['blocks'][2], [('return', 0)]), {})
            finally:
                self.source.release.set()
            task.result(timeout=10)
        self.assert_block(self.snapshot['blocks'][0])
        self.assert_block(self.snapshot['blocks'][2])


@unittest.skipUnless(os.environ.get('FACTORPOOL_TEST_DB') == '1', 'Requires an isolated test PostgreSQL service')
class PreparedPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if (os.environ.get('FACTORPOOL_DB_HOST') != 'test-db'
                or os.environ.get('FACTORPOOL_DB_NAME') != 'factorpool'):
            raise RuntimeError('Prepared-matrix PG tests require host=test-db and dbname=factorpool')
        from services.data_service.postgres import connect
        cls.connection = connect()
        cls.connection.autocommit = True
        cls.connection.execute(Path('/opt/factorpool/schema.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'connection'):
            cls.connection.close()

    def test_actual_pg_preparation_and_grpc_reads_skip_mixed_block_decode(self):
        from psycopg.types.json import Jsonb
        from services.data_service.postgres.market import PostgresMarketSource
        original = OriginalBlocks(uuid.uuid4().hex)
        identity = original.metadata['snapshot_id']
        self.addCleanup(lambda: self.connection.execute('DELETE FROM market_snapshots WHERE id=%s', (identity,)))
        from services.data_service.market_store import TABLES
        for table in TABLES:
            self.addCleanup(lambda table=table: self.connection.execute(
                f'DELETE FROM {table} WHERE snapshot_id=%s', (identity,)))
        with self.connection.transaction():
            self.connection.execute('INSERT INTO market_snapshots(id,manifest,symbols,dates,fields) VALUES(%s,%s,%s,%s,%s)',
                (identity, Jsonb(original.metadata['manifest']), Jsonb(original.metadata['symbols']),
                 Jsonb(original.metadata['dates']), Jsonb(list(FIELDS))))
            for block in original.metadata['field_blocks']:
                start, count = block['start_index'], block['row_count']
                for field, descriptor in block['fields'].items():
                    table = original.metadata['field_catalog'][field]['table']
                    raw = original.columns[field][start:start+count].tobytes()
                    self.connection.execute(f'INSERT INTO {table}(snapshot_id,field,start_index,row_count,dtype,payload,sha256) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                        (identity, field, start, count, descriptor['dtype'], raw, descriptor['sha256']))
        before = self.connection.execute('SELECT field,start_index,sha256,md5(payload) FROM cn_stock_daily_bar WHERE snapshot_id=%s ORDER BY field,start_index', (identity,)).fetchall()
        source = PostgresMarketSource(self.connection)
        snapshot = source.snapshot(identity)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,
                FACTORPOOL_INTERNAL_TOKEN='prepared-synthetic-only', FACTORPOOL_CACHE_ENABLED='true',
                FACTORPOOL_CACHE_TILE_SYMBOLS='2', FACTORPOOL_CACHE_CHUNK_MAX_BYTES='8192'):
            prepared = PreparedMatrices(Path(directory))
            fields = ('return', 'total_turnover', PRESENT_FIELD, MEMBERSHIP_FIELD)
            prepared.prepare(snapshot, source, fields=fields, start=snapshot['dates'][0], end=snapshot['dates'][-1])
            self.assertEqual(source.stats['pg_blocks'], 3)
            redis = grpc_cases.MemoryRedis()
            service = DataService(source=source, cache=Cache(redis), prepared=prepared)
            server = grpc.server(ThreadPoolExecutor(max_workers=4))
            data_pb2_grpc.add_DataServiceServicer_to_server(service, server)
            port = server.add_insecure_port('127.0.0.1:0')
            server.start()
            try:
                with grpc.insecure_channel(f'127.0.0.1:{port}') as channel:
                    client = DataClient(stub=data_pb2_grpc.DataServiceStub(channel), cache=redis)
                    metadata = client.snapshot(identity)
                    with patch.object(source, '_staging_block', side_effect=AssertionError('Prepared read decoded mixed PG block')):
                        chunks = list(matrix_blocks(client, metadata, 0, 8, np.arange(5), fields=fields))
                    for start, count, values in chunks:
                        for n, field in enumerate(fields):
                            np.testing.assert_array_equal(values[n], expected_field(original.matrix, field)[start:start+count])
                    self.assertEqual(source.stats['pg_blocks'], 3)
                    path = Path(directory)/identity/'total_turnover.npy'
                    writable = np.load(path, mmap_mode='r+')
                    writable[0, 0] += 100
                    writable.flush()
                    del writable
                    redis.delete(tile_key(metadata, metadata['blocks'][0], 'total_turnover', 0))
                    repaired = list(matrix_blocks(client, metadata, 0, 3, np.array([0]), fields=('total_turnover',)))
                    np.testing.assert_array_equal(repaired[0][2][0], original.matrix[6, :3, :1])
                    self.assertEqual(source.stats['pg_blocks'], 4)
                    client.close()
            finally:
                server.stop(0).wait()
        after = self.connection.execute('SELECT field,start_index,sha256,md5(payload) FROM cn_stock_daily_bar WHERE snapshot_id=%s ORDER BY field,start_index', (identity,)).fetchall()
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
