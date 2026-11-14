"""Published market bytes are verified once before the RPC server is ready."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

import numpy as np

from factorpool.data_client.contract import FIELDS, FIELD_BLOCK_FORMAT
from services.data_service import app
from services.data_service.market_store import TABLES, block_digest, decode_field
from services.data_service.postgres.market import PostgresMarketSource
from test_prepared_matrices import OriginalBlocks
from test_typed_market_bridge import ColumnSource


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class PublishedMarket:
    """Read-only SQL fixture with several dates/fields and no write surface."""
    def __init__(self, *, table=True, populated=True):
        self.table, self.queries = table, []
        self.snapshots, self.original, self.columns = {}, {}, {}
        if not populated:
            return
        old, typed = OriginalBlocks('original'), ColumnSource()
        raw = deepcopy(old.metadata)
        for key in ('storage_format', 'field_catalog', 'field_blocks'):
            raw['manifest'].pop(key, None)
            raw.pop(key, None)
        self.snapshots['original'] = raw
        self.snapshots['typed'] = deepcopy(typed.metadata)
        for block in old.metadata['blocks']:
            self.original['original', block['start_index']] = (
                block['row_count'], old.payloads[block['start_index']], block['sha256'])
        indexed = []
        for start in range(0, 9, 3):
            fields = {}
            for field, values in typed.columns.items():
                raw = values[start:start+3].tobytes()
                sha = hashlib.sha256(raw).hexdigest()
                dtype = values.dtype.str
                fields[field] = dict(dtype=dtype, sha256=sha)
                table = typed.metadata['field_catalog'][field]['table']
                self.columns[table, 'typed', field, start] = (3, dtype, raw, sha)
            indexed.append(dict(start_index=start, row_count=3, fields=fields,
                                sha256=block_digest(start, 3, fields)))
        metadata = self.snapshots['typed']
        metadata['blocks'] = [{key: block[key] for key in ('start_index', 'row_count', 'sha256')}
                              for block in indexed]
        metadata['field_blocks'] = metadata['manifest']['field_blocks'] = indexed

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, query, args=(), *, binary=False):
        self.queries.append((query, args, binary))
        if query == 'SET TRANSACTION READ ONLY':
            return Cursor([])
        if not query.startswith('SELECT '):
            raise AssertionError('Startup validation attempted a database write')
        if 'to_regclass' in query:
            return Cursor([('market_snapshots' if self.table else None,)])
        if query.startswith('SELECT id FROM market_snapshots WHERE'):
            return Cursor([(identity,) for identity in sorted(self.snapshots)
                           if self.snapshots[identity]['manifest'].get('storage_format') == args[0]])
        if query.startswith('SELECT id,manifest,symbols,dates,fields,created_at'):
            identity = args[0] if args else sorted(self.snapshots)[-1]
            metadata = deepcopy(self.snapshots[identity])
            return Cursor([(identity, metadata['manifest'], metadata['symbols'], metadata['dates'],
                            list(FIELDS), '2024-01-02T00:00:00+00:00')])
        if query.startswith('SELECT start_index,row_count,sha256 FROM market_matrix_blocks'):
            return Cursor([(start, row[0], row[2]) for (identity, start), row in self.original.items()
                           if identity == args[0]])
        if query.startswith('SELECT row_count,payload,sha256 FROM market_matrix_blocks'):
            return Cursor([self.original[args[0], args[1]]])
        table = query.split('FROM ', 1)[1].split()[0]
        if table not in TABLES:
            raise AssertionError('Field query escaped the fixed market tables')
        if query.startswith('SELECT field,start_index,row_count,dtype,sha256'):
            return Cursor([(field, start, row[0], row[1], row[3])
                           for (owner, identity, field, start), row in self.columns.items()
                           if owner == table and identity == args[0]])
        if query.startswith('SELECT field,row_count,dtype,payload,sha256'):
            # Payload reads must stay bounded to one date block.
            self.assert_block_query(query, args)
            return Cursor([(field, *row) for (owner, identity, field, start), row in self.columns.items()
                           if owner == table and identity == args[0] and start == args[1] and field in args[2]])
        raise AssertionError('Unexpected market query: '+query)

    @staticmethod
    def assert_block_query(query, args):
        if 'start_index=%s' not in query or len(args) != 3:
            raise AssertionError('Startup read attempted to collect full-history payloads')


class MarketStartupTests(unittest.TestCase):
    def test_service_default_reader_does_not_repeat_startup_verification(self):
        from services.data_service.api import DataService
        service = DataService(cache=Mock(), values=Mock(), prepared=Mock())
        self.assertFalse(service.source.verify_reads)

    def test_absent_schema_and_empty_market_start_without_imports(self):
        for table in (False, True):
            connection = PublishedMarket(table=table, populated=False)
            callback = Mock()
            counts = PostgresMarketSource(connection, verify_reads=False).validate(callback)
            self.assertEqual(counts, dict(snapshots=0, original_blocks=0, field_blocks=0))
            callback.assert_not_called()

    def test_current_fields_are_verified_readonly_and_staging_snapshots_are_skipped(self):
        connection = PublishedMarket()
        callback = Mock()
        counts = PostgresMarketSource(connection, verify_reads=False).validate(callback)
        self.assertEqual(counts, dict(snapshots=1, original_blocks=0, field_blocks=54))
        self.assertEqual([call.args[0]['snapshot_id'] for call in callback.call_args_list],
                         ['typed'])
        payload_queries = [entry for entry in connection.queries if 'payload' in entry[0]]
        self.assertTrue(payload_queries)
        self.assertTrue(all(binary for _, _, binary in payload_queries))
        self.assertFalse(any('market_matrix_blocks' in query for query, _, _ in payload_queries))

    def test_startup_does_not_trust_fast_mode_for_corrupt_payloads_or_index(self):
        for storage in ('typed', 'index', 'status'):
            with self.subTest(storage=storage):
                connection = PublishedMarket()
                if storage == 'status':
                    key = ('cn_stock_daily_adj_factor', 'typed', '__adj_known__', 0)
                    count, dtype, payload, _ = connection.columns[key]
                    payload = b'\x02'+payload[1:]
                    sha = hashlib.sha256(payload).hexdigest()
                    connection.columns[key] = count, dtype, payload, sha
                    metadata = connection.snapshots['typed']
                    indexed = metadata['field_blocks'][0]
                    indexed['fields']['__adj_known__']['sha256'] = sha
                    indexed['sha256'] = block_digest(0, 3, indexed['fields'])
                    metadata['blocks'][0]['sha256'] = indexed['sha256']
                else:
                    key = next(iter(connection.columns))
                    count, dtype, payload, sha = connection.columns[key]
                    connection.columns[key] = (count, dtype, payload if storage == 'index' else
                        payload[:-1]+bytes([payload[-1]^1]), '0'*64 if storage == 'index' else sha)
                with self.assertRaises((ValueError, zlib.error)):
                    PostgresMarketSource(connection, verify_reads=False).validate()

    def test_runtime_reads_preserve_bytes_without_repeating_startup_scans(self):
        connection = PublishedMarket()
        source = PostgresMarketSource(connection, verify_reads=False)
        source.validate()
        with patch('services.data_service.postgres.market.checked_catalog', side_effect=AssertionError('catalog rescan')), \
                patch('services.data_service.postgres.market.checked_block', side_effect=AssertionError('index rescan')), \
                patch('services.data_service.postgres.market.validate_snapshot', side_effect=AssertionError('metadata rescan')), \
                patch('hashlib.sha256', side_effect=AssertionError('SHA rescan')), \
                patch('numpy.any', side_effect=AssertionError('status rescan')):
            typed = source.snapshot('typed')
            block = typed['blocks'][0]
            fields = ('volume', '__segment__', '__adj_known__')
            actual = source.read_fields(typed, block, fields)
            for field in fields:
                table = typed['field_catalog'][field]['table']
                self.assertEqual(actual[field].tobytes(), connection.columns[table, 'typed', field, 0][2])
                self.assertFalse(actual[field].flags.writeable)
            from services.data_service.api import DataService
            service = DataService(source=source, cache=Mock(), values=Mock(), prepared=Mock())
            with self.assertRaisesRegex(ValueError, 'current field-blocks-v1'):
                service.snapshot('original')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            decode_field(b'\x00'*7, '0'*64, 1, 1, '<f8', verify=False)

    def test_fast_field_reads_keep_fixed_sql_routing_and_shape_checks(self):
        connection = PublishedMarket()
        source = PostgresMarketSource(connection, verify_reads=False)
        typed = source.snapshot('typed')
        typed['field_catalog']['volume']['table'] = 'unrelated_table'
        source.read_fields(typed, typed['blocks'][0], ('volume',))
        self.assertIn('FROM cn_stock_daily_bar', connection.queries[-1][0])
        key = ('cn_stock_daily_bar', 'typed', 'volume', 0)
        count, dtype, raw, sha = connection.columns[key]
        connection.columns[key] = count, dtype, raw[:-1], sha
        with self.assertRaisesRegex(ValueError, 'integrity'):
            source.read_fields(typed, typed['blocks'][0], ('volume',))

    def test_app_starts_rpc_only_after_validation_and_not_on_failure(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                order = []
                service = SimpleNamespace(source=Mock(), prepared=Mock())
                def validate(**kwargs):
                    self.assertIs(kwargs['check_prepared'], service.prepared.validate)
                    order.append('validate')
                    if failed:
                        raise ValueError('corrupt published market')
                    return dict(snapshots=0, original_blocks=0, field_blocks=0)
                service.source.validate.side_effect = validate
                server = Mock()
                server.start.side_effect = lambda: order.append('start')
                with patch.dict(os.environ, FACTORPOOL_INTERNAL_TOKEN='isolated-startup-token'), \
                        patch('sys.argv', ['data-service']), \
                        patch('services.data_service.api.DataService', return_value=service), \
                        patch('grpc.server', return_value=server) as create, \
                        patch('factorpool.data_client.generated.data_pb2_grpc.add_DataServiceServicer_to_server'), \
                        patch('signal.signal'):
                    if failed:
                        with self.assertRaisesRegex(ValueError, 'corrupt published market'):
                            app.main()
                        create.assert_not_called()
                    else:
                        app.main()
                        server.wait_for_termination.assert_called_once()
                self.assertEqual(order, ['validate'] if failed else ['validate', 'start'])

    def test_health_probe_does_not_repeat_market_validation(self):
        @contextmanager
        def channel(address):
            yield object()
        stub = Mock()
        stub.Health.return_value = SimpleNamespace(json=b'{"status":"UP"}')
        with patch.dict(os.environ, FACTORPOOL_INTERNAL_TOKEN='isolated-startup-token'), \
                patch('sys.argv', ['data-service', '--health']), \
                patch('grpc.insecure_channel', side_effect=channel), \
                patch('factorpool.data_client.generated.data_pb2_grpc.DataServiceStub', return_value=stub), \
                patch('services.data_service.api.DataService') as service:
            app.main()
            service.assert_not_called()
