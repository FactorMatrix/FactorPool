"""Quote-only synthetic factor-window contracts; no external event data."""
from copy import deepcopy
import math
import unittest

from factortrader import PythonIndicator
from factorpool import PythonIndicator as PoolPythonIndicator
from factorpool.adapter import IndicatorAdapter
from factorpool.quote_adjustment import QuoteAdjustment, quote_bridge
from factorpool.quote_adjusted_indicator import QuoteAdjustedIndicatorAdapter


class Close(PythonIndicator):
    def update(self, input):
        self.value = input.close
        return True


class Mean2(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(input.close, 2)
        self.value = value
        return self.ts.is_ready(value)


class MeanMean(PythonIndicator):
    def update(self, input):
        inner = self.ts.mean(input.close, 2)
        outer = self.ts.mean(inner, 2)
        self.value = outer
        return self.ts.is_ready(outer)


class RankMean(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(input.close, 2)
        self.value = self.cs.rank_unit(value)
        return self.ts.is_ready(value)


class MeanRank(PythonIndicator):
    def update(self, input):
        ranked = self.cs.rank_unit(input.close)
        value = self.ts.mean(ranked, 2)
        self.value = value
        return self.ts.is_ready(value)


class MeanRankMean(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(input.close, 2)
        ranked = self.cs.rank_unit(value)
        smoothed = self.ts.mean(ranked, 2)
        self.value = smoothed
        return self.ts.is_ready(smoothed)


class MixedBound(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(self.cs.rank_unit(input.close) + input.close, 2)
        self.value = value
        return self.ts.is_ready(value)


class EMA(PythonIndicator):
    def update(self, input):
        value = self.ts.ema(input.close, 2)
        self.value = value
        return self.ts.is_ready(value)


class MeanVolume(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(input.volume, 2)
        self.value = value
        return self.ts.is_ready(value)


class MeanPriceRatio(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(input.close / input.prev_close, 2)
        self.value = value
        return self.ts.is_ready(value)


class MeanOffset(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(input.close + 10, 2)
        self.value = value
        return self.ts.is_ready(value)


class MeanSquared(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(input.close * input.close, 2)
        self.value = value
        return self.ts.is_ready(value)


class Std2(PythonIndicator):
    def update(self, input):
        value = self.ts.std(input.close, 2)
        self.value = value
        return self.ts.is_ready(value)


class Delay1(PythonIndicator):
    def update(self, input):
        value = self.ts.delay(input.close, 1)
        self.value = value
        return self.ts.is_ready(value)


class Decay2(PythonIndicator):
    def update(self, input):
        value = self.ts.decay_linear(input.close, 2)
        self.value = value
        return self.ts.is_ready(value)


class DifferenceDenominator(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(input.close / (input.close - input.prev_close), 2)
        self.value = value
        return self.ts.is_ready(value)


class IndependentPriceAndVolume(PythonIndicator):
    def update(self, input):
        price = self.ts.mean(input.close, 2)
        volume = self.ts.mean(input.volume, 3)
        self.value = self.cs.rank_unit(price) + self.cs.rank_unit(volume)
        return self.ts.is_ready(price) & self.ts.is_ready(volume)


class IndependentConstantWindow(PythonIndicator):
    def update(self, input):
        price = self.ts.mean(input.close, 2)
        constant = self.ts.mean(5, 3)
        self.value = price + constant
        return self.ts.is_ready(price)


class PoolExtension(PoolPythonIndicator):
    def update(self, input):
        self.value = self.cs.sqrt(input.close)
        return True


def frame(day, values, *, previous=None, volumes=None, amounts=None, symbols=None, session=None):
    rows = {symbol: dict(open=value, high=value, low=value, close=value, value=value,
                        prev_close=(previous or {}).get(symbol, value),
                        volume=(volumes or {}).get(symbol, 100),
                        total_turnover=(amounts or {}).get(symbol, 1000), returns=.123)
            for symbol, value in values.items()}
    output = dict(date=day, symbols=list(values) if symbols is None else list(symbols), rows=rows)
    if session is not None:
        output["session_index"] = session
    return output


def run(factory, frames, **kwargs):
    adapter = QuoteAdjustedIndicatorAdapter(factory(), **kwargs)
    values = [{row["symbol"]: row for row in adapter.update(current, factory)} for current in frames]
    return adapter, values


def full_replay_reference(factory, frames):
    """Independent per-asof product formula, not the fast-path Q state."""
    output = []
    for end in range(len(frames)):
        adapter = IndicatorAdapter(factory())
        for start in range(end+1):
            current = deepcopy(frames[start])
            for symbol, row in current["rows"].items():
                multiplier = math.prod(frames[k]["rows"][symbol]["prev_close"] /
                                       frames[k-1]["rows"][symbol]["close"] for k in range(start+1, end+1))
                for field in ("open", "high", "low", "close", "value", "prev_close"):
                    row[field] *= multiplier
            actual = adapter.update(current, factory)
        output.append({row["symbol"]: row for row in actual})
    return output


class QuoteBridgeTests(unittest.TestCase):
    def test_bridge_is_previous_close_over_prior_raw_close(self):
        previous = frame("2024-01-02", {"A": 102}, session=0)
        current = frame("2024-01-03", {"A": 51}, previous={"A": 51}, session=1)
        self.assertEqual(quote_bridge(previous, current, "A"), (.5, "known"))

    def test_nonadjacent_sessions_and_missing_index_are_unknown(self):
        previous = frame("2024-01-02", {"A": 102}, session=1)
        for session in (3, None):
            current = frame("2024-01-04", {"A": 51}, session=session)
            self.assertIsNone(quote_bridge(previous, current, "A")[0])

    def test_weekend_is_not_a_gap_when_session_indices_are_adjacent(self):
        previous = frame("2024-01-05", {"A": 102}, session=4)
        current = frame("2024-01-08", {"A": 51}, session=5)
        self.assertEqual(quote_bridge(previous, current, "A")[0], .5)

    def test_missing_nonfinite_nonpositive_prices_cannot_create_a_bridge(self):
        before = frame("2024-01-02", {"A": 102})
        for field in ("close", "prev_close"):
            for value in (None, math.nan, math.inf, 0, -1, True):
                current = frame("2024-01-03", {"A": 51})
                current["rows"]["A"][field] = value
                with self.subTest(field=field, value=value):
                    self.assertIsNone(quote_bridge(before, current, "A")[0])
        self.assertIsNone(quote_bridge(frame("2024-01-02", {}, symbols=["A"]),
                                      frame("2024-01-03", {"A": 51}), "A")[0])

    def test_zero_or_unknown_volume_on_either_side_blocks_phantom_rebasing(self):
        for side in (0, 1):
            for volume in (0, None, math.nan, -1):
                values = [frame("2024-01-02", {"A": 102}), frame("2024-01-03", {"A": 51})]
                values[side]["rows"]["A"]["volume"] = volume
                with self.subTest(side=side, volume=volume):
                    self.assertIsNone(quote_bridge(*values, "A")[0])

    def test_turnover_is_optional_but_must_be_positive_when_present(self):
        before, after = frame("2024-01-02", {"A": 102}), frame("2024-01-03", {"A": 51})
        for value in (0, None, math.nan, -1):
            after["rows"]["A"]["total_turnover"] = value
            self.assertIsNone(quote_bridge(before, after, "A")[0])
        del before["rows"]["A"]["total_turnover"]
        del after["rows"]["A"]["total_turnover"]
        self.assertEqual(quote_bridge(before, after, "A")[0], .5)

    def test_views_adjust_all_price_fields_and_preserve_volume_amount_and_R(self):
        frames = [frame("2024-01-02", {"A": 102}, previous={"A": 100}),
                  frame("2024-01-03", {"A": 51})]
        original = deepcopy(frames)
        adjustment = QuoteAdjustment()
        for current in frames:
            adjustment.advance(current)
        historical = adjustment.view(frames[0], 0)["rows"]["A"]
        for key in ("open", "high", "low", "close", "value"):
            self.assertEqual(historical[key], 51)
        self.assertEqual(historical["prev_close"], 50)
        for key in ("volume", "total_turnover", "returns"):
            self.assertEqual(historical[key], frames[0]["rows"]["A"][key])
        self.assertEqual(adjustment.view(frames[1], 1), frames[1])
        self.assertEqual(frames, original)

    def test_bridge_underflow_and_cumulative_overflow_reset_instead_of_zero(self):
        adjustment = QuoteAdjustment()
        adjustment.advance(frame("2024-01-02", {"A": 1e300}))
        _, resets = adjustment.advance(frame("2024-01-03", {"A": 1}, previous={"A": 1e-300}))
        self.assertEqual(resets["A"], "unrepresentable_bridge")
        adjustment = QuoteAdjustment()
        adjustment.advance(frame("2024-01-02", {"A": 1}))
        adjustment.advance(frame("2024-01-03", {"A": 1}, previous={"A": 1e300}))
        _, resets = adjustment.advance(frame("2024-01-04", {"A": 1}, previous={"A": 1e300}))
        self.assertEqual(resets["A"], "unrepresentable_bridge_product")

    def test_applying_a_scale_cannot_publish_an_underflowed_false_zero(self):
        adjustment = QuoteAdjustment()
        original = frame("2024-01-02", {"A": 1})
        original["rows"]["A"]["low"] = 1e-300
        adjustment.advance(original)
        adjustment.advance(frame("2024-01-03", {"A": 1}, previous={"A": 1e-300}))
        with self.assertRaisesRegex(ValueError, "underflows"):
            adjustment.view(original, 0)


class QuoteAdjustedIndicatorTests(unittest.TestCase):
    def test_known_unit_bridges_match_raw_and_never_replay(self):
        frames = [frame("2024-01-02", {"A": 100}),
                  frame("2024-01-03", {"A": 102}, previous={"A": 100}),
                  frame("2024-01-04", {"A": 104}, previous={"A": 102})]
        adapter, actual = run(Mean2, frames)
        raw = IndicatorAdapter(Mean2())
        expected = [raw.update(current, Mean2) for current in frames]
        self.assertEqual([list(day.values()) for day in actual], expected)
        self.assertEqual(adapter.contract["replay_count"], 0)

    def test_quote_split_example_rebases_window_without_an_event_table(self):
        frames = [frame("2024-01-02", {"A": 102}), frame("2024-01-03", {"A": 51})]
        before = deepcopy(frames)
        adapter, values = run(Mean2, frames)
        self.assertEqual(values[-1]["A"]["value"], 51)
        self.assertEqual(adapter.contract["non_unit_bridge_count"], 1)
        self.assertEqual(adapter.contract["replay_count"], 0)
        self.assertEqual(adapter.contract["execution_mode"], "homogeneous_scalar_incremental")
        self.assertEqual(frames, before)

    def test_nested_price_means_are_50_75_on_one_current_basis(self):
        frames = [frame("2024-01-02", {"A": 100}),
                  frame("2024-01-03", {"A": 102}, previous={"A": 100}),
                  frame("2024-01-04", {"A": 51})]
        adapter, values = run(MeanMean, frames)
        self.assertEqual(values[-1]["A"]["value"], 50.75)
        self.assertEqual(adapter.contract["replayed_frames"], 0)

    def test_two_nonunit_bridges_compose_without_double_application(self):
        frames = [frame("2024-01-02", {"A": 120}), frame("2024-01-03", {"A": 60}),
                  frame("2024-01-04", {"A": 30})]
        adapter, values = run(MeanMean, frames)
        self.assertEqual(values[-1]["A"]["value"], 30)
        self.assertEqual(adapter.contract["replay_count"], 0)

    def test_published_close_values_and_earlier_factor_outputs_are_frozen(self):
        frames = [frame("2024-01-02", {"A": 102}), frame("2024-01-03", {"A": 51})]
        adapter, values = run(Close, frames)
        self.assertEqual([day["A"]["value"] for day in values], [102, 51])
        self.assertEqual(adapter.contract["replay_count"], 0)

    def test_cs_history_then_ts_remains_a_frozen_history(self):
        frames = [frame("2024-01-02", {"A": 102, "B": 80}),
                  frame("2024-01-03", {"A": 51, "B": 80})]
        adapter, values = run(MeanRank, frames)
        self.assertEqual(values[-1]["A"]["value"], .5)
        self.assertEqual(values[-1]["B"]["value"], .5)
        self.assertEqual(adapter.contract["replay_count"], 0)

    def test_price_ts_then_final_cs_uses_current_price_basis(self):
        frames = [frame("2024-01-02", {"A": 102, "B": 80}),
                  frame("2024-01-03", {"A": 51, "B": 80})]
        _, values = run(RankMean, frames)
        self.assertEqual(values[-1]["A"]["value"], 0)
        self.assertEqual(values[-1]["B"]["value"], 1)

    def test_unknown_bridge_rewarms_only_affected_symbol(self):
        frames = [frame("2024-01-02", {"A": 100, "B": 10}),
                  frame("2024-01-03", {"A": 51, "B": 12}, previous={"A": None, "B": 10}),
                  frame("2024-01-04", {"A": 53, "B": 14}, previous={"A": 51, "B": 12})]
        adapter, values = run(Mean2, frames)
        self.assertIsNone(values[1]["A"]["value"])
        self.assertEqual(values[1]["B"]["value"], 11)
        self.assertEqual(values[2]["A"]["value"], 52)
        self.assertEqual(values[2]["B"]["value"], 13)
        self.assertEqual(adapter.contract["window_reset_count"], 1)
        self.assertEqual(adapter.contract["replay_count"], 0)

    def test_missing_bar_does_not_stitch_around_unknown_gap(self):
        frames = [frame("2024-01-02", {"A": 100}), frame("2024-01-03", {}, symbols=["A"]),
                  frame("2024-01-04", {"A": 51}),
                  frame("2024-01-05", {"A": 53}, previous={"A": 51})]
        _, values = run(Mean2, frames)
        self.assertEqual(values[1]["A"]["reason"], "missing_bar")
        self.assertIsNone(values[2]["A"]["value"])
        self.assertEqual(values[3]["A"]["value"], 52)

    def test_zero_volume_phantom_previous_close_cannot_rebase_old_history(self):
        frames = [frame("2024-01-02", {"A": 102}),
                  frame("2024-01-03", {"A": 51}, previous={"A": 1}, volumes={"A": 0}, amounts={"A": 0}),
                  frame("2024-01-04", {"A": 52}, previous={"A": 51}),
                  frame("2024-01-05", {"A": 54}, previous={"A": 52})]
        adapter, values = run(Mean2, frames)
        self.assertIsNone(values[1]["A"]["value"])
        self.assertIsNone(values[2]["A"]["value"])
        self.assertEqual(values[3]["A"]["value"], 53)
        self.assertEqual(adapter.contract["non_unit_bridge_count"], 0)
        self.assertEqual(adapter.contract["replay_count"], 0)

    def test_raw_zero_is_valid_even_though_its_bridge_is_unknown(self):
        frames = [frame("2024-01-02", {"A": 102}), frame("2024-01-03", {"A": 0})]
        _, values = run(Close, frames)
        self.assertTrue(values[-1]["A"]["valid"])
        self.assertEqual(values[-1]["A"]["value"], 0)

    def test_session_index_gap_resets_and_weekend_adjacent_does_not(self):
        frames = [frame("2024-01-05", {"A": 102}, session=4), frame("2024-01-08", {"A": 51}, session=6)]
        adapter, values = run(Mean2, frames)
        self.assertIsNone(values[-1]["A"]["value"])
        self.assertEqual(adapter.contract["reset_reasons"], {"session_gap": 1})
        frames[-1]["session_index"] = 5
        _, values = run(Mean2, frames)
        self.assertEqual(values[-1]["A"]["value"], 51)

    def test_current_price_ratio_and_volume_are_unchanged_by_views(self):
        frames = [frame("2024-01-02", {"A": 102}, previous={"A": 100}), frame("2024-01-03", {"A": 51})]
        _, values = run(MeanPriceRatio, frames)
        self.assertAlmostEqual(values[-1]["A"]["value"], (1.02+1)/2)
        frames[-1]["rows"]["A"]["volume"] = 200
        _, values = run(MeanVolume, frames)
        self.assertEqual(values[-1]["A"]["value"], 150)

    def test_dynamic_exit_and_reentry_restarts_the_same_symbol(self):
        frames = [frame("2024-01-02", {"A": 102}), frame("2024-01-03", {}, symbols=[]),
                  frame("2024-01-04", {"A": 51}), frame("2024-01-05", {"A": 53}, previous={"A": 51})]
        _, values = run(Mean2, frames)
        self.assertIsNone(values[2]["A"]["value"])
        self.assertEqual(values[3]["A"]["value"], 52)

    def test_future_quotes_do_not_rewrite_an_earlier_output(self):
        first = frame("2024-01-02", {"A": 100})
        second = frame("2024-01-03", {"A": 102}, previous={"A": 100})
        adapter = QuoteAdjustedIndicatorAdapter(Mean2())
        adapter.update(first, Mean2)
        published = adapter.update(second, Mean2)
        before = deepcopy(published)
        adapter.update(frame("2024-01-04", {"A": 51}), Mean2)
        self.assertEqual(published, before)
        self.assertEqual(published[0]["value"], 101)

    def test_recursive_mixed_cs_and_new_pool_programs_are_explicitly_rejected(self):
        for factory in (EMA, MixedBound, MeanRankMean, PoolExtension):
            with self.subTest(factory=factory.__name__), self.assertRaises(ValueError):
                QuoteAdjustedIndicatorAdapter(factory())

    def test_homogeneous_incremental_matches_independent_full_native_replay(self):
        frames = [frame("2024-01-02", {"A": 100, "B": 40}),
                  frame("2024-01-03", {"A": 102, "B": 42}, previous={"A": 100, "B": 40}),
                  frame("2024-01-04", {"A": 51, "B": 44}, previous={"A": 51, "B": 42}),
                  frame("2024-01-05", {"A": 53, "B": 22}, previous={"A": 51, "B": 22}),
                  frame("2024-01-08", {"A": 106, "B": 23}, previous={"A": 106, "B": 22})]
        for factory in (Mean2, MeanMean, Std2, Delay1, Decay2):
            adapter, actual = run(factory, frames)
            reference = full_replay_reference(factory, frames)
            self.assertEqual(adapter.contract["execution_mode"], "homogeneous_scalar_incremental")
            self.assertEqual(adapter.contract["replay_count"], 0)
            for current, expected in zip(actual, reference):
                for symbol in current:
                    with self.subTest(factory=factory.__name__, symbol=symbol):
                        self.assertEqual(current[symbol]["valid"], expected[symbol]["valid"])
                        self.assertEqual(current[symbol]["ready"], expected[symbol]["ready"])
                        value, other = current[symbol]["value"], expected[symbol]["value"]
                        if other is None:
                            self.assertIsNone(value)
                        else:
                            self.assertTrue(math.isclose(value, other, rel_tol=1e-12, abs_tol=1e-11), (value, other))

    def test_unproved_nonhomogeneous_or_squared_chains_use_reference_replay(self):
        frames = [frame("2024-01-02", {"A": 102}), frame("2024-01-03", {"A": 51})]
        for factory, expected in ((MeanOffset, 61), (MeanSquared, 2601)):
            adapter, actual = run(factory, frames)
            self.assertEqual(actual[-1]["A"]["value"], expected)
            self.assertEqual(adapter.contract["replay_count"], 1)

    def test_algebraic_degree_does_not_prove_floating_point_denominator_validity(self):
        frames = [frame("2024-01-02", {"A": .38}, previous={"A": .37}),
                  frame("2024-01-03", {"A": math.nextafter(1.9, math.inf)}, previous={"A": 1.9})]
        adapter, actual = run(DifferenceDenominator, frames)
        expected = full_replay_reference(DifferenceDenominator, frames)
        self.assertTrue(actual[-1]["A"]["valid"])
        self.assertTrue(actual[-1]["A"]["ready"])
        self.assertEqual(actual[-1]["A"]["value"], expected[-1]["A"]["value"])
        self.assertGreater(actual[-1]["A"]["value"], 1e15)
        self.assertIsNone(adapter.contract["homogeneous_output_degree"])
        self.assertEqual(adapter.contract["replay_count"], 1)

    def test_unknown_bridge_cannot_reset_an_independent_nonprice_history_port(self):
        for factory in (IndependentPriceAndVolume, IndependentConstantWindow):
            with self.subTest(factory=factory.__name__), self.assertRaisesRegex(ValueError, "independent nonprice temporal ports"):
                QuoteAdjustedIndicatorAdapter(factory())

    def test_duplicate_dates_or_indices_and_replay_budget_are_rejected(self):
        first, second = frame("2024-01-02", {"A": 102}, session=0), frame("2024-01-03", {"A": 51}, session=1)
        adapter = QuoteAdjustedIndicatorAdapter(MeanOffset(), replay_cell_limit=1)
        adapter.update(first, MeanOffset)
        with self.assertRaisesRegex(ValueError, "budget"):
            adapter.update(second, MeanOffset)
        for duplicate in (first, dict(second, session_index=0)):
            adapter = QuoteAdjustedIndicatorAdapter(Mean2())
            adapter.update(first, Mean2)
            with self.assertRaises(ValueError):
                adapter.update(duplicate, Mean2)


if __name__ == "__main__":
    unittest.main()
