from copy import deepcopy
import math
import unittest
from unittest.mock import patch
import numpy as np
import _factorpool_native as native
from factorpool.config import normalize_config
from factorpool.reporting.pnl import add_amounts, format_trace, yearly_statistics
from factorpool.reporting.partition import add_test_period
from factorpool.simulation import alpha_targets
from factorpool.settings import normalize_settings


def config():
    return dict(base=dict(start_date='2024-01-02', end_date='2024-01-04'),
                data=dict(provider='fixture', universe='fixture'))


def brain_reference(book, turnover, annual_days=252):
    """Independent NumPy oracle for fixed-book BRAIN metrics, including gaps."""
    q = np.asarray(book, dtype=np.longdouble)
    traded = np.asarray(turnover, dtype=np.longdouble)
    finite = q[np.isfinite(q)]
    mean = float(np.mean(finite)) if finite.size else math.nan
    with np.errstate(over='ignore', invalid='ignore'):
        sd = float(np.std(finite, ddof=1)) if finite.size > 1 else math.nan
        sharpe = mean / sd * math.sqrt(annual_days) if sd > 0 else math.nan
        returns = 2 * mean * annual_days
        turn = float(np.sum(traded)/len(q)) if len(q) and np.isfinite(traded).all() else math.nan
        fitness = sharpe*math.sqrt(abs(returns)/max(turn, .125)) if math.isfinite(sharpe) and math.isfinite(turn) else math.nan
        cumulative = np.cumsum(q)
        peak = np.maximum.accumulate(np.r_[np.longdouble(0), cumulative])[1:]
        drawdown = float(np.max((peak-cumulative)*2)) if len(q) and np.isfinite(q).all() else math.nan
        margin = float(np.sum(q)/np.sum(traded)) if len(q) and np.isfinite(q).all() and np.isfinite(traded).all() and np.sum(traded)>0 else math.nan
    values = dict(sharpe=sharpe,turnover=turn,fitness=fitness,returns=returns,drawdown=drawdown,margin=margin)
    return {key: value if math.isfinite(value) else None for key,value in values.items()}


