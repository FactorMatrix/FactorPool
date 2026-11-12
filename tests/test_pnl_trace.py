"""Selected native Alpha contributions reconcile with the existing fixed book."""
import unittest

import numpy as np
import _factorpool_native as native


def prepared(factors, returns, membership=None, weights=None):
    if membership is None:
        membership = np.ones(np.asarray(factors).shape, dtype=np.float64)
    return native.prepare_research(factors, returns, membership, weights)


def report(value, horizon=1, signals=None):
    return native.portfolio_report(value, None, None, 4, horizon, 252, signals)


class PnlTraceTests(unittest.TestCase):
    def test_four_stock_weights_contributions_and_scaled_book_match_hand_calculation(self):
        value = prepared([[1., 3, 5, 7]], [[0.]*4, [-.01, .02, .01, .03]])
        plain = native.portfolio_report(value, None, None, 4, 1, 252)
        traced = report(value, signals=[0])
        trace = traced.pop('trace')
        self.assertEqual(traced, plain)
        self.assertNotIn('trace', plain)
        self.assertEqual(report(value), plain)
        self.assertEqual(report(value, signals=[]), plain)
        self.assertEqual(len(trace), 1)
        day = trace[0]
        self.assertEqual((day['signal_index'], day['valuation_index'], day['status']), (0, 1, 'ok'))
        self.assertEqual(day['target_gross'], 1)
        self.assertEqual(day['target_net'], 0)
        rows = day['rows']
        self.assertEqual([row['symbol_index'] for row in rows], [0, 1, 2, 3])
        self.assertEqual([row['raw_alpha'] for row in rows], [1, 3, 5, 7])
        self.assertEqual([row['neutralized'] for row in rows], [-3, -1, 1, 3])
        self.assertTrue(all(row['member'] and row['status'] == 'ok' for row in rows))
        expected_weights = [-.375, -.125, .125, .375]
        expected_pnl = [.00375, -.0025, .00125, .01125]
        np.testing.assert_allclose([row['target_weight'] for row in rows], expected_weights, atol=1e-15)
        np.testing.assert_allclose([row['effective_weight_before_return'] for row in rows], expected_weights, atol=1e-15)
        np.testing.assert_allclose([row['pnl_fraction'] for row in rows], expected_pnl, atol=1e-15)
        self.assertAlmostEqual(sum(row['pnl_fraction'] for row in rows), .01375, places=15)
        self.assertEqual(day['pnl_fraction'], plain['strategies']['alpha']['book_pnl_series'][0])
        self.assertAlmostEqual(day['pnl_fraction'], .01375, places=15)
        self.assertAlmostEqual(day['pnl_fraction']*20_000_000, 275_000, places=8)

    def test_holding_growth_changes_effective_exposure_without_nav_normalization(self):
        returns = np.array([[0., 0, 0, 0], [-.01, .02, .01, .03], [.1, -.2, .05, .1], [0., .1, -.1, .2]])
        value = prepared([[1., 3, 5, 7]], returns)
        actual = report(value, horizon=3, signals=[0])
        weights = np.array([-.375, -.125, .125, .375])
        growth_before = np.vstack([np.ones(4), np.cumprod(1+returns[1:3], axis=0)])
        contributions = growth_before*weights*returns[1:]
        for d, day in enumerate(actual['trace']):
            self.assertEqual(day['signal_index'], 0)
            self.assertEqual(day['valuation_index'], d+1)
            np.testing.assert_allclose([row['target_weight'] for row in day['rows']], weights, atol=1e-15)
            np.testing.assert_allclose([row['effective_weight_before_return'] for row in day['rows']],
                                       weights*growth_before[d], atol=1e-15)
            np.testing.assert_allclose([row['pnl_fraction'] for row in day['rows']], contributions[d], atol=1e-15)
            self.assertAlmostEqual(day['pnl_fraction'], np.sum(contributions[d]), places=14)
            self.assertEqual(day['pnl_fraction'], actual['strategies']['alpha']['book_pnl_series'][d])
        previous_nav = 1+np.dot(weights, returns[1])
        self.assertNotAlmostEqual(actual['trace'][1]['rows'][0]['effective_weight_before_return'],
                                  weights[0]*growth_before[1, 0]/previous_nav)
        plain = native.portfolio_report(value, None, None, 4, 3, 252)
        actual.pop('trace')
        self.assertEqual(actual, plain)

    def test_true_zero_nonfinite_alpha_and_outside_members_are_auditable(self):
        value = prepared([[0., 1, 2, np.nan, 100]],
                         [[0.]*5, [.1, np.nan, -.2, np.nan, np.nan]], [[1., 1, 1, 1, 0]])
        day = report(value, signals=[0])['trace'][0]
        rows = day['rows']
        self.assertEqual(rows[0]['raw_alpha'], 0)
        self.assertEqual(rows[0]['target_weight'], -.5)
        self.assertEqual(rows[1]['neutralized'], 0)
        self.assertEqual(rows[1]['target_weight'], 0)
        self.assertIsNone(rows[1]['return'])
        self.assertEqual(rows[1]['pnl_fraction'], 0)
        self.assertEqual(rows[1]['status'], 'zero_weight')
        self.assertIsNone(rows[3]['raw_alpha'])
        self.assertIsNone(rows[3]['neutralized'])
        self.assertEqual(rows[3]['pnl_fraction'], 0)
        self.assertEqual(rows[3]['status'], 'nonfinite_alpha')
        self.assertFalse(rows[4]['member'])
        self.assertIsNone(rows[4]['neutralized'])
        self.assertEqual(rows[4]['status'], 'outside_universe')
        self.assertEqual(rows[4]['pnl_fraction'], 0)
        self.assertEqual(day['status'], 'ok')
        self.assertAlmostEqual(day['pnl_fraction'], -.15)

    def test_explicit_targets_do_not_invent_a_market_neutralized_column(self):
        value = prepared([[np.nan, 0., 2, 100]], [[0.]*4, [.1, np.nan, -.2, np.nan]],
                         [[1., 1, 1, 0]], [[.2, 0, -.1, 0]])
        day = report(value, signals=[0])['trace'][0]
        self.assertTrue(all(row['neutralized'] is None for row in day['rows']))
        self.assertIsNone(day['rows'][0]['raw_alpha'])
        self.assertEqual(day['rows'][0]['status'], 'ok')
        self.assertEqual(day['rows'][1]['status'], 'zero_weight')
        np.testing.assert_allclose([row['pnl_fraction'] for row in day['rows']], [.02, 0, .02, 0], atol=1e-15)
        self.assertAlmostEqual(day['pnl_fraction'], .04)
        self.assertAlmostEqual(day['target_gross'], .3)
        self.assertAlmostEqual(day['target_net'], .1)

    def test_missing_nonzero_holding_propagates_instead_of_becoming_zero(self):
        value = prepared([[1., 3, 5, 7]], [[0.]*4, [.1, np.nan, 0, .2], [.1, .1, -.1, 0]])
        actual = report(value, horizon=2, signals=[0])
        self.assertEqual(actual['strategies']['alpha']['book_pnl_series'], [None, None])
        self.assertEqual([day['pnl_fraction'] for day in actual['trace']], [None, None])
        first, second = actual['trace']
        self.assertEqual(first['status'], 'missing_return')
        self.assertEqual(first['rows'][1]['status'], 'missing_return')
        self.assertIsNone(first['rows'][1]['return'])
        self.assertIsNone(first['rows'][1]['pnl_fraction'])
        self.assertEqual(second['status'], 'prior_missing_return')
        self.assertEqual(second['rows'][1]['status'], 'prior_missing_return')
        self.assertIsNone(second['rows'][1]['effective_weight_before_return'])
        self.assertIsNone(second['rows'][1]['pnl_fraction'])
        self.assertIsNotNone(second['rows'][0]['pnl_fraction'])

    def test_constant_market_alpha_has_no_valid_target_or_fabricated_cash_return(self):
        day = report(prepared([[5., 5, 5, 5]], [[0.]*4, [.1]*4]), signals=[0])['trace'][0]
        self.assertEqual(day['status'], 'no_valid_weights')
        self.assertIsNone(day['pnl_fraction'])
        self.assertIsNone(day['target_gross'])
        self.assertIsNone(day['target_net'])
        self.assertEqual([row['neutralized'] for row in day['rows']], [0]*4)
        for row in day['rows']:
            self.assertEqual(row['status'], 'no_valid_weights')
            self.assertIsNone(row['target_weight'])
            self.assertIsNone(row['effective_weight_before_return'])
            self.assertIsNone(row['pnl_fraction'])

    def test_selection_validation_and_partial_future_budget_are_bounded(self):
        value = prepared([[1., 3, 5, 7]], [[0.]*4, [.1]*4])
        for signals in ((0,), '0', {}, [True], [0.], [-1], [1], [2**100], [0]*6):
            with self.subTest(signals=signals):
                with self.assertRaises(ValueError):
                    report(value, signals=signals)
        self.assertEqual(report(value, signals=[0, 0]), report(value, signals=[0]))
        rows = [[1., 3, 5, 7]]*3
        source = prepared(rows, [[0.]*4]*5)
        skipped = report(source, horizon=2, signals=[1])
        self.assertEqual(skipped.pop('trace'), [])
        self.assertEqual(skipped, native.portfolio_report(source, None, None, 4, 2, 252))
        traced = report(source, horizon=2, signals=[0, 2])['trace']
        self.assertEqual([(day['signal_index'], day['valuation_index']) for day in traced],
                         [(0, 1), (0, 2), (2, 3), (2, 4)])
        n = 10001
        factors = np.linspace(1, 2, n).reshape(1, n)
        members = np.ones_like(factors)
        too_many = prepared(factors, np.zeros((6, n)), members)
        with self.assertRaisesRegex(ValueError, '50,000'):
            report(too_many, horizon=5, signals=[0])
        # Budget actual available days, not a nonexistent five-day future tail.
        partial = prepared(factors, np.zeros((2, n)), members)
        trace = report(partial, horizon=5, signals=[0])['trace']
        self.assertEqual(len(trace), 1)
        self.assertEqual(len(trace[0]['rows']), n)
