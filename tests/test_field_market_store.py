"""Immutable field storage, requested-column reads and atomic native derivation."""
from contextlib import contextmanager
from datetime import date, timedelta
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from factorpool.data_client.contract import FIELDS, FORMAT, pack_block
from services.data_service.market_store import (BASE_DTYPES,
    INPUT_TABLE, STORAGE_FORMAT, TABLES, block_digest, checked_block,
    checked_catalog, decode_field, field_catalog, prepare_snapshot)
from services.data_service.postgres import connect
from services.data_service.postgres.market import PostgresMarketSource


class FieldStorageTests(unittest.TestCase):
    def fixture(self):
        fields = field_catalog(('capitalization',))
        descriptors = {name: dict(dtype=value['dtype'], sha256='a'*64)
                       for name, value in fields.items()}
        block = dict(start_index=0, row_count=2, fields=descriptors,
                     sha256=block_digest(0, 2, descriptors))
        return dict(snapshot_id='fixture', manifest=dict(storage_format=STORAGE_FORMAT,
            field_catalog=fields, field_blocks=[block]), field_catalog=fields,
            field_blocks=[block], blocks=[{key: block[key] for key in ('start_index','row_count','sha256')}],
            symbols=['A','B'])

    def test_field_payload_preserves_missing_zero_and_integer_segment_bits(self):
        floats = np.array([[np.nan, -0.0], [1.25, np.inf]], dtype='<f8')
        segments = np.array([[-1, 0], [2**53+1, 2**63-1]], dtype='<i8')
        for values in (floats, segments):
            raw = values.tobytes()
            actual = decode_field(raw, hashlib.sha256(raw).hexdigest(), 2, 2, values.dtype)
            self.assertEqual(actual.tobytes(), raw)
            self.assertFalse(actual.flags.writeable)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            decode_field(floats.tobytes()[:-1], '0'*64, 2, 2, '<f8')
        invalid = b'\x00\x01\x02\x00'
        with self.assertRaisesRegex(ValueError, 'boolean'):
            decode_field(invalid, hashlib.sha256(invalid).hexdigest(), 2, 2, '|u1')

    def test_ownership_is_fixed_and_block_checksums_anchor_each_field(self):
        snapshot = self.fixture()
        self.assertEqual(checked_catalog(snapshot)['capitalization'], dict(table=INPUT_TABLE, dtype='<f8'))
        self.assertEqual(checked_block(snapshot, snapshot['blocks'][0]), snapshot['field_blocks'][0])
        snapshot['field_catalog']['return']['table'] = INPUT_TABLE
        with self.assertRaisesRegex(ValueError, 'ownership'):
            checked_catalog(snapshot)
        snapshot = self.fixture()
        snapshot['field_blocks'][0]['fields']['__q__']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'pinned'):
            checked_block(snapshot, snapshot['blocks'][0])
        for name in ('return','../path','__invented__','a'*65):
            with self.assertRaises(ValueError):
                field_catalog((name,))

    def test_projected_reader_fetches_one_column_and_rejects_missing_or_corrupt_rows(self):
        snapshot = self.fixture()
        values = np.array([[1., np.nan], [3., 4.]], dtype='<f8')
        raw = values.tobytes()
        checksum = hashlib.sha256(raw).hexdigest()
        descriptor = snapshot['field_blocks'][0]['fields']['total_turnover']
        descriptor['sha256'] = checksum
        updated = block_digest(0, 2, snapshot['field_blocks'][0]['fields'])
        snapshot['field_blocks'][0]['sha256'] = snapshot['blocks'][0]['sha256'] = updated

        class Connection:
            def __init__(self):
                self.queries = []
                self.binary_reads = []
                self.rows = [('total_turnover', 2, '<f8', raw, checksum)]
            @contextmanager
            def transaction(self):
                yield self
            def execute(self, query, args=None, *, binary=False):
                self.queries.append((query,args))
                if query.startswith('SELECT'):
                    self.binary_reads.append(binary)
                return self
            def fetchall(self):
                return self.rows

        connection = Connection()
        source = PostgresMarketSource(connection)
        actual = source.read_fields(snapshot, snapshot['blocks'][0], ('total_turnover',))
        np.testing.assert_array_equal(actual['total_turnover'], values)
        queries = [(query,args) for query,args in connection.queries if query.startswith('SELECT')]
        self.assertEqual(len(queries), 1)
        self.assertIn('cn_stock_daily_bar', queries[0][0])
        self.assertEqual(queries[0][1][-1], ['total_turnover'])
        self.assertEqual(connection.binary_reads, [True])
        self.assertFalse(actual['total_turnover'].flags.writeable)
        with self.assertRaisesRegex(ValueError, 'not supplied'):
            source.read_fields(snapshot, snapshot['blocks'][0], ('not_provided',))
        connection.rows = []
        with self.assertRaisesRegex(ValueError, 'missing'):
            source.read_fields(snapshot, snapshot['blocks'][0], ('total_turnover',))
        connection.rows = [('total_turnover', 2, '<f8', b'0'*len(raw), checksum)]
        with self.assertRaisesRegex(ValueError, 'integrity'):
            source.read_fields(snapshot, snapshot['blocks'][0], ('total_turnover',))


