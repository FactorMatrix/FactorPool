"""Shared Graph synchronization acceptance using only synthetic daily inputs.

These tests establish FactorPool/FactorTrader contracts, not official BRAIN
threading, partial-window behavior, or numerical parity.
"""
from collections import deque
from datetime import date, timedelta
import unittest
import os

import numpy as np
from scipy.stats import rankdata
from factortrader import NativeVector
from factorpool import PythonIndicator
from factorpool.adapter import IndicatorAdapter
from factortrader.binding.loading import _load_native_rows


class RankClose(PythonIndicator):
    def update(self, input):
        self.value = self.cs.rank_unit(input.close, normalization="percentile")
        return True


class MeanRankMean(PythonIndicator):
    def update(self, input):
        average = self.ts.mean(input.close, 5)
        ranked = self.cs.rank_unit(average, normalization="percentile")
        smoothed = self.ts.mean(ranked, 3)
        self.value = smoothed
        return self.ts.is_ready(smoothed)


class MeanRankMeanRank(PythonIndicator):
    def update(self, input):
        average = self.ts.mean(input.close, 2)
        # Integral/half-integral internal ranks isolate synchronization from
        # floating accumulation of repeating percentile fractions.
        ranked = self.cs.rank_unit(average, normalization="ordinal")
        smoothed = self.ts.mean(ranked, 2)
        self.value = self.cs.rank_unit(smoothed, normalization="percentile")
        return self.ts.is_ready(smoothed)


class IndependentWindows(PythonIndicator):
    def update(self, input):
        slow = self.ts.mean(input.volume, 20)
        fast = self.ts.mean(input.close, 2)
        selected = slow if input.volume < 0 else fast
        self.value = self.cs.rank_unit(selected, normalization="percentile")
        return self.ts.is_ready(fast)


class TopDecileThreshold(PythonIndicator):
    def update(self, input):
        ranked = self.cs.rank_unit(input.close)
        self.value = 1.0 if ranked >= 0.9 else 0/0
        return True


def trading_dates(count):
    current, output = date(2024, 1, 2), []
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current.isoformat())
        current += timedelta(days=1)
    return output


def frame(day, closes, symbols=None, *, research_symbols=None):
    rows = {symbol: dict(open=value, high=value, low=value, close=value, volume=1)
            for symbol, value in closes.items()}
    value = dict(date=day, symbols=list(closes) if symbols is None else list(symbols), rows=rows)
    if research_symbols is not None:
        value['research_symbols'] = list(research_symbols)
    return value


def ranks(values, *, pct=True):
    """Independent average-tie ranks, optionally on the shared 1/N..1 scale."""
    names = [name for name, value in values.items() if np.isfinite(value)]
    if not names:
        return {}
    result = rankdata([values[name] for name in names], method='average')
    if pct:
        result /= len(names)
    return dict(zip(names, result))


def reference(frames, inner, outer, final_rank):
    """Separate per-symbol deques and a complete current-day cross section."""
    prices, history, results = {}, {}, []
    for current in frames:
        members = set(current['symbols'])
        prices = {name: values for name, values in prices.items() if name in members}
        history = {name: values for name, values in history.items() if name in members}
        average = {}
        for name in sorted(members):
            values = prices.setdefault(name, deque(maxlen=inner))
            value = current['rows'].get(name, {}).get('close')
            if value is None or not np.isfinite(value):
                continue  # Explicitly absent today does not publish stale history.
            values.append(value)
            if len(values) == inner:
                average[name] = float(np.mean(values))
        ranked = ranks(average, pct=not final_rank)
        smoothed = {}
        for name, value in ranked.items():
            values = history.setdefault(name, deque(maxlen=outer))
            values.append(value)
            if len(values) == outer:
                smoothed[name] = float(np.mean(values))
        results.append(ranks(smoothed) if final_rank else smoothed)
    return results


def values(binding, snapshot, key):
    return np.frombuffer(binding.native_vector_export(snapshot[key]),
                         dtype=bool if key in ('valid','ready') else np.float64).copy()


