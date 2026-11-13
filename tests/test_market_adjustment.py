"""Independent references for the prebuilt market kernel and its buffer bridge."""
import copy
import math
import unittest

import numpy as np

from factorpool.market_adjustment import adjustment_policy, adjust_values, derive_adjustment
from factorpool.market_returns import daily_returns


def scalar_adjustment(fields):
    """One symbol at a time; no native implementation or Q helpers are reused."""
    close, previous, volume, amount, member, suspended = fields
    shape = close.shape
    factor = np.full(shape, np.nan)
    q = np.full(shape, np.nan)
    segment = np.full(shape, -1, dtype=np.int64)
    known = np.zeros(shape, dtype=bool)
    positive = lambda value: math.isfinite(value) and value > 0
    for symbol in range(shape[1]):
        last_close = cumulative = math.nan
        comparable = -1
        for t in range(shape[0]):
            c, p, v, a, m, s = [float(field[t, symbol]) for field in fields]
            if math.isfinite(v) and v == 0 and positive(m) and (
                    math.isfinite(a) and a == 0 or positive(s) and math.isnan(a)):
                continue
            if not (positive(v) and math.isfinite(c) and c >= 0
                    and (math.isnan(a) or positive(a))):
                last_close = cumulative = math.nan
                continue
            ratio = p / last_close if positive(last_close) and positive(cumulative) and positive(p) else math.nan
            product = cumulative * ratio if positive(ratio) else math.nan
            if positive(ratio) and positive(product):
                factor[t, symbol] = ratio
                q[t, symbol] = product
                known[t, symbol] = True
                cumulative = product
            else:
                comparable += 1
                cumulative = q[t, symbol] = 1.
            segment[t, symbol] = comparable
            last_close = c
    return factor, q, segment, known


def regular_fields(rows, symbols=1):
    shape = (rows, symbols)
    return [np.full(shape, value, dtype=np.float64)
            for value in (100., 100., 10., 1000., 1., 0.)]


