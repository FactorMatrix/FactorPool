"""Current report projections preserve independent numeric conventions."""
import math
import unittest

import numpy as np
import _factorpool_native as native


class NativeComputeProjectionTests(unittest.TestCase):
    def test_summary_matches_fixed_book_reference_even_when_nav_is_unavailable(self):
        returns = [None, .2, None, .1]
        book = [-2., .5, -.25, .125]
        turnover = [1., .5, 0., .25]
        summary = native.brain_summary(book, turnover, 252)
        self.assertEqual(set(summary), {'sharpe', 'returns', 'turnover', 'drawdown', 'fitness', 'margin'})
        full = native.performance_stats(returns, turnover, book, 252)
        self.assertEqual(summary, full['brain_style'])
        self.assertTrue(all(value is None for value in full['nav']))
        mean, sd = np.mean(book), np.std(book, ddof=1)
        expected_sharpe = mean / sd * math.sqrt(252)
        self.assertAlmostEqual(summary['sharpe'], expected_sharpe)
        self.assertAlmostEqual(summary['returns'], mean * 504)
        self.assertAlmostEqual(summary['turnover'], sum(turnover) / len(turnover))
        self.assertEqual(summary['drawdown'], 4)
        self.assertAlmostEqual(summary['margin'], sum(book) / sum(turnover))
        self.assertAlmostEqual(summary['fitness'], expected_sharpe * math.sqrt(abs(mean * 504) / (sum(turnover)/4)))

    def test_scalar_summary_preserves_flat_missing_and_invalid_axes(self):
        for book, turnover in (([], []), ([0., 0], [0., 0]), ([.1], [1.]),
                               ([None, .2], [1., 0]), ([.1, -.2], [None, 0]),
                               ([float('inf'), 0], [1, 0])):
            with self.subTest(book=book, turnover=turnover):
                self.assertEqual(native.brain_summary(book, turnover, 252),
                                 native.performance_stats(book, turnover, book, 252)['brain_style'])
        for args in (([.1], [], 252), ([.1], [1], 0), ([.1], [1], 367)):
            with self.assertRaises(ValueError):
                native.brain_summary(*args)

    def test_performance_keeps_only_current_normal_and_fixed_book_outputs(self):
        prepared = native.prepare_research([[1., 2, 3]] * 3,
                                           [[0.] * 3, [.1, 0, -.1], [0, .2, -.2], [.2, 0, -.1]], None)
        primary = native.analyze(prepared, 3, 3, 1)
        self.assertEqual(set(primary['daily'][0]), {'ic', 'rank_ic', 'pairs', 'reason'})
        for key in ('forward_returns', 'groups'):
            self.assertIsInstance(primary[key], np.ndarray)
            self.assertFalse(primary[key].flags.writeable)
        strategies = native.portfolio_report(prepared, 3, 1, 252)['strategies']
        removed = {'period_returns', 'period_turnover_series', 'positions', 'max_weight_series',
                   'constant_book_drawdown', 'missing', 'mean', 'std', 'ddof', 'ir',
                   'positive_ratio', 'skewness', 'excess_kurtosis', 't_iid', 't_hac', 'hac_lag'}
        for name, stats in strategies.items():
            self.assertFalse(removed & stats.keys(), name)
            self.assertEqual(stats['n'], 3)
            self.assertEqual('brain_style' in stats, name == 'alpha')
            self.assertEqual('book_pnl_series' in stats, name == 'alpha')
            self.assertEqual('constant_book_pnl' in stats, name == 'alpha')
        # IC research inference remains complete.
        moments = native.series_stats([-.1, 0, .2, .3], 1, 1)
        self.assertTrue({'mean', 'std', 't_hac', 'skewness', 'excess_kurtosis'} <= moments.keys())

    def test_report_means_match_original_reduction_and_preserve_missing_dates(self):
        values = [1e18, 1., -1e18, 2., None, .3, .2, float('inf'), .1, -.4]
        self.assertEqual(native.series_mean(values), native.series_stats(values, 1, 0)['mean'])
        for window in (1, 2, 3, 30):
            expected = []
            for i in range(len(values)):
                sample = values[max(0, i - window + 1):i + 1]
                expected.append(native.series_stats(sample, 1, 0)['mean']
                    if len(sample) == window and all(x is not None and math.isfinite(x) for x in sample) else None)
            self.assertEqual(native.report_rolling_mean(values, window), expected)
        self.assertEqual(native.report_rolling_mean([], 3), [])
        self.assertIsNone(native.series_mean([None, float('nan')]))
        with self.assertRaises(ValueError):
            native.report_rolling_mean(values, 0)

    def test_research_reports_require_one_owned_prepared_input(self):
        for call in (lambda: native.analyze([], 3, 3, 1),
                     lambda: native.portfolio_report([], 3, 1, 252),
                     lambda: native.ic_decay([], 1, 2, 3)):
            with self.assertRaisesRegex(ValueError, 'prepared research input'):
                call()
        prepared = native.prepare_research([[1., 2, 3]], [[0.] * 3, [.1, 0, -.1]], None)
        # Superseded tuple placeholders and list-publication switches are not accepted.
        for call in (lambda: native.analyze(prepared, None, 3, 3, 1),
                     lambda: native.analyze(prepared, 3, 3, 1, 1, 1, False),
                     lambda: native.portfolio_report(prepared, None, None, 3, 1, 252),
                     lambda: native.ic_decay(prepared, None, None, 1, 2, 3)):
            with self.assertRaises(TypeError):
                call()
