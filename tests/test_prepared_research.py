import copy
import unittest
import numpy as np
from scipy.stats import pearsonr, spearmanr
import _factorpool_native as native
from factorpool.reporting.diagnostics import build


class PreparedResearchTests(unittest.TestCase):
    def test_masked_ties_and_nonfinite_values_match_independent_scipy(self):
        factors = np.array([[0, 0, 1, 2, 2, 4, np.nan], [0, 0, 1, 2, 2, 4, 6.]])
        returns = np.array([[0]*7, [.1, np.nan, -.1, .2, 0, .3, 0],
                            [0, .2, .1, -.1, .3, 0, .4], [-.1, 0, .2, .1, .4, .3, 0],
                            [.03, .02, -.01, 0, .01, -.02, .04]])
        members = np.array([[1, 1, 1, 1, 1, 0, 1], [1]*7], dtype=float)
        prepared = native.prepare_research(factors, returns, members)
        for h in (1, 2, 3):
            series = native.ic_series(prepared, h, 3)
            for t, row in enumerate(factors):
                sample = returns[t+1:t+h+1]
                usable = np.isfinite(row) & (members[t] > 0)
                usable &= np.isfinite(sample).all(axis=0) & (sample >= -1).all(axis=0)
                future = np.prod(1 + sample, axis=0) - 1
                usable &= np.isfinite(future)
                a, b = row[usable], future[usable]
                for key, oracle in (('ic', pearsonr), ('rank_ic', spearmanr)):
                    expected = float(oracle(a, b).statistic) if len(a) >= 3 and np.ptp(a) and np.ptp(b) else None
                    if expected is None:
                        self.assertIsNone(series[key][t])
                    else:
                        self.assertAlmostEqual(series[key][t], expected)
            full = native.analyze(factors, returns, 5, 3, 1, members, h)
            self.assertEqual(series['ic'], [r['ic'] for r in full['daily']])
            self.assertEqual(series['rank_ic'], [r['rank_ic'] for r in full['daily']])

    def test_numpy_buffer_is_owned_immutable_and_tasks_are_isolated(self):
        factors = np.array([[0., 1, 2]])
        returns = np.array([[0., 0, 0], [.1, 0, -.1]])
        members = np.ones((1, 3))
        for matrix in (factors, returns, members):
            matrix.flags.writeable = False
        prepared = native.prepare_research(factors, returns, members)
        expected = copy.deepcopy(native.analyze(prepared, None, 3, 3, 1))
        for matrix in (factors, returns, members):
            matrix.flags.writeable = True
        factors[0, 0] = 100
        returns[1, 0] = -.3
        members[0, 0] = 0
        self.assertEqual(native.analyze(prepared, None, 3, 3, 1), expected)
        other = native.prepare_research(factors, returns, members)
        self.assertNotEqual(native.analyze(other, None, 3, 3, 1), expected)
        self.assertEqual(native.analyze(prepared, None, 3, 3, 1), expected)
        lists = native.prepare_research(factors.tolist(), returns.tolist(), members.tolist())
        self.assertEqual(native.analyze(lists, None, 3, 3, 1), native.analyze(other, None, 3, 3, 1))
        for matrices in (([[1, 2]], [[0]], [[1, 1]]), ([[1]], [[0]], []),
                         ([[1], [1, 2]], [[0], [0]], [[1], [1]])):
            with self.assertRaises(ValueError):
                native.prepare_research(*matrices)
        with self.assertRaises(ValueError):
            native.analyze(prepared, returns, 3, 3, 1, members)
        for h, minimum in ((0, 3), (253, 3), (1, 1)):
            with self.assertRaises(ValueError):
                native.ic_series(prepared, h, minimum)

    def test_internal_missing_total_loss_and_overflow_are_not_endpoint_returns(self):
        factors = [[0., 1, 2, 3, 4, 5]]
        returns = [[0]*6, [.1, .1, -1, -.1, 1e308, .1],
                   [None, 0, .1, -2, 1e308, 0], [.1, .1, .1, .1, 0, .1]]
        out = native.analyze(factors, returns, 3, 3, 1, [[1]*6], 3)
        future = out['forward_returns'][0]
        self.assertIsNone(future[0])
        self.assertAlmostEqual(future[1], 1.1*1*1.1-1)
        self.assertEqual(future[2], -1)
        self.assertIsNone(future[3])
        self.assertIsNone(future[4])
        self.assertAlmostEqual(future[5], 1.1*1*1.1-1)
        self.assertEqual(out['daily'][0]['pairs'], 3)
        for h in (1, 2, 3):
            primary = native.analyze(factors, returns, 3, 3, 1, [[1]*6], h)
            series = native.ic_series(native.prepare_research(factors, returns, [[1]*6]), h, 3)
            self.assertEqual([d['ic'] for d in primary['daily']], series['ic'])

    def test_long_axis_small_returns_match_direct_products(self):
        rng = np.random.default_rng(31415)
        factors = np.tile([0., 1, 2], (800, 1))
        returns = rng.normal(0, 1e-6, (1100, 3))
        prepared = native.prepare_research(factors, returns, np.ones_like(factors))
        for h in (1, 5, 20, 252):
            out = native.analyze(prepared, None, 3, 3, 1, None, h)
            expected = np.array([np.prod(1 + returns[t+1:t+h+1], axis=0)-1 for t in range(800)])
            np.testing.assert_allclose(out['forward_returns'], expected, rtol=1e-8, atol=3e-14)

    def test_full_report_primary_reuse_and_nonfinite_membership(self):
        factors = [[0, 0, 1, 2], [2, 1, 0, 0], [1, 1, 1, 1]]
        returns = [[0]*4, [.1, .2, 0, -.1], [0, -.1, .2, .1], [.2, -.2, -.1, 0]]
        members = [[1]*4, [1, float('nan'), 1, 1], [1]*4]
        dates = ['2024-01-30', '2024-01-31', '2024-02-01']
        price_dates = dates + ['2024-02-02']
        analysis = dict(groups=5, horizons=[1, 2, 20], decay_steps=3)
        prepared = native.prepare_research(factors, returns, members)
        primary = native.analyze(prepared, None, 5, 3, 1)
        expected = build(factors, returns, members, dates, price_dates, analysis)
        actual = build(prepared, None, None, dates, price_dates, analysis, primary=primary)
        self.assertEqual(actual, expected)
        self.assertEqual(actual['version'], 3)
        self.assertIsNone(actual['horizons']['1']['series']['ic'][2])
        self.assertEqual(actual['horizons']['20']['ic']['n'], 0)
