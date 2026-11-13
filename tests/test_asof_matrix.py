"""Exact shared-stream prefix references for the independent temporal bridge."""
import os
import unittest
from unittest.mock import patch

import numpy as np

from factorpool.factor_matrix import FactorMatrix
from factorpool.data_client.contract import FIELDS
import test_factor_matrix as reference

split_payload = reference.split_payload


class AsOfMatrixTests(unittest.TestCase):
    factor = reference.FactorMatrixTests.factor

    def compare(self, payload, expression, indices=None, ready='self.ts.is_ready(value)'):
        dates = payload['quotes']['dates']
        indices = list(range(len(dates))) if indices is None else list(indices)
        indicator, factory, _ = self.factor(expression, ready=ready)
        old = FactorMatrix(payload, indicator, factory, dates[-1])
        shape = (len(indices), len(old.symbols))
        expected = dict(value=np.full(shape, np.nan), valid=np.zeros(shape, dtype=bool),
                        ready=np.zeros(shape, dtype=bool), reason=np.full(shape, 6, dtype=np.uint8))
        for row, index in enumerate(indices):
            result = old.evaluate_arrays(index)
            for key in expected:
                expected[key][row, result['columns']] = result[key]
        indicator, factory, _ = self.factor(expression, ready=ready)
        current = FactorMatrix(payload, indicator, factory, dates[-1])
        actual = current.evaluate_many(indices)
        self.assertIsNotNone(actual)
        for key in expected:
            wanted, got = expected[key], actual[key]
            np.testing.assert_array_equal(got.view(np.uint64) if key == 'value' else got,
                                          wanted.view(np.uint64) if key == 'value' else wanted,
                                          err_msg=key)
        return current, actual

    def test_price_volume_and_nested_raw_temporals_preserve_stream_rounding(self):
        payload = split_payload(53)
        values = payload['quotes']['values'].copy()
        values[FIELDS.index('volume'), :6, 0] = [1e16, 1, 1, 1, 1, 1]
        values.flags.writeable = False
        payload['quotes']['values'] = values
        for expression in (
                'self.cs.rank_unit(-self.ts.mean(input.volume, 3))',
                'self.ts.mean(input.volume, 3)',
                'self.ts.mean(input.volume / input.close, 3)',
                'self.ts.mean(self.ts.mean(input.close, 3), 4)',
                'self.cs.rank_unit(self.ts.std(input.close, 5))',
                'self.ts.mean(input.volume, 3) + self.ts.mean(input.total_turnover, 4)',
                'self.cs.rank_unit(self.ts.decay_linear(input.volume, 4))'):
            with self.subTest(expression=expression):
                self.compare(payload, expression)

    def test_halt_unknown_segment_and_resume_keep_the_pre_halt_prefix(self):
        payload = split_payload(47)
        shape = payload['quotes']['values'].shape[1:]
        q = np.ones(shape); segments = np.zeros(shape, dtype=np.int64)
        q[10:, 0] = .5; q[30:, 0] = .4
        q[7:13, 1] = np.nan; segments[7:13, 1] = -1
        # Resumption restores the old comparable segment; its earlier valid
        # samples are still required by the original observation clock.
        q[25:27, 2] = np.nan; segments[25:27, 2] = -1
        segments[27:, 2] = 1
        values = payload['quotes']['values'].copy()
        values[FIELDS.index('volume'), 7:13, 1] = 0
        values[FIELDS.index('total_turnover'), 7:13, 1] = 0
        values.flags.writeable = False
        payload['quotes'].update(values=values, adjustment=dict(q=q, segment=segments))
        for expression in ('self.cs.rank_unit(-self.ts.mean(input.volume, 4))',
                           'self.ts.mean(input.volume, 4) + self.ts.mean(input.total_turnover, 3)',
                           'self.ts.mean(self.ts.is_ready(self.ts.mean(input.volume, 2)) + input.total_turnover*0, 2)'):
            with self.subTest(expression=expression):
                self.compare(payload, expression)

    def test_missing_bars_membership_exit_reentry_and_sparse_signals_are_exact(self):
        payload = split_payload(44)
        values = payload['quotes']['values'].copy()
        for day in (4, 5, 18):
            values[:, day, 0] = np.nan
            payload['frames'][day]['rows'].pop('A')
        for day in range(9, 15):
            payload['frames'][day]['symbols'].remove('B')
        values.flags.writeable = False
        payload['quotes']['values'] = values
        self.compare(payload, 'self.cs.rank_unit(self.ts.mean(input.volume, 5))',
                     indices=[0, 3, 8, 15, 19, 28, 43], ready='True')

    def test_batch_then_ordinary_calls_rebuild_instead_of_using_suffix_state(self):
        payload = split_payload(35)
        current, _ = self.compare(payload, 'self.cs.rank_unit(self.ts.mean(input.volume, 3))', indices=[2, 8, 14])
        reference, factory, _ = self.factor('self.cs.rank_unit(self.ts.mean(input.volume, 3))')
        old = FactorMatrix(payload, reference, factory, payload['quotes']['dates'][-1])
        for index in (15, 21, 34):
            expected, actual = old.evaluate_arrays(index), current.evaluate_arrays(index)
            for key in ('value', 'valid', 'ready', 'reason'):
                np.testing.assert_array_equal(actual[key], expected[key])

    def test_coupled_or_calendar_plans_use_the_unchanged_fallback(self):
        payload = split_payload(14)
        for expression in ('self.ts.mean(self.cs.rank_unit(input.volume), 3)',
                           "self.ts.mean(input.volume, 3, clock='trading_days')"):
            with self.subTest(expression=expression):
                indicator, factory, _ = self.factor(expression)
                current = FactorMatrix(payload, indicator, factory, payload['quotes']['dates'][-1])
                self.assertIsNone(current.evaluate_many([3, 8, 13]))
                self.assertEqual(len(current.evaluate_arrays(13)['value']), 3)

    def test_input_views_stay_readonly_and_outputs_are_owned(self):
        payload = split_payload(18)
        original = payload['quotes']['values'].tobytes()
        current, actual = self.compare(payload, 'self.cs.rank_unit(self.ts.mean(input.volume, 3))')
        before = actual['value'].copy()
        current.evaluate_arrays(17)
        np.testing.assert_array_equal(actual['value'], before)
        self.assertFalse(actual['value'].flags.writeable)
        self.assertEqual(payload['quotes']['values'].tobytes(), original)

    def test_partitioned_threads_publish_identical_values_masks_and_tied_ranks(self):
        payload = split_payload(81)
        width = 1024
        original = payload['quotes']['values']
        values = np.tile(original, (1, 1, (width+2)//3))[:, :, :width].copy()
        symbols = [f'S{n:04d}' for n in range(width)]
        shape = values.shape[1:]
        q = np.ones(shape); segment = np.zeros(shape, dtype=np.int64)
        q[24:, ::7] = .5; q[57:, ::11] = .7
        q[31:37, ::13] = np.nan; segment[31:37, ::13] = -1
        for t, frame in enumerate(payload['frames']):
            frame['symbols'] = [symbol for n, symbol in enumerate(symbols)
                                if not (17 <= t < 20 and 12 <= n < 27)]
            frame['rows'] = {}
        payload['symbols'] = symbols
        payload['returns'] = np.zeros(shape)
        payload['quotes'].update(symbols=symbols, values=values,
                                 adjustment=dict(q=q, segment=segment))
        expression = 'self.cs.rank_unit(-self.ts.mean(input.volume, 5))'
        with patch.dict(os.environ, FACTORPOOL_COMPUTE_THREADS='1'):
            _, serial = self.compare(payload, expression, indices=[0, 5, 16, 20, 24, 33, 38, 57, 66, 80])
        with patch.dict(os.environ, FACTORPOOL_COMPUTE_THREADS='4'):
            _, parallel = self.compare(payload, expression, indices=range(81))
        for row, day in enumerate(serial['indices']):
            for name in ('value', 'valid', 'ready', 'reason'):
                np.testing.assert_array_equal(serial[name][row], parallel[name][day])

    def test_adjacent_large_int64_segments_never_share_a_float_key(self):
        payload = split_payload(29)
        shape = payload['quotes']['values'].shape[1:]
        segment = np.full(shape, 2**53+7, dtype=np.int64)
        segment[12:, 0] += 1  # The two identifiers collapse to one float64.
        payload['quotes']['adjustment'] = dict(q=np.ones(shape), segment=segment)
        self.compare(payload, 'self.cs.rank_unit(self.ts.mean(input.volume, 5))')

    def test_future_lifetimes_cannot_change_an_earlier_publication(self):
        payload = split_payload(41)
        shape = payload['quotes']['values'].shape[1:]
        q = np.ones(shape); segment = np.zeros(shape, dtype=np.int64)
        q[24:, 0] = .5; q[33:, 0] = .25
        payload['quotes']['adjustment'] = dict(q=q, segment=segment)
        expression = 'self.cs.rank_unit(-self.ts.mean(input.volume, 5))'
        _, before = self.compare(payload, expression)
        values = payload['quotes']['values'].copy()
        values[:, 24:] *= 17
        q[24:, 0] = .7; q[29:, 1] = .3; segment[30:, 2] = 2**53+9
        payload['quotes']['values'] = values
        _, after = self.compare(payload, expression)
        for name in ('value', 'valid', 'ready', 'reason'):
            first, second = before[name][:24], after[name][:24]
            np.testing.assert_array_equal(first.view(np.uint64) if name == 'value' else first,
                                          second.view(np.uint64) if name == 'value' else second)
