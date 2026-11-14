"""Pre-delisting selection uses each holding period, never fabricated returns."""
import unittest

import numpy as np
import _factorpool_native as native


def prepared(factors, returns, exits=None, members=None, weights=None):
    factors = np.asarray(factors, dtype=np.float64)
    membership = (np.ones_like(factors) if members is None
                  else np.asarray(members, dtype=np.float64))
    return native.prepare_research(factors, np.asarray(returns, dtype=np.float64),
                                  membership, weights, exits)


def report(value, horizon=2, traces=None, weights=None):
    return native.portfolio_report(value, None, None, 2, horizon, 252, traces, weights)


class DelistingFilterTests(unittest.TestCase):
    def test_prior_periods_remain_and_normal_rebalance_closes_before_delisting(self):
        factors = np.tile([1., 2, 3], (5, 1))
        membership = np.ones_like(factors)
        membership[3:, 0] = 0
        returns = [[0., 0, 0], [.25, 0, 0], [0, 0, 0],
                   [np.nan, .25, 0], [np.nan, 0, 0], [np.nan, 0, 0]]
        actual = report(prepared(factors, returns, [3, -1, -1], membership),
                        traces=[0, 2, 4])
        alpha = actual['strategies']['alpha']
        np.testing.assert_allclose(alpha['book_pnl_series'], [-.125, 0, -.125, 0, 0])
        np.testing.assert_allclose(alpha['nav'], [.875, .875, .765625, .765625, .765625])
        np.testing.assert_allclose(alpha['turnover_series'], [1, 0, 1.125, 0, .125])
        np.testing.assert_allclose(actual['strategies']['long_G1']['nav'],
                                   [1.125, 1.125, 1.40625, 1.40625, 1.40625])
        first, excluded = actual['trace'][0], actual['trace'][2]
        self.assertEqual(first['rows'][0]['target_weight'], -.5)
        self.assertEqual(excluded['signal_index'], 2)
        self.assertTrue(excluded['rows'][0]['member'])
        self.assertEqual(excluded['rows'][0]['target_weight'], 0)
        self.assertEqual(excluded['rows'][0]['status'], 'delisting_next_period')
        self.assertIsNone(excluded['rows'][0]['neutralized'])
        self.assertIsNone(excluded['rows'][0]['return'])
        self.assertEqual(excluded['rows'][0]['pnl_fraction'], 0)

    def test_next_rebalance_equal_to_first_inactive_day_is_excluded(self):
        factors = [[1., 2, 3]]
        returns = [[0., 0, 0], [.25, .25, 0], [np.nan, 0, 0]]
        boundary = report(prepared(factors, returns, [2, -1, -1]), traces=[0])
        np.testing.assert_allclose(boundary['strategies']['alpha']['nav'], [.875, .875])
        self.assertEqual(boundary['trace'][0]['rows'][0]['target_weight'], 0)
        later = report(prepared(factors, returns, [3, -1, -1]), traces=[0])
        self.assertEqual(later['trace'][0]['rows'][0]['target_weight'], -.5)
        self.assertIsNone(later['strategies']['alpha']['nav'][1])

    def test_horizons_do_not_mutate_shared_order_or_raw_return_research(self):
        factors = [[1., 2, 3]]
        returns = [[0., 0, 0], [.25, .1, -.1], [0, 0, .1], [np.nan, .1, 0]]
        value = prepared(factors, returns, [3, -1, -1])
        before = report(value, 1, [0])
        longer = report(value, 3, [0])
        self.assertEqual(before['trace'][0]['rows'][0]['target_weight'], -.5)
        self.assertEqual(longer['trace'][0]['rows'][0]['target_weight'], 0)
        self.assertEqual(report(value, 1, [0]), before)
        plain = prepared(factors, returns)
        self.assertEqual(native.analyze(value, None, 2, 2, 1, None, 3),
                         native.analyze(plain, None, 2, 2, 1, None, 3))
        self.assertEqual(native.ic_series(value, 3, 2), native.ic_series(plain, 3, 2))

    def test_real_missing_return_within_lifetime_remains_unknown(self):
        value = prepared([[1., 2, 3]], [[0.]*3, [.25, np.nan, 0], [np.nan, 0, 0]],
                         [2, -1, -1])
        alpha = report(value)['strategies']['alpha']
        self.assertEqual(alpha['nav'], [None, None])
        self.assertEqual(alpha['book_pnl_series'], [None, None])

    def test_all_candidates_excluded_are_known_cash_with_regular_exit_turnover(self):
        value = prepared([[1., 2], [1, 2]], [[0., 0], [.25, 0], [np.nan, np.nan]], [2, 2])
        actual = report(value, 1, [1])
        for strategy in actual['strategies'].values():
            self.assertEqual(strategy['returns'][1], 0)
            self.assertEqual(strategy['positions'][1], 0)
            self.assertIsNotNone(strategy['nav'][1])
        alpha = actual['strategies']['alpha']
        np.testing.assert_allclose(alpha['nav'], [.875, .875])
        np.testing.assert_allclose(alpha['turnover_series'], [1, 1.125])
        self.assertEqual(actual['trace'][0]['target_gross'], 0)
        unavailable = report(prepared([[np.nan, np.nan]], [[0., 0], [0, 0]], [1, 1]), 1)
        self.assertEqual(unavailable['strategies']['alpha']['nav'], [None])

    def test_no_terminal_date_and_future_end_outside_loaded_axis_preserve_reports(self):
        factors = [[1., 2, 3], [3, 1, 2]]
        returns = [[0., 0, 0], [.25, -.25, .5], [0, .25, -.25], [.1, 0, .1]]
        plain = prepared(factors, returns)
        for exits in ([-1]*3, [2**53+1]*3, np.full(3, 2**63-1, dtype=np.int64)):
            with self.subTest(exits=exits):
                value = prepared(factors, returns, exits)
                for h in (1, 2, 3):
                    self.assertEqual(report(value, h), report(plain, h))

    def test_exit_vector_is_owned_and_rejects_noninteger_or_wrong_symbol_axes(self):
        factors, returns = [[1., 2, 3]], [[0.]*3, [.25, 0, 0], [0, 0, 0]]
        exits = np.array([3, -1, -1], dtype=np.int64)
        value = prepared(factors, returns, exits)
        expected = report(value)
        exits[0] = 0
        self.assertEqual(report(value), expected)
        for invalid in ([1, 2], [1, 2, 3, 4], [True, -1, -1], [1., -1, -1],
                        [-2, -1, -1], [2**63, -1, -1], [[1, -1, -1]],
                        np.zeros(3), np.zeros((1, 3), dtype=np.int64),
                        np.zeros(3, dtype=np.uint64), np.arange(6, dtype=np.int64)[::2]):
            with self.subTest(invalid=invalid):
                with self.assertRaises((ValueError, TypeError)):
                    prepared(factors, returns, invalid)

    def test_horizon_weight_override_preserves_caps_and_owns_only_target_matrix(self):
        factors = [[1., 2, 3]]
        returns = [[0.]*3, [np.nan, .25, -.5], [np.nan, .25, .5]]
        value = prepared(factors, returns, [2, -1, -1])
        actual = report(value, 2, [0], [[0., .2, .4]])
        alpha = actual['strategies']['alpha']
        np.testing.assert_allclose(alpha['book_pnl_series'], [-.15, .1625], atol=1e-15)
        self.assertAlmostEqual(actual['trace'][0]['target_gross'], .6)
        self.assertEqual([row['target_weight'] for row in actual['trace'][0]['rows']], [0, .2, .4])
        self.assertTrue(all(row['neutralized'] is None for row in actual['trace'][0]['rows']))
        for invalid in ([[0., .2]], [[0., .2, .4]]*2, [[.1, .2, .4]],
                        [[0., np.nan, .4]], [[0., np.inf, .4]], [[0., .8, .8]]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    report(value, 2, None, invalid)
        stored = prepared(factors, returns, [2, -1, -1], weights=[[.1, .2, .4]])
        with self.assertRaises(ValueError):
            report(stored, 2)
        self.assertEqual(report(stored, 2, None, [[0., .2, .4]])['strategies']['alpha'], alpha)

    def test_ordinary_membership_exit_does_not_shorten_existing_holding(self):
        value = prepared([[1., 2, 3], [1, 2, 3]],
                         [[0.]*3, [.25, 0, 0], [.25, 0, 0]],
                         members=[[1, 1, 1], [0, 1, 1]])
        alpha = report(value, 2)['strategies']['alpha']
        np.testing.assert_allclose(alpha['book_pnl_series'], [-.125, -.15625])
        np.testing.assert_allclose(alpha['nav'], [.875, .71875])
