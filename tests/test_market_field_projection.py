"""Field projection preserves original axes/status and constrains actual memory."""
from datetime import date, timedelta
import hashlib
import os
import unittest
from unittest.mock import patch

import numpy as np

from factorpool import market_loader as store
from factorpool.data_client.contract import (FIELDS, MEMBERSHIP_FIELD, PRESENT_FIELD,
    decode_tile, encode_tile, tile_key)
from test_market_loading import _Connection
from market_fixtures import matrix_blocks
import test_data_service_boundary as grpc_cases


class FieldProjectionTests(unittest.TestCase):
    def setUp(self):
        self.matrix = np.arange(len(FIELDS)*9*4, dtype=float).reshape(len(FIELDS), 9, 4)
        self.matrix[8] = 1
        self.matrix[8, :2, 1] = 0
        self.matrix[:7, 3, 2] = np.nan
        self.matrix[6, 4, 0] = np.nan  # Observed bar whose requested turnover is missing.
        self.data = dict(snapshot_id='synthetic', factor_start='2024-01-03', factor_end='2024-01-07')

    def test_projected_fields_match_full_quotes_and_original_presence_for_both_axes(self):
        for pasteurization in ('ON', 'OFF'):
            for universe in (['D', 'A'], 'all_a_dynamic'):
                with self.subTest(pasteurization=pasteurization, universe=universe):
                    data = dict(self.data, universe=universe)
                    expected = store.load(data, 3, 2, source=_Connection(self.matrix),
                                          pasteurization=pasteurization)
                    actual = store.load(data, 3, 2, source=_Connection(self.matrix),
                                        pasteurization=pasteurization, fields=['total_turnover'])
                    self.assertEqual(actual['symbols'], expected['symbols'])
                    np.testing.assert_array_equal(actual['returns'], expected['returns'])
                    self.assertEqual(actual['quotes']['fields'], ('total_turnover',))
                    np.testing.assert_array_equal(actual['quotes']['values'][0], expected['quotes']['values'][6])
                    np.testing.assert_array_equal(actual['quotes']['present'],
                                                  np.any(np.isfinite(expected['quotes']['values'][:7]), axis=0))
                    np.testing.assert_array_equal(actual['quotes']['membership'], expected['quotes']['values'][8] > 0)
                    for index, (frame, original) in enumerate(zip(actual['frames'], expected['frames'])):
                        for key in ('date', 'symbols', 'research_symbols', 'session_index'):
                            self.assertEqual(frame[key], original[key])
                        members = set(original['research_symbols'])
                        self.assertEqual(actual['membership'][index].tolist(), [s in members for s in actual['symbols']])
                        if frame['symbols']:
                            self.assertEqual([actual['quotes']['symbols'][int(n)] for n in frame['symbol_columns']], frame['symbols'])
                    for key in ('values', 'present', 'membership'):
                        self.assertFalse(actual['quotes'][key].flags.writeable)
                    self.assertFalse(np.shares_memory(actual['returns'], actual['quotes']['values']))

    def test_eager_axes_keep_original_string_identity_and_dynamic_presence_hash(self):
        class NamedSource(_Connection):
            def __init__(self, values):
                super().__init__(values)
                self.symbols = [f'TICKER_{n:03d}_{symbol}' for n, symbol in enumerate(self.symbols)]

        matrix = self.matrix.copy()
        matrix[8, :, 3] = 0  # OFF keeps this calculation column but dynamic research drops it.
        matrix[8, 5] = 0  # Exercise a genuinely empty historical market axis.
        matrix[8, 6] = (1, 0, 1, 0)
        for pasteurization in ('ON', 'OFF'):
            for dynamic in (False, True):
                with self.subTest(pasteurization=pasteurization, dynamic=dynamic):
                    source = NamedSource(matrix)
                    universe = ('all_a_dynamic' if dynamic else
                                [''.join((source.symbols[n][:-1], source.symbols[n][-1])) for n in (0, 3)])
                    data = dict(self.data, universe=universe)
                    expected = store.load(data, 3, 2, source=NamedSource(matrix),
                                          pasteurization=pasteurization)
                    actual = store.load(data, 3, 2, source=source, pasteurization=pasteurization,
                                        fields=['total_turnover'])
                    for current, old in zip(actual['frames'], expected['frames']):
                        self.assertIsInstance(current['symbols'], list)
                        self.assertIsInstance(current['research_symbols'], list)
                        self.assertEqual(current['symbols'], old['symbols'])
                        self.assertEqual(current['research_symbols'], old['research_symbols'])
                        for symbol in current['symbols']:
                            self.assertIs(symbol, source.symbols[source.symbols.index(symbol)])
                        if current['symbols']:
                            self.assertFalse(current['symbol_columns'].flags.writeable)
                    original_presence = np.any(np.isfinite(expected['quotes']['values'][:7]), axis=0)
                    self.assertEqual(hashlib.sha256(actual['quotes']['present'].tobytes()).digest(),
                                     hashlib.sha256(original_presence.tobytes()).digest())
                    original_membership = expected['quotes']['values'][8] > 0
                    self.assertEqual(hashlib.sha256(actual['quotes']['membership'].tobytes()).digest(),
                                     hashlib.sha256(original_membership.tobytes()).digest())

    def test_future_requests_are_returns_only_and_prices_add_only_adjustment_dependencies(self):
        class RecordingSource(_Connection):
            def __init__(self, values):
                super().__init__(values)
                self.requests = []

            def selected_columns(self, snapshot, initial, stop, columns, *, fields):
                self.requests.append((initial, stop, fields))
                yield from super().selected_columns(snapshot, initial, stop, columns, fields=fields)

        source = RecordingSource(self.matrix)
        payload = store.load(dict(self.data, universe=['A']), 3, 2, source=source,
                             fields=['value', 'open', 'volume'])
        self.assertEqual(payload['quotes']['fields'], ('open', 'close', 'volume'))
        self.assertEqual(source.requests[-1], (7, 9, ('return',)))
        self.assertEqual(payload['quotes']['values'].shape[1], 7)
        self.assertEqual(payload['returns'].shape[0], 9)
        self.assertFalse(payload['membership'][7:].any())
        self.assertIn(PRESENT_FIELD, source.requests[0][2])
        self.assertIn(MEMBERSHIP_FIELD, source.requests[0][2])
        source = RecordingSource(self.matrix)
        payload = store.load(dict(self.data, universe=['A']), 3, 2, source=source,
                             fields=['total_turnover'])
        self.assertNotIn('close', source.requests[0][2])
        self.assertNotIn('volume', source.requests[0][2])
        self.assertTrue(payload['quotes']['present'][4, 0])
        self.assertTrue(np.isnan(payload['quotes']['values'][0, 4, 0]))

    def test_zero_field_factors_and_inactive_explicit_research_axis_keep_their_contract(self):
        class InactiveSource(_Connection):
            def fetchone(self):
                manifest, symbols, dates, fields = super().fetchone()
                manifest['instrument_lifetimes'][-1] = [20300101, 20990101]
                return manifest, symbols, dates, fields

        payload = store.load(dict(self.data, universe=['D', 'A']), 3, 2, source=InactiveSource(self.matrix),
                             fields=[], pasteurization='OFF')
        self.assertEqual(payload['quotes']['fields'], ())
        self.assertEqual(payload['quotes']['values'].shape, (0, 7, 3))
        self.assertEqual(payload['quotes']['research_columns'].tolist(), [0, -1])
        self.assertTrue(payload['membership'][:7].all())
        self.assertFalse(payload['membership'][7:].any())
        np.testing.assert_array_equal(payload['returns'], self.matrix[7, :, [0, 3]].T)

    def test_column_views_and_complete_masks_preserve_bits_and_owned_returns(self):
        for prune in (False, True):
            matrix = self.matrix.copy()
            matrix[7, 0, 0] = -0.0
            matrix.view(np.uint64)[7, 1, 1] = 0x7ff8000000000029
            if prune:
                matrix[8, :, 3] = 0
            matrix.setflags(write=False)
            original = matrix.tobytes()
            for pasteurization in ('ON', 'OFF'):
                for universe in (['B', 'C'], ['D', 'A'], 'all_a_dynamic'):
                    with self.subTest(prune=prune, pasteurization=pasteurization, universe=universe):
                        source = _Connection(matrix)
                        payload = store.load(dict(self.data, universe=universe), 3, 2,
                            source=source, pasteurization=pasteurization,
                            fields=['total_turnover'])
                        columns = [source.symbols.index(symbol) for symbol in payload['symbols']]
                        expected_returns = matrix[7][:, columns]
                        self.assertEqual(payload['returns'].tobytes(), expected_returns.tobytes())
                        calculation = [source.symbols.index(symbol) for symbol in payload['quotes']['symbols']]
                        self.assertEqual(payload['quotes']['values'][0].tobytes(),
                                         matrix[6, :7][:, calculation].tobytes())
                        self.assertFalse(payload['quotes']['values'].flags.writeable)
                        payload['returns'][0, 0] = 17
                        self.assertEqual(matrix.tobytes(), original)
                        self.assertFalse(matrix.flags.writeable)

    def test_byte_budget_replaces_fixed_cell_count_and_fails_before_block_reads(self):
        with patch.object(store, '_input_budget', return_value=2*1024**3):
            store._check_memory(2500, 2520, 5580, 5580, 1)
            with self.assertRaisesRegex(ValueError, 'memory budget'):
                store._check_memory(2500, 2520, 5580, 5580, 11)
        source = _Connection(self.matrix)
        with patch.dict(os.environ, FACTORPOOL_INPUT_MAX_BYTES='1'), \
                patch.object(source, 'selected_columns', side_effect=AssertionError('data read before budget guard')):
            with self.assertRaisesRegex(ValueError, 'memory budget'):
                store.load(dict(self.data, universe=['A']), 3, 2, source=source,
                           fields=['total_turnover'])
        for value in ('0', '-1', 'wrong'):
            with patch.dict(os.environ, FACTORPOOL_INPUT_MAX_BYTES=value), self.assertRaises(ValueError):
                store._input_budget()

    def test_projection_validates_field_names(self):
        for fields in ('close', ['unknown'], [None]):
            with self.assertRaises(ValueError):
                store.load(dict(self.data, universe=['A']), 1, 0, fields=fields, source=_Connection(self.matrix))

    def test_derived_status_tiles_use_one_byte_and_preserve_identity_validation(self):
        from services.data_service.market_store import STORAGE_FORMAT, field_catalog
        metadata = dict(snapshot_id='shape-test', symbols=[f'S{n:05d}' for n in range(1024)],
                        dates=[(date(2024, 1, 1)+timedelta(days=n)).isoformat() for n in range(64)],
                        manifest=dict(storage_format=STORAGE_FORMAT, field_catalog=field_catalog()),
                        fields=list(FIELDS), cache=dict(codec='raw', tile_symbols=1024, chunk_max_bytes=4*1024**2))
        block = dict(start_index=0, row_count=64, sha256='0'*64)
        raw = encode_tile(metadata, block, 'close', 0, np.ones((64, 1024)))
        for field in (PRESENT_FIELD, MEMBERSHIP_FIELD):
            packed = encode_tile(metadata, block, field, 0, np.ones((64, 1024), dtype=bool))
            restored = decode_tile(packed, metadata, block, field, 0)
            self.assertEqual(restored.dtype, np.dtype('uint8'))
            self.assertTrue(restored.all())
            self.assertLess(len(packed), len(raw)/7)
            with self.assertRaisesRegex(ValueError, 'metadata mismatch'):
                decode_tile(packed, metadata, block, 'close', 0)
            with self.assertRaisesRegex(ValueError, 'boolean'):
                encode_tile(metadata, block, field, 0, np.full((64, 1024), 2))


