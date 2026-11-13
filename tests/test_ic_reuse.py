"""Independent paired-sample IC checks for reused forward-return ordering."""
import unittest

import numpy as np
from scipy.stats import rankdata
import _factorpool_native as native


def forward_returns(returns, horizon):
    values = np.array(returns, dtype=np.float64, copy=True)
    values[~np.isfinite(values) | (values < -1)] = np.nan
    out = np.full(values.shape, np.nan)
    for t in range(len(values) - horizon):
        out[t] = np.prod(1 + values[t + 1:t + horizon + 1], axis=0) - 1
    return out


def paired_correlations(factors, members, forward, lag, minimum):
    ic, rank_ic = [], []
    for t, factor in enumerate(factors):
        if t + lag >= len(forward):
            ic.append(np.nan)
            rank_ic.append(np.nan)
            continue
        values = forward[t + lag]
        mask = (np.isfinite(factor) & (members[t] > 0) & np.isfinite(values))
        a, b = factor[mask], values[mask]
        if len(a) < minimum or np.ptp(a) == 0 or np.ptp(b) == 0:
            ic.append(np.nan)
            rank_ic.append(np.nan)
        else:
            ic.append(np.corrcoef(a, b)[0, 1])
            rank_ic.append(np.corrcoef(rankdata(a), rankdata(b))[0, 1])
    return ic, rank_ic


class ReusedReturnOrderTests(unittest.TestCase):
    def assert_series(self, actual, expected):
        np.testing.assert_allclose(np.array(actual, dtype=float), expected,
                                   atol=2e-12, rtol=2e-12, equal_nan=True)

    def assert_summary(self, actual, expected):
        values = np.array(expected)
        finite = values[np.isfinite(values)]
        self.assertEqual(actual['n'], len(finite))
        self.assertEqual(actual['missing'], len(values) - len(finite))
        self.assert_series([actual['mean']], [np.mean(finite) if len(finite) else np.nan])
        self.assert_series([actual['std']], [np.std(finite, ddof=1) if len(finite) > 1 else np.nan])

    def check_case(self, factors, returns, members, horizons, minimums):
        prepared = native.prepare_research(factors, returns, members)
        for h in horizons:
            forward = forward_returns(returns, h)
            for minimum in minimums:
                with self.subTest(horizon=h, minimum=minimum):
                    expected = paired_correlations(factors, members, forward, 0, minimum)
                    series = native.ic_series(prepared, h, minimum)
                    self.assert_series(series['ic'], expected[0])
                    self.assert_series(series['rank_ic'], expected[1])
                    decay = native.ic_decay(prepared, None, None, h, 4, minimum)
                    for k, item in enumerate(decay):
                        pair = paired_correlations(factors, members, forward, k * h, minimum)
                        self.assertEqual(item['entry_lag'], k * h)
                        self.assert_summary(item['ic'], pair[0])
                        self.assert_summary(item['rank_ic'], pair[1])
                    # A second entry point uses the same cached rows, but must
                    # still honor its own minimum and the paired sample mask.
                    primary = native.analyze(prepared, None, 3, minimum, 1, None, h)
                    self.assert_series([row['rank_ic'] for row in primary['daily']], expected[1])
                    self.assertEqual(native.ic_series(prepared, h, minimum), series)

    def test_ties_zero_nonfinite_and_changed_masks_match_independent_ranks(self):
        rng = np.random.default_rng(20261113)
        factors = rng.integers(-3, 4, size=(37, 19)).astype(float)
        returns = rng.choice([0., -.0, .125, .125, -.5, -1., np.nan, np.inf, -2.],
                             size=(48, 19))
        members = rng.choice([0., 1., 1., 1., np.nan], size=factors.shape)
        factors[rng.random(factors.shape) < .13] = np.nan
        factors[2, :4] = [np.inf, -np.inf, 0., -.0]
        factors[4] = 0.
        self.check_case(factors, returns, members, (1, 1), (2, 3, 8, 30))

    def test_multiple_horizons_and_lags_preserve_actual_signal_membership(self):
        rng = np.random.default_rng(8321)
        factors = np.round(rng.normal(size=(41, 23)) * 2)
        returns = rng.normal(0, .02, size=(58, 23))
        members = (rng.random(factors.shape) > .2).astype(float)
        factors[rng.random(factors.shape) < .11] = np.nan
        returns[rng.random(returns.shape) < .03] = np.nan
        returns[4, :4] = [-1., np.inf, -np.inf, -2.]
        members[2, 0] = np.nan
        self.check_case(factors, returns, members, (2, 5, 7, 2), (3, 10, 30))


if __name__ == '__main__':
    unittest.main()
