"""Rebalance-only signals keep their original daily return-matrix indices."""
from datetime import date, timedelta
import unittest

import numpy as np
from scipy.stats import rankdata
import _factorpool_native as native
from factorpool.reporting.diagnostics import build


def fixture():
    rng = np.random.default_rng(20261114)
    factors = np.round(rng.normal(size=(17, 9)) * 2) / 2
    returns = rng.normal(0, .025, size=(31, 9))
    members = (rng.random(factors.shape) > .1).astype(float)
    factors[4, 2] = np.nan
    returns[7, 3] = np.nan
    dates = [(date(2024, 1, 22) + timedelta(days=i)).isoformat()
             for i in range(len(returns))]
    return factors, returns, members, dates[:len(factors)], dates


def expected_ic(factors, returns, members, h, stride, lag=0):
    output = [[], []]
    for t in range(0, len(factors), stride):
        entry = t + lag
        values = (np.prod(1 + returns[entry + 1:entry + h + 1], axis=0) - 1
                  if entry + h < len(returns) else np.full(returns.shape[1], np.nan))
        valid = np.isfinite(factors[t]) & (members[t] > 0) & np.isfinite(values)
        a, b = factors[t, valid], values[valid]
        if len(a) < 3 or np.ptp(a) == 0 or np.ptp(b) == 0:
            output[0].append(np.nan)
            output[1].append(np.nan)
        else:
            output[0].append(np.corrcoef(a, b)[0, 1])
            output[1].append(np.corrcoef(rankdata(a), rankdata(b))[0, 1])
    return output


class SignalSamplingTests(unittest.TestCase):
    def assert_series(self, actual, expected):
        np.testing.assert_allclose(np.asarray(actual, dtype=float), expected,
                                   atol=2e-12, rtol=2e-12, equal_nan=True)

    def test_native_sampling_and_decay_use_original_return_dates(self):
        factors, returns, members, _, _ = fixture()
        prepared = native.prepare_research(factors, returns, members)
        for h in (1, 2, 5):
            with self.subTest(horizon=h):
                expected = expected_ic(factors, returns, members, h, h)
                result = native.analyze(prepared, None, 3, 3, 1, None, h, h)
                series = native.ic_series(prepared, h, 3, h)
                self.assert_series([day['ic'] for day in result['daily']], expected[0])
                self.assert_series(series['ic'], expected[0])
                self.assert_series(series['rank_ic'], expected[1])
                self.assertEqual(len(result['groups']), len(range(0, len(factors), h)))
                for row, t in zip(result['forward_returns'], range(0, len(factors), h)):
                    self.assert_series(row, np.prod(1 + returns[t + 1:t + h + 1], axis=0) - 1)
                decay = native.ic_decay(prepared, None, None, h, 4, 3, h)
                for k, item in enumerate(decay):
                    expected = expected_ic(factors, returns, members, h, h, k * h)
                    self.assertEqual(item['entry_lag'], k * h)
                    for name, values in zip(('ic', 'rank_ic'), expected):
                        valid = np.asarray(values)[np.isfinite(values)]
                        stats = item[name]
                        self.assertEqual(stats['n'], len(valid))
                        self.assertEqual(stats['missing'], len(values) - len(valid))
                        self.assert_series([stats['mean']], [np.mean(valid) if len(valid) else np.nan])
                        self.assert_series([stats['std']], [np.std(valid, ddof=1) if len(valid) > 1 else np.nan])
                        if stats['t_iid'] is not None and not stats['missing']:
                            # HAC uses population long-run variance even at
                            # lag zero; IID uses sample standard deviation.
                            hac = np.mean(valid) / np.sqrt(np.var(valid) / len(valid))
                            self.assert_series([stats['t_hac']], [hac])

    def test_reporting_samples_ic_but_preserves_all_daily_portfolios(self):
        factors, returns, members, dates, price_dates = fixture()
        prepared = native.prepare_research(factors, returns, members)
        analysis = dict(groups=3, horizons=[1, 2, 5], decay_steps=3)
        primary = native.analyze(prepared, None, 3, 3, 1)
        daily = build(prepared, None, None, dates, price_dates, analysis, primary=primary)
        sampled = build(prepared, None, None, dates, price_dates, analysis,
                        primary=primary, rebalance_only=True)
        for h in (1, 2, 5):
            before, after = daily['horizons'][str(h)], sampled['horizons'][str(h)]
            self.assertEqual(after['dates'], dates[::h])
            self.assertEqual(after['portfolio_signal_dates'], dates[::h])
            self.assertEqual(after['portfolio_dates'], before['portfolio_dates'])
            self.assertEqual(after['portfolios'], before['portfolios'])
            self.assertEqual(after['series']['ic'], before['series']['ic'][::h])
            self.assertEqual(after['series']['rank_ic'], before['series']['rank_ic'][::h])
            self.assertEqual(after['ic']['n'] + after['ic']['missing'], len(dates[::h]))
            values = np.asarray(after['series']['ic'], dtype=float)
            hac = np.mean(values) / np.sqrt(np.var(values) / len(values))
            self.assert_series([after['ic']['t_hac']], [hac])
            self.assertEqual(sum(item['n'] + item['missing'] for item in after['monthly']['ic']), len(dates[::h]))

    def test_only_rebalance_union_needs_factor_values(self):
        factors, returns, members, dates, price_dates = fixture()
        analysis = dict(groups=3, horizons=[2, 5], decay_steps=3)
        selected = sorted(set(range(0, len(factors), 2)) | set(range(0, len(factors), 5)))
        sparse = np.full_like(factors, np.nan)
        sparse[selected] = factors[selected]
        dense_report = build(factors, returns, members, dates, price_dates, analysis,
                             rebalance_only=True)
        sparse_report = build(sparse, returns, members, dates, price_dates, analysis,
                              rebalance_only=True)
        self.assertEqual(sparse_report, dense_report)

    def test_default_stride_is_compatible_and_invalid_stride_is_rejected(self):
        factors, returns, members, _, _ = fixture()
        prepared = native.prepare_research(factors, returns, members)
        self.assertEqual(native.analyze(prepared, None, 3, 3, 1),
                         native.analyze(prepared, None, 3, 3, 1, None, 1, 1))
        self.assertEqual(native.ic_series(prepared, 2, 3), native.ic_series(prepared, 2, 3, 1))
        self.assertEqual(native.ic_decay(prepared, None, None, 2, 3, 3),
                         native.ic_decay(prepared, None, None, 2, 3, 3, 1))
        for stride in (0, -1):
            for call in (lambda: native.analyze(prepared, None, 3, 3, 1, None, 1, stride),
                         lambda: native.ic_series(prepared, 2, 3, stride),
                         lambda: native.ic_decay(prepared, None, None, 2, 3, 3, stride)):
                with self.assertRaises(ValueError):
                    call()


if __name__ == '__main__':
    unittest.main()
