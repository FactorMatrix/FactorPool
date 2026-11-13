from copy import deepcopy
import math
import unittest
import numpy as np
import _factorpool_native as native
from factorpool.config import normalize_config
from factorpool.reporting.pnl import add_amounts, format_trace
from factorpool.reporting.partition import add_test_period
from factorpool.simulation import alpha_targets
from factorpool.settings import normalize_settings


def config():
    return dict(base=dict(start_date='2024-01-02', end_date='2024-01-04'),
                data=dict(provider='fixture', universe='fixture'))


class PnlReportingTests(unittest.TestCase):
    def test_book_and_trace_defaults_are_explicit_and_original_config_is_immutable(self):
        source = config()
        parsed = normalize_config(source)
        self.assertEqual(parsed['analysis']['book_size'], 1)
        self.assertEqual(parsed['analysis']['pnl_unit'], 'BOOK')
        self.assertEqual(parsed['analysis']['trace_dates'], [])
        self.assertEqual(parsed['source_config'], source)
        source['analysis'] = dict(book_size=20000000, pnl_unit='USD', trace_dates=['20240104', '2024-01-02'])
        parsed = normalize_config(source)
        self.assertEqual(parsed['analysis']['trace_dates'], ['2024-01-02', '2024-01-04'])
        self.assertEqual(parsed['source_config']['analysis']['trace_dates'], ['20240104', '2024-01-02'])

    def test_invalid_book_labels_and_trace_dates_are_rejected(self):
        for key, value in [('book_size', 0), ('book_size', True), ('book_size', -1),
                           ('book_size', 10**400), ('book_size', float('inf')),
                           ('pnl_unit', ''), ('pnl_unit', 'usd'), ('pnl_unit', 'USD<script>'),
                           ('trace_dates', ['2024-01-01']), ('trace_dates', ['2024-01-05']),
                           ('trace_dates', ['2024-01-02', '20240102']), ('trace_dates', [None]),
                           ('trace_dates', ['2024-01-02']*6)]:
            with self.subTest(key=key, value=str(value)[:30]):
                source=config(); source['analysis']={key:value}
                with self.assertRaises(ValueError): normalize_config(source)

    def test_fixed_book_amounts_are_arithmetic_and_leave_returns_ic_and_partitions_unchanged(self):
        q = [.01375, -.0025, .005]
        p = native.performance_stats(q, [1, .1, .2], q, 252)
        p.update(returns=q, book_pnl_series=q, turnover_series=[1,.1,.2])
        d = dict(horizons={'1':dict(portfolio_dates=['2024-01-02','2024-01-03','2024-01-04'],
                                  portfolios={'alpha':p}, series={'ic':[.5,-.5,1]})}, conventions={})
        add_test_period(d, 'P2D', '2024-01-04', 252, '2024-01-02')
        original=deepcopy(d)
        add_amounts(d, dict(book_size=20000000,pnl_unit='USD'))
        alpha=d['horizons']['1']['portfolios']['alpha']
        np.testing.assert_allclose(alpha['pnl']['daily'], [275000,-50000,100000], atol=1e-8)
        np.testing.assert_allclose(alpha['pnl']['cumulative'], [275000,225000,325000], atol=1e-8)
        np.testing.assert_allclose(d['horizons']['1']['partitions']['test']['alpha']['pnl']['cumulative'], [-50000,50000],atol=1e-8)
        self.assertEqual(alpha['returns'], q)
        self.assertEqual(alpha['nav'], original['horizons']['1']['portfolios']['alpha']['nav'])
        self.assertEqual(alpha['brain_style'], original['horizons']['1']['portfolios']['alpha']['brain_style'])
        self.assertEqual(d['horizons']['1']['series'], original['horizons']['1']['series'])
        self.assertEqual(alpha['pnl']['unit'], 'USD')
        self.assertEqual(d['conventions']['book_size'], 20000000)

    def test_missing_daily_pnl_remains_missing_in_amounts_and_cumulative(self):
        p=native.performance_stats([.1,None,.2],[1,0,0],[.1,None,.2],252)
        p['book_pnl_series']=[.1,None,.2]
        d=dict(horizons={'1':dict(portfolios={'alpha':p})},conventions={})
        add_amounts(d,dict(book_size=10,pnl_unit='BOOK'))
        self.assertEqual(p['pnl']['daily'], [1,None,2])
        self.assertEqual(p['pnl']['cumulative'], [1,None,None])

    def test_truncation_reports_infeasible_capacity_actual_gross_and_unallocated_fraction(self):
        F=np.array([[1.,3.,5.,7.],[2.,2.,2.,2.],[0.,0.,0.,0.]])
        settings=normalize_settings(dict(truncation=.08),{'universe':'fixture'})
        details={}
        W=alpha_targets(F,np.ones_like(F),settings,details=details,trace_indices=[0])
        np.testing.assert_allclose(W[0],[-.08,-.08,.08,.08])
        report=details['constraints'][0]
        self.assertFalse(report['full_gross_feasible'])
        self.assertEqual(report['status'],'unit_gross_capacity_infeasible')
        self.assertAlmostEqual(report['capacity'],.32)
        self.assertAlmostEqual(report['target_gross'],.32)
        self.assertAlmostEqual(report['unallocated_gross'],.68)
        for item in details['constraints'][1:]:
            self.assertEqual(item['status'],'unit_gross_capacity_infeasible')
            self.assertEqual(item['target_gross'],0)
            self.assertEqual(item['target_net'],0)
            self.assertEqual(item['unallocated_gross'],1)
        np.testing.assert_array_equal(W[1:],np.zeros((2,4)))
        np.testing.assert_allclose(details['trace'][0]['neutralized'],[-3,-1,1,3])

    def test_trace_formats_actual_date_symbol_transforms_and_nominal_amount(self):
        trace=[dict(signal_index=0,valuation_index=1,pnl_fraction=.01375,status='ok',rows=[dict(symbol_index=0,raw_alpha=1.,neutralized=None,pnl_fraction=.00375)])]
        info=dict(symbols=['A'],transforms={0:dict(decayed=np.array([2.]),neutralized=np.array([-3.]))})
        actual=format_trace(trace,info,['2024-01-02'],['2024-01-02','2024-01-03'],dict(book_size=20000000,pnl_unit='USD'))[0]
        self.assertEqual((actual['signal_date'],actual['valuation_date']),('2024-01-02','2024-01-03'))
        self.assertEqual(actual['rows'][0]['symbol'],'A')
        self.assertEqual(actual['rows'][0]['decayed_alpha'],2.)
        self.assertEqual(actual['rows'][0]['neutralized'],-3.)
        self.assertAlmostEqual(actual['rows'][0]['pnl'],75000)
        self.assertAlmostEqual(actual['daily_pnl'],275000)

    def test_requested_last_signal_without_future_prices_has_an_explicit_status(self):
        from factorpool.reporting.diagnostics import build
        prepared=native.prepare_research([[1.,3.,5.,7.]], [[0.]*4], [[1.]*4])
        report=build(prepared,None,None,['2024-01-02'],['2024-01-02'],
                     dict(horizons=[1],decay_steps=1),
                     trace_info=dict(indices=[0],symbols=['A','B','C','D']))
        horizon=report['horizons']['1']
        self.assertEqual(horizon['alpha_trace'], [])
        self.assertEqual(horizon['trace_status']['no_future_dates'], ['2024-01-02'])
        self.assertEqual(horizon['portfolio_dates'], [])
