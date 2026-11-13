"""Typed field blocks cross real RPC/cache without loading unrelated columns."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from factorpool import market_loader
from factorpool.data_client.contract import (ADJUSTMENT_FIELDS, FIELDS, FIELD_BLOCK_FORMAT,
    MEMBERSHIP_FIELD, PRESENT_FIELD, SERVED_FIELDS, decode_tile, encode_tile, field_dtype,
    json_bytes, market_field, tile_key, validate_snapshot)
import test_data_service_boundary as grpc_cases
from test_data_service_boundary import OriginalSource
from services.data_service.prepared_matrices import PreparedMatrices


class ColumnSource(OriginalSource):
    def __init__(self):
        super().__init__('typed')
        self.columns = {field: np.ascontiguousarray(market_field(self.matrix, field),
                        dtype='|u1' if field in (PRESENT_FIELD, MEMBERSHIP_FIELD) else '<f8')
                        for field in SERVED_FIELDS}
        shape = self.matrix.shape[1:]
        self.columns.update({'__q__': np.full(shape, .8, dtype='<f8'),
                             '__segment__': np.full(shape, 2**53+7, dtype='<i8'),
                             '__adj_factor__': np.ones(shape, dtype='<f8'),
                             '__adj_known__': np.ones(shape, dtype='|u1'),
                             'market_cap': np.arange(36, dtype='<f8').reshape(shape)*1000.})
        self.columns['__q__'][3, 2] = np.nan
        self.columns['__segment__'][3, 2] = -1
        self.columns['__adj_known__'][3, 2] = 0
        catalog = {}
        for field, array in self.columns.items():
            table = ('cn_stock_daily_return' if field == 'return' else
                     'cn_stock_daily_adj_factor' if field in ADJUSTMENT_FIELDS else
                     'cn_stock_factor_input' if field == 'market_cap' else 'cn_stock_daily_bar')
            catalog[field] = dict(table=table, dtype=array.dtype.str)
        descriptors = {field: dict(dtype=array.dtype.str, sha256=hashlib.sha256(array.tobytes()).hexdigest())
                       for field, array in self.columns.items()}
        block = self.metadata['blocks'][0]
        block['sha256'] = hashlib.sha256(json_bytes(dict(start_index=0, row_count=9, fields=descriptors))).hexdigest()
        indexed = [dict(block, fields=descriptors)]
        self.metadata['manifest'].update(storage_format=FIELD_BLOCK_FORMAT, field_catalog=catalog,
                                          field_blocks=indexed, adjustment_policy={'version': 'test-only'})
        self.metadata.update(field_catalog=catalog, field_blocks=indexed)
        self.requests = []

    def original_block(self, snapshot, block):
        raise AssertionError('Column reads must not reconstruct a full market block')

    def read_fields(self, snapshot, block, fields):
        self.requests.append(tuple(fields))
        self.stats['pg_blocks'] += 1
        return {field: self.columns[field] for field in fields}


class TypedMarketBridgeTests(unittest.TestCase):
    setUp = grpc_cases.GrpcBoundaryTests.setUp
    client = grpc_cases.GrpcBoundaryTests.client

    def _source(self):
        self.source = ColumnSource()
        self.service.source = self.source
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.service.prepared = PreparedMatrices(temporary.name)
        return self.source

    def test_optional_prepared_q_keeps_types_and_rejects_invalid_known_status(self):
        source = self._source()
        client = self.client()
        metadata = client.snapshot('typed')
        prepared = self.service.prepared
        stats = prepared.prepare(metadata, source, fields=ADJUSTMENT_FIELDS,
                                 start=metadata['dates'][0], end=metadata['dates'][-1])
        self.assertEqual(stats['blocks_prepared'], len(ADJUSTMENT_FIELDS))
        source.requests.clear()
        with patch.object(source, 'read_fields', side_effect=AssertionError('Prepared read queried storage')):
            actual = list(client.selected_columns(metadata, 0, 9, [3, 0], fields=ADJUSTMENT_FIELDS))[0][2]
        for field in ADJUSTMENT_FIELDS:
            self.assertEqual(actual[field].dtype, source.columns[field].dtype)
            self.assertEqual(actual[field].tobytes(), source.columns[field][:, [3, 0]].tobytes())
        self.assertEqual(source.requests, [])
        self.assertEqual(prepared.stats['hits'], len(ADJUSTMENT_FIELDS)*2)
        unrelated = deepcopy(metadata)
        unrelated['snapshot_id'] = 'another-immutable-dataset'
        self.assertEqual(prepared.tiles(unrelated, metadata['blocks'][0], [('__segment__', 0)]), {})

        # A self-consistent optional-cache checksum cannot make an invalid u1
        # status authoritative. Drop just this field's Redis tiles to exercise
        # the prepared-file check and precise PostgreSQL-column fallback.
        directory = Path(prepared.root) / metadata['snapshot_id']
        path = directory / '__adj_known__.npy'
        values = np.load(path, mmap_mode='r+')
        values[0, 0] = 2
        values.flush()
        checksum = hashlib.sha256(values.tobytes()).hexdigest()
        values._mmap.close()
        marker = directory / 'manifest.json'
        publication = json.loads(marker.read_text())
        publication['fields']['__adj_known__']['blocks']['0']['sha256'] = checksum
        marker.write_text(json.dumps(publication))
        for start in (0, 2):
            self.cache.values.pop(tile_key(metadata, metadata['blocks'][0], '__adj_known__', start))
        repaired = list(client.selected_columns(metadata, 0, 9, [3, 0], fields=('__adj_known__',)))[0][2]
        np.testing.assert_array_equal(repaired['__adj_known__'], source.columns['__adj_known__'][:, [3, 0]])
        self.assertEqual(source.requests, [('__adj_known__',)])
        self.assertEqual(prepared.stats['corrupt'], 1)

    def test_integer_segments_and_boolean_columns_keep_exact_types_over_rpc_and_cache(self):
        source = self._source()
        client = self.client()
        metadata = client.snapshot('typed')
        fields = ('total_turnover', PRESENT_FIELD, '__segment__', '__adj_known__')
        columns = [3, 0, 3, 2]
        actual = list(client.selected_columns(metadata, 0, 9, columns, fields=fields))[0][2]
        for field in fields:
            np.testing.assert_array_equal(actual[field], source.columns[field][:, columns])
            self.assertEqual(actual[field].dtype, source.columns[field].dtype)
            self.assertFalse(actual[field].flags.writeable)
        self.assertEqual(set(source.requests[0]), set(fields))
        self.assertEqual(int(actual['__segment__'][0, 0]), 2**53+7)
        with patch.object(client.stub, 'MarketBlocks', side_effect=AssertionError('Hot read proxied')):
            cached = list(client.selected_columns(metadata, 0, 9, columns, fields=fields))[0][2]
        for field in fields:
            np.testing.assert_array_equal(cached[field], actual[field])

    def test_turnover_and_extra_inputs_do_not_fetch_q_and_prices_only_fetch_persisted_q(self):
        source = self._source()
        config = dict(snapshot_id='typed', factor_start='2024-01-03', factor_end='2024-01-07',
                      universe=['D', 'A'])
        turnover = market_loader.load(config, 3, 2, source=self.client(), matrix_only=True,
                                       fields=['total_turnover', 'market_cap'])
        self.assertEqual(turnover['quotes']['fields'], ('total_turnover', 'market_cap'))
        self.assertNotIn('adjustment', turnover['quotes'])
        fetched = {field for request in source.requests for field in request}
        self.assertEqual(fetched, {'total_turnover', 'market_cap', PRESENT_FIELD, MEMBERSHIP_FIELD, 'return'})
        np.testing.assert_array_equal(turnover['quotes']['values'][1], source.columns['market_cap'][:7, [0, 3]])
        source.requests.clear()
        prices = market_loader.load(config, 3, 2, source=self.client(), matrix_only=True,
                                    fields=['open', 'volume'])
        quotes = prices['quotes']
        self.assertEqual(quotes['fields'], ('open', 'volume'))
        for key, field in zip(('q', 'segment', 'factor', 'known'), ADJUSTMENT_FIELDS):
            np.testing.assert_array_equal(quotes['adjustment'][key], source.columns[field][:7, [0, 3]])
            self.assertFalse(quotes['adjustment'][key].flags.writeable)
        self.assertEqual(quotes['adjustment']['segment'].dtype, np.dtype('<i8'))
        self.assertEqual(quotes['adjustment']['known'].dtype, np.dtype('|u1'))
        fetched = {field for request in source.requests for field in request}
        self.assertEqual(fetched, {'open', 'volume', *ADJUSTMENT_FIELDS})
        self.assertEqual(prices['returns'].shape, (9, 2))
        self.assertEqual(quotes['adjustment']['q'].shape, (7, 2))
        self.assertIs(quotes['source_member'], quotes['membership'])

    def test_catalog_and_cache_dtype_corruption_fail_or_repair_explicitly(self):
        source = self._source()
        client = self.client()
        metadata = client.snapshot('typed')
        block = metadata['blocks'][0]
        fields = ('__segment__', '__adj_known__')
        expected = list(client.selected_columns(metadata, 0, 9, [0], fields=fields))[0][2]
        key = tile_key(metadata, block, '__segment__', 0)
        self.cache.values[key] = self.cache.values[key].replace(b'"<i8"', b'"<f8"')
        repaired = list(client.selected_columns(metadata, 0, 9, [0], fields=fields))[0][2]
        np.testing.assert_array_equal(repaired['__segment__'], expected['__segment__'])
        self.assertEqual(client.stats['corrupt_blocks'], 1)
        with self.assertRaisesRegex(ValueError, 'boolean'):
            encode_tile(metadata, block, '__adj_known__', 0, np.full((9, 2), 2))
        with self.assertRaisesRegex(ValueError, 'shape'):
            encode_tile(metadata, block, '__segment__', 0, np.zeros((8, 2), dtype='<i8'))
        with self.assertRaisesRegex(ValueError, 'Invalid requested market fields'):
            list(client.selected_columns(metadata, 0, 9, [0], fields=('unknown_financial',)))
        for changed in ('dtype', 'index'):
            broken = deepcopy(metadata)
            if changed == 'dtype':
                broken['manifest']['field_catalog']['__segment__']['dtype'] = '<f8'
            else:
                broken['manifest']['field_blocks'][0]['fields']['__q__']['sha256'] = 'bad'
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_snapshot(broken, 'typed')
        with self.assertRaisesRegex(ValueError, 'Unknown market field'):
            field_dtype(OriginalSource().metadata, '__q__')


if __name__ == '__main__':
    unittest.main()
