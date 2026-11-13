"""Known flat books must stay distinct from unavailable Alpha or held returns."""
import unittest

import numpy as np
import _factorpool_native as native

from factorpool.settings import normalize_settings
from factorpool.simulation import alpha_targets


def alpha_report(factors, returns, *, weights=None, members=None, horizon=1, traces=()):
    factors = np.asarray(factors, dtype=np.float64)
    members = np.ones_like(factors) if members is None else np.asarray(members, dtype=np.float64)
    prepared = native.prepare_research(factors, np.asarray(returns, dtype=np.float64), members,
                                       None if weights is None else np.asarray(weights, dtype=np.float64))
    report = native.portfolio_report(prepared, None, None, 3, horizon, 252, list(traces))
    return report['strategies']['alpha'], report.get('trace', [])


class ZeroExposureTests(unittest.TestCase):
    def test_finite_constants_and_one_available_factor_form_known_flat_books(self):
        for factors in ([[5., 5, 5]], [[np.nan, .5, np.nan]], [[0., 0, 0]]):
            with self.subTest(factors=factors):
                alpha, trace = alpha_report(factors, [[0.]*3, [np.nan, -1, 2]], traces=[0])
                self.assertEqual(alpha['returns'], [0])
                self.assertEqual(alpha['book_pnl_series'], [0])
                self.assertEqual(alpha['turnover_series'], [0])
                self.assertEqual(alpha['positions'], [0])
                self.assertEqual(alpha['nav'], [1])
                self.assertEqual(alpha['max_drawdown'], 0)
                self.assertEqual(alpha['brain_style']['drawdown'], 0)
                self.assertEqual(trace[0]['status'], 'ok')
                self.assertEqual(trace[0]['target_gross'], 0)
                self.assertEqual(trace[0]['pnl_fraction'], 0)

    def test_exit_cash_reentry_and_total_loss_have_hand_computed_drift_turnover(self):
        factors = [[-1., 1], [5, 5], [1, -1], [0, 0]]
        returns = [[0., 0], [.1, .2], [np.nan, 2], [-1, .2], [.8, np.nan]]
        alpha, traces = alpha_report(factors, returns, traces=[1, 3])
        np.testing.assert_allclose(alpha['returns'], [.05, 0, -.6, 0], atol=1e-14)
        np.testing.assert_allclose(alpha['book_pnl_series'], [.05, 0, -.6, 0], atol=1e-14)
        # Exit marks -.5*1.1 and .5*1.2; reentry starts from a flat book;
        # the later long lost everything, leaving only -.5*1.2 to close.
        np.testing.assert_allclose(alpha['turnover_series'], [1, 1.15, 1, .6], atol=1e-14)
        self.assertEqual(alpha['positions'], [2, 0, 2, 0])
        np.testing.assert_allclose(alpha['nav'], [1.05, 1.05, .42, .42], atol=1e-14)
        self.assertAlmostEqual(alpha['brain_style']['turnover'], 3.75/4)
        self.assertAlmostEqual(alpha['brain_style']['margin'], -.55/3.75)
        self.assertAlmostEqual(alpha['brain_style']['drawdown'], 1.2)
        self.assertTrue(all(day['target_gross'] == 0 and day['status'] == 'ok' for day in traces))

    def test_explicit_zero_target_is_known_even_when_raw_factors_are_unavailable(self):
        alpha, _ = alpha_report([[np.nan]*3]*2, [[0.]*3, [np.nan]*3, [np.nan]*3],
                                weights=[[0.]*3, [np.nan]*3])
        self.assertEqual(alpha['returns'], [0, None])
        self.assertEqual(alpha['positions'], [0, None])
        self.assertEqual(alpha['turnover_series'], [0, None])
        self.assertEqual(alpha['nav'], [1, None])

    def test_genuinely_unavailable_factor_sample_remains_unknown(self):
        for factors, members in (([[np.nan]*3], [[1.]*3]), ([[1., 2, 3]], [[0.]*3])):
            with self.subTest(factors=factors, members=members):
                alpha, trace = alpha_report(factors, [[0.]*3, [.1]*3], members=members, traces=[0])
                self.assertEqual(alpha['returns'], [None])
                self.assertEqual(alpha['positions'], [None])
                self.assertEqual(trace[0]['status'], 'no_valid_weights')

    def test_held_missing_return_is_not_repaired_by_a_later_cash_target(self):
        alpha, _ = alpha_report([[-1., 1], [5, 5], [1, -1]],
                                [[0., 0], [np.nan, .2], [0, np.nan], [.1, 0]])
        self.assertEqual(alpha['returns'][:2], [None, 0])
        self.assertAlmostEqual(alpha['returns'][2], .05)
        self.assertEqual(alpha['positions'], [2, 0, 2])
        self.assertEqual(alpha['turnover_series'], [1, None, 1])
        self.assertEqual(alpha['nav'], [None]*3)
        self.assertEqual(alpha['constant_book_pnl'], [None]*3)
        self.assertIsNone(alpha['brain_style']['drawdown'])
        self.assertIsNone(alpha['brain_style']['turnover'])
        self.assertIsNone(alpha['brain_style']['margin'])

    def test_zero_weight_missing_return_is_ignored_without_dropping_valid_zero_factors(self):
        alpha, trace = alpha_report([[0., 1, 2]], [[0.]*3, [-1, np.nan, 0]], traces=[0])
        self.assertEqual(alpha['positions'], [2])
        self.assertEqual(alpha['returns'], [.5])
        self.assertEqual(trace[0]['rows'][0]['raw_alpha'], 0)
        self.assertEqual(trace[0]['rows'][1]['status'], 'zero_weight')

    def test_cash_over_multiple_days_ignores_positive_zero_total_loss_and_missing_returns(self):
        alpha, _ = alpha_report([[1., 1]], [[0., 0], [.2, -1], [0, 2], [np.nan, np.nan]], horizon=3)
        self.assertEqual(alpha['returns'], [0]*3)
        self.assertEqual(alpha['period_returns'], [0])
        self.assertEqual(alpha['turnover_series'], [0]*3)
        self.assertEqual(alpha['positions'], [0]*3)
        self.assertEqual(alpha['nav'], [1]*3)

    def test_native_custom_transforms_mark_only_finite_zero_norm_rows_as_cash(self):
        factors = np.array([[5., 5, np.nan], [np.nan, .5, np.nan], [0., -0., 0.], [np.nan]*3])
        members = np.ones_like(factors)
        for mode in ('MARKET', 'NONE'):
            for cap in (0, .2, 1):
                if mode == 'MARKET' and cap == 0:
                    continue  # The ordinary portfolio branch is covered above.
                settings = normalize_settings(dict(neutralization=mode, truncation=cap),
                                              dict(provider='fixture', universe='fixture'))
                details = {}
                targets = alpha_targets(factors, members, settings, details=details, trace_indices=[0, 2])
                np.testing.assert_array_equal(targets[2], [0]*3)
                self.assertTrue(np.isnan(targets[3]).all())
                if mode == 'MARKET':
                    np.testing.assert_array_equal(targets[:3], np.zeros((3, 3)))
                if cap > 0:
                    cash = details['constraints'][2]
                    self.assertEqual(cash['target_gross'], 0)
                    self.assertEqual(cash['target_net'], 0)
                    self.assertEqual(cash['unallocated_gross'], 1)
                    self.assertEqual(details['constraints'][3]['status'], 'no_valid_weights')
