"""Independent signal-basis quote and daily-R checks for the factor entry path."""
from copy import deepcopy
from datetime import date, timedelta
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from factorpool.adapter import IndicatorAdapter
from factorpool.factor_matrix import FactorMatrix, signal_indices
from factorpool.data_client.contract import FIELDS
from factorpool.runner import load_factor, run
import test_core66


PRICES = frozenset(("open", "high", "low", "close", "prev_close", "value"))


def quote_oracle(values):
    """Scalar cumulative-index reference; never calls FactorMatrix helpers."""
    count, symbols = values.shape[1:]
    q = np.ones((count, symbols))
    segments = np.zeros((count, symbols), dtype=np.int64)
    index = {field: n for n, field in enumerate(FIELDS)}
    for t in range(1, count):
        for n in range(symbols):
            positive = [values[index["close"], t-1, n], values[index["close"], t, n],
                        values[index["prev_close"], t, n], values[index["volume"], t-1, n],
                        values[index["volume"], t, n]]
            good = all(math.isfinite(x) and x > 0 for x in positive)
            for day in (t-1, t):
                amount = values[index["total_turnover"], day, n]
                good &= math.isnan(amount) or math.isfinite(amount) and amount > 0
            candidate = q[t-1, n] * positive[2] / positive[0] if good else math.nan
            if good and math.isfinite(candidate) and candidate > 0:
                q[t, n], segments[t, n] = candidate, segments[t-1, n]
            else:
                q[t, n], segments[t, n] = 1, t
    return q, segments


def payload_from_frames(frames, *, future=0):
    symbols = sorted({s for frame in frames for s in frame["symbols"]})
    values = np.full((len(FIELDS), len(frames), len(symbols)), np.nan)
    for t, frame in enumerate(frames):
        for n, symbol in enumerate(symbols):
            row = frame["rows"].get(symbol, {})
            for k, field in enumerate(FIELDS):
                if field == "member":
                    values[k, t, n] = float(symbol in frame["symbols"])
                elif field in row and row[field] is not None:
                    values[k, t, n] = row[field]
    returns = np.tile(np.arange(1, len(symbols)+1)*.01, (len(frames)+future, 1))
    values[FIELDS.index("return")] = returns[:len(frames)]
    history = deepcopy(frames)
    day = date.fromisoformat(frames[-1]["date"])
    for i in range(future):
        history.append(dict(date=(day+timedelta(days=i+1)).isoformat(),
                            session_index=len(frames)+i, symbols=[], research_symbols=[], rows={}))
    values.setflags(write=False)
    return dict(symbols=symbols, frames=history, returns=returns,
                quotes=dict(fields=FIELDS, values=values,
                            dates=[frame["date"] for frame in frames], symbols=symbols,
                            research_columns=np.arange(len(symbols))),
                provider="fixture", adjustment="raw", synthetic=True, snapshot_id="signal-q-fixture")


def split_payload(count=25, *, future=0):
    frames = []
    start = date(2024, 1, 2)
    for t in range(count):
        rows = {}
        for n, symbol in enumerate(("A", "B", "C")):
            q = (.5 if t >= 10 else 1.) * (.8 if t >= 20 else 1.)
            underlying = 100 + n*30 + t*(n+1)
            rows[symbol] = dict(open=(underlying-.4)*q, high=(underlying+.8)*q,
                                low=(underlying-.9)*q, close=underlying*q,
                                prev_close=(underlying-(n+1))*q,
                                volume=(10+10*n+t)/q, total_turnover=10000+100*n+t,
                                is_st=float(t % 2), is_suspended=0.)
        frames.append(dict(date=(start+timedelta(days=t)).isoformat(),
                           session_index=t, symbols=["A", "B", "C"], rows=rows))
    return payload_from_frames(frames, future=future)


def asof_frames(payload, basis):
    values, symbols = payload["quotes"]["values"], payload["quotes"]["symbols"]
    q, segments = quote_oracle(values)
    result = deepcopy(payload["frames"][:basis+1])
    for t, frame in enumerate(result):
        for n, symbol in enumerate(symbols):
            row = frame["rows"].get(symbol)
            if row is None:
                continue
            scale = q[basis, n]/q[t, n] if segments[t, n] == segments[basis, n] else math.nan
            for field in PRICES:
                if field in row and row[field] is not None:
                    value = row[field]*scale
                    row[field] = value if math.isfinite(value) else None
            if row.get("volume") is not None:
                value = row["volume"]/scale
                row["volume"] = value if math.isfinite(value) else None
    return result


