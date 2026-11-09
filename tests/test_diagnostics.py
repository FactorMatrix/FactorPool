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
        f=[[1,2,3]]*6
        prices=[[10.,20.,30.]]
        for t in range(10):prices.append([prices[-1][0]*1.01,prices[-1][1],prices[-1][2]*.99])
        m=[[1,1,1]]*6
        raw=native.analyze(f,prices,3,3,1,m,5)
        np.testing.assert_allclose(raw['forward_returns'][0],[(1.01**5)-1,0,(.99**5)-1])
        self.assertTrue(all(x is None for x in raw['daily'][0]['cumulative']))
        portfolios=native.portfolio_report(f,prices,m,3,5,252)
        self.assertEqual(portfolios['signal_indices'],[0,5])
        expected=[.5*((.99**5)-(1.01**5)),None]
        self.assertAlmostEqual(portfolios['strategies']['high_low']['returns'][0],expected[0])
        self.assertIsNone(portfolios['strategies']['high_low']['returns'][1])
        self.assertIsNone(portfolios['strategies']['high_low']['cagr'])

    def test_long_short_brain_and_price_drift_turnover(self):
        # A returns +10%, B 0%, C -10%, then A -20%, C +10%.
        f=[[1,.5,0],[1,.5,0],[1,.5,0]]
        p=[[10,20,30],[10,20,30],[11,20,27],[8.8,20,29.7],[9.68,20,29.7]]
        report=native.portfolio_report(f,p,[[1]*3]*3,3,1,252)['strategies']
        np.testing.assert_allclose(report['long_G3']['returns'],[.1,-.2,.1],atol=1e-14)
        np.testing.assert_allclose(report['short_G3']['returns'],[-.1,.2,-.1],atol=1e-14)
        spread=report['high_low']
        np.testing.assert_allclose(spread['returns'],[.1,-.15,.05],atol=1e-14)
        np.testing.assert_allclose(spread['turnover_series'],[1,.1,.15],atol=1e-14)
        expected_nav=np.cumprod(1+np.array([.1,-.15,.05]))
        np.testing.assert_allclose(spread['nav'],expected_nav)
        self.assertAlmostEqual(spread['max_drawdown'],-.15)
        b=spread['brain_style']
        mu=np.mean([.1,-.15,.05]);sigma=np.std([.1,-.15,.05],ddof=1)
        self.assertAlmostEqual(b['returns'],mu*252*2)
        self.assertAlmostEqual(b['turnover'],1.25/3)
        self.assertAlmostEqual(b['sharpe'],mu/sigma*np.sqrt(252))
        self.assertAlmostEqual(b['margin'],0/1.25,places=14)
        self.assertAlmostEqual(b['drawdown'],.3)
        self.assertAlmostEqual(spread['win_rate'],2/3)
        self.assertAlmostEqual(spread['payoff_ratio'],.075/.15)

    def test_no_hindsight_reweighting_and_degenerate_alpha(self):
        f=[[1,2,3]]
        p=[[10,20,30],[10,20,30],[11,20,None]]
        s=native.portfolio_report(f,p,[[1,1,1]],1,1,252)['strategies']
        # Legacy paired-value group mean would use A/B; portfolio retains C.
        self.assertIsNone(s['long_G1']['returns'][0])
        self.assertIsNone(s['alpha']['brain_style']['margin'])
        self.assertEqual(s['alpha']['brain_style']['turnover'],1)
        self.assertNotIn('inner_high_low',s)
        flat=native.portfolio_report([[0,0,0]],[[1]*3]*3,[[1]*3],3,1,252)['strategies']['alpha']
        self.assertIsNone(flat['returns'][0])
        self.assertIsNone(flat['brain_style']['sharpe'])

    def test_decay_entry_lag_and_missing_future(self):
        f=[[1,2,3]];m=[[1]*3]
        p=[[10,20,30],[10,20,30],[11,20,27],[9.9,20,29.7]]
        decay=native.ic_decay(f,p,m,1,3,3)
        self.assertAlmostEqual(decay[0]['rank_ic']['mean'],-1)
        self.assertAlmostEqual(decay[1]['rank_ic']['mean'],1)
        self.assertEqual(decay[2]['rank_ic']['n'],0)
        self.assertEqual([r['entry_lag'] for r in decay],[0,1,2])

    def test_report_shapes_units_missing_months_and_invalid_options(self):
        dates=['2024-01-30','2024-01-31']
        pdates=dates+['2024-02-01','2024-02-02']
        result=build([[1,2,3]]*2,[[10,20,30],[10,20,30],[11,20,27],[12,20,24]],[[1]*3]*2,dates,pdates,dict(groups=3,horizons=[1,5],decay_steps=2))
        self.assertEqual(set(result['horizons']),{'1','5'})
        self.assertEqual(result['horizons']['5']['ic']['n'],0)
        self.assertIsNone(result['unavailable']['market_cap'])
        self.assertEqual(result['horizons']['1']['portfolios']['long_G3']['monthly'][0]['period'],'2024-02')
        self.assertEqual(result['horizons']['1']['portfolio_exit_dates'],['2024-02-01','2024-02-02'])
        for a in (dict(horizons=[]),dict(horizons=[1,1]),dict(horizons=[True]),dict(horizons=[253]),dict(decay_steps=0),dict(annual_days=0)):
            with self.assertRaises(ValueError):options(a)
        self.assertAlmostEqual(native.compound_return([.1,-.1]),-.01)
        self.assertIsNone(native.compound_return([.1,None]))


if __name__=='__main__':unittest.main()
