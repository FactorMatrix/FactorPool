"""Independent array reference for held portfolios, including daily risk."""
from datetime import date, timedelta
import unittest
import numpy as np
import _factorpool_native as native
from factorpool.reporting.diagnostics import build


def reference_weights(factor, membership, groups):
    selected = [i for i, value in enumerate(factor) if np.isfinite(value) and membership[i] > 0]
    selected.sort(key=lambda i: (factor[i], i))
    labels = np.full(len(factor), -1)
    for rank, symbol in enumerate(selected):
        labels[symbol] = rank*groups//len(selected)
    output = {}
    for group in range(groups):
        count = np.sum(labels == group)
        w = np.where(labels == group, 1/max(1, count), 0)
        output[f'long_G{group+1}'] = w if count else None
        output[f'short_G{group+1}'] = -w if count else None
    for name, lo, hi in [('high_low', 0, groups-1)] + ([('inner_high_low', 1, groups-2)] if groups >= 4 else []):
        output[name] = ((output[f'long_G{hi+1}']-output[f'long_G{lo+1}'])/2
                        if hi > lo and output[f'long_G{hi+1}'] is not None
                        and output[f'long_G{lo+1}'] is not None else None)
    w = np.zeros(len(factor))
    if selected:
        w[selected] = factor[selected]-np.mean(factor[selected])
    output['alpha'] = w/np.sum(abs(w)) if np.sum(abs(w)) > 0 else None
    return output


def reference_portfolios(factors, returns, membership, groups, horizon):
    output = {}
    for signal in range(0, len(factors), horizon):
        weights = reference_weights(factors[signal], membership[signal], groups)
        growth = np.cumprod(1+returns[signal+1:signal+horizon+1], axis=0)
        for name, w in weights.items():
            state = output.setdefault(name, dict(returns=[], pnl=[], turnover=[], period_returns=[], previous=np.zeros(factors.shape[1])))
            if w is None:
                raise AssertionError('The dense reference fixture must have a nondegenerate target')
            wealth = 1+(growth-1)@w
            daily_return = np.diff(np.r_[1, wealth])/np.r_[1, wealth[:-1]]
            state['returns'].extend(daily_return)
            state['pnl'].extend(np.diff(np.r_[1, wealth]))
            state['turnover'].extend([np.sum(abs(w-state['previous']))]+[0]*(len(growth)-1))
            state['period_returns'].append(wealth[-1]-1)
            state['previous'] = w*growth[-1]
    return output


class ReturnMatrixTests(unittest.TestCase):
    def test_complete_multi_horizon_report_matches_independent_array_portfolios(self):
        rng = np.random.default_rng(20261112)
        factors = np.round(rng.normal(size=(47, 13))*3)/3
        members = np.ones_like(factors)
        members[::3, 0] = 0
        members[::5, 4] = 0
        factors[::7, 1] = np.nan
        returns = rng.normal(0, .012, (75, 13))
        dates = [(date(2024, 1, 15)+timedelta(days=i)).isoformat() for i in range(len(returns))]
        result = build(factors, returns, members, dates[:len(factors)], dates,
                       dict(groups=4, horizons=[1, 5, 20], decay_steps=3))
        for h in (1, 5, 20):
            with self.subTest(horizon=h):
                horizon = result['horizons'][str(h)]
                reference = reference_portfolios(factors, returns, members, 4, h)
                final_signal = (len(factors)-1)//h*h
                expected_dates = dates[1:final_signal+h+1]
                self.assertEqual(horizon['portfolio_dates'], expected_dates)
                for name, expected in reference.items():
                    actual = horizon['portfolios'][name]
                    daily = np.array(expected['returns'])
                    pnl = np.array(expected['pnl'])
                    nav = np.cumprod(1+daily)
                    peaks = np.maximum.accumulate(np.r_[1, nav])[1:]
                    np.testing.assert_allclose(actual['returns'], daily, rtol=1e-11, atol=3e-14, err_msg=name)
                    np.testing.assert_allclose(actual['period_returns'], expected['period_returns'], rtol=1e-11, atol=3e-14, err_msg=name)
                    np.testing.assert_allclose(actual['nav'], nav, rtol=1e-11, atol=3e-14, err_msg=name)
                    np.testing.assert_allclose(actual['drawdown_series'], nav/peaks-1, rtol=1e-11, atol=3e-14, err_msg=name)
                    np.testing.assert_allclose(actual['turnover_series'], expected['turnover'], rtol=1e-11, atol=3e-14, err_msg=name)
                    np.testing.assert_allclose(actual['book_pnl_series'], pnl, rtol=1e-11, atol=3e-14, err_msg=name)
                    np.testing.assert_allclose(actual['constant_book_pnl'], np.cumsum(pnl), rtol=1e-11, atol=3e-14, err_msg=name)
                    self.assertAlmostEqual(actual['sharpe'], np.mean(daily)/np.std(daily, ddof=1)*np.sqrt(252), places=9)
                    self.assertAlmostEqual(actual['max_drawdown'], np.min(nav/peaks-1), places=12)
                    brain = actual['brain_style']
                    self.assertAlmostEqual(brain['returns'], np.mean(pnl)*252*2, places=10)
                    self.assertAlmostEqual(brain['sharpe'], np.mean(pnl)/np.std(pnl, ddof=1)*np.sqrt(252), places=9)
                    self.assertAlmostEqual(brain['turnover'], np.mean(expected['turnover']), places=12)
                    self.assertAlmostEqual(brain['margin'], np.sum(pnl)/np.sum(expected['turnover']), places=12)