def alpha_fixture(book, turnover, **fields):
    alpha = native.performance_stats(book, turnover, book, 252)
    alpha.update(returns=list(book),turnover_series=list(turnover),book_pnl_series=list(book),
                 long_positions=[2]*len(book),short_positions=[3]*len(book),**fields)
    return alpha


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
        p = alpha_fixture(q, [1, .1, .2])
        d = dict(horizons={'1':dict(portfolio_dates=['2024-01-02','2024-01-03','2024-01-04'],
                                  portfolios={'alpha':p}, series={'ic':[.5,-.5,1]})}, conventions={})
        add_test_period(d, 'P2D', '2024-01-04', 252, '2024-01-02')
        original=deepcopy(d)
        add_amounts(d, dict(book_size=20000000,pnl_unit='USD'))
        alpha=d['horizons']['1']['portfolios']['alpha']
        np.testing.assert_allclose(alpha['pnl']['cumulative'], [275000,225000,325000], atol=1e-8)
        np.testing.assert_allclose(d['horizons']['1']['partitions']['train']['alpha']['pnl']['cumulative'], [275000],atol=1e-8)
        self.assertEqual(set(d['horizons']['1']['partitions']['test']['alpha']), {'brain_style'})
        self.assertEqual(set(alpha['pnl']), {'book_size','unit','cumulative'})
        self.assertEqual(alpha['returns'], q)
        self.assertEqual(alpha['nav'], original['horizons']['1']['portfolios']['alpha']['nav'])
        self.assertEqual(alpha['brain_style'], original['horizons']['1']['portfolios']['alpha']['brain_style'])
        self.assertEqual(d['horizons']['1']['series'], original['horizons']['1']['series'])
        self.assertEqual(alpha['pnl']['unit'], 'USD')
        self.assertEqual(d['conventions']['book_size'], 20000000)
        for part in [alpha, *(part['alpha'] for part in d['horizons']['1']['partitions'].values())]:
            for field in ('book_pnl_series','constant_book_pnl','long_positions','short_positions'):
                self.assertNotIn(field, part)

    def test_missing_daily_pnl_remains_missing_in_amounts_and_cumulative(self):
        p=native.performance_stats([.1,None,.2],[1,0,0],[.1,None,.2],252)
        p['book_pnl_series']=[.1,None,.2]
        d=dict(horizons={'1':dict(portfolios={'alpha':p})},conventions={})
        add_amounts(d,dict(book_size=10,pnl_unit='BOOK'))
        self.assertEqual(p['pnl']['cumulative'], [1,None,None])
        self.assertNotIn('daily', p['pnl'])

    def test_yearly_statistics_use_calendar_days_fixed_book_and_actual_position_counts(self):
        dates = ['2023-12-28', '2023-12-29', '2024-01-02', '2024-01-03']
        returns = [.01, -.02, .03, .01]
        traded = [1., .1, .2, .1]
        alpha = native.performance_stats(returns, traded, returns, 252)
        alpha.update(returns=returns, turnover_series=traded, book_pnl_series=returns,
                     long_positions=[2, 4, 6, 4], short_positions=[3, 3, 2, 4])
        with patch.object(native, 'performance_stats', side_effect=AssertionError('Year rows need no NAV or full performance')):
            rows = yearly_statistics(alpha, dates, 252)
        self.assertEqual([row['year'] for row in rows], ['2023', '2024'])
        self.assertEqual((rows[0]['long_count'], rows[0]['short_count']), (3, 3))
        self.assertEqual((rows[1]['long_count'], rows[1]['short_count']), (5, 3))
        self.assertAlmostEqual(rows[0]['sharpe'], np.mean(returns[:2]) / np.std(returns[:2], ddof=1) * np.sqrt(252))
        self.assertAlmostEqual(rows[0]['turnover'], .55)
        self.assertAlmostEqual(rows[0]['returns'], -2.52)
        self.assertAlmostEqual(rows[0]['drawdown'], .04)
        self.assertAlmostEqual(rows[0]['margin'], -.01 / 1.1)
        for row, sl in zip(rows, (slice(0, 2), slice(2, 4))):
            expected = brain_reference(returns[sl], traded[sl])
            for key, value in expected.items():
                if value is None: self.assertIsNone(row[key])
                else: self.assertAlmostEqual(row[key], value)
            self.assertEqual(set(row), {'year','start','end','days','long_count','short_count',*expected})
        missing = dict(alpha, book_pnl_series=[.01, None, .03, .01])
        self.assertIsNone(yearly_statistics(missing, dates, 252)[0]['drawdown'])
        self.assertIsNone(yearly_statistics(missing, dates, 252)[0]['margin'])
        self.assertIsNone(yearly_statistics(dict(alpha, long_positions=[]), dates, 252)[0]['long_count'])
        self.assertIsNone(yearly_statistics(dict(alpha, short_positions=[3,None,2,4]), dates, 252)[0]['short_count'])

    def test_train_yearly_rows_and_test_scalar_summary_use_only_the_selected_window(self):
        dates = ['2023-12-28', '2023-12-29', '2024-01-02', '2024-01-03']
        returns, traded = [.01, -.02, .03, .01], [1., .1, .2, .1]
        alpha = native.performance_stats(returns, traded, returns, 252)
        alpha.update(returns=returns, turnover_series=traded, book_pnl_series=returns,
                     long_positions=[2, 4, 6, 4], short_positions=[3, 3, 2, 4])
        report = dict(horizons={'1':dict(portfolio_dates=dates, portfolios={'alpha':alpha})}, conventions={})
        add_test_period(report, 'P2D', '2024-01-03', 252, '2023-12-28')
        add_amounts(report, dict(book_size=100, pnl_unit='BOOK'))
        parts = report['horizons']['1']['partitions']
        self.assertEqual([row['year'] for row in parts['train']['alpha']['yearly_stats']], ['2023'])
        self.assertEqual(set(parts['test']['alpha']), {'brain_style'})
        for key,value in brain_reference(returns[2:],traded[2:]).items():
            self.assertAlmostEqual(parts['test']['alpha']['brain_style'][key],value)
        self.assertEqual(parts['test']['dates'],dates[2:])
        self.assertNotIn('portfolios',parts['train'])
        self.assertNotIn('portfolios',parts['test'])

    def test_cumulative_amount_uses_extended_precision_for_overflow_and_cancellation(self):
        for pnl in ([1e308, 1e308, -1e308], [1e16, 1., -1e16]):
            with self.subTest(pnl=pnl):
                alpha = native.performance_stats([.1]*len(pnl), [1.]*len(pnl), pnl, 252)
                alpha['book_pnl_series'] = pnl
                diagnostics = dict(horizons={'1':dict(portfolios={'alpha':alpha})},conventions={})
                with np.errstate(over='ignore'):
                    values = np.cumsum(np.asarray(pnl,dtype=np.longdouble)).astype(float)
                expected = [float(value) if np.isfinite(value) else None for value in values]
                add_amounts(diagnostics,dict(book_size=1,pnl_unit='BOOK'))
                self.assertEqual(alpha['pnl']['cumulative'],expected)
                self.assertNotIn('daily',alpha['pnl'])

    def test_lightweight_brain_summary_matches_independent_math_for_zero_gaps_and_drawdown(self):
        cases = [([.01,-.02,.03],[1,.1,.2]), ([0,0,0],[0,0,0]),
                 ([.1,None,.2],[1,0,0]), ([.1,.2,.3],[1,None,0]),
                 ([None,None],[0,0]), ([],[]), ([.1],[0]),
                 ([-2,1.3,.1],[1,.1,.2]), ([.1,-.1,.1],[0,0,0])]
        for book,turnover in cases:
            with self.subTest(book=book,turnover=turnover):
                actual = native.brain_summary(book,turnover,252)
                expected = brain_reference(book,turnover)
                self.assertEqual(set(actual),set(expected))
                for key,value in expected.items():
                    if value is None: self.assertIsNone(actual[key],key)
                    else: self.assertAlmostEqual(actual[key],value,msg=key)
        for book,turnover,annual in [([.1],[],252),([.1],[0],0),([.1],[0],367)]:
            with self.subTest(invalid=(book,turnover,annual)),self.assertRaises(ValueError):
                native.brain_summary(book,turnover,annual)

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
        info=dict(symbols=['A'],transforms={0:dict(decayed=np.array([2.]),neutralized=np.array([-3.]))},
                  factor_arrays=dict(reason=np.array([[3]],dtype=np.uint8),ready=np.array([[False]]),valid=np.array([[True]])))
        actual=format_trace(trace,info,['2024-01-02'],['2024-01-02','2024-01-03'],dict(book_size=20000000,pnl_unit='USD'))[0]
        self.assertEqual((actual['signal_date'],actual['valuation_date']),('2024-01-02','2024-01-03'))
        self.assertEqual(actual['rows'][0]['symbol'],'A')
        self.assertEqual(actual['rows'][0]['decayed_alpha'],2.)
        self.assertEqual(actual['rows'][0]['neutralized'],-3.)
        self.assertAlmostEqual(actual['rows'][0]['pnl'],75000)
        self.assertAlmostEqual(actual['daily_pnl'],275000)
        self.assertEqual(actual['rows'][0]['raw_factor_status'],'not_ready')
        self.assertIs(actual['rows'][0]['raw_factor_ready'],False)
        self.assertIs(actual['rows'][0]['raw_factor_valid'],True)

    def test_requested_last_signal_without_future_prices_has_an_explicit_status(self):
        from factorpool.reporting.diagnostics import build
        prepared=native.prepare_research([[1.,3.,5.,7.]], [[0.]*4], [[1.]*4])
        report=build(prepared,['2024-01-02'],['2024-01-02'],
                     dict(horizons=[1],decay_steps=1),
                     trace_info=dict(indices=[0],symbols=['A','B','C','D']))
        horizon=report['horizons']['1']
        self.assertEqual(horizon['alpha_trace'], [])
        self.assertEqual(horizon['trace_status']['no_future_dates'], ['2024-01-02'])
        self.assertEqual(horizon['portfolio_dates'], [])
