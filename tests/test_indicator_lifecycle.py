"""LEAN manual/managed contracts and Pool publications; no market writes."""
from collections import deque
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest

import numpy as np
from factorpool import PythonIndicator, IndicatorDataPoint, TradeBar, TradeBarConsolidator
from factorpool.adapter import IndicatorAdapter
from factorpool.factor_matrix import FactorMatrix


def point(value=7., day=2, symbol='A'):
    return IndicatorDataPoint(datetime(2024, 1, day), value, symbol)


def frame(day, values=(2., 4.)):
    return dict(date=f'2024-01-{day:02}', symbols=['A', 'B'], rows={
        symbol: dict(open=value, high=value, low=value, close=value, volume=value,
                     prev_close=value, total_turnover=value, is_st=0., is_suspended=0.)
        for symbol, value in zip(('A', 'B'), values)})


class LeanQueue(PythonIndicator):
    def __init__(self, period=2):
        # Omitting super is deliberately the documented LEAN shape.
        self.name = 'queue'
        self.warm_up_period = period
        self.queue = deque(maxlen=period)

    @property
    def is_ready(self): return len(self.queue) == self.warm_up_period

    def update(self, input):
        self.queue.append(input.value)
        self.value = sum(self.queue) / len(self.queue)
        return self.is_ready

    def reset(self):
        self.queue.clear()
        self.value = 0.
        super().reset()


class ManualQueue(LeanQueue):
    def update(self, input):
        self.queue.append(input.value)
        self.value = sum(self.queue) / len(self.queue)
        self.current = IndicatorDataPoint(input.end_time, self.value)
        self.on_updated(self.current)
        return self.is_ready


class ExplicitNone(PythonIndicator):
    def update(self, input):
        self.value = input.value
        return None


class ImplicitNone(PythonIndicator):
    def update(self, input):
        self.value = input.value


class ReturnTrue(PythonIndicator):
    def update(self, input):
        self.value = input.value
        return True


class ReturnFalse(PythonIndicator):
    def update(self, input):
        self.value = input.value
        return False


class NativeWindow(PythonIndicator):
    def __init__(self, period=2):
        super().__init__()
        self.period = period

    def update(self, input):
        self.value = self.cs.rank_unit(self.ts.mean(input.volume, self.period))
        return self.is_ready


class NativeAmount(PythonIndicator):
    def update(self, input):
        self.value = self.cs.rank_unit(input.total_turnover)
        return self.is_ready