@unittest.skipUnless(os.environ.get('FACTORPOOL_TEST_DB') == '1',
                     'Requires isolated test PostgreSQL service and prebuilt native module')
class FieldMarketDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if (os.environ.get('FACTORPOOL_DB_HOST') != 'test-db'
                or os.environ.get('FACTORPOOL_DB_NAME') != 'factorpool'):
            raise RuntimeError('Field market integration requires host=test-db and dbname=factorpool')
        import _factorpool_market  # Fail before connecting if the fixed native build is missing.
        cls.writer = connect()
        cls.writer.autocommit = True
        cls.reader = connect()
        cls.reader.autocommit = True
        with cls.writer.transaction():
            cls.writer.execute(Path('/opt/factorpool/schema.sql').read_text())
        cls.source = PostgresMarketSource(cls.reader)

    @classmethod
    def tearDownClass(cls):
        for name in ('reader','writer'):
            if hasattr(cls,name):
                getattr(cls,name).close()

    def setUp(self):
        self.writer.execute('TRUNCATE market_snapshots CASCADE')
        from psycopg.types.json import Jsonb
        self.values = np.ones((len(FIELDS), 6, 2), dtype='<f8')
        self.values[0:5] = 10
        self.values[FIELDS.index('close'), :, 0] = [100,100,100,44,45,50]
        self.values[FIELDS.index('prev_close'), :, 0] = [100,90,80,40,44,45]
        self.values[FIELDS.index('volume'), :, 0] = [10,0,0,10,np.nan,10]
        self.values[FIELDS.index('total_turnover'), :, 0] = [1000,0,0,440,450,500]
        self.values[FIELDS.index('close'), :, 1] = np.arange(10,16)
        self.values[FIELDS.index('prev_close'), :, 1] = np.arange(9,15)
        self.values[FIELDS.index('volume'), :, 1] = 10
        self.values[FIELDS.index('total_turnover'), :, 1] = np.arange(10,16)*10
        self.values[FIELDS.index('return')] = 99  # Old immutable derivation must remain 99.
        self.values[FIELDS.index('member')] = 1
        self.values[FIELDS.index('is_st')] = 0
        self.values[FIELDS.index('is_suspended')] = 0
        self.values[FIELDS.index('is_suspended'),1:3,0] = 1
        self.parent_id = 'field-storage-raw-fixture'
        dates = [(date(2024,1,2)+timedelta(days=index)).isoformat() for index in range(6)]
        with self.writer.transaction():
            self.writer.execute('INSERT INTO market_snapshots(id,manifest,symbols,dates,fields) VALUES(%s,%s,%s,%s,%s)',
                (self.parent_id, Jsonb(dict(format=FORMAT, return_policy=dict(version='fixed-legacy'))),
                 Jsonb(['A','B']), Jsonb(dates), Jsonb(list(FIELDS))))
            for start in range(0,6,2):
                payload, checksum = pack_block(self.values[:,start:start+2])
                self.writer.execute('INSERT INTO market_matrix_blocks(snapshot_id,start_index,row_count,payload,sha256) VALUES(%s,%s,%s,%s,%s)',
                    (self.parent_id,start,2,payload,checksum))
        self.before = self.writer.execute('SELECT start_index,sha256,md5(payload) FROM market_matrix_blocks WHERE snapshot_id=%s ORDER BY start_index', (self.parent_id,)).fetchall()

    def prepare(self, **kwargs):
        return prepare_snapshot(self.parent_id, connection=self.writer, source=self.source, **kwargs)

    def assert_parent_unchanged(self):
        self.assertEqual(self.writer.execute('SELECT start_index,sha256,md5(payload) FROM market_matrix_blocks WHERE snapshot_id=%s ORDER BY start_index', (self.parent_id,)).fetchall(), self.before)
        legacy = self.source.snapshot(self.parent_id)
        np.testing.assert_array_equal(self.source.original_block(legacy,legacy['blocks'][0]), self.values[:,:2])

    def all_fields(self, snapshot, fields):
        blocks = [self.source.read_fields(snapshot,block,fields) for block in snapshot['blocks']]
        return {field: np.concatenate([block[field] for block in blocks]) for field in fields}

    def test_four_tables_native_return_and_resume_q_are_published_atomically_and_reused(self):
        first = self.prepare()
        self.assertTrue(first['created'])
        self.assertNotEqual(first['snapshot_id'],self.parent_id)
        snapshot = self.source.snapshot(first['snapshot_id'])
        self.assertEqual(snapshot['manifest']['return_policy']['version'],'volume-zero-v1')
        self.assertEqual(snapshot['manifest']['adjustment_policy']['version'],'prev-close-last-actual-v2')
        self.assertEqual(snapshot['manifest']['storage_format'],STORAGE_FORMAT)
        self.assertEqual(set(snapshot['field_catalog']),set(BASE_DTYPES))
        values = self.all_fields(snapshot,('return','__q__','__segment__','__adj_factor__','__adj_known__'))
        np.testing.assert_allclose(values['return'][:,0],[0,0,0,.1,np.nan,50/45-1],rtol=0,atol=1e-15,equal_nan=True)
        np.testing.assert_allclose(values['__q__'][:,0],[1,np.nan,np.nan,.4,np.nan,1],rtol=0,atol=1e-15,equal_nan=True)
        self.assertEqual(values['__segment__'].dtype,np.dtype('<i8'))
        np.testing.assert_array_equal(values['__segment__'][:,0],[0,-1,-1,0,-1,1])
        np.testing.assert_array_equal(values['__adj_known__'][:,0],[0,0,0,1,0,0])
        self.assertEqual(self.writer.execute('SELECT count(*) FROM cn_stock_factor_input').fetchone()[0],0)
        self.assertEqual(self.writer.execute('SELECT count(*) FROM market_matrix_blocks').fetchone()[0],3)
        second = self.prepare()
        self.assertFalse(second['created'])
        self.assertEqual(second['snapshot_id'],first['snapshot_id'])
        self.assert_parent_unchanged()
        # Pointing explicitly at the already-prepared dataset validates/reuses it.
        again = prepare_snapshot(first['snapshot_id'], connection=self.writer, source=self.source)
        self.assertFalse(again['created'])
        self.assertEqual(again['snapshot_id'],first['snapshot_id'])

    def test_raw_and_field_payloads_use_binary_wire_format_with_exact_bytes(self):
        result = self.prepare()
        raw_snapshot = self.source.snapshot(self.parent_id)
        snapshot = self.source.snapshot(result['snapshot_id'])
        execute = self.reader.execute
        observed = []
        def track(query, args=None, **kwargs):
            cursor = execute(query, args, **kwargs)
            if query.startswith('SELECT') and 'payload' in query:
                payload_index = [column.name for column in cursor.description].index('payload')
                observed.append(cursor.pgresult.fformat(payload_index))
            return cursor
        with patch.object(self.reader, 'execute', side_effect=track):
            original = self.source.original_block(raw_snapshot, raw_snapshot['blocks'][0])
            columns = self.source.read_fields(snapshot, snapshot['blocks'][0], ('close','__segment__'))
        self.assertEqual(observed, [1,1,1])
        self.assertEqual(original.tobytes(), self.values[:,:2].tobytes())
        self.assertEqual(columns['close'].tobytes(), self.values[FIELDS.index('close'),:2].tobytes())
        self.assertEqual(columns['__segment__'].dtype, np.dtype('<i8'))
        np.testing.assert_array_equal(columns['__segment__'], [[0,0],[-1,0]])
        self.assert_parent_unchanged()

    def test_supplied_inputs_are_exact_and_change_snapshot_identity_without_invented_fields(self):
        extra = np.arange(12,dtype=np.float64).reshape(6,2)
        extra[2,1] = np.nan
        first = self.prepare(factor_inputs={'capitalization':extra})
        snapshot = self.source.snapshot(first['snapshot_id'])
        np.testing.assert_array_equal(self.all_fields(snapshot,('capitalization',))['capitalization'],extra)
        self.assertEqual(snapshot['field_catalog']['capitalization']['table'],INPUT_TABLE)
        with self.assertRaisesRegex(ValueError,'not supplied'):
            self.source.read_fields(snapshot,snapshot['blocks'][0],('industry',))
        different = extra.copy()
        different[0,0] = 123
        second = self.prepare(factor_inputs={'capitalization':different})
        self.assertNotEqual(first['snapshot_id'],second['snapshot_id'])
        self.assert_parent_unchanged()

    def test_real_postgres_grpc_cache_reads_typed_fields_without_loading_q_for_turnover(self):
        from concurrent.futures import ThreadPoolExecutor
        import grpc
        from factorpool.data_client import DataClient
        from factorpool.data_client.generated import data_pb2_grpc
        from services.data_service.api import DataService
        from services.data_service.redis_cache import Cache
        from test_data_service_boundary import MemoryRedis
        result = self.prepare(factor_inputs={'capitalization':np.arange(12).reshape(6,2)})
        with patch.dict(os.environ, FACTORPOOL_INTERNAL_TOKEN='field-storage-test-only',
                        FACTORPOOL_CACHE_ENABLED='true', FACTORPOOL_CACHE_TILE_SYMBOLS='2',
                        FACTORPOOL_CACHE_CHUNK_MAX_BYTES='8192'):
            redis = MemoryRedis()
            service = DataService(source=self.source,cache=Cache(redis))
            server = grpc.server(ThreadPoolExecutor(max_workers=2))
            data_pb2_grpc.add_DataServiceServicer_to_server(service,server)
            port = server.add_insecure_port('127.0.0.1:0')
            server.start()
            try:
                with grpc.insecure_channel(f'127.0.0.1:{port}') as channel:
                    client = DataClient(stub=data_pb2_grpc.DataServiceStub(channel),cache=redis)
                    snapshot = client.snapshot(result['snapshot_id'])
                    with patch.object(self.source,'original_block',side_effect=AssertionError('new storage loaded full legacy block')), \
                         patch.object(self.source,'read_fields',wraps=self.source.read_fields) as reads:
                        chunks = list(client.selected_columns(snapshot,0,6,np.array([1,0]),fields=('total_turnover',)))
                        self.assertEqual(len(reads.call_args_list),3)
                        self.assertTrue(all(call.args[2] == ('total_turnover',) for call in reads.call_args_list))
                        turnover = np.concatenate([block['total_turnover'] for _,_,block in chunks])
                        np.testing.assert_array_equal(turnover,self.values[FIELDS.index('total_turnover'),:,[1,0]].T)
                        warm = list(client.selected_columns(snapshot,0,6,np.array([1,0]),fields=('total_turnover',)))
                        self.assertEqual(len(reads.call_args_list),3)
                        self.assertEqual([block['total_turnover'].tobytes() for _,_,block in chunks],
                                         [block['total_turnover'].tobytes() for _,_,block in warm])
                        typed = list(client.selected_columns(snapshot,0,6,np.array([0,1]),
                            fields=('__segment__','__q__','__adj_known__','capitalization')))
                        for _,_,block in typed:
                            self.assertEqual(block['__segment__'].dtype,np.dtype('<i8'))
                            self.assertEqual(block['__adj_known__'].dtype,np.dtype('|u1'))
                        np.testing.assert_array_equal(np.concatenate([block['capitalization'] for _,_,block in typed]),np.arange(12).reshape(6,2))
                    client.close()
            finally:
                server.stop(0).wait()
        self.assert_parent_unchanged()

    def test_interruption_rolls_back_every_new_field_and_snapshot(self):
        def interrupt(progress):
            self.assertEqual(self.reader.execute('SELECT count(*) FROM market_snapshots').fetchone()[0],1)
            if progress['completed_dates'] >= 4:
                raise RuntimeError('synthetic preparation interruption')
        with self.assertRaisesRegex(RuntimeError,'synthetic preparation'):
            self.prepare(progress=interrupt)
        self.assertEqual(self.writer.execute('SELECT count(*) FROM market_snapshots').fetchone()[0],1)
        for table in TABLES:
            self.assertEqual(self.writer.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)
        self.assertTrue(self.prepare()['created'])
        self.assert_parent_unchanged()

    def test_corrupt_or_missing_field_is_rejected_on_read_and_idempotent_retry(self):
        result = self.prepare()
        snapshot = self.source.snapshot(result['snapshot_id'])
        self.writer.execute('UPDATE cn_stock_daily_return SET payload=%s WHERE snapshot_id=%s AND start_index=0',
                            (b'0'*32,result['snapshot_id']))
        with self.assertRaisesRegex(ValueError,'integrity'):
            self.source.read_fields(snapshot,snapshot['blocks'][0],('return',))
        self.writer.execute('DELETE FROM cn_stock_daily_return WHERE snapshot_id=%s AND start_index=0',(result['snapshot_id'],))
        with self.assertRaisesRegex(ValueError,'missing'):
            self.source.read_fields(snapshot,snapshot['blocks'][0],('return',))
        with self.assertRaisesRegex(ValueError,'missing or inconsistent'):
            self.prepare()
        self.assert_parent_unchanged()

    def test_mutating_supplied_input_during_preparation_rolls_back(self):
        extra = np.ones((6,2),dtype=np.float64)
        def modify(progress):
            if progress['completed_dates'] == 2:
                extra[0,0] = 2
        with self.assertRaisesRegex(ValueError,'changed during preparation'):
            self.prepare(factor_inputs={'capitalization':extra},progress=modify)
        self.assertEqual(self.writer.execute('SELECT count(*) FROM market_snapshots').fetchone()[0],1)
        self.assert_parent_unchanged()
