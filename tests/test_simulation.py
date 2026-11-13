"""Independent matrix targets and shared-Indicator window/axis acceptance."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import _factorpool_native as native
from factortrader import NativeVector, PythonIndicator as SharedIndicator
from factorpool.adapter import IndicatorAdapter
from factorpool.runner import run
from factorpool.settings import normalize_settings
from factorpool.simulation import alpha_targets


DATA = dict(provider='fixture', universe='fixture')


class SharedDecayReference(SharedIndicator):
    def __init__(self, period=3):
        super().__init__()
        self.period = period

    def update(self, input):
        value = self.ts.decay_linear(input.close, self.period)
        self.value = value
        return self.ts.is_ready(value)


def shared_decay_values(factors, members, period):
    reference = SharedDecayReference(period)
    reference._prepare()
    out = np.full(factors.shape, np.nan)
    for t, row in enumerate(factors):
        symbols = [f'S{n:04d}' for n in range(factors.shape[1]) if members[t, n] > 0]
        rows = {f'S{n:04d}': dict(open=value, high=value, low=value, close=value, volume=1)
                for n, value in enumerate(row) if members[t, n] > 0 and np.isfinite(value)}
        frame = dict(date=(date(2024, 1, 2)+timedelta(days=t)).isoformat(), symbols=symbols, rows=rows)
        # This is an explicit fixed-SDK numerical oracle, not a Pool user
        # factor entry or a route through IndicatorAdapter.
        ids = tuple(sorted(symbols))
        if reference._graph is None:
            reference._activate(ids, batch=True)
        columns = {field: [rows.get(symbol, {}).get(field, float('nan')) for symbol in ids]
                   for field in reference._manual_fields}
        stamp = int(frame['date'].replace('-', '')+'150000')
        reference._update_native_columns(stamp, columns, symbols=ids,
                                         valid=[symbol in rows for symbol in ids])
        state = reference._binding.graph_snapshot(reference._graph)
        current = NativeVector._view(reference._binding, state['current']).to_numpy()
        valid = NativeVector._view(reference._binding, state['valid']).to_numpy()
        ready = NativeVector._view(reference._binding, state['ready']).to_numpy()
        for i, symbol in enumerate(state['symbols']):
            if valid[i] and ready[i] and np.isfinite(current[i]):
                out[t, int(symbol[1:])] = current[i]
    return out


def reference_targets(values, members, neutralization='MARKET', cap=0):
    out = np.zeros(values.shape)
    for t, row in enumerate(values):
        selected = np.isfinite(row) & np.isfinite(members[t]) & (members[t] > 0)
        data = row[selected].copy()
        if neutralization == 'MARKET' and len(data):
            data -= np.mean(data)
        norm = np.sum(abs(data))
        if not norm > 0:
            out[t] = np.nan
        else:
            out[t, selected] = data/norm
            if cap:
                out[t] = np.clip(out[t], -cap, cap)
    return out


class AlphaTargetTests(unittest.TestCase):
    def test_default_controls_preserve_the_original_native_report(self):
        factors = np.array([[1., 2, 3], [2, 3, 1], [1, 3, 2]])
        returns = np.array([[0., 0, 0], [.1, 0, -.1], [-.1, .2, 0], [.02, -.01, 0]])
        members = np.ones_like(factors)
        weights = alpha_targets(factors, members, normalize_settings({}, DATA))
        self.assertIsNone(weights)
        before = native.prepare_research(factors, returns, members)
        after = native.prepare_research(factors, returns, members, weights)
        self.assertEqual(native.analyze(before, None, 3, 3, 1), native.analyze(after, None, 3, 3, 1))
        self.assertEqual(native.portfolio_report(before, None, None, 3, 1, 252),
                         native.portfolio_report(after, None, None, 3, 1, 252))

    def test_none_market_and_truncation_change_actual_weights_without_relevering(self):
        factors = np.array([[1., 2, 3], [-3, 1, 2], [5, 5, 5], [0, 0, 0], [np.nan]*3])
        members = np.ones_like(factors)
        before = factors.copy()
        for neutralization in ('NONE', 'MARKET'):
            for cap in (0, .2):
                if neutralization == 'MARKET' and cap == 0:
                    continue  # The default takes the existing native path.
                with self.subTest(neutralization=neutralization, cap=cap):
                    actual = alpha_targets(factors, members, normalize_settings(
                        dict(neutralization=neutralization, truncation=cap), DATA))
                    expected = reference_targets(factors, members, neutralization, cap)
                    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-15, equal_nan=True)
                    self.assertEqual(actual.dtype, np.dtype('float64'))
                    self.assertTrue(actual.flags.c_contiguous)
                    if cap:
                        self.assertLessEqual(np.nanmax(abs(actual)), cap)
                        self.assertLess(np.sum(abs(actual[0])), 1)
        np.testing.assert_array_equal(factors, before)
        # Missing or nonmember securities have no target; zero stays a real input.
        masked = np.array([[1., np.nan, 3, 100]])
        membership = np.array([[1., 1, 1, 0]])
        actual = alpha_targets(masked, membership, normalize_settings(dict(truncation=.4), DATA))
        np.testing.assert_allclose(actual, [[-.4, 0, .4, 0]], atol=1e-15)

    def test_decay_uses_shared_window_and_has_no_future_dependency(self):
        factors = np.array([[1., 3, 2], [2, 2, 3], [3, 1, 4], [6, 2, 5]])
        members = np.ones_like(factors)
        settings = normalize_settings(dict(decay=3), DATA)
        actual = alpha_targets(factors, members, settings)
        self.assertTrue(np.isnan(actual[:2]).all())
        # Independent dense linear window: weights [1,2,3] on oldest..newest.
        np.testing.assert_allclose(actual[2], [-.0625, -.4375, .5], atol=2e-14)
        oracle = shared_decay_values(factors, members, 3)
        np.testing.assert_allclose(actual, reference_targets(oracle, members), atol=2e-14, equal_nan=True)
        prefix = alpha_targets(factors[:3], members[:3], settings)
        np.testing.assert_allclose(prefix, actual[:3], atol=2e-14, equal_nan=True)
        changed_future = factors.copy(); changed_future[3] = [1000, -1000, 0]
        np.testing.assert_allclose(alpha_targets(changed_future, members, settings)[:3], prefix, atol=2e-14, equal_nan=True)

    def test_decay_missing_observations_and_membership_reset_match_shared_indicator(self):
        factors = np.array([[1., 2, 3], [2, 4, 6], [np.nan, 6, 9], [4, 8, 12],
                            [5, 10, 15], [6, 12, 18], [7, 14, 21]])
        members = np.ones_like(factors); members[4, 0] = 0
        settings = normalize_settings(dict(decay=2), DATA)
        actual = alpha_targets(factors, members, settings)
        decayed = shared_decay_values(factors, members, 2)
        expected = reference_targets(decayed, members)
        np.testing.assert_allclose(actual, expected, atol=2e-14, equal_nan=True)
        self.assertTrue(np.isnan(actual[0]).all())
        self.assertEqual(actual[2, 0], 0)  # Missing today is not a new zero observation.
        self.assertEqual(actual[4, 0], 0)
        self.assertEqual(actual[5, 0], 0)  # Reentry starts a fresh two-input window.


class SettingsIntegrationTests(unittest.TestCase):
    def test_script_settings_change_alpha_but_preserve_raw_values_ic_and_other_portfolios(self):
        template = '''from factorpool import PythonIndicator
config = CONFIG
class CloseFactor(PythonIndicator):
    def update(self, input):
        self.value = input.close
        return True
if __name__ == "__main__":
    CloseFactor.run(config)
'''
        reports, exports = [], []
        for supplied in ({}, dict(neutralization='NONE', truncation=.2),
                         dict(neutralization='NONE', truncation=.2, visualization=False)):
            config = dict(base=dict(start_date='2024-01-02', end_date='2024-01-04'),
                          data=deepcopy(DATA), analysis=dict(groups=3, horizons=[1], decay_steps=1),
                          settings=supplied)
            source = template.replace('CONFIG', repr(config))
            with tempfile.TemporaryDirectory() as temporary, \
                    patch('factortrader.Engine', side_effect=AssertionError('Trading Engine constructed')):
                directory = Path(temporary)
                (directory/'request.json').write_text(json.dumps(dict(name='settings integration', source_mode='script', source_code=source)))
                run(directory)
                report = json.loads((directory/'result.json').read_text())
                reports.append(report)
                exports.append((directory/'values.jsonl').read_text())
                self.assertEqual(report['metadata']['source_config'], config)
                self.assertEqual(report['metadata']['effective_config']['settings'], report['metadata']['brain_settings'])
        self.assertEqual(exports[0], exports[1])
        self.assertEqual(exports[1], exports[2])
        self.assertFalse(reports[2]['metadata']['brain_settings']['visualization'])
        self.assertEqual(reports[1]['diagnostics']['horizons'], reports[2]['diagnostics']['horizons'])
        self.assertEqual(reports[0]['summary'], reports[1]['summary'])
        self.assertEqual(reports[0]['daily'], reports[1]['daily'])
        original = reports[0]['diagnostics']['horizons']['1']['portfolios']
        changed = reports[1]['diagnostics']['horizons']['1']['portfolios']
        self.assertNotEqual(original['alpha']['returns'], changed['alpha']['returns'])
        self.assertLessEqual(max(changed['alpha']['max_weight_series']), .2)
        for name in original:
            if name != 'alpha':
                self.assertEqual(original[name], changed[name])


class NativeTargetTests(unittest.TestCase):
    def test_custom_alpha_weights_are_owned_and_leave_raw_ic_groups_unchanged(self):
        factors = np.array([[np.nan, 1., 2], [3, 2, 1]])
        returns = np.array([[0., 0, 0], [.1, 0, -.2], [.2, .1, 0]])
        members = np.ones_like(factors)
        weights = np.array([[.2, 0, -.1], [.1, .1, 0]])
        before = native.prepare_research(factors, returns, members)
        after = native.prepare_research(factors, returns, members, weights)
        self.assertEqual(native.analyze(before, None, 3, 3, 1), native.analyze(after, None, 3, 3, 1))
        old = native.portfolio_report(before, None, None, 3, 1, 252)['strategies']
        new = native.portfolio_report(after, None, None, 3, 1, 252)['strategies']
        for name in old:
            if name != 'alpha':
                self.assertEqual(old[name], new[name])
        alpha = new['alpha']
        np.testing.assert_allclose(alpha['returns'], [.04, .03], atol=1e-14)
        np.testing.assert_allclose(alpha['turnover_series'], [.3, .3], atol=1e-14)
        np.testing.assert_allclose(alpha['max_weight_series'], [.2, .1], atol=1e-14)
        self.assertEqual(alpha['positions'], [2, 2])
        self.assertNotEqual(alpha['returns'], old['alpha']['returns'])
        weights[:] = 100
        self.assertEqual(native.portfolio_report(after, None, None, 3, 1, 252)['strategies'], new)
        stats = native.performance_stats(alpha['returns'], alpha['turnover_series'], alpha['book_pnl_series'], 252)
        for key, value in stats.items():
            self.assertEqual(value, alpha[key])

    def test_invalid_target_axes_partial_missing_gross_and_pool_violations_fail(self):
        factors, returns, members = [[1., 2, 3]], [[0.]*3, [.1, 0, -.1]], [[1., 1, 0]]
        for weights in ([[.1, .2]], [[.1, .2, 0], [.1, .2, 0]], [[.1, np.nan, 0]],
                        [[.1, np.inf, 0]], [[1., 1, 0]], [[.1, 0, .1]]):
            with self.subTest(weights=weights):
                with self.assertRaises(ValueError):
                    native.prepare_research(factors, returns, members, weights)
        for weights in ([[np.nan]*3], [[0.]*3]):
            prepared = native.prepare_research(factors, returns, members, weights)
            alpha = native.portfolio_report(prepared, None, None, 3, 1, 252)['strategies']['alpha']
            self.assertIsNone(alpha['returns'][0])
        with self.assertRaises(ValueError):
            native.performance_stats([.1], [1, 0], [.1], 252)


class CalculationUniverseTests(unittest.TestCase):
    def test_pasteurization_off_uses_outside_securities_for_cs_but_exports_only_research_pool(self):
        source_template = '''from factorpool import PythonIndicator
config = CONFIG
class PoolRank(PythonIndicator):
    def update(self, input):
        self.value = self.cs.rank_unit(input.volume, normalization='percentile')
        return True
if __name__ == "__main__":
    PoolRank.run(config)
'''
        data = dict(provider='market_matrix', universe=['A', 'B'], snapshot_id='synthetic-off-snapshot')
        results = {}
        for mode in ('ON', 'OFF'):
            symbols = ['A', 'B', 'C'] if mode == 'OFF' else ['A', 'B']
            frames = []
            for i in range(3):
                rows = {s: dict(open=10, high=10, low=10, close=10, prev_close=10,
                                volume={'A':10, 'B':20, 'C':0}[s]) for s in symbols}
                frames.append(dict(date=f'2024-01-0{i+2}', symbols=symbols.copy(), rows=rows,
                                   research_symbols=['A', 'B']))
            payload = dict(provider='market_matrix', adjustment='raw', symbols=['A', 'B'], frames=frames,
                           returns=np.array([[0., 0], [.1, -.1], [0, 0]]),
                           snapshot_id='synthetic-off-snapshot', synthetic=False)
            config = dict(base=dict(start_date='2024-01-02', end_date='2024-01-02'), data=data,
                          analysis=dict(groups=2, horizons=[1], min_ic_pairs=2, decay_steps=1),
                          settings=dict(pasteurization=mode))
            source = source_template.replace('CONFIG', repr(config))
            held = []
            def capture(indicator):
                adapter = IndicatorAdapter(indicator); adapter.binding = Mock(wraps=adapter.binding); held.append(adapter); return adapter
            with tempfile.TemporaryDirectory() as temporary, \
                    patch('factorpool.market_store.load', return_value=payload) as load, \
                    patch('factorpool.factor_matrix.IndicatorAdapter', side_effect=capture), \
                    patch('factortrader.Engine', side_effect=AssertionError('Trading Engine constructed')):
                directory = Path(temporary)
                (directory/'request.json').write_text(json.dumps(dict(name='calculation axis', source_mode='script', source_code=source)))
                run(directory)
                values = [json.loads(line) for line in (directory/'values.jsonl').read_text().splitlines()]
                self.assertEqual([row['symbol'] for row in values], ['A', 'B'])
                results[mode] = [row['value'] for row in values]
                if mode == 'OFF':
                    self.assertEqual(load.call_args.kwargs, dict(matrix_only=True,pasteurization='OFF'))
                    adapter = held[-1]
                    state = adapter.binding.graph_snapshot(adapter.graph)
                    self.assertEqual(list(state['symbols']), ['A', 'B', 'C'])
                    self.assertEqual(adapter.binding.graph_update.call_count, 1)
                self.assertEqual(len(values), 2)
        np.testing.assert_allclose(results['ON'], [.5, 1], atol=1e-14)
        np.testing.assert_allclose(results['OFF'], [2/3, 1], atol=1e-14)


def partition_reference(returns, turnover, book):
    values = np.asarray(returns, dtype=float)
    book = np.asarray(book, dtype=float)
    nav = np.cumprod(1+values)
    peaks = np.maximum.accumulate(np.r_[1., nav])[1:]
    volatility = np.std(values, ddof=1)*np.sqrt(252)
    arithmetic = np.cumsum(book)
    book_peak = np.maximum.accumulate(np.r_[0., arithmetic])[1:]
    return dict(n=len(values), missing=0, annual_return=float(np.mean(values)*252),
                annual_volatility=float(volatility),
                sharpe=float(np.mean(values)/np.std(values, ddof=1)*np.sqrt(252)),
                cagr=float(np.prod(1+values)**(252/len(values))-1),
                max_drawdown=float(np.min(nav/peaks-1)),
                brain_style=dict(returns=float(np.mean(book)*504),
                                 turnover=float(np.mean(turnover)),
                                 margin=float(np.sum(book)/np.sum(turnover)),
                                 drawdown=float(np.max(book_peak-arithmetic)*2)),
                nav=nav.tolist(), drawdown_series=(nav/peaks-1).tolist(),
                constant_book_pnl=arithmetic.tolist(),
                constant_book_drawdown=((arithmetic-book_peak)*2).tolist())


def partition_fixture():
    dates = ['2024-02-27', '2024-02-28', '2024-02-29', '2024-03-01', '2024-03-04']
    returns, turnover = [.1, -.1, .2, -.2, .05], [1., .1, .2, .2, .1]
    alpha = partition_reference(returns, turnover, returns)
    alpha.update(returns=returns, turnover_series=turnover, book_pnl_series=returns.copy(),
                 positions=[2]*5, max_weight_series=[.5]*5)
    opposite = [-value for value in returns]
    other = partition_reference(opposite, turnover, opposite)
    other.update(returns=opposite, turnover_series=turnover.copy(), book_pnl_series=opposite.copy())
    return dict(version=3, horizons={'1': dict(portfolio_dates=dates, portfolios=dict(alpha=alpha, long_G1=other),
                                              series=dict(ic=[.1, .2], rank_ic=[.2, .3]))})


class TestPeriodTests(unittest.TestCase):
    def test_calendar_boundaries_include_leap_months_weeks_and_explicit_zero(self):
        from factorpool.reporting.partition import test_start
        self.assertEqual(test_start('2024-03-31', 'P1M'), '2024-03-01')
        self.assertEqual(test_start('2023-03-31', 'P1M'), '2023-03-01')
        self.assertEqual(test_start('2024-02-29', 'P1Y'), '2023-03-01')
        self.assertEqual(test_start('2024-03-31', 'P2W'), '2024-03-18')
        self.assertIsNone(test_start('2024-03-31', 'P0Y0M'))
        self.assertIsNone(test_start('2024-03-31', 'P0D'))
        for period in ('P', 'PT1H', 'P-1M'):
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    test_start('2024-03-31', period)

    def test_explicit_zero_reuses_full_statistics_and_leaves_curves_unchanged(self):
        from factorpool.reporting.partition import add_test_period
        diagnostics = partition_fixture()
        before = deepcopy(diagnostics)
        with patch.object(native, 'performance_stats', side_effect=AssertionError('Default period must reuse existing statistics')):
            add_test_period(diagnostics, 'P0Y0M', '2024-03-01', 252)
        horizon = diagnostics['horizons']['1']
        self.assertIsNone(diagnostics['test_period']['start'])
        self.assertEqual(set(horizon['partitions']), {'all'})
        self.assertEqual(horizon['partitions']['all']['count'], 5)
        self.assertEqual(horizon['portfolios'], before['horizons']['1']['portfolios'])
        self.assertEqual(horizon['series'], before['horizons']['1']['series'])
        self.assertEqual(horizon['partitions']['all']['alpha']['brain_style'], horizon['portfolios']['alpha']['brain_style'])
        self.assertNotIn('nav', horizon['partitions']['all']['alpha'])

    def test_train_test_statistics_match_independent_daily_math_without_changing_full_report(self):
        from factorpool.reporting.partition import add_test_period
        diagnostics = partition_fixture()
        before = deepcopy(diagnostics)
        add_test_period(diagnostics, 'P2D', '2024-03-01', 252)
        self.assertEqual(diagnostics['test_period']['start'], '2024-02-29')
        horizon = diagnostics['horizons']['1']
        for key, value in before['horizons']['1'].items():
            self.assertEqual(horizon[key], value)
        sections = horizon['partitions']
        self.assertEqual((sections['train']['start'], sections['train']['end'], sections['train']['count']),
                         ('2024-02-27', '2024-02-28', 2))
        # Future valuation quotes remain in All, outside the configured Test window.
        self.assertEqual((sections['test']['start'], sections['test']['end'], sections['test']['count']),
                         ('2024-02-29', '2024-03-01', 2))
        self.assertEqual(sections['train']['outside_window_count'], 1)
        self.assertEqual(sections['test']['outside_window_count'], 1)
        alpha = horizon['portfolios']['alpha']
        for name, index in (('train', slice(0, 2)), ('test', slice(2, 4))):
            expected = partition_reference(alpha['returns'][index], alpha['turnover_series'][index], alpha['book_pnl_series'][index])
            actual = sections[name]['alpha']
            for key in ('annual_return', 'annual_volatility', 'sharpe', 'cagr', 'max_drawdown'):
                self.assertAlmostEqual(actual[key], expected[key], places=10)
            for key in ('returns', 'turnover', 'margin', 'drawdown'):
                self.assertAlmostEqual(actual['brain_style'][key], expected['brain_style'][key], places=10)
            self.assertEqual(actual['n'], expected['n'])
            self.assertEqual(sections[name]['dates'], horizon['portfolio_dates'][index])
            for key in ('nav', 'drawdown_series', 'constant_book_pnl', 'constant_book_drawdown'):
                np.testing.assert_allclose(actual[key], expected[key], atol=2e-14)
            for key in ('returns', 'turnover_series', 'book_pnl_series', 'positions', 'max_weight_series'):
                self.assertEqual(actual[key], alpha[key][index])
            other = sections[name]['portfolios']['long_G1']
            self.assertEqual(other['returns'], horizon['portfolios']['long_G1']['returns'][index])
            self.assertEqual(len(other['nav']), sections[name]['count'])
        np.testing.assert_allclose([row['value'] for row in sections['train']['alpha']['monthly']], [-.01], atol=1e-14)
        np.testing.assert_allclose([row['value'] for row in sections['test']['alpha']['monthly']], [.2, -.2], atol=1e-14)
        self.assertEqual([row['period'] for row in sections['test']['alpha']['monthly']], ['2024-02', '2024-03'])
        self.assertAlmostEqual(sections['test']['alpha']['yearly'][0]['value'], -.04)
        empty = partition_fixture()
        add_test_period(empty, 'P10Y', '2024-03-01', 252)
        train = empty['horizons']['1']['partitions']['train']
        self.assertEqual(train['count'], 0)
        self.assertIsNone(train['start'])
        self.assertIsNone(train['end'])
        self.assertIsNone(train['alpha']['sharpe'])

    def test_validation_window_excludes_before_start_and_future_valuation_tail(self):
        from factorpool.reporting.partition import add_test_period
        diagnostics = partition_fixture()
        before = deepcopy(diagnostics)
        add_test_period(diagnostics, 'P1D', '2024-03-01', 252, factor_start='2024-02-28')
        window = diagnostics['test_period']
        self.assertEqual(window['start'], '2024-03-01')
        self.assertEqual(window['evaluation_start'], '2024-02-28')
        self.assertEqual(window['evaluation_end'], '2024-03-01')
        horizon = diagnostics['horizons']['1']
        self.assertEqual(horizon['portfolio_dates'], before['horizons']['1']['portfolio_dates'])
        self.assertEqual(horizon['portfolios'], before['horizons']['1']['portfolios'])
        self.assertEqual(horizon['series'], before['horizons']['1']['series'])
        train, test = horizon['partitions']['train'], horizon['partitions']['test']
        self.assertEqual(train['dates'], ['2024-02-28', '2024-02-29'])
        self.assertEqual(test['dates'], ['2024-03-01'])
        self.assertEqual((train['count'], test['count']), (2, 1))
        self.assertEqual((train['outside_window_count'], test['outside_window_count']), (2, 2))
        self.assertEqual(test['alpha']['returns'], [-.2])
        np.testing.assert_allclose(test['alpha']['nav'], [.8], atol=1e-14)
        np.testing.assert_allclose(train['alpha']['nav'], np.cumprod([.9, 1.2]), atol=1e-14)
        self.assertEqual(test['portfolios']['long_G1']['returns'], [.2])
        self.assertEqual(train['portfolios']['long_G1']['returns'], [.1, -.2])
        self.assertEqual(len(horizon['portfolios']['alpha']['nav']), 5)
        self.assertEqual(horizon['portfolio_dates'][-1], '2024-03-04')
