"""Frozen pre-native formulas verify the small report and fixture transforms."""
import hashlib
import json
import math
import struct
import unittest

import numpy as np
import _factorpool_native as native

from factorpool.reporting.diagnostics import cumulative_sum
from factorpool.runner import returns_matrix


def _previous_returns(close, previous):
    valid = np.isfinite(close) & np.isfinite(previous) & (previous > 0) & (close >= 0)
    output = np.full(close.shape, np.nan, dtype=np.float64, order='C')
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        np.divide(close, previous, out=output, where=valid)
        np.subtract(output, 1, out=output, where=valid)
    output[~np.isfinite(output)] = np.nan
    return output


def _previous_prefix(values):
    total, output = 0.0, []
    for value in values:
        if total is None or value is None:
            total = None
        else:
            total += value
        output.append(total)
    return output


class SeriesTransformTests(unittest.TestCase):
    def test_price_returns_equal_frozen_divide_then_subtract_for_complete_matrix(self):
        random = np.random.default_rng(1927)
        close = random.normal(size=(157, 193)) * np.exp2(random.uniform(-900, 900, (157, 193)))
        previous = random.normal(size=close.shape) * np.exp2(random.uniform(-900, 900, close.shape))
        close[0, :10] = [0., -0., -1., np.nan, np.inf, -np.inf, 1e308, 1e-308, 1., 1.]
        previous[0, :10] = [1., 1., 1., 1., 1., 1., 1e-308, 1e308, 0., -1.]
        close.setflags(write=False)
        previous.setflags(write=False)
        before = [hashlib.sha256(value.tobytes()).digest() for value in (close, previous)]
        expected = _previous_returns(close, previous)
        output = native.price_returns(close, previous)
        self.assertEqual(hashlib.sha256(output.tobytes()).digest(), hashlib.sha256(expected.tobytes()).digest())
        self.assertFalse(output.flags.writeable)
        self.assertEqual(output.dtype, np.dtype('float64'))
        self.assertEqual(output.shape, close.shape)
        self.assertFalse(np.shares_memory(output, close))
        self.assertEqual(before, [hashlib.sha256(value.tobytes()).digest() for value in (close, previous)])

    def test_row_wrapper_keeps_missing_quotes_zero_close_and_axis_order(self):
        symbols = ['C', 'A', 'B']
        frames = [dict(rows={'A': dict(close=10., prev_close=8.),
                             'B': dict(close=0., prev_close=2.),
                             'C': dict(close=12., prev_close=12.)}),
                  dict(rows={'C': dict(close=1., prev_close=0.),
                             'A': dict(close=None, prev_close=9.)})]
        expected = np.array([[0., .25, -1.], [np.nan, np.nan, np.nan]])
        output = returns_matrix(frames, symbols)
        np.testing.assert_array_equal(output, expected)
        self.assertFalse(output.flags.writeable)
        for shape in ((0, 3), (3, 0), (0, 0)):
            with self.subTest(shape=shape):
                empty = np.empty(shape, dtype=np.float64)
                self.assertEqual(native.price_returns(empty, empty).shape, shape)
        self.assertEqual(returns_matrix([], symbols).shape, (0, 3))
        self.assertEqual(returns_matrix([dict(rows={})], []).shape, (1, 0))
        with self.assertRaisesRegex(ValueError, 'axes differ'):
            native.price_returns(np.ones((1, 2)), np.ones((2, 1)))
        with self.assertRaises(ValueError):
            native.price_returns(np.ones((3, 4))[:, ::2], np.ones((3, 2)))

    def test_prefix_keeps_sequential_binary64_rounding_and_none_poison(self):
        random = np.random.default_rng(1929)
        series = [1e16, 1., -1e16, -0., 0., *random.uniform(-1., 1., 4096).tolist()]
        for missing in (None, 0, 1, 2, 37, len(series)-1):
            values = series.copy()
            if missing is not None:
                values[missing] = None
            with self.subTest(missing=missing):
                expected = _previous_prefix(values)
                for output in (native.prefix_ic_sum(values), cumulative_sum(values)):
                    self.assertEqual(len(output), len(expected))
                    for actual, before in zip(output, expected):
                        if before is None:
                            self.assertIsNone(actual)
                        else:
                            self.assertEqual(struct.pack('d', actual), struct.pack('d', before))
        self.assertEqual(native.prefix_ic_sum([None, 'not-a-number']), [None, None])
        self.assertEqual(cumulative_sum([]), [])

    def test_prefix_nonfinite_input_and_overflow_preserve_previous_json_rejection(self):
        for values in ([1e308, 1e308], [math.inf, -math.inf], [math.nan, 1.], [None, math.inf]):
            expected, actual = _previous_prefix(values), cumulative_sum(values)
            for before, output in zip(expected, actual):
                if before is None:
                    self.assertIsNone(output)
                elif math.isnan(before):
                    self.assertTrue(math.isnan(output))
                else:
                    self.assertEqual(before, output)
            if values[0] is not None:
                with self.assertRaises(ValueError):
                    json.dumps(actual, allow_nan=False)
        with self.assertRaises(TypeError):
            native.prefix_ic_sum(['not-a-number'])


if __name__ == '__main__':
    unittest.main()