def sdk_graph(symbols=('A', 'B', 'C'), *, inner=None, outer=None):
    """Pinned numerical primitive oracle; no legacy user factor API is exposed."""
    binding = _load_native_rows(os.environ['FACTORTRADER_NATIVE_LIBRARY'])
    def scalar(op, inputs=(), *, field='', period=0, value=0):
        return dict(op=op, inputs=list(inputs), field=field, period=period, value=value, ddof=0)
    def batch(op, inputs=(), *, field='', dependency=0, value=0):
        return dict(op=op, inputs=list(inputs), field=field, dependency=dependency, value=value)
    scalar_ir = dict(version=1, nodes=[scalar('constant'), scalar('constant', value=1)], output=0, ready=1)
    if inner is None:
        program = dict(version=1, nodes=[batch('input', field='close'),
                       batch('rank', (0,), field='ascending', value=1), batch('constant', value=1)],
                       output=1, ready=2, temporals=[])
    else:
        def mean_port(field, period, deferred=False):
            return dict(version=1, nodes=[scalar('input', field=field), scalar('sma', (0,), period=period),
                        scalar('ready', (1,))], output=1, ready=2, deferred=deferred,
                        bindings=[field] if deferred else [])
        program = dict(version=1, nodes=[batch('constant', value=1),
                       batch('temporal', field='current'), batch('rank', (1,), field='ascending'),
                       batch('temporal', (0, 2), field='current', dependency=1),
                       batch('rank', (3,), field='ascending', value=1),
                       batch('temporal', (0, 2), field='ready', dependency=1)], output=4, ready=5,
                       temporals=[mean_port('close', inner), mean_port('__ranked', outer, True)])
    handle = binding.graph_create(scalar_ir, tuple(symbols))
    binding.graph_configure_batch(handle, program, (), False)
    return binding, handle


