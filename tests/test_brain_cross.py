"""Independent examples for the core cross-section, vector and state operators."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.special import ndtri

from factorpool.adapter import IndicatorAdapter
from factorpool.runner import load_factor


class BrainCrossTests(unittest.TestCase):
    def adapter(self, expression, ready="True"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "factor.py"
        path.write_text("from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n    def update(self, input):\n        self.value = " + expression + "\n        return " + ready + "\n")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        factory = load_factor(path, "Factor", {})
        value = factory()
        adapter = IndicatorAdapter(value)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertIsNotNone(getattr(value, "_pool_ir", None))
        return adapter, factory

    def frame(self, adapter, factory, close, *, volume=None, opening=None, date=2, names=None, absent=()):
        names = names or [str(n) for n in range(len(close))]
        rows = {symbol: dict(open=close[n] if opening is None else opening[n], high=close[n], low=close[n], close=close[n], volume=1 if volume is None else volume[n]) for n, symbol in enumerate(names) if symbol not in absent}
        return adapter.update(dict(date=f"2024-01-{date:02}", symbols=names, rows=rows), factory)

    def evaluate(self, expression, close, **options):
        adapter, factory = self.adapter(expression)
        return [row["value"] for row in self.frame(adapter, factory, close, **options)]

    def assert_values(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for value, target in zip(actual, expected):
            if target is None or not np.isfinite(target):
                self.assertIsNone(value)
            else:
                self.assertIsNotNone(value)
                self.assertAlmostEqual(value, target, places=10)

    def test_densify_is_a_same_day_encoding_and_preserves_missing(self):
        self.assert_values(self.evaluate("self.cs.densify(input.close)", [99, 2, 0, 2, np.nan, 1]), [3, 2, 0, 2, None, 1])

    def test_cross_quantile_drivers_and_small_sections_match_independent_mapping(self):
        source = [.2, .5, -.1, .8, .3]
        probabilities = np.array([.35, .65, .2, .8, .5])
        for driver, expected in (("gaussian", ndtri(probabilities)), ("uniform", probabilities), ("cauchy", np.tan(np.pi * (probabilities - .5)))):
            with self.subTest(driver=driver):
                actual = self.evaluate(f"self.cs.quantile(input.close, driver='{driver}', sigma=2)", source)
                self.assert_values(actual, 2 * expected)
        self.assert_values(self.evaluate("self.cs.quantile(input.close)", [10]), [0])
        self.assert_values(self.evaluate("self.cs.quantile(input.close)", [10, 20]), [0, 0])

    def test_winsorize_clips_population_bounds_without_dropping_rows(self):
        values = np.array([2., 3., 4., 5., 100.])
        target = np.clip(values, values.mean() - values.std(), values.mean() + values.std())
        self.assert_values(self.evaluate("self.cs.winsorize(input.close, std=1)", values), target)
        self.assert_values(self.evaluate("self.cs.winsorize(input.close, std=0)", values), [values.mean()] * len(values))

    def test_normalize_std_limit_and_scale_sides_have_explicit_effects(self):
        values = np.array([3., 5., 6., 2.])
        target = np.clip((values - values.mean()) / values.std(), -1, 1)
        self.assert_values(self.evaluate("self.cs.normalize(input.close, useStd=True, limit=1)", values), target)
        self.assert_values(self.evaluate("self.cs.normalize(input.close, useStd=True)", [2, 2]), [None, None])
        self.assert_values(self.evaluate("self.cs.scale(input.close, scale=4)", [2, -3, 5]), [.8, -1.2, 2])
        self.assert_values(self.evaluate("self.cs.scale(input.close, longscale=2, shortscale=3)", [2, -3, 5]), [4/7, -3, 10/7])

    def test_variadic_filter_math_distinguishes_nan_and_real_zero(self):
        self.assert_values(self.evaluate("self.add(input.close, input.volume, 3, filter=True)", [0, np.nan, 2], volume=[2, 4, np.nan]), [5, 7, 5])
        self.assert_values(self.evaluate("self.multiply(input.close, input.volume, 2, filter=True)", [0, np.nan, 3], volume=[4, 5, np.nan]), [0, 10, 6])
        self.assert_values(self.evaluate("self.subtract(input.close, input.volume, 2, filter=True)", [10, np.nan], volume=[3, 5]), [5, -7])
        self.assert_values(self.evaluate("self.max(input.close, input.volume, 4)", [2, 10], volume=[6, 3]), [6, 10])

    def test_vector_components_aggregate_within_each_security(self):
        expression = "self.vc.avg((input.close, input.volume, input.open))"
        self.assert_values(self.evaluate(expression, [1, 10, np.nan], volume=[2, 20, np.nan], opening=[3, 30, np.nan]), [2, 20, None])
        self.assert_values(self.evaluate("self.vc.sum([input.close, input.volume])", [1, 10, np.nan], volume=[2, 20, 3]), [3, 30, 3])
        with self.assertRaises(ValueError): self.adapter("self.vc.avg(())")

    def test_bucket_reference_examples_explicit_edges_and_missing_group(self):
        self.assert_values(self.evaluate("self.tf.bucket(input.close, range='0,1,0.1')", [.05, .45, .9]), [0, 4, 8])
        self.assert_values(self.evaluate("self.tf.bucket(input.close, buckets='.2,.5,.7')", [.1, .3, .6, .8]), [0, 1, 2, 3])
        self.assert_values(self.evaluate("self.tf.bucket(input.close, range='0,1,.5', NaNGroup=True)", [-.1, 0, .5, 1, 1.1, np.nan]), [None, 0, 0, 1, None, 2])
        self.assert_values(self.evaluate("self.tf.bucket(input.close, buckets='.2,.5', skipBoth=True, NaNGroup=True)", [.1, .3, .8, np.nan]), [None, 0, None, 1])

    def test_groups_use_real_aligned_labels_and_weighted_arithmetic_mean(self):
        actual = self.evaluate('self.gp.mean(input.close, input.volume, weight=input.open)', [10, 20, 1, np.nan], opening=[1, 3, 2, 2], volume=[0, 0, 1, 1])
        self.assert_values(actual, [17.5, 17.5, 1, 1])
        actual = self.evaluate("self.gp.zscore(input.close, input.volume)", [10, 20, 30, 99], volume=[0, 0, 0, 1])
        self.assert_values(actual, [-np.sqrt(1.5), 0, np.sqrt(1.5), None])
        actual = self.evaluate("self.gp.scale(input.close, input.volume)", [10, 20, 30, 99], volume=[0, 0, 0, 1])
        self.assert_values(actual, [0, .5, 1, None])

    def test_group_mean_equal_weight_default_and_keyword_weight_are_explicit(self):
        source, labels = [10, 20, 1, np.nan], [0, 0, 1, 1]
        plain = self.evaluate("self.gp.mean(input.close, input.volume)", source, volume=labels)
        explicit = self.evaluate("self.gp.mean(input.close, input.volume, weight=1)", source, volume=labels)
        self.assert_values(plain, [15, 15, 1, 1])
        self.assertEqual(plain, explicit)
        for expression in ("self.gp.mean(input.close, input.open, input.volume)",
                           "self.gp.mean(input.close, input.volume, weight='invalid')"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                self.adapter(expression)

    def test_group_labels_and_unavailable_standard_deviation_remain_distinct(self):
        actual = self.evaluate("self.gp.zscore(input.close, input.volume)",
                               [1, 3, 10, 14, 0, np.nan], volume=[0, 0, 1, 1, np.nan, 2])
        self.assert_values(actual, [-1, 1, -1, 1, None, None])
        self.assert_values(self.evaluate("self.gp.zscore(input.close, input.volume)",
                                         [4, 4, 9], volume=[0, 0, 1]), [None, None, None])

    def test_vector_nonfinite_components_and_symbol_order_have_native_results(self):
        source, second = [1, np.nan, np.inf, np.nan], [3, 7, 9, np.nan]
        expected_sum, expected_mean = [4, 7, None, None], [2, 7, None, None]
        for expression, expected in (("self.vc.sum((input.close, input.volume))", expected_sum),
                                     ("self.vc.avg((input.close, input.volume))", expected_mean)):
            with self.subTest(expression=expression):
                self.assert_values(self.evaluate(expression, source, volume=second,
                                                  names=['D', 'B', 'A', 'C']),
                                   [expected[2], expected[1], expected[3], expected[0]])
        with self.assertRaises(ValueError):
            self.adapter("self.vc.sum(())")

    def test_group_backfill_uses_bounded_point_in_time_group_history(self):
        adapter, factory = self.adapter("self.gp.backfill(input.close, input.volume, 2, std=100)")
        self.frame(adapter, factory, [2, 10, 100], volume=[0, 0, 1])
        result = self.frame(adapter, factory, [np.nan, 20, np.nan], volume=[0, 0, 1], date=3)
        self.assert_values([row["value"] for row in result], [32/3, 20, 100])
        result = self.frame(adapter, factory, [np.nan, 30, np.nan], volume=[1, 0, 1], date=4)
        self.assert_values([row["value"] for row in result], [None, 30, None])

    def test_trade_when_exit_priority_hold_and_axis_reentry(self):
        adapter, factory = self.adapter("self.tf.trade_when(input.volume, input.close, input.open)")
        expected = ([4, 8], [4, 8], [None, 8], [None, 8], [10, 8])
        for n, (close, volume, opening) in enumerate((([4, 8], [1, 1], [-1, -1]), ([5, 9], [0, 0], [-1, -1]), ([6, 10], [1, 0], [1, -1]), ([7, 11], [0, 0], [-1, -1]), ([10, 12], [1, 0], [-1, -1]))):
            result = self.frame(adapter, factory, close, volume=volume, opening=opening, date=n+2, names=['A', 'B'])
            self.assert_values([row['value'] for row in result], expected[n])
        self.frame(adapter, factory, [13], volume=[0], opening=[-1], date=7, names=['B'])
        result = self.frame(adapter, factory, [14, 15], volume=[0, 0], opening=[-1, -1], date=8, names=['A', 'B'])
        self.assert_values([row['value'] for row in result], [None, 8])

    def test_trade_when_unselected_unready_value_does_not_block_holding(self):
        adapter, factory = self.adapter("self.tf.trade_when(input.volume, self.ts.mean(input.close, 3), -1)")
        first = self.frame(adapter, factory, [1], volume=[0])[0]
        self.assertTrue(first['ready']); self.assertIsNone(first['value'])
        second = self.frame(adapter, factory, [2], volume=[1], date=3)[0]
        self.assertFalse(second['ready'])
        third = self.frame(adapter, factory, [3], volume=[1], date=4)[0]
        self.assertAlmostEqual(third['value'], 2)


if __name__ == '__main__': unittest.main()
