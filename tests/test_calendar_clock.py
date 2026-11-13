"""Independent calendar-slot oracles for the Pool-owned optional TS clock.

Ordinary valid-observation expressions retain the pinned shared Graph contract.
All inputs here are synthetic completed daily frames, without disk or DB reads.
"""
import math
from datetime import date, timedelta
import unittest

import numpy as np
from factorpool import PythonIndicator
from factorpool.adapter import IndicatorAdapter


class Window(PythonIndicator):
    def __init__(self, period=3, ddof=0):
        super().__init__()
        self.period, self.ddof = period, ddof


class CalendarDelay(Window):
    def update(self, input):
        self.value = self.ts.delay(input.close, self.period, clock='trading_days')
        return True


class CalendarDelta(Window):
    def update(self, input):
        self.value = self.ts.delta(input.close, self.period, clock='trading_days')
        return True


class CalendarMean(Window):
    def update(self, input):
        self.value = self.ts.mean(input.close, self.period, clock='trading_days')
        return True


class CalendarSum(Window):
    def update(self, input):
        self.value = self.ts.sum(input.close, self.period, clock='trading_days')
        return True


class CalendarMin(Window):
    def update(self, input):
        self.value = self.ts.min(input.close, self.period, clock='trading_days')
        return True


class CalendarMax(Window):
    def update(self, input):
        self.value = self.ts.max(input.close, self.period, clock='trading_days')
        return True


class CalendarStd(Window):
    def update(self, input):
        self.value = self.ts.std(input.close, self.period, ddof=self.ddof, clock='trading_days')
        return True


class CalendarDecay(Window):
    def update(self, input):
        self.value = self.ts.decay_linear(input.close, self.period, clock='trading_days')
        return True


class ExplicitObservationDelay(Window):
    def update(self, input):
        self.value = self.ts.delay(input.close, self.period, clock='valid_observations')
        return True


class ExistingObservationDelay(Window):
    def update(self, input):
        self.value = self.ts.delay(input.close, self.period)
        return True


class NestedCalendarMean(PythonIndicator):
    def update(self, input):
        inner = self.ts.mean(input.close, 2, clock='trading_days')
        self.value = self.ts.mean(inner, 2, clock='trading_days')
        return True


class CalendarMissingProbe(PythonIndicator):
    def update(self, input):
        average = self.ts.mean(input.close, 3, clock='trading_days')
        self.value = self.is_nan(average)
        return True


def frame(index, closes, *, symbols=None):
    # Every supplied index is one trading-calendar slot. Dates never imply
    # generating weekend Bars or reading additional data.
    rows = {name: dict(open=value, high=value, low=value, close=value, volume=1)
            for name, value in closes.items()}
    day = date(2024, 1, 2)
    for _ in range(index):
        day += timedelta(days=1)
        while day.weekday() > 4:
            day += timedelta(days=1)
    return dict(date=day.isoformat(), symbols=list(closes) if symbols is None else symbols, rows=rows)


def stream(factory, samples):
    adapter = IndicatorAdapter(factory())
    output = []
    for index, sample in enumerate(samples):
        current = sample if isinstance(sample, dict) and 'date' in sample else frame(index, {'A': sample})
        output.append({row['symbol']: row for row in adapter.update(current, factory)})
    return adapter, output


