"""Threaded research keeps ordered reductions, mask-dependent ties and traces."""
import json
import os
from unittest.mock import patch
import unittest

import numpy as np
import _factorpool_native as native


class NativeParallelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(20261113)
        cls.factors = rng.integers(-8, 9, size=(160, 513)).astype(np.float64)
        cls.returns = rng.normal(0, .02, size=(185, 513))
        cls.members = rng.random(cls.factors.shape) > .12
        cls.factors[rng.random(cls.factors.shape) < .03] = np.nan
        cls.returns[rng.random(cls.returns.shape) < .02] = np.nan
        cls.returns[17, :7] = [-1., -2., np.inf, np.nan, 0., -.0, .1]
        cls.factors[7] = 1.
        cls.members[13] = False
        cls.weights = np.zeros(cls.factors.shape)
        for t in range(len(cls.weights)):
            selected = cls.members[t] & np.isfinite(cls.factors[t])
            if np.count_nonzero(selected) > 1:
                values = cls.factors[t, selected]
                values = values - values.mean()
                gross = np.abs(values).sum()
                if gross:
                    cls.weights[t, selected] = values / gross * .8
        cls.weights[23] = np.nan

    def publication(self, threads, weights):
        with patch.dict(os.environ, FACTORPOOL_COMPUTE_THREADS=str(threads)):
            prepared = native.prepare_research(self.factors, self.returns,
                                               self.members, weights)
            out = {}
            for horizon in (1, 5, 20):
                out[str(horizon)] = dict(
                    primary=native.analyze(prepared, None, 5, 3, 1, None, horizon, horizon),
                    series=native.ic_series(prepared, horizon, 3, horizon),
                    decay=native.ic_decay(prepared, None, None, horizon, 3, 3, horizon),
                    portfolios=native.portfolio_report(prepared, None, None, 5, horizon, 252),
                )
                columns = native.analyze(prepared, None, 5, 3, 1, None, horizon, horizon, True)
                for field in ('forward_returns', 'groups'):
                    np.testing.assert_array_equal(columns[field],
                                                  np.asarray(out[str(horizon)]['primary'][field], dtype=np.float64))
                self.assertEqual({k: v for k, v in columns.items() if k not in ('forward_returns', 'groups')},
                                 {k: v for k, v in out[str(horizon)]['primary'].items() if k not in ('forward_returns', 'groups')})
            out['trace'] = native.portfolio_report(prepared, None, None, 5, 5, 252,
                                                    [0, 5, 155])
            return json.dumps(out, sort_keys=True, separators=(',', ':'), allow_nan=False)

    def test_complete_publication_is_byte_identical_with_one_and_four_threads(self):
        for weights in (None, self.weights):
            with self.subTest(explicit_weights=weights is not None):
                self.assertEqual(self.publication(1, weights),
                                 self.publication(4, weights))

    def test_shared_holding_growth_resets_and_ignores_unheld_missing_returns(self):
        factors = np.tile(np.arange(1., 5.), (7, 1))
        factors[0, 2] = np.nan  # explicit target weights remain authoritative
        returns = np.array([[0., 0., 0., 0.], [.5, -.5, 0., 1.],
                            [.5, 1., -1., -.5], [0., 0., 0., 0.],
                            [-.5, 0., .2, -1.], [1., 1., np.nan, 0.],
                            [0., np.nan, 0., 0.], [.5, .5, .5, .5]])
        weights = np.zeros_like(factors)
        weights[0] = [.25, -.25, .25, -.25]
        weights[3] = [-.25, .25, 0., .5]
        prepared = native.prepare_research(factors, returns, None, weights)
        report = native.portfolio_report(prepared, None, None, 5, 3, 252, [0, 3, 6])
        alpha = report['strategies']['alpha']
        self.assertEqual(alpha['book_pnl_series'], [0., .0625, 0., -.375, .125, None, 0.])
        self.assertEqual(alpha['returns'], [0., .0625, 0., -.375, .75 / .625 - 1, None, 0.])
        self.assertEqual(alpha['turnover_series'], [1., 0., 0., 2.0625, 0., 0., None])
        self.assertEqual(alpha['period_returns'], [.0625, None, None])
        trace = report['trace']
        self.assertIsNone(trace[0]['rows'][2]['raw_alpha'])
        self.assertEqual(trace[1]['rows'][0]['effective_weight_before_return'], .375)
        self.assertEqual(trace[1]['rows'][0]['pnl_fraction'], .1875)
        self.assertEqual(trace[4]['status'], 'ok')
        self.assertEqual(trace[4]['rows'][2]['status'], 'zero_weight')
        self.assertEqual(trace[4]['rows'][2]['pnl_fraction'], 0.)
        self.assertEqual(trace[5]['status'], 'missing_return')
        self.assertEqual(trace[6]['status'], 'ok')
        self.assertEqual(trace[6]['target_gross'], 0)
        self.assertEqual(alpha['positions'][-1], 0)
        # The current flat book is known, but it cannot repair the previous
        # interval's held missing return or reconstruct its exit turnover.
        self.assertIsNone(alpha['nav'][-1])
        self.assertIsNone(alpha['turnover_series'][-1])

    def test_shared_growth_preserves_overflow_underflow_and_trace_error_order(self):
        factors = np.ones((4, 1))
        returns = np.array([[0.], [1e308], [1e308], [-1.], [.5]])
        prepared = native.prepare_research(factors, returns, None, np.ones_like(factors))
        report = native.portfolio_report(prepared, None, None, 1, 3, 252, [0, 3])
        alpha = report['strategies']['alpha']
        self.assertEqual(alpha['book_pnl_series'], [1e308, None, None, .5])
        self.assertEqual(alpha['turnover_series'], [1., 0., 0., None])
        self.assertEqual([day['status'] for day in report['trace']],
                         ['ok', 'nonfinite_growth', 'prior_missing_return', 'ok'])
        factors = np.ones((1, 1))
        returns = np.full((41, 1), -1. + 2. ** -30)
        prepared = native.prepare_research(factors, returns, None, np.ones_like(factors))
        report = native.portfolio_report(prepared, None, None, 1, 40, 252, [0])
        self.assertEqual(report['trace'][-1]['rows'][0]['effective_weight_before_return'], 0.)
        self.assertEqual(report['trace'][-1]['rows'][0]['status'], 'ok')
        self.assertEqual(report['strategies']['alpha']['period_returns'], [-1.])

    def test_thread_configuration_is_validated_and_bounded(self):
        for value in ('', '0', '-1', '65', '2.5', '4x'):
            with self.subTest(value=value), patch.dict(os.environ, FACTORPOOL_COMPUTE_THREADS=value):
                with self.assertRaisesRegex(ValueError, 'FACTORPOOL_COMPUTE_THREADS'):
                    native.compute_threads()
        with patch.dict(os.environ, FACTORPOOL_COMPUTE_THREADS='1'):
            self.assertEqual(native.compute_threads(), 1)
        with patch.dict(os.environ, FACTORPOOL_COMPUTE_THREADS='auto'):
            self.assertGreaterEqual(native.compute_threads(), 1)
            self.assertLessEqual(native.compute_threads(), 64)


if __name__ == '__main__':
    unittest.main()