class IndicatorLifecycleTests(unittest.TestCase):
    def test_boolean_truth_table_with_default_and_custom_properties(self):
        for base, result_value in ((ReturnTrue, True), (ReturnFalse, False)):
            for property_value in (None, False, True):
                for prior in (False, True):
                    with self.subTest(base=base.__name__, property=property_value, prior=prior):
                        cls = base
                        if property_value is not None:
                            cls = type('CustomBoolean', (base,), {
                                'is_ready': property(lambda self, value=property_value: value)})
                        ind = cls()
                        ind._ready = prior
                        self.assertEqual(ind._receive(point()), result_value or bool(property_value))
                        self.assertEqual(ind.samples, 1)
                        self.assertEqual(ind.current.value, 7.)

    def test_none_truth_table_preserves_property_and_prior_state(self):
        for base in (ExplicitNone, ImplicitNone):
            for property_value in (None, False, True):
                for prior in (False, True):
                    with self.subTest(base=base.__name__, property=property_value, prior=prior):
                        cls = base
                        if property_value is not None:
                            cls = type('CustomNone', (base,), {
                                'is_ready': property(lambda self, value=property_value: value)})
                        ind = cls()
                        ind._ready = prior
                        events = []
                        ind.updated += lambda sender, value: events.append(value.value)
                        self.assertEqual(ind._receive(point()),
                                         prior if property_value is None else property_value)
                        self.assertEqual(ind.samples, 1)
                        self.assertEqual(ind.current.value, 7.)
                        self.assertEqual(ind.previous.value, 0.)
                        self.assertEqual(events, [7.])
                        self.assertEqual(ind.execution_mode, 'python')

    def test_managed_boolean_priority_is_distinct_from_direct_property(self):
        class TruePropertyFalse(PythonIndicator):
            @property
            def is_ready(self): return False
            def update(self, input):
                self.value = input.value
                return True
        class FalsePropertyTrue(PythonIndicator):
            @property
            def is_ready(self): return True
            def update(self, input):
                self.value = input.value
                return False
        ind = TruePropertyFalse()
        self.assertTrue(ind._receive(point()))
        self.assertFalse(ind.is_ready)
        self.assertTrue(ind._wrapped_is_ready())
        ind = FalsePropertyTrue()
        self.assertTrue(ind._receive(point()))
        self.assertTrue(ind.is_ready)
        manual = FalsePropertyTrue()
        self.assertFalse(manual.update(point()))
        self.assertEqual(manual.samples, 0)

    def test_none_after_real_ready_update_retains_the_base_state(self):
        class LaterNone(PythonIndicator):
            def update(self, input):
                self.value = input.value
                if self.samples == 1:
                    return True
                return None
        ind = LaterNone()
        self.assertTrue(ind._receive(point(2.)))
        self.assertTrue(ind._receive(point(4., 3)))
        self.assertEqual((ind.samples, ind.current.value, ind.previous.value), (2, 4., 2.))

    def test_manual_and_managed_current_and_events_are_not_double_counted(self):
        for managed in (False, True):
            with self.subTest(managed=managed):
                ind = ManualQueue()
                events = []
                ind.updated += lambda sender, value: events.append(value.value)
                call = ind._receive if managed else ind.update
                self.assertFalse(call(point(2.)))
                self.assertTrue(call(point(4., 3)))
                self.assertEqual(ind.current.value, 3.)
                self.assertEqual(ind.previous.value, 2.)
                self.assertEqual(ind.samples, 2 if managed else 0)
                self.assertEqual(events, [2., 3.])
                ind.reset()
                self.assertEqual((len(ind.queue), ind.samples, ind.current.value), (0, 0, 0.))
                self.assertFalse(ind.is_ready)

    def test_data_point_and_trade_bar_inputs_use_the_same_managed_contract(self):
        for bar in (point(2.), TradeBar(datetime(2024, 1, 2), 'A', 1., 3., 1., 2., 100., timedelta(days=1))):
            ind = LeanQueue()
            self.assertFalse(ind._receive(bar))
            self.assertEqual(ind.current.value, 2.)
            self.assertEqual(ind.current.end_time, bar.end_time)
            self.assertEqual(ind.samples, 1)

    def test_managed_identity_backwards_time_and_same_time_distinct_bars(self):
        ind = LeanQueue()
        first = point(2.)
        self.assertFalse(ind._receive(first))
        self.assertFalse(ind._receive(first))
        self.assertEqual(ind.samples, 1)
        earlier = point(9., 1)
        self.assertFalse(ind._receive(earlier))
        self.assertEqual(ind.samples, 1)
        self.assertTrue(ind._receive(point(4.)))
        self.assertEqual((ind.samples, ind.current.value), (2, 3.))

    def test_pascal_current_update_reset_and_state_aliases(self):
        class Pascal(PythonIndicator):
            def __init__(self):
                self.queue = []
                self.WarmUpPeriod = 2
            @property
            def IsReady(self): return len(self.queue) >= self.WarmUpPeriod
            def Update(self, input):
                self.queue.append(input.Value)
                self.Value = sum(self.queue) / len(self.queue)
                self.Current = IndicatorDataPoint(input.EndTime, self.Value)
                self.OnUpdated(self.Current)
                return self.IsReady
            def Reset(self):
                self.queue.clear()
                super().Reset()
        ind = Pascal()
        events = []
        ind.Updated += lambda sender, value: events.append(value.Value)
        self.assertFalse(ind._receive(point(2.)))
        self.assertTrue(ind._receive(point(4., 3)))
        self.assertEqual((ind.Current.Value, ind.Previous.Value, ind.Samples), (3., 2., 2))
        self.assertEqual(events, [2., 3.])
        ind.Reset()
        self.assertEqual((ind.queue, ind.Samples, ind.Current.Value), ([], 0, 0.))
        self.assertFalse(ind.IsReady)

    def test_python_readiness_is_not_numerical_validity(self):
        class Missing(LeanQueue):
            def update(self, input):
                self.queue.append(input.value)
                self.value = float('nan')
                return True
        adapter = IndicatorAdapter(Missing())
        rows = adapter.update(frame(2), Missing)
        self.assertTrue(all(row['ready'] for row in rows))
        self.assertFalse(any(row['valid'] for row in rows))

    def test_native_publication_views_and_reset_share_the_actual_graph(self):
        ind = NativeWindow()
        adapter = IndicatorAdapter(ind)
        first = adapter.update(frame(2), NativeWindow)
        self.assertFalse(any(row['ready'] for row in first))
        rows = adapter.update(frame(3), NativeWindow)
        self.assertTrue(all(row['ready'] for row in rows))
        self.assertTrue(ind['A'].is_ready)
        self.assertTrue(ind['B'].is_ready)
        self.assertEqual(ind['A'].samples, 2)
        self.assertEqual(ind['A'].current.value, 0.)
        self.assertEqual(ind['B'].current.value, 1.)
        np.testing.assert_array_equal(ind.is_ready.to_numpy(), [True, True])
        before = adapter.binding.graph_snapshot(adapter.graph)
        mode = ind.execution_mode
        for read in (lambda: ind.window, lambda: ind.Window, lambda: ind[0]):
            with self.assertRaisesRegex(NotImplementedError, 'output Window and integer history'):
                read()
        self.assertEqual(ind.execution_mode, mode)
        _ = ind['A'].current
        _ = ind['B'].previous
        self.assertEqual(before['samples'], adapter.binding.graph_snapshot(adapter.graph)['samples'])
        ind.reset()
        self.assertFalse(ind['A'].is_ready)
        self.assertEqual(ind['A'].samples, 0)
        self.assertEqual(ind['A'].current.value, 0.)
        self.assertFalse(any(row['ready'] for row in adapter.update(frame(4), NativeWindow)))

    def test_dynamic_members_and_separate_instances_keep_independent_clocks(self):
        first, second = NativeWindow(), NativeWindow()
        left, right = IndicatorAdapter(first), IndicatorAdapter(second)
        early = frame(2)
        early['symbols'], early['rows'] = ['A'], {'A': early['rows']['A']}
        left.update(early, NativeWindow)
        left.update(frame(3), NativeWindow)
        self.assertTrue(first['A'].is_ready)
        self.assertFalse(first['B'].is_ready)
        self.assertEqual((first['A'].samples, first['B'].samples), (2, 1))
        right.update(frame(2), NativeWindow)
        first.reset()
        self.assertEqual(second['A'].samples, 1)
        right.update(frame(3), NativeWindow)
        self.assertTrue(second['A'].is_ready)
        self.assertFalse(first['A'].is_ready)

    def test_concurrent_tasks_keep_windows_publications_and_reset_independent(self):
        barrier = Barrier(2)
        def task(reverse):
            ind = NativeWindow()
            adapter = IndicatorAdapter(ind)
            observations = []
            for step, day in enumerate((2, 3, 4, 5)):
                barrier.wait(timeout=10)
                values = (float(day), float(day + 10))
                rows = adapter.update(frame(day, values[::-1] if reverse else values), NativeWindow)
                observations.append(dict(ready=[row['ready'] for row in rows],
                                         value=[row['value'] for row in rows],
                                         samples=[ind[symbol].samples for symbol in ('A', 'B')]))
                barrier.wait(timeout=10)
                if step == 1 and not reverse:
                    ind.reset()
                barrier.wait(timeout=10)
                if step == 1 and reverse:
                    self.assertEqual(ind['A'].samples, 2)
                    self.assertTrue(ind['A'].is_ready)
            return observations
        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = list(pool.map(task, (False, True)))
        self.assertEqual([row['ready'] for row in left],
                         [[False, False], [True, True], [False, False], [True, True]])
        self.assertEqual([row['ready'] for row in right],
                         [[False, False], [True, True], [True, True], [True, True]])
        self.assertEqual(left[-1]['samples'], [2, 2])
        self.assertEqual(right[-1]['samples'], [4, 4])
        self.assertEqual(left[-1]['value'], [0., 1.])
        self.assertEqual(right[-1]['value'], [1., 0.])

    def test_native_manual_data_point_supports_public_current_samples_and_reset(self):
        class Mean(PythonIndicator):
            def update(self, input):
                self.value = self.ts.mean(input.value, 2)
                return self.is_ready
        ind = Mean()
        self.assertFalse(ind.update(point(2.)))
        self.assertEqual(ind.execution_mode, 'native')
        self.assertIsNone(ind.native_failure_reason)
        self.assertTrue(ind.update(point(4., 3)))
        self.assertEqual(ind.samples, 2)
        self.assertEqual(ind.current.value, 3.)
        self.assertEqual(ind.current.end_time, datetime(2024, 1, 3))
        ind.Reset()
        self.assertEqual(ind.samples, 0)
        self.assertFalse(ind.is_ready)

    def test_plain_native_preparation_reports_its_actual_mode(self):
        ind = ReturnTrue()
        self.assertIsNotNone(ind._prepare())
        self.assertEqual(ind.execution_mode, 'native')
        self.assertIsNone(ind.native_failure_reason)

    def test_native_manual_updates_reject_symbol_switches_before_losing_state(self):
        class Mean(PythonIndicator):
            def update(self, input):
                self.value = self.ts.mean(input.value, 2)
                return self.is_ready
        ind = Mean()
        self.assertFalse(ind.update(point(2., symbol='A')))
        with self.assertRaisesRegex(ValueError, 'one symbol.*complete research frame'):
            ind.update(point(4., 3, symbol='B'))
        self.assertEqual(ind.samples, 1)
        self.assertTrue(ind.update(point(4., 3, symbol='A')))
        self.assertEqual(ind.current.value, 3.)

    def test_native_manual_inputs_distinguish_missing_fields_from_invalid_values(self):
        class Volume(PythonIndicator):
            def update(self, input):
                self.value = self.ts.mean(input.volume, 1)
                return self.is_ready
        with self.assertRaisesRegex(TypeError, 'Indicator input has no volume field'):
            Volume().update(point(2.))
        ind = Volume()
        bar = TradeBar(datetime(2024, 1, 2), 'A', 1., 3., 1., 2., 100., timedelta(days=1))
        self.assertTrue(ind.update(bar))
        self.assertEqual((ind.current.value, ind.samples), (100., 1))
        for invalid in (None, np.nan):
            bar.volume = invalid
            candidate = Volume()
            candidate.update(bar)
            self.assertFalse(candidate._pool_state.column('valid')[0])

    def test_real_native_consolidator_events_feed_managed_queue(self):
        ind = LeanQueue()
        events = []
        ind.updated += lambda sender, value: events.append(value.value)
        consolidator = TradeBarConsolidator(2)
        consolidator.data_consolidated += lambda sender, bar: ind._deliver_consolidated(bar)
        for index in range(4):
            price = float(10 + index)
            consolidator.update(TradeBar(datetime(2024, 1, 2, 9, 30) + timedelta(minutes=index),
                                        'A', price, price, price, price, 100., timedelta(minutes=1)))
        self.assertEqual((ind.samples, ind.current.value, ind.previous.value), (2, 12., 11.))
        self.assertTrue(ind.is_ready)
        self.assertEqual(events, [11., 12.])
        ind.reset()
        self.assertEqual((ind.samples, ind.current.value), (0, 0.))

    def test_matrix_publication_is_read_only_and_reset_restarts_windows(self):
        ind = NativeWindow()
        payload = dict(provider='fixture', frames=[frame(day) for day in (2, 3, 4, 5)])
        matrix = FactorMatrix(payload, ind, NativeWindow, '2024-01-05')
        result = matrix.evaluate_many([0, 1, 2, 3])
        self.assertIsNotNone(result)
        self.assertEqual(ind['A'].current.value, result['value'][-1, 0])
        self.assertEqual(ind['B'].current.value, result['value'][-1, 1])
        self.assertTrue(ind['A'].is_ready)
        self.assertGreater(ind['A'].samples, 0)
        before = (matrix.input_frames, matrix.evaluations)
        _ = ind.current
        _ = ind.previous
        _ = ind.samples
        self.assertEqual((matrix.input_frames, matrix.evaluations), before)
        ind.reset()
        self.assertFalse(ind['A'].is_ready)
        self.assertEqual(ind['A'].samples, 0)
        first = matrix.evaluate_arrays(0)
        self.assertFalse(any(first['ready']))

    def test_explicit_matrix_observers_receive_each_requested_publication_once(self):
        # This research event is one completed frame/vector publication, not
        # LEAN Runtime per-security delivery. Hidden Q replays are not events.
        ind = NativeWindow()
        observations = []
        def observe(sender, point):
            observations.append((sender['A'].samples, point.end_time, point.value.shape))
        ind.updated += observe
        matrix = FactorMatrix(dict(provider='fixture', frames=[frame(day) for day in (2, 3, 4)]),
                              ind, NativeWindow, '2024-01-04')
        self.assertIsNone(matrix.evaluate_many([1, 2]))
        matrix.evaluate_arrays(1)
        matrix.evaluate_arrays(2)
        self.assertEqual(observations, [(2, datetime(2024, 1, 3, 15), (2,)),
                                        (3, datetime(2024, 1, 4, 15), (2,))])

    def test_custom_samples_constraint_uses_complete_prefix_after_rebasing(self):
        class SamplesReady(PythonIndicator):
            def __init__(self): self.warm_up_period = 2
            @property
            def is_ready(self): return self.samples >= 3
            def update(self, input):
                self.value = self.ts.mean(input.close, 2)
                return self.is_ready
        frames = [frame(day, (value, value * 2)) for day, value in
                  zip((2, 3, 4, 5), (10., 10., 10., 5.))]
        ind = SamplesReady()
        matrix = FactorMatrix(dict(provider='fixture', frames=frames), ind,
                              SamplesReady, '2024-01-05')
        self.assertTrue(matrix.unbounded)
        result = matrix.evaluate_arrays(3)
        self.assertTrue(all(result['ready']))
        self.assertEqual(ind['A'].samples, 4)


if __name__ == '__main__':
    unittest.main()