class CalendarClockTests(unittest.TestCase):
    def test_delay_occupies_nan_day_and_does_not_compress_to_valid_observations(self):
        samples = [10, None, 30, 40]
        _, output = stream(lambda: CalendarDelay(1), samples)
        self.assertFalse(output[0]['A']['ready'])
        self.assertEqual(output[1]['A']['value'], 10)
        self.assertTrue(output[2]['A']['ready'])
        self.assertFalse(output[2]['A']['valid'])
        self.assertEqual(output[3]['A']['value'], 30)
        _, existing = stream(lambda: ExistingObservationDelay(1), samples)
        _, explicit = stream(lambda: ExplicitObservationDelay(1), samples)
        self.assertEqual(existing, explicit)
        self.assertEqual(existing[2]['A']['value'], 10)

    def test_missing_bar_still_occupies_slot_but_never_publishes_a_value(self):
        frames = [frame(0, {'A': 10}), frame(1, {}, symbols=['A']),
                  frame(2, {'A': 30}), frame(3, {'A': 40})]
        _, output = stream(lambda: CalendarDelay(1), frames)
        self.assertFalse(output[1]['A']['valid'])
        self.assertEqual(output[1]['A']['reason'], 'missing_bar')
        self.assertTrue(output[2]['A']['ready'])
        self.assertIsNone(output[2]['A']['value'])
        self.assertEqual(output[3]['A']['value'], 30)

    def test_delta_requires_both_current_and_lagged_calendar_values(self):
        _, output = stream(lambda: CalendarDelta(1), [10, None, 30, 40])
        self.assertFalse(output[0]['A']['ready'])
        for index in (1, 2):
            self.assertTrue(output[index]['A']['ready'])
            self.assertFalse(output[index]['A']['valid'])
        self.assertEqual(output[3]['A']['value'], 10)

    def test_strict_windows_match_independent_numpy_slices_and_recover_after_nan_exits(self):
        samples = [1., math.nan, 3., 4., 5., 0.]
        cases = [(CalendarMean, np.mean, 0), (CalendarSum, np.sum, 0),
                 (CalendarMin, np.min, 0), (CalendarMax, np.max, 0),
                 (CalendarStd, lambda values: np.std(values, ddof=0), 0),
                 (CalendarStd, lambda values: np.std(values, ddof=1), 1),
                 (CalendarDecay, lambda values: np.dot(values, [1, 2, 3])/6, 0)]
        for cls, oracle, ddof in cases:
            with self.subTest(operator=cls.__name__, ddof=ddof):
                _, output = stream(lambda: cls(3, ddof), samples)
                for index, day in enumerate(output):
                    cell = day['A']
                    self.assertEqual(cell['ready'], index >= 2)
                    subset = samples[max(0, index-2):index+1]
                    valid = index >= 2 and all(math.isfinite(value) for value in subset)
                    self.assertEqual(cell['valid'], valid)
                    if valid:
                        self.assertAlmostEqual(cell['value'], float(oracle(subset)), places=13)
                    else:
                        self.assertIsNone(cell['value'])

    def test_zero_period_lags_and_real_zero_are_valid_values(self):
        for cls, expected in ((CalendarDelay, 0), (CalendarDelta, 0)):
            with self.subTest(operator=cls.__name__):
                _, output = stream(lambda: cls(0), [0])
                self.assertTrue(output[0]['A']['valid'])
                self.assertEqual(output[0]['A']['value'], expected)
        _, output = stream(lambda: CalendarMean(2), [2, 0])
        self.assertEqual(output[1]['A']['value'], 1)

    def test_nested_slots_retain_upstream_unready_distinct_from_known_nan(self):
        _, output = stream(NestedCalendarMean, [1, None, 3, 4, 5])
        self.assertFalse(output[0]['A']['ready'])
        self.assertFalse(output[1]['A']['ready'])
        for index in (2, 3):
            self.assertTrue(output[index]['A']['ready'])
            self.assertIsNone(output[index]['A']['value'])
        self.assertEqual(output[4]['A']['value'], 4)
        _, probe = stream(CalendarMissingProbe, [1, None, 3, 4, 5])
        self.assertFalse(probe[0]['A']['valid'])
        self.assertFalse(probe[1]['A']['valid'])
        self.assertEqual(probe[2]['A']['value'], 1)
        self.assertEqual(probe[4]['A']['value'], 0)

    def test_dynamic_axis_retains_existing_names_and_reentry_restarts_calendar_warmup(self):
        frames = [frame(0, {'B': 10, 'A': 2}), frame(1, {'A': 4}),
                  frame(2, {'B': 30, 'A': 6}), frame(3, {'A': 8, 'B': 40})]
        _, output = stream(lambda: CalendarMean(2), frames)
        self.assertEqual(output[1]['A']['value'], 3)
        self.assertNotIn('B', output[1])
        self.assertEqual(output[2]['A']['value'], 5)
        self.assertFalse(output[2]['B']['ready'])
        self.assertEqual(output[3]['B']['value'], 35)

    def test_std_denominators_and_overflow_are_explicit(self):
        _, population = stream(lambda: CalendarStd(1, 0), [5])
        _, sample = stream(lambda: CalendarStd(1, 1), [5])
        self.assertEqual(population[0]['A']['value'], 0)
        self.assertTrue(sample[0]['A']['ready'])
        self.assertFalse(sample[0]['A']['valid'])
        _, overflow = stream(lambda: CalendarSum(2), [1e308, 1e308])
        self.assertTrue(overflow[1]['A']['ready'])
        self.assertIsNone(overflow[1]['A']['value'])
        _, mean = stream(lambda: CalendarMean(2), [1e308, 1e308])
        self.assertEqual(mean[1]['A']['value'], 1e308)

    def test_calendar_rejects_same_or_reversed_dates_without_adding_a_slot(self):
        factory = lambda: CalendarMean(2)
        adapter = IndicatorAdapter(factory())
        adapter.update(frame(1, {'A': 2}), factory)
        for current in (frame(1, {'A': 100}), frame(0, {'A': 200})):
            with self.subTest(day=current['date']), self.assertRaises(ValueError):
                adapter.update(current, factory)
        output = adapter.update(frame(2, {'A': 4}), factory)
        self.assertEqual(output[0]['value'], 3)


if __name__ == '__main__':
    unittest.main()