class MarketAdjustmentTests(unittest.TestCase):
    def derive(self, fields, *, dates=None, state=None):
        return derive_adjustment(*fields[:4], member=fields[4], suspended=fields[5],
                                 dates=np.arange(fields[0].shape[0]) if dates is None else dates,
                                 state=state)

    def assert_result(self, actual, expected):
        for name, wanted in zip(('factor', 'q', 'segment', 'known'), expected):
            # Arithmetic order is part of the native contract. Missing payloads
            # are checked as missing, not by an incidental NaN payload bit.
            np.testing.assert_array_equal(getattr(actual, name), wanted)
            self.assertFalse(getattr(actual, name).flags.writeable)

    def test_pause_ignores_filled_quotes_and_bridges_last_actual_close_once(self):
        fields = regular_fields(7)
        fields[0][:, 0] = [100, 102, 98, 51, 51, 38.25, 39]
        fields[1][:, 0] = [99, 100, 99, 51, 51, 38.25, 38.25]
        fields[2][2:4] = fields[3][2:4] = 0
        fields[5][2:4] = 1
        actual = self.derive(fields)
        self.assert_result(actual, scalar_adjustment(fields))
        np.testing.assert_array_equal(actual.q[:, 0], [1, 1, np.nan, np.nan, .5, .375, .375])
        self.assertEqual(actual.factor[4, 0], 51/102)
        self.assertEqual(actual.segment[4, 0], 0)

    def test_actual_zero_close_is_not_replaced_with_an_older_positive_close(self):
        fields = regular_fields(4)
        fields[0][:, 0] = [100, 0, 50, 25]
        fields[1][:, 0] = [100, 80, 50, 25]
        actual = self.derive(fields)
        np.testing.assert_array_equal(actual.q[:, 0], [1, .8, 1, .5])
        np.testing.assert_array_equal(actual.factor[:, 0], [np.nan, .8, np.nan, .5])
        np.testing.assert_array_equal(actual.segment[:, 0], [0, 0, 1, 1])
        np.testing.assert_array_equal(actual.known[:, 0], [False, True, False, True])
        np.testing.assert_array_equal(daily_returns(*fields[:5])[:, 0], [0, -1, 0, 0])

    def test_unknowns_break_continuity_and_cannot_supply_a_multiplier(self):
        for field, value in ((0, np.nan), (0, -1), (0, np.inf),
                             (1, np.nan), (1, 0), (1, -1), (1, np.inf),
                             (2, np.nan), (2, -1), (2, np.inf),
                             (3, 0), (3, -1), (3, np.inf), (3, -np.inf)):
            with self.subTest(field=field, value=value):
                fields = regular_fields(4)
                fields[field][1, 0] = value
                actual = self.derive(fields)
                self.assert_result(actual, scalar_adjustment(fields))
                self.assertTrue(math.isnan(actual.factor[1, 0]))
                self.assertEqual(actual.q[2, 0], 1)
                self.assertEqual(actual.segment[2, 0], 1)
                if field == 1:
                    # A known actual close may immediately anchor a new segment
                    # even though its preceding link could not be recovered.
                    self.assertEqual(actual.q[1, 0], 1)
                    self.assertTrue(actual.known[2, 0])
                else:
                    self.assertTrue(math.isnan(actual.factor[2, 0]))
                self.assertEqual(actual.q[3, 0], 1)

    def test_missing_amount_is_optional_and_halt_requires_volume_and_lifetime(self):
        for amount, member, suspended, pause in (
                (0, 1, 0, True), (np.nan, 1, 1, True),
                (np.nan, 1, 0, False), (0, 0, 1, False),
                (0, np.nan, 1, False), (2, 1, 1, False)):
            with self.subTest(amount=amount, member=member, suspended=suspended):
                fields = regular_fields(3)
                fields[0][:, 0] = [100, 51, 51]
                fields[1][2, 0] = 50
                for index, value in ((2, 0), (3, amount), (4, member), (5, suspended)):
                    fields[index][1, 0] = value
                actual = self.derive(fields)
                self.assertTrue(math.isnan(actual.q[1, 0]))
                self.assertEqual(actual.q[2, 0], .5 if pause else 1)
                self.assertEqual(actual.segment[2, 0], 0 if pause else 1)
        fields = regular_fields(2)
        fields[3][:] = np.nan
        fields[5][:] = 1
        actual = self.derive(fields)
        self.assertEqual(actual.factor[1, 0], 1)
        self.assertEqual(actual.segment[1, 0], 0)

    def test_full_and_arbitrary_blocks_match_scalar_oracle_without_mutating_inputs(self):
        random = np.random.default_rng(20261113)
        shape = (97, 11)
        fields = [random.uniform(1, 150, shape), random.uniform(1, 150, shape),
                  random.uniform(1, 1000, shape), random.uniform(1, 1e6, shape),
                  np.ones(shape), np.zeros(shape)]
        for t in range(4, 97, 9):
            fields[2][t:t+2, t % 11] = fields[3][t:t+2, t % 11] = 0
            fields[5][t:t+2, t % 11] = 1
        for t in range(5, 97, 13):
            fields[2][t, (t+3) % 11] = np.nan
        fields[0][31, 3] = 0
        fields[1][57, 5] = 0
        fields[3][63, 7] = np.nan
        before = [field.tobytes() for field in fields]
        for field in fields:
            field.flags.writeable = False
        whole = self.derive(fields)
        self.assert_result(whole, scalar_adjustment(fields))
        state, blocked = None, []
        for first, last in zip((0, 1, 8, 31, 32, 63, 95), (1, 8, 31, 32, 63, 95, 97)):
            part = self.derive([field[first:last] for field in fields],
                               dates=np.arange(first, last), state=state)
            state = part.state
            blocked.append(part)
        for name in ('factor', 'q', 'segment', 'known'):
            np.testing.assert_array_equal(np.concatenate([getattr(x, name) for x in blocked]),
                                          getattr(whole, name))
        self.assertEqual(before, [field.tobytes() for field in fields])

    def test_state_can_branch_and_repeat_without_reapplying_a_ratio(self):
        fields = regular_fields(4)
        fields[0][:, 0] = [100, 50, 25, 25]
        fields[1][:, 0] = [100, 50, 25, 25]
        first = self.derive([field[:2] for field in fields], dates=[1, 2])
        retry = self.derive([field[1:3] for field in fields], dates=[2, 3], state=first.state)
        branch = self.derive([field[2:] for field in fields], dates=[3, 4], state=first.state)
        np.testing.assert_array_equal(retry.q[:, 0], [.5, .25])
        np.testing.assert_array_equal(branch.q[:, 0], [.25, .25])
        np.testing.assert_array_equal(first.q[:, 0], [1, .5])
        again = self.derive([field[1:2] for field in fields], dates=[2], state=first.state)
        np.testing.assert_array_equal(again.factor, first.factor[1:])
        changed = [field[1:2].copy() for field in fields]
        changed[1][0, 0] = 49
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.derive(changed, dates=[2], state=first.state)
        with self.assertRaisesRegex(ValueError, 'chronological'):
            self.derive([field[:1] for field in fields], dates=[1], state=first.state)

    def test_invalid_axes_dates_and_capsules_are_rejected(self):
        fields = regular_fields(3, 2)
        for dates in ([1, 3, 2], [1, 2], [[1, 2, 3]], [1., 2., 3.],
                      np.array([1, 2, 2**63], dtype=np.uint64)):
            with self.subTest(dates=dates):
                with self.assertRaises(ValueError):
                    self.derive(fields, dates=dates)
        with self.assertRaises((TypeError, ValueError)):
            self.derive(fields, state=object())
        state = self.derive(fields).state
        with self.assertRaisesRegex(ValueError, 'symbol axis'):
            self.derive(regular_fields(1, 3), dates=[4], state=state)
        with self.assertRaisesRegex(ValueError, 'axes'):
            derive_adjustment(*fields[:4], member=np.ones((2, 3)), dates=[1, 2, 3])

    def test_native_bridge_validates_format_strides_and_length(self):
        import _factorpool_market as native
        fields = regular_fields(3, 2)
        for wrong in (np.ones((3, 2), dtype=np.float32),
                      np.ones((3, 2), dtype='>f8'), fields[0][:, ::-1], np.ones(6)):
            with self.subTest(dtype=wrong.dtype, shape=wrong.shape):
                with self.assertRaises(ValueError):
                    native.daily_returns(wrong, *fields[1:5])
        for wrong in (np.arange(3, dtype=np.float64), np.arange(3, dtype='>i8'),
                      np.arange(6, dtype=np.int64)[::2]):
            with self.assertRaises(ValueError):
                native.adjustment(wrong, *fields)

    def test_wrapper_normalizes_strides_empty_axes_and_policy_ownership(self):
        fields = regular_fields(6, 4)
        fields[0][:] = np.arange(24).reshape(6, 4) + 1
        selected = [field[::2, ::-1] for field in fields]
        self.assert_result(self.derive(selected), scalar_adjustment(selected))
        for shape in ((0, 0), (0, 3), (3, 0)):
            arrays = [np.empty(shape) for _ in range(6)]
            result = self.derive(arrays)
            for name in ('factor', 'q', 'segment', 'known'):
                self.assertEqual(getattr(result, name).shape, shape)
        policy = adjustment_policy()
        original = copy.deepcopy(policy)
        policy['version'] = 'caller-local-change'
        self.assertEqual(adjustment_policy(), original)

    def test_price_and_volume_scaling_are_native_and_preserve_raw_values(self):
        row = np.array([100., 0., -2., np.nan, np.inf, 120., 80., 20.])
        history = np.array([1., 1., 1., 1., 1., 1., np.nan, 1.])
        basis = np.full(8, .5)
        historical_segment = np.array([0, 0, 0, 0, 0, 0, 0, -1], dtype=np.int64)
        basis_segment = np.array([0, 0, 0, 0, 0, 1, 0, -1], dtype=np.int64)
        before = [value.tobytes() for value in (row, history, basis, historical_segment, basis_segment)]
        for value in (row, history, basis, historical_segment, basis_segment):
            value.flags.writeable = False
        for inverse, expected in ((False, [50, 0, -1, np.nan, np.nan, np.nan, np.nan, np.nan]),
                                  (True, [200, 0, -4, np.nan, np.nan, np.nan, np.nan, np.nan])):
            actual = adjust_values(row, history, basis, historical_segment, basis_segment,
                                   inverse=inverse)
            np.testing.assert_array_equal(actual, expected)
            self.assertFalse(actual.flags.writeable)
        self.assertEqual(before, [value.tobytes() for value in
                                  (row, history, basis, historical_segment, basis_segment)])
        with self.assertRaisesRegex(ValueError, 'axes'):
            adjust_values(row, history[:-1], basis, historical_segment, basis_segment)
        self.assertEqual(adjust_values([], [], [], [], []).shape, (0,))

    def test_native_returns_match_frozen_numpy_policy_bit_for_bit(self):
        random = np.random.default_rng(7713)
        shape = (513, 37)
        fields = [random.uniform(0, 1000, shape) for _ in range(5)]
        choices = np.array([0., -0., -1., np.nan, np.inf, -np.inf])
        for field in fields:
            cells = random.choice(field.size, size=field.size // 5, replace=False)
            field.ravel()[cells] = random.choice(choices, len(cells))
        close, previous, volume, turnover, member = fields
        expected = np.full(shape, np.nan)
        normal = (np.isfinite(volume) & (volume > 0) & np.isfinite(close) & (close >= 0)
                  & np.isfinite(previous) & (previous > 0))
        with np.errstate(all='ignore'):
            np.divide(close, previous, out=expected, where=normal)
            np.subtract(expected, 1, out=expected, where=normal)
        expected[~np.isfinite(expected)] = np.nan
        expected[(np.isfinite(volume) & (volume == 0) & np.isfinite(turnover) & (turnover == 0)
                  & np.isfinite(member) & (member > 0))] = 0
        actual = daily_returns(*fields)
        np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))

