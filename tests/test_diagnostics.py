import math
import unittest
import numpy as np
from scipy import stats
import _factorpool_native as native
from factorpool.reporting.diagnostics import build, inference, options


class DiagnosticsTests(unittest.TestCase):
    def test_moments_and_inference_match_independent_scipy(self):
        values=[-.2,0.,.1,.5,.25,-.1,.4,.3]
        result=inference(values,1,2)
        self.assertEqual(result['n'],8)
        self.assertAlmostEqual(result['mean'],np.mean(values))
        self.assertAlmostEqual(result['std'],np.std(values,ddof=1))
        self.assertAlmostEqual(result['skewness'],stats.skew(values,bias=False))
        self.assertAlmostEqual(result['excess_kurtosis'],stats.kurtosis(values,bias=False))
        expected=stats.ttest_1samp(values,0)
        self.assertAlmostEqual(result['t_iid'],expected.statistic)
        self.assertAlmostEqual(result['p_iid'],expected.pvalue)
        x=np.array(values)-np.mean(values)
        lr=np.mean(x*x)+2*sum((1-k/3)*np.dot(x[k:],x[:-k])/len(x) for k in (1,2))
        self.assertAlmostEqual(result['t_hac'],np.mean(values)/math.sqrt(lr/len(x)))
        self.assertIsNone(inference([.1,None,.3],1,1)['t_hac'])
        for values in ([],[None],[0,0,0],[1]):
            r=inference(values,1,0)
            self.assertIsNone(r['t_iid'])
            self.assertIsNone(r['p_iid'])

    def test_horizon_returns_and_nonoverlapping_rebalance(self):
        factors = [[1, 2, 3]]*6
        returns = [[0, 0, 0]] + [[.01, 0, -.01]]*9
        members = [[1]*3]*6
        raw = native.analyze(factors, returns, 3, 3, 1, members, 5)
        np.testing.assert_allclose(raw['forward_returns'][0], [1.01**5-1, 0, .99**5-1])
        self.assertTrue(all(x is None for x in raw['daily'][0]['cumulative']))
        report = native.portfolio_report(factors, returns, members, 3, 5, 252)
        self.assertEqual(report['signal_indices'], [0, 5])
        self.assertEqual(report['daily_indices'], list(range(1, 10)))
        spread = report['strategies']['high_low']
        self.assertAlmostEqual(spread['period_returns'][0], .5*(.99**5-1.01**5))
        self.assertIsNone(spread['period_returns'][1])
        self.assertEqual(len(spread['returns']), 9)  # An incomplete final period still has four observed days.

    def test_long_short_brain_and_return_drift_turnover(self):
        factors = [[1, .5, 0]]*3
        returns = [[0, 0, 0], [.1, 0, -.1], [-.2, 0, .1], [.1, 0, 0]]
        report = native.portfolio_report(factors, returns, [[1]*3]*3, 3, 1, 252)['strategies']
        np.testing.assert_allclose(report['long_G3']['returns'], [.1, -.2, .1], atol=1e-14)
        np.testing.assert_allclose(report['short_G3']['returns'], [-.1, .2, -.1], atol=1e-14)
        spread = report['high_low']
        np.testing.assert_allclose(spread['returns'], [.1, -.15, .05], atol=1e-14)
        np.testing.assert_allclose(spread['turnover_series'], [1, .1, .15], atol=1e-14)
        np.testing.assert_allclose(spread['nav'], np.cumprod(1+np.array([.1, -.15, .05])))
        self.assertAlmostEqual(spread['max_drawdown'], -.15)
        brain = spread['brain_style']
        mu, sigma = np.mean([.1, -.15, .05]), np.std([.1, -.15, .05], ddof=1)
        self.assertAlmostEqual(brain['returns'], mu*252*2)
        self.assertAlmostEqual(brain['turnover'], 1.25/3)
        self.assertAlmostEqual(brain['sharpe'], mu/sigma*np.sqrt(252))
        self.assertAlmostEqual(brain['margin'], 0/1.25, places=14)
        self.assertAlmostEqual(brain['drawdown'], .3)
        self.assertAlmostEqual(spread['win_rate'], 2/3)
        self.assertAlmostEqual(spread['payoff_ratio'], .075/.15)

    def test_period_holdings_drift_instead_of_daily_equal_weight(self):
        # Buy equal amounts: [1,1] -> [2,1] -> [1,2].  NAV stays 1.5 on day two.
        factors = [[1, 2]]
        returns = [[0, 0], [1, 0], [-.5, 1]]
        report = native.portfolio_report(factors, returns, [[1, 1]], 1, 2, 252)
        long = report['strategies']['long_G1']
        np.testing.assert_allclose(long['returns'], [.5, 0], atol=1e-14)
        np.testing.assert_allclose(long['nav'], [1.5, 1.5], atol=1e-14)
        np.testing.assert_allclose(long['period_returns'], [.5], atol=1e-14)
        self.assertNotAlmostEqual(long['nav'][-1], np.prod(1+np.mean(returns[1:], axis=1)))
        spread = native.portfolio_report(factors, returns, [[1, 1]], 2, 2, 252)['strategies']['high_low']
        # High factor is B: V=[.5,1.5], daily returns differ from fixed-book daily PnL.
        np.testing.assert_allclose(spread['returns'], [-.5, 2], atol=1e-14)
        np.testing.assert_allclose(spread['constant_book_pnl'], [-.5, .5], atol=1e-14)
        self.assertAlmostEqual(spread['brain_style']['returns'], .25*252*2)
        self.assertAlmostEqual(spread['brain_style']['margin'], .5)

    def test_daily_drawdown_detects_loss_recovered_before_rebalance(self):
        report = native.portfolio_report([[1]], [[0], [0], [-.5], [1]], [[1]], 1, 3, 252)
        long = report['strategies']['long_G1']
        self.assertEqual(report['signal_indices'], [0])
        self.assertEqual(report['daily_indices'], [1, 2, 3])
        np.testing.assert_allclose(long['period_returns'], [0], atol=1e-14)
        np.testing.assert_allclose(long['nav'], [1, .5, 1], atol=1e-14)
        np.testing.assert_allclose(long['drawdown_series'], [0, -.5, 0], atol=1e-14)
        self.assertAlmostEqual(long['max_drawdown'], -.5)

    def test_total_loss_reaches_zero_nav_without_recovery_by_division(self):
        last = native.portfolio_report([[1]], [[0], [-1]], [[1]], 1, 1, 252)['strategies']['long_G1']
        self.assertEqual(last['returns'], [-1])
        self.assertEqual(last['nav'], [0])
        self.assertEqual(last['cagr'], -1)
        self.assertEqual(last['max_drawdown'], -1)
        continued = native.portfolio_report([[1]], [[0], [-1], [.5]], [[1]], 1, 2, 252)['strategies']['long_G1']
        self.assertEqual(continued['returns'], [-1, None])
        self.assertEqual(continued['nav'], [0, None])
        self.assertIsNone(continued['cagr'])

    def test_fixed_book_metrics_survive_an_insolvent_compounded_portfolio(self):
        short = native.portfolio_report([[1]], [[0], [2]], [[1]], 1, 1, 252)['strategies']['short_G1']
        self.assertIsNone(short['returns'][0])
        self.assertIsNone(short['nav'][0])
        self.assertIsNone(short['cagr'])
        self.assertEqual(short['book_pnl_series'], [-2])
        self.assertEqual(short['constant_book_pnl'], [-2])
        self.assertEqual(short['brain_style']['returns'], -1008)
        self.assertEqual(short['brain_style']['drawdown'], 4)
        self.assertEqual(short['brain_style']['margin'], -2)

    def test_no_hindsight_reweighting_and_degenerate_alpha(self):
        factors = [[1, 2, 3]]
        returns = [[0, 0, 0], [.1, 0, None], [0, .2, .3]]
        strategies = native.portfolio_report(factors, returns, [[1]*3], 1, 2, 252)['strategies']
        self.assertEqual(strategies['long_G1']['returns'], [None, None])
        self.assertIsNone(strategies['long_G1']['period_returns'][0])
        self.assertIsNone(strategies['alpha']['brain_style']['margin'])
        self.assertEqual(strategies['alpha']['brain_style']['turnover'], .5)
        self.assertNotIn('inner_high_low', strategies)
        flat = native.portfolio_report([[0, 0, 0]], [[0]*3]*2, [[1]*3], 3, 1, 252)['strategies']['alpha']
        self.assertIsNone(flat['returns'][0])
        self.assertIsNone(flat['brain_style']['sharpe'])

    def test_decay_entry_lag_and_internal_missing_future(self):
        factors, members = [[1, 2, 3]], [[1]*3]
        returns = [[0, 0, 0], [.1, 0, -.1], [-.1, 0, .1]]
        decay = native.ic_decay(factors, returns, members, 1, 3, 3)
        self.assertAlmostEqual(decay[0]['rank_ic']['mean'], -1)
        self.assertAlmostEqual(decay[1]['rank_ic']['mean'], 1)
        self.assertEqual(decay[2]['rank_ic']['n'], 0)
        self.assertEqual([row['entry_lag'] for row in decay], [0, 1, 2])
        missing = [[0]*3, [.1, 0, -.1], [None, .2, -.1], [.2, -.2, .1], [0, .1, -.1]]
        decay = native.ic_decay(factors, missing, members, 2, 2, 3)
        self.assertEqual(decay[0]['ic']['n'], 0)
        self.assertEqual(decay[1]['ic']['n'], 1)

    def test_report_daily_months_and_invalid_options(self):
        dates = ['2024-01-30']
        return_dates = dates + ['2024-01-31', '2024-02-01', '2024-02-02']
        result = build([[1]], [[0], [.1], [.2], [-.1]], [[1]], dates, return_dates,
                       dict(groups=1, horizons=[1, 3, 20], decay_steps=2))
        self.assertEqual(result['version'], 3)
        self.assertEqual(set(result['horizons']), {'1', '3', '20'})
        self.assertEqual(result['horizons']['20']['ic']['n'], 0)
        self.assertIsNone(result['unavailable']['market_cap'])
        horizon = result['horizons']['3']
        self.assertEqual(horizon['portfolio_dates'], return_dates[1:])
        self.assertEqual(horizon['portfolio_exit_dates'], ['2024-02-02'])
        monthly = horizon['portfolios']['long_G1']['monthly']
        self.assertEqual([row['period'] for row in monthly], ['2024-01', '2024-02'])
        self.assertAlmostEqual(monthly[0]['value'], .1)
        self.assertAlmostEqual(monthly[1]['value'], .08)
        for analysis in (dict(horizons=[]), dict(horizons=[1, 1]), dict(horizons=[True]),
                         dict(horizons=[253]), dict(decay_steps=0), dict(annual_days=0)):
            with self.assertRaises(ValueError):
                options(analysis)
        self.assertAlmostEqual(native.compound_return([.1, -.1]), -.01)
        self.assertIsNone(native.compound_return([.1, None]))


if __name__=='__main__':unittest.main()