class FieldProjectionGrpcTests(unittest.TestCase):
    setUp = grpc_cases.GrpcBoundaryTests.setUp
    client = grpc_cases.GrpcBoundaryTests.client

    def test_projected_original_fields_and_status_cross_real_grpc_and_direct_cache(self):
        data = dict(snapshot_id='original', factor_start='2024-01-03', factor_end='2024-01-07',
                    universe='all_a_dynamic')
        client = self.client()
        actual = store.load(data, 3, 2, source=client, fields=['total_turnover'])
        np.testing.assert_array_equal(actual['quotes']['values'][0], self.source.matrix[6, :7])
        np.testing.assert_array_equal(actual['returns'], self.source.matrix[7])
        np.testing.assert_array_equal(actual['quotes']['present'], np.any(np.isfinite(self.source.matrix[:7, :7]), axis=0))
        np.testing.assert_array_equal(actual['membership'][:7], self.source.matrix[8, :7] > 0)
        self.assertEqual(self.source.stats['pg_blocks'], 1)
        self.assertEqual(client.stats['grpc_blocks'], 8)
        metadata = client.snapshot('original')
        for field in ('open', 'high', 'low', 'close', 'prev_close', 'volume', 'is_st', 'is_suspended'):
            self.assertNotIn(tile_key(metadata, metadata['blocks'][0], field, 0), self.cache.values)
        hot = self.client()
        expected = store.load(data, 3, 2, source=hot, fields=['total_turnover'])
        self.assertEqual(hot.stats['grpc_blocks'], 0)
        np.testing.assert_array_equal(expected['quotes']['values'], actual['quotes']['values'])
        for columns in ([3, 0], [2, 0, 3, 1], [1, 1], [0, 2, 3]):
            blocks = list(matrix_blocks(hot, metadata, 0, 9, np.asarray(columns),
                                              fields=('total_turnover', PRESENT_FIELD)))
            self.assertEqual(len(blocks), 1)
            np.testing.assert_array_equal(blocks[0][2][0], self.source.matrix[6][:, columns])
            np.testing.assert_array_equal(blocks[0][2][1], np.any(np.isfinite(self.source.matrix[:7]), axis=0)[:, columns])
        key = tile_key(metadata, metadata['blocks'][0], PRESENT_FIELD, 0)
        self.cache.values[key] = b'wrong-status-envelope'
        repair = self.client()
        repaired = store.load(data, 3, 2, source=repair, fields=['total_turnover'])
        self.assertEqual(repair.stats['corrupt_blocks'], 1)
        self.assertEqual(repair.stats['grpc_blocks'], 1)
        np.testing.assert_array_equal(repaired['quotes']['present'], actual['quotes']['present'])
        self.cache.online = False
        outage = store.load(data, 3, 2, source=self.client(), fields=['total_turnover'])
        np.testing.assert_array_equal(outage['quotes']['values'], actual['quotes']['values'])
        np.testing.assert_array_equal(outage['membership'], actual['membership'])


if __name__ == '__main__':
    unittest.main()