class FactorMatrixTests(unittest.TestCase):
    def factor(self, expression, *, ready="self.ts.is_ready(value)"):
        source = ("from factorpool import PythonIndicator\n"
                  "class Factor(PythonIndicator):\n"
                  "    def update(self, input):\n"
                  f"        value = {expression}\n"
                  "        self.value = value\n"
                  f"        return {ready}\n")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)/"factor.py"
        path.write_text(source)
        factory = load_factor(path, "Factor", {})
        return factory(), factory, source

    def assert_outputs(self, actual, expected):
        json.dumps(actual, allow_nan=False)
        self.assertEqual([(x["symbol"], x["valid"], x["ready"]) for x in actual],
                         [(x["symbol"], x["valid"], x["ready"]) for x in expected])
        for a, b in zip(actual, expected):
            if b["value"] is None:
                self.assertIsNone(a["value"])
            else:
                self.assertAlmostEqual(a["value"], b["value"], delta=1e-11*max(1, abs(b["value"])))

    def test_cumulative_q_and_price_volume_views_use_signal_close_basis(self):
        payload = split_payload()
        fields = ("open", "high", "low", "close", "prev_close", "volume", "total_turnover", "is_st")
        indicator, factory, _ = self.factor(" + ".join("input."+field for field in fields))
        evaluator = FactorMatrix(payload, indicator, factory, payload["quotes"]["dates"][-1])
        evaluator._prepare_adjustment()
        q, segments = quote_oracle(payload["quotes"]["values"])
        np.testing.assert_allclose(evaluator._adjustment, q, rtol=1e-15)
        np.testing.assert_array_equal(evaluator._segments, segments)
        np.testing.assert_allclose(q[[0, 9, 10, 19, 20], 0], [1, 1, .5, .5, .4])
        for basis in (10, 20, 24):
            expected = asof_frames(payload, basis)
            for t in (0, 9, basis):
                actual = evaluator._frame(t, basis)
                for symbol in payload["symbols"]:
                    for field in fields:
                        self.assertAlmostEqual(actual["rows"][symbol][field],
                                               expected[t]["rows"][symbol][field], places=11)

    def test_unknown_bridge_starts_new_segment_without_changing_r_or_status(self):
        payload = split_payload(12)
        values = payload["quotes"]["values"].copy()
        values[FIELDS.index("volume"), 5, 0] = 0
        payload["frames"][5]["rows"]["A"]["volume"] = 0
        values.setflags(write=False)
        payload["quotes"]["values"] = values
        original_returns = payload["returns"].copy()
        indicator, factory, _ = self.factor("input.close + input.volume + input.total_turnover + input.is_st")
        evaluator = FactorMatrix(payload, indicator, factory, payload["quotes"]["dates"][-1])
        evaluator._prepare_adjustment()
        q, segments = quote_oracle(values)
        np.testing.assert_array_equal(evaluator._segments, segments)
        np.testing.assert_allclose(evaluator._adjustment, q)
        historical = evaluator._frame(4, 8)["rows"]["A"]
        self.assertIsNone(historical["close"])
        self.assertIsNone(historical["volume"])
        self.assertEqual(historical["total_turnover"], payload["frames"][4]["rows"]["A"]["total_turnover"])
        self.assertEqual(historical["is_st"], payload["frames"][4]["rows"]["A"]["is_st"])
        evaluator.evaluate(8)
        np.testing.assert_array_equal(payload["returns"], original_returns)

    def test_stateless_rank_evaluates_only_requested_rebalance_signals(self):
        payload = split_payload(23)
        indicator, factory, _ = self.factor("self.cs.rank_unit(input.close, rate=0)")
        evaluator = FactorMatrix(payload, indicator, factory, payload["quotes"]["dates"][-1])
        signals = sorted(signal_indices(23, [5, 20]))
        self.assertEqual(signals, [0, 5, 10, 15, 20])
        for t in signals:
            out = evaluator.evaluate(t)
            self.assertTrue(all(row["ready"] and row["valid"] for row in out))
        self.assertEqual(evaluator.evaluations, len(signals))
        self.assertEqual(evaluator.input_frames, len(signals))
        self.assertIsNone(evaluator._adjustment)

    def test_volume_twenty_day_window_is_ready_after_share_basis_adjustment(self):
        payload = split_payload()
        indicator, factory, _ = self.factor("self.ts.mean(input.volume, 20)")
        evaluator = FactorMatrix(payload, indicator, factory, payload["quotes"]["dates"][-1])
        out = evaluator.evaluate(19)
        self.assertTrue(all(row["ready"] and row["valid"] for row in out), out)
        np.testing.assert_allclose([row["value"] for row in out],
                                   [np.mean(np.arange(10+10*n, 30+10*n))/.5 for n in range(3)])

    def test_mixed_windows_and_ts_cs_ts_match_independent_asof_replay(self):
        payload = split_payload()
        expressions = [
            "self.ts.mean(input.close, 3) + self.ts.mean(input.volume, 3)",
            "self.cs.rank_unit(self.ts.corr(input.close, input.volume, 3))",
            "self.ts.mean(self.cs.rank_unit(self.ts.mean(input.close, 3)) + self.cs.rank_unit(input.volume), 2)",
        ]
        original_returns = payload["returns"].copy()
        for expression in expressions:
            with self.subTest(expression=expression):
                indicator, factory, _ = self.factor(expression)
                evaluator = FactorMatrix(payload, indicator, factory, payload["quotes"]["dates"][-1])
                for basis in (9, 10, 20, 24):
                    reference = IndicatorAdapter(factory())
                    for frame in asof_frames(payload, basis):
                        expected = reference.update(frame, factory)
                    self.assert_outputs(evaluator.evaluate(basis), expected)
        np.testing.assert_array_equal(payload["returns"], original_returns)

    def test_all_sixty_six_core_operators_reach_the_matrix_entry(self):
        core = test_core66.Core66Tests()
        frames = [core.frame(t) for t in range(6)]
        for t in range(1, len(frames)):
            for symbol, row in frames[t]["rows"].items():
                row["prev_close"] = frames[t-1]["rows"][symbol]["close"]
        payload = payload_from_frames(frames)
        for name, expression in test_core66.CORE_EXPRESSIONS.items():
            with self.subTest(operator=name):
                indicator, factory, _ = self.factor(expression)
                evaluator = FactorMatrix(payload, indicator, factory, frames[-1]["date"])
                direct = IndicatorAdapter(factory())
                for frame in frames:
                    expected = direct.update(frame, factory)
                self.assert_outputs(evaluator.evaluate(5), expected)
                self.assertTrue(all(row["valid"] for row in expected), expected)

    def test_nan_window_and_conditional_branch_keep_ready_and_value_semantics(self):
        payload = split_payload(12)
        values = payload["quotes"]["values"].copy()
        values[FIELDS.index("close"), 5, 0] = np.nan
        payload["frames"][5]["rows"]["A"]["close"] = None
        values.setflags(write=False)
        payload["quotes"]["values"] = values
        expressions = [
            "self.ts.count_nans(input.close, 3)",
            '(self.ts.backfill(input.volume, 3) if self.is_nan(input.close) else self.ts.mean(input.close, 3))',
            'self.ts.mean((input.volume if self.is_nan(input.close) else input.close), 3)',
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                indicator, factory, _ = self.factor(expression)
                evaluator = FactorMatrix(payload, indicator, factory, payload["quotes"]["dates"][-1])
                for basis in (5, 6, 10):
                    reference = IndicatorAdapter(factory())
                    for frame in asof_frames(payload, basis):
                        expected = reference.update(frame, factory)
                    self.assert_outputs(evaluator.evaluate(basis), expected)

    def test_valid_observation_window_extends_history_for_selected_nan_branch(self):
        frames = []
        for t in range(10):
            row = dict(close=100+t, prev_close=99+t, high=101+t, low=99+t,
                       open=100+t if t in (0, 3, 6, 9) else None,
                       volume=100, total_turnover=None)
            frames.append(dict(date=(date(2024, 1, 2)+timedelta(days=t)).isoformat(),
                               session_index=t, symbols=["A"], rows={"A": row}))
        payload = payload_from_frames(frames)
        expression = ('self.ts.mean((input.open if self.is_nan(input.total_turnover) else input.total_turnover), 3, clock="valid_observations")')
        indicator, factory, _ = self.factor(expression)
        reference = IndicatorAdapter(factory())
        for frame in frames:
            expected = reference.update(frame, factory)
        self.assertTrue(expected[0]["ready"] and expected[0]["valid"], expected)
        self.assertEqual(expected[0]["value"], 106)
        evaluator = FactorMatrix(payload, indicator, factory, frames[-1]["date"])
        self.assert_outputs(evaluator.evaluate(9), expected)

    def test_custom_ready_does_not_publish_a_truncated_partial_valid_window(self):
        frames = []
        for t in range(10):
            row = dict(close=100+t, prev_close=99+t, high=101+t, low=99+t,
                       open=100+t if t in (0, 3, 6, 9) else None,
                       volume=100, total_turnover=1000)
            frames.append(dict(date=(date(2024, 1, 2)+timedelta(days=t)).isoformat(),
                               session_index=t, symbols=["A"], rows={"A": row}))
        payload = payload_from_frames(frames)
        indicator, factory, _ = self.factor("self.ts.mean(input.open, 3)", ready="True")
        reference = IndicatorAdapter(factory())
        for frame in frames:
            expected = reference.update(frame, factory)
        self.assertTrue(expected[0]["ready"] and expected[0]["valid"], expected)
        self.assertEqual(expected[0]["value"], 106)
        evaluator = FactorMatrix(payload, indicator, factory, frames[-1]["date"])
        self.assert_outputs(evaluator.evaluate(9), expected)

    def test_finite_raw_inputs_do_not_hide_derived_nan_valid_observations(self):
        frames = []
        for t in range(10):
            row = dict(close=100+t, prev_close=99+t, high=101+t, low=99+t,
                       open=1 if t in (0, 3, 6, 9) else 0,
                       volume=100, total_turnover=1000)
            frames.append(dict(date=(date(2024, 1, 2)+timedelta(days=t)).isoformat(),
                               session_index=t, symbols=["A"], rows={"A": row}))
        payload = payload_from_frames(frames)
        indicator, factory, _ = self.factor("self.ts.mean(input.close / input.open, 3)", ready="True")
        reference = IndicatorAdapter(factory())
        for frame in frames:
            expected = reference.update(frame, factory)
        self.assertTrue(expected[0]["ready"] and expected[0]["valid"], expected)
        self.assertEqual(expected[0]["value"], 106)
        evaluator = FactorMatrix(payload, indicator, factory, frames[-1]["date"])
        self.assert_outputs(evaluator.evaluate(9), expected)

    def test_core_explicit_valid_observation_clock_keeps_older_required_samples(self):
        frames = []
        for t in range(10):
            row = dict(close=100+t, prev_close=99+t, high=101+t, low=99+t,
                       open=100+t if t in (0, 3, 6, 9) else None,
                       volume=100, total_turnover=1000)
            frames.append(dict(date=(date(2024, 1, 2)+timedelta(days=t)).isoformat(),
                               session_index=t, symbols=["A"], rows={"A": row}))
        payload = payload_from_frames(frames)
        expression = 'self.ts.product(input.open, 3, clock="valid_observations")'
        indicator, factory, _ = self.factor(expression)
        reference = IndicatorAdapter(factory())
        for frame in frames:
            expected = reference.update(frame, factory)
        self.assertTrue(expected[0]["ready"] and expected[0]["valid"], expected)
        self.assertEqual(expected[0]["value"], 103*106*109)
        evaluator = FactorMatrix(payload, indicator, factory, frames[-1]["date"])
        self.assert_outputs(evaluator.evaluate(9), expected)

    def test_period_one_valid_clock_keeps_ready_when_current_observation_is_missing(self):
        frames = []
        for t in range(4):
            row = dict(close=100+t, prev_close=99+t, high=101+t, low=99+t,
                       open=100+t if t < 3 else None, volume=100, total_turnover=1000)
            frames.append(dict(date=(date(2024, 1, 2)+timedelta(days=t)).isoformat(),
                               session_index=t, symbols=["A"], rows={"A": row}))
        payload = payload_from_frames(frames)
        expression = 'self.ts.product(input.open, 1, clock="valid_observations")'
        indicator, factory, _ = self.factor(expression)
        reference = IndicatorAdapter(factory())
        for frame in frames:
            expected = reference.update(frame, factory)
        self.assertTrue(expected[0]["ready"], expected)
        self.assertFalse(expected[0]["valid"], expected)
        evaluator = FactorMatrix(payload, indicator, factory, frames[-1]["date"])
        self.assert_outputs(evaluator.evaluate(3), expected)

    def test_nan_current_input_is_observed_and_earlier_output_ignores_future_quotes(self):
        payload = split_payload(12, future=3)
        values = payload["quotes"]["values"].copy()
        values[FIELDS.index("close"), 5, 0] = np.nan
        payload["frames"][5]["rows"]["A"]["close"] = None
        values.setflags(write=False)
        payload["quotes"]["values"] = values
        indicator, factory, _ = self.factor("self.is_nan(input.close)")
        evaluator = FactorMatrix(payload, indicator, factory, payload["quotes"]["dates"][-1])
        output = evaluator.evaluate(5)
        self.assertEqual(output[0]["value"], 1)
        self.assertTrue(output[0]["valid"] and output[0]["ready"])
        for t in range(6, len(payload["frames"])):
            payload["frames"][t]["rows"] = {"future-only": {"close": 1e300}}
        indicator, factory, _ = self.factor("self.ts.mean(input.close, 3)")
        full = FactorMatrix(payload, indicator, factory, payload["quotes"]["dates"][-1])
        first = full.evaluate(4)
        shortened = deepcopy(payload)
        shortened["frames"] = shortened["frames"][:5]
        shortened["quotes"]["values"] = shortened["quotes"]["values"][:, :5]
        shortened["quotes"]["dates"] = shortened["quotes"]["dates"][:5]
        second = FactorMatrix(shortened, factory(), factory, shortened["frames"][-1]["date"]).evaluate(4)
        self.assert_outputs(first, second)

    def test_runner_exports_only_five_day_signals_but_preserves_daily_nav(self):
        payload = split_payload(12, future=5)
        raw_quotes, stored_returns = payload["quotes"]["values"].copy(), payload["returns"].copy()
        _, _, source = self.factor("input.volume")
        request = dict(name="signal-only-fixture", source_code=source, factor_class="Factor", factor_params={},
                       data=dict(provider="fixture", universe="fixture", frequency="1d", adjustment="raw",
                                 factor_start=payload["frames"][0]["date"],
                                 factor_end=payload["quotes"]["dates"][-1]),
                       analysis=dict(groups=3, horizons=[5], decay_steps=1))
        with tempfile.TemporaryDirectory() as directory, patch("factorpool.runner.fixture", return_value=payload):
            path = Path(directory)
            (path/"request.json").write_text(json.dumps(request))
            run(path)
            result = json.loads((path/"result.json").read_text())
            rows = [json.loads(line) for line in (path/"values.jsonl").read_text().splitlines()]
        signals = [payload["frames"][i]["date"] for i in (0, 5, 10)]
        self.assertEqual(sorted({row["date"] for row in rows}), signals)
        self.assertEqual(len(rows), 9)
        self.assertEqual(result["metadata"]["value_count"], 9)
        self.assertEqual(result["metadata"]["price_contract"]["factor_evaluations"], 3)
        self.assertEqual([row["date"] for row in result["daily"]], signals)
        horizon = result["diagnostics"]["horizons"]["5"]
        self.assertEqual(horizon["portfolio_signal_dates"], signals)
        self.assertEqual(horizon["portfolio_dates"], [f["date"] for f in payload["frames"][1:16]])
        long_high = horizon["portfolios"]["long_G3"]
        np.testing.assert_allclose(long_high["returns"], stored_returns[1:16, 2], atol=1e-14)
        np.testing.assert_allclose(long_high["nav"], np.cumprod(1+stored_returns[1:16, 2]), atol=1e-14)
        np.testing.assert_array_equal(payload["returns"], stored_returns)
        np.testing.assert_array_equal(payload["quotes"]["values"], raw_quotes)


if __name__ == "__main__":
    unittest.main()