class SynchronizationTests(unittest.TestCase):
    def assert_reference(self, factory, frames, expected):
        adapter = IndicatorAdapter(factory())
        self.assertEqual(adapter.mode, 'native')
        actual = []
        for current, result in zip(frames, expected):
            output = adapter.update(current, factory)
            actual.append({row['symbol']: row for row in output})
            snapshot = adapter.binding.graph_snapshot(adapter.graph)
            self.assertEqual(tuple(snapshot['symbols']),tuple(sorted(current['symbols'])))
            self.assertEqual(len(values(adapter.binding,snapshot,'current')),len(current['symbols']))
            for row in output:
                if row['symbol'] in result:
                    self.assertTrue(row['valid'], (current['date'], row))
                    self.assertTrue(row['ready'], (current['date'], row))
                    self.assertAlmostEqual(row['value'], result[row['symbol']], places=13,
                                           msg=(current['date'], row))
                else:
                    self.assertFalse(row['valid'], (current['date'], row))
                    self.assertIsNone(row['value'])
        return adapter, actual

    def test_five_day_mean_rank_three_day_mean_first_full_result_is_seventh_observation(self):
        samples = np.array([[10, 20, 30], [12, 18, 31], [8, 23, 5], [16, 10, 7],
                            [9, 30, 9], [11, 8, 25], [13, 14, 10], [6, 16, 40]], dtype=float)
        frames = [frame(day, dict(zip(('A', 'B', 'C'), row)))
                  for day, row in zip(trading_dates(8), samples)]
        expected = reference(frames, 5, 3, False)
        adapter, actual = self.assert_reference(MeanRankMean, frames, expected)
        self.assertEqual(adapter.indicator.warm_up_period, 7)
        self.assertEqual(len(adapter.indicator._batch_ir.temporals), 2)
        self.assertEqual([port.deferred for port in adapter.indicator._batch_ir.temporals], [False, True])
        self.assertTrue(all(not row['valid'] for day in actual[:6] for row in day.values()))
        np.testing.assert_allclose([actual[6][name]['value'] for name in ('A', 'B', 'C')],
                                   [4/9, 1, 5/9], atol=2e-14)
        # Every IR dependency precedes its consumer; deferred rolling consumes
        # the current complete rank vector rather than per-symbol partial ranks.
        for index, node in enumerate(adapter.indicator._batch_ir.nodes):
            self.assertTrue(all(source < index for source in node.inputs))

    def test_unfinished_cohort_never_publishes_partial_rank_and_known_missing_finishes(self):
        binding, handle = sdk_graph()
        first = 20240102150000
        binding.graph_update_batch(handle, first, {'close': [10, 20, np.nan]}, [1, 1, 0], 101, False)
        waiting = binding.graph_snapshot(handle)
        self.assertFalse(waiting['complete'])
        self.assertFalse(waiting['tradable'])
        np.testing.assert_equal(values(binding, waiting, 'valid'), [False]*3)
        np.testing.assert_equal(values(binding, waiting, 'samples'), [0]*3)
        binding.graph_update_batch(handle, first, {'close': [10, 20, 30]}, [1]*3, 101, True)
        complete = binding.graph_snapshot(handle)
        self.assertTrue(complete['complete'])
        np.testing.assert_allclose(values(binding, complete, 'current'), [1/3, 2/3, 1])
        # The prior publication remains owned and unchanged after new deliveries.
        second = 20240103150000
        binding.graph_update_batch(handle, second, {'close': [10, 20, np.nan]}, [1, 1, 0], 102, False)
        waiting_again = binding.graph_snapshot(handle)
        self.assertFalse(waiting_again['complete'])
        np.testing.assert_equal(values(binding, waiting_again, 'ready'), [False]*3)
        np.testing.assert_equal(values(binding, waiting_again, 'samples'), [1]*3)
        np.testing.assert_allclose(values(binding, complete, 'current'), [1/3, 2/3, 1])
        binding.graph_update_batch(handle, second, {'close': [10, 20, np.nan]}, [1, 1, 0], 102, True)
        missing = binding.graph_snapshot(handle)
        self.assertTrue(missing['complete'])
        np.testing.assert_equal(values(binding, missing, 'valid'), [True, True, False])
        np.testing.assert_allclose(values(binding, missing, 'current')[:2], [.5, 1])
        self.assertTrue(np.isnan(values(binding, missing, 'current')[2]))
        binding.graph_update_batch(handle, 20240104150000, {'close': [10, 20, 5]}, [1]*3, 103, True)
        np.testing.assert_allclose(values(binding, binding.graph_snapshot(handle), 'current'), [2/3, 1, 1/3])

    def test_pending_delivery_does_not_advance_deferred_windows(self):
        binding, handle = sdk_graph(inner=2, outer=2)
        for day, generation in zip(trading_dates(3), (1, 2, 3)):
            stamp = int(day.replace('-', '')+'150000')
            # An arbitrary number of pending deliveries is not extra history.
            for _ in range(3):
                binding.graph_update_batch(handle, stamp, {'close': [10, 20, np.nan]}, [1, 1, 0], generation, False)
            binding.graph_update_batch(handle, stamp, {'close': [10, 20, 30]}, [1]*3, generation, True)
            state = binding.graph_snapshot(handle)
            np.testing.assert_equal(values(binding, state, 'ready'), [generation >= 3]*3)
            np.testing.assert_equal(values(binding, state, 'samples'), [generation]*3)
        np.testing.assert_allclose(values(binding, state, 'current'), [1/3, 2/3, 1])

    def test_missing_and_unready_are_completed_states_and_real_zero_is_a_sample(self):
        days = trading_dates(7)
        frames = [frame(day, {'A': 10+i, 'B': 20-i, 'C': 30+i}, symbols=('C', 'A', 'B'))
                  for i, day in enumerate(days)]
        del frames[0]['rows']['C']
        frames[1]['rows']['B']['close'] = None
        for field in ('open', 'high', 'low', 'close'):
            frames[2]['rows']['A'][field] = 0
        adapter, actual = self.assert_reference(MeanRankMeanRank, frames, reference(frames, 2, 2, True))
        self.assertEqual(actual[0]['C']['reason'], 'missing_bar')
        self.assertEqual(actual[1]['B']['reason'], 'source_missing')
        self.assertTrue(actual[2]['A']['valid'])
        self.assertTrue(actual[-1]['C']['valid'])
        self.assertEqual(tuple(adapter.binding.graph_snapshot(adapter.graph)['symbols']),('A','B','C'))

    def test_unready_independent_branch_does_not_block_the_selected_fast_path(self):
        adapter = IndicatorAdapter(IndependentWindows())
        self.assertEqual(len(adapter.indicator._batch_ir.temporals), 2)
        # Both live branches are retained; this is not a dead-code pruning test.
        self.assertEqual(sorted(port.warm_up_period for port in adapter.indicator._batch_ir.temporals), [2, 20])
        for i, day in enumerate(trading_dates(2)):
            output = adapter.update(frame(day, {'A': 10+i, 'B': 20+i, 'C': 30+i}), IndependentWindows)
        self.assertTrue(all(row['ready'] and row['valid'] for row in output))
        np.testing.assert_allclose([row['value'] for row in output], [1/3, 2/3, 1])

    def test_dynamic_reentry_resets_calculation_membership_but_off_keeps_outside_history(self):
        days = trading_dates(9)
        full = [{'A': 10+i, 'B': 20+2*i, 'C': 30-i} for i in range(9)]
        target_axes = [('A', 'B') if i in (3, 4) else ('A', 'B', 'C') for i in range(9)]
        on = [frame(day, {name: row[name] for name in axis}, symbols=axis, research_symbols=axis)
              for day, row, axis in zip(days, full, target_axes)]
        off = [frame(day, row, symbols=('A', 'B', 'C'), research_symbols=axis)
               for day, row, axis in zip(days, full, target_axes)]
        _, local = self.assert_reference(MeanRankMeanRank, on, reference(on, 2, 2, True))
        _, expanded = self.assert_reference(MeanRankMeanRank, off, reference(off, 2, 2, True))
        self.assertNotIn('C', local[3])
        self.assertFalse(local[5]['C']['ready'])
        self.assertFalse(local[6]['C']['ready'])
        self.assertTrue(local[7]['C']['ready'])
        self.assertTrue(expanded[5]['C']['ready'])
        # OFF calculates C while it is outside the research pool. Filtering
        # results by research_symbols does not erase its shared temporal state.
        self.assertNotIn('C', off[3]['research_symbols'])
        self.assertIn('C', expanded[3])

    def test_malformed_upstream_is_an_error_not_a_completed_missing_market_row(self):
        binding, handle = sdk_graph(inner=2, outer=2)
        binding.graph_update_batch(handle, 20240102150000, {'close': [10, 20, 30]}, [1]*3, 1, True)
        with self.assertRaises(ValueError):
            binding.graph_update_batch(handle, 20240103150000, {'close': [11, 21]}, [1]*3, 2, True)
        failed = binding.graph_snapshot(handle)
        self.assertFalse(failed['complete'])
        self.assertFalse(failed['tradable'])
        np.testing.assert_equal(values(binding, failed, 'valid'), [False]*3)

    def test_explicit_rank_threshold_omits_nan_and_ties_are_not_exact_top_k(self):
        from factorpool.simulation import alpha_targets

        names = [f'S{index:02}' for index in range(20)]
        closes = dict(zip(names, list(range(1, 17)) + [100]*4))
        closes['MISSING'] = None
        adapter = IndicatorAdapter(TopDecileThreshold())
        output = adapter.update(frame('2024-01-02', closes), TopDecileThreshold)
        actual = {row['symbol']: row for row in output}
        # Average rank of the four equal maxima is 18.5, so rank_unit is
        # (18.5-1)/(20-1) > .9. A percentile threshold selects all four rather
        # than arbitrarily choosing exactly two of twenty finite names.
        selected = names[-4:]
        self.assertEqual([name for name in names if actual[name]['valid']], selected)
        self.assertEqual(len(selected), 4)
        for name in selected:
            self.assertEqual(actual[name]['value'], 1)
        for name in names[:-4] + ['MISSING']:
            self.assertFalse(actual[name]['valid'])
            self.assertIsNone(actual[name]['value'])
        all_names = names + ['MISSING']
        factors = np.array([[actual[name]['value'] if actual[name]['valid'] else np.nan
                             for name in all_names]])
        targets = alpha_targets(factors, np.ones_like(factors),
                                dict(universe=all_names, neutralization='NONE'))
        expected = np.array([[0]*16 + [.25]*4 + [0]])
        np.testing.assert_equal(targets, expected)


if __name__ == '__main__':
    unittest.main()
