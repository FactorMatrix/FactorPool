"""Independent local operator contracts, without BRAIN-parity claims."""
import hashlib
import inspect
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from factorpool.adapter import IndicatorAdapter
from factorpool.runner import load_factor, run


class PoolOperatorTests(unittest.TestCase):
    def factor(self, expression, statements="", ready="True", imports=""):
        tail = "" if expression is None else "        self.value = " + expression + "\n        return " + ready + "\n"
        source = ("from factorpool import PythonIndicator\n" + imports + "\nclass Factor(PythonIndicator):\n"
                  "    def update(self, input):\n" + statements + tail)
        return self.source_factor(source)

    def source_factor(self, source):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "factor.py"
        path.write_text(source)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        factory = load_factor(path, "Factor", {})
        instance = factory()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        return instance, factory

    def evaluate(self, expression, closes, volumes=None, *, statements="", ready="True", imports="", missing=()):
        indicator, factory = self.factor(expression, statements, ready, imports)
        adapter = IndicatorAdapter(indicator)
        names = [str(i) for i in range(len(closes))]
        rows = {name: dict(open=value, high=value, low=value, close=value, volume=1 if volumes is None else volumes[i])
                for i, (name, value) in enumerate(zip(names, closes)) if name not in missing}
        result = adapter.update(dict(date="2024-01-02", symbols=names, rows=rows), factory)
        return adapter, [row["value"] for row in result]

    def assert_values(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for value, target in zip(actual, expected):
            if target is None:
                self.assertIsNone(value)
            else:
                self.assertIsNotNone(value)
                self.assertAlmostEqual(value, target, places=12)

    def test_legacy_program_retains_shared_backend_and_rank_contracts(self):
        adapter, values = self.evaluate("self.cs.rank(input.close, pct=True)", [10, 20, 30])
        self.assertFalse(hasattr(adapter.indicator, "_pool_ir"))
        self.assert_values(values, [1/3, 2/3, 1])
        _, values = self.evaluate("self.cs.rank_unit(input.close)", [10, 20, 30])
        self.assert_values(values, [0, .5, 1])

    def test_same_named_python_functions_keep_the_existing_callback_path(self):
        object_source = ("class Tools:\n"
                         "    def sqrt(self, x, clock=None):\n"
                         "        return x + 7\n"
                         "tools = Tools()\n")
        for expression, imports, statements, expected in (
            ("np.sqrt(input.close)", "import numpy as np\n", "", [2, 3]),
            ("sum([np.sqrt(input.close), 1])", "import numpy as np\n", "", [3, 4]),
            ("tools.sqrt(input.close)", object_source, "", [11, 16]),
            ("tools.sqrt(input.close, clock='trading_days')", object_source, "", [11, 16]),
            ("pow(input.close, 2)", "def pow(x, y):\n    return x + y\n", "", [6, 11]),
            ("pow(input.close, 2)", "def helper(x, y):\n    return x + y\n", "        pow = helper\n", [6, 11]),
            ("math.sqrt(input.close)", "import math\nimport numpy as np\n", "        math = np\n", [2, 3]),
        ):
            with self.subTest(expression=expression, statements=statements):
                adapter, values = self.evaluate(expression, [4, 9], imports=imports, statements=statements)
                self.assertEqual(adapter.mode, "python")
                self.assertFalse(hasattr(adapter.indicator, "_pool_ir"))
                self.assert_values(values, expected)

    def test_true_math_and_reserved_domain_aliases_still_compile_and_cs_never_falls_back(self):
        for expression, imports, statements in (
            ("m.sqrt(input.close)", "import math as m\n", ""),
            ("ops.sqrt(input.close)", "", "        ops = self.cs\n"),
            ("pow(input.close, 2)", "", ""),
        ):
            with self.subTest(expression=expression):
                adapter, values = self.evaluate(expression, [4, 9], imports=imports, statements=statements)
                self.assertEqual(adapter.binding.backend_name, "pool_native")
                self.assert_values(values, [16, 81] if expression.startswith("pow") else [2, 3])
        for expression in ("self.cs.rank_unit(np.sqrt(input.close))", "self.ts.mean(np.sqrt(input.close), 2)"):
            with self.subTest(expression=expression):
                indicator, _ = self.factor(expression, imports="import numpy as np\n")
                with self.assertRaises(ValueError):
                    IndicatorAdapter(indicator)

    def test_ordinary_stateful_math_callbacks_match_shared_python_execution(self):
        prefix = "from factorpool import PythonIndicator\nimport math\nclass Factor(PythonIndicator):\n"
        constructor = ("    def __init__(self):\n"
                       "        super().__init__()\n"
                       "        self.history=[]\n"
                       "        self.total=0\n")
        property_ = ("    @property\n"
                     "    def value(self):\n"
                     "        return self._value\n"
                     "    @value.setter\n"
                     "    def value(self, number):\n"
                     "        self._value=number\n")
        cases = (
            ("        self.history.append(math.sqrt(input.close))\n"
             "        self.value=sum(self.history)\n", "", [2, 5, 9]),
            ("        self.history.append(input.close)\n"
             "        total=0\n"
             "        for value in self.history:\n"
             "            total += math.sqrt(value)\n"
             "        self.value=total\n", "", [2, 5, 9]),
            ("        self.total += math.sqrt(input.close)\n"
             "        self.value=self.total\n", "", [2, 5, 9]),
            ("        self.value=math.sqrt(input.close)\n", property_, [2, 3, 4]),
        )
        for body, definitions, expected in cases:
            with self.subTest(body=body, custom_value=bool(definitions)):
                source = prefix + constructor + definitions + "    def update(self, input):\n" + body + "        return True\n"
                actual = []
                for implementation in ("factortrader", "factorpool"):
                    indicator, factory = self.source_factor(source.replace("from factorpool import", "from " + implementation + " import"))
                    adapter = IndicatorAdapter(indicator)
                    self.assertEqual(adapter.mode, "python")
                    self.assertFalse(hasattr(indicator, "_pool_ir"))
                    output = []
                    for index, close in enumerate((4, 9, 16)):
                        row = dict(open=close, high=close, low=close, close=close, volume=1)
                        result = adapter.update(dict(date=f"2024-01-{index+2:02}", symbols=["A"], rows={"A": row}), factory)
                        output.append(result[0]["value"])
                    self.assert_values(output, expected)
                    actual.append(output)
                self.assertEqual(*actual)

    def test_math_with_python_state_still_fails_when_native_domains_are_declared(self):
        for domain_expression, helper in (
            ("self.cs.rank_unit(input.close)", ""),
            ("self.ts.mean(input.close, 2)", ""),
            ("input.valid", ""),
            ("self.native(input)", "    def native(self, input):\n        return self.cs.rank_unit(input.close)\n"),
        ):
            source = ("from factorpool import PythonIndicator\nimport math\nclass Factor(PythonIndicator):\n"
                      "    def __init__(self):\n        super().__init__()\n        self.history=[]\n"
                      + helper + "    def update(self, input):\n"
                      "        self.history.append(math.sqrt(input.close))\n"
                      "        self.value=" + domain_expression + "\n        return True\n")
            indicator, _ = self.source_factor(source)
            with self.assertRaises(ValueError):
                IndicatorAdapter(indicator)

    def test_math_domains_and_python_math_names_have_native_results(self):
        for expression, source, expected in (
            ("self.cs.sqrt(input.close)", [0, 4, -1, np.nan, np.inf], [0, 2, None, None, None]),
            ("math.log(input.close)", [1, math.e, 0, -1, np.inf], [0, 1, None, None, None]),
            ("math.sqrt(input.close)", [0, 9], [0, 3]),
            ("self.cs.sign(input.close)", [-3, -0.0, 2, np.nan], [-1, 0, 1, None]),
        ):
            with self.subTest(expression=expression):
                adapter, values = self.evaluate(expression, source, imports="import math\n")
                self.assertEqual(adapter.binding.backend_name, "pool_native")
                self.assert_values(values, expected)

    def test_power_signed_power_and_modulo_are_real_finite_and_keep_zero(self):
        cases = (
            ("input.close ** input.volume", [-2, -2, 0, 0, 2, 1e300], [3, .5, 0, -1, -2, 2], [-8, None, 1, None, .25, None]),
            ("pow(input.close, input.volume)", [-2, 0, 3], [2, 0, 2], [4, 1, 9]),
            ("math.pow(input.close, input.volume)", [-2, 0], [.5, 2], [None, 0]),
            ("self.cs.signed_power(input.close, input.volume)", [-4, 0, 0, 4], [.5, 0, -1, .5], [-2, 0, None, 2]),
            ("input.close % input.volume", [-5, 5, 5, 0], [3, -3, 0, -2], [1, -1, None, 0]),
        )
        for expression, close, volume, expected in cases:
            with self.subTest(expression=expression):
                _, values = self.evaluate(expression, close, volume, imports="import math\n")
                self.assert_values(values, expected)

    def test_is_nan_does_not_turn_infinity_unready_or_absent_bars_into_zero(self):
        _, values = self.evaluate("self.cs.is_nan(input.close)", [0, np.nan, np.inf, -np.inf, np.nan], missing=("4",))
        self.assert_values(values, [0, 1, None, None, None])
        _, values = self.evaluate("math.isnan(input.close)", [0, np.nan], imports="import math\n")
        self.assert_values(values, [0, 1])

    def test_if_else_only_requires_the_selected_branch_and_current_column(self):
        expression = "self.cs.if_else(input.close > 0, input.close, input.volume)"
        _, values = self.evaluate(expression, [1, 0, np.nan, np.inf, -2], [np.nan, 3, 5, 4, 0])
        self.assert_values(values, [1, 3, None, None, 0])
        _, values = self.evaluate("self.cs.if_else(self.cs.is_nan(input.close), 9, input.close)", [0, np.nan, np.inf])
        self.assert_values(values, [0, 9, None])
        for condition, expected in (("0", 4), ("2", 9), ("0/0", None)):
            _, values = self.evaluate("self.cs.if_else(" + condition + ", 9, 4) + input.close*0", [1, 2])
            self.assert_values(values, [expected, expected])

    def test_raw_missing_columns_do_not_mask_valid_cross_section_peers(self):
        _, values = self.evaluate("self.cs.rank_unit(self.cs.if_else(self.cs.is_nan(input.close), input.volume, input.close))",
                                  [np.nan, 20, 30], [10, np.nan, np.nan])
        self.assert_values(values, [0, .5, 1])
        _, values = self.evaluate("self.cs.rank_unit(self.cs.if_else(self.cs.is_nan(input.close), 9, input.close))",
                                  [1, 2, np.nan], missing=("2",))
        self.assert_values(values, [0, 1, None])

    def test_invalid_logic_is_strict_and_scalar_control_flow_uses_the_same_selection_rule(self):
        for expression, expected in (
            ("(self.cs.sign(input.close) > 0) & (input.volume > 0)", [None, 1, None, 0]),
            ("(self.cs.sign(input.close) > 0) | (input.volume > 0)", [None, 1, None, 0]),
            ("~(self.cs.sign(input.close) > 0)", [1, 0, None, 1]),
            ("(9 if 0/0 else 4) + self.cs.sign(input.close)*0", [None]*4),
        ):
            with self.subTest(expression=expression):
                _, values = self.evaluate(expression, [0, 1, np.nan, -1], [np.nan, 1, 1, 0])
                self.assert_values(values, expected)
        statements = ("        marker = self.cs.sign(input.close)\n"
                      "        if 0/0:\n"
                      "            self.value = marker + 9\n"
                      "            return True\n"
                      "        else:\n"
                      "            self.value = marker + 4\n"
                      "            return True\n")
        indicator, factory = self.factor(None, statements)
        adapter = IndicatorAdapter(indicator)
        result = adapter.update(dict(date="2024-01-02", symbols=["A"], rows={"A": dict(close=1)}), factory)
        self.assertIsNone(result[0]["value"])

    def test_unselected_temporal_warmup_does_not_gate_current_branch(self):
        _, values = self.evaluate("self.cs.if_else(input.close > 0, input.close, slow)", [1, -1],
                                  statements="        slow = self.ts.mean(input.close, 3)\n")
        self.assert_values(values, [1, None])

    def test_ts_cs_ts_reuses_observation_clock_and_dynamic_axis_reset(self):
        statements = ("        first = self.ts.mean(input.close, 2)\n"
                      "        transformed = self.cs.sqrt(first)\n"
                      "        ranked = self.cs.rank_unit(transformed)\n"
                      "        final = self.ts.mean(ranked, 2)\n")
        indicator, factory = self.factor("final", statements, "self.ts.is_ready(final)")
        adapter = IndicatorAdapter(indicator)
        self.assertEqual(indicator.warm_up_period, 3)
        expected = [[None, None], [None, None], [0, 1], [None, .75], [0, .75]]
        for index, close in enumerate(([1, 9], [1, 9], [1, 9], [np.nan, 9], [1, 9])):
            result = adapter.update(dict(date=f"2024-01-{index+2:02}", symbols=["A", "B"],
                                         rows={name: dict(close=value) for name, value in zip(("A", "B"), close)}), factory)
            self.assert_values([row["value"] for row in result], expected[index])
        adapter.update(dict(date="2024-01-08", symbols=["B"], rows={"B": dict(close=9)}), factory)
        result = adapter.update(dict(date="2024-01-09", symbols=["A", "B"], rows={"A": dict(close=1), "B": dict(close=9)}), factory)
        self.assertIsNone(result[0]["value"])

    def test_unready_temporal_is_nan_stays_unready_but_known_raw_nan_can_be_selected(self):
        indicator, factory = self.factor("self.cs.if_else(self.cs.is_nan(mean), 99, mean)",
                                          "        mean = self.ts.mean(input.close, 2)\n")
        adapter = IndicatorAdapter(indicator)
        for index, value in enumerate((2, 4, np.nan)):
            result = adapter.update(dict(date=f"2024-01-{index+2:02}", symbols=["A"], rows={"A": dict(close=value)}), factory)
            self.assert_values([result[0]["value"]], [[None], [3], [99]][index])

    def test_shared_cs_contracts_still_apply_inside_extended_programs(self):
        for expression, closes, expected in (
            ("self.cs.zscore(self.cs.sqrt(input.close))", [1, 1, 1], [None]*3),
            ("self.cs.rank_unit(self.cs.sign(input.close))", [1, 1, 1], [.5]*3),
            ("self.cs.rank_unit(self.cs.sign(input.close))", [1], [.5]),
            ("self.cs.scale(self.cs.sign(input.close))", [-1, 0, 2], [-.5, 0, .5]),
            ("self.cs.rank(self.cs.sqrt(input.close), pct=True)", [0, 4, 4, np.nan], [1/3, 5/6, 5/6, None]),
        ):
            with self.subTest(expression=expression):
                _, values = self.evaluate(expression, closes)
                self.assert_values(values, expected)

    def test_bad_or_unsupported_source_fails_during_preparation_without_python_callback(self):
        for expression in ("self.cs.if_else(input.close, 1)", "self.cs.sqrt(input.close, 2)",
                           "self.cs.ts_corr(input.close, input.volume, 2) + self.cs.sign(input.close)",
                           "self.cs.sign(input.close) + math.sin(input.close)"):
            with self.subTest(expression=expression):
                indicator, _ = self.factor(expression, imports="import math\n")
                with self.assertRaises(ValueError):
                    IndicatorAdapter(indicator)

    def test_full_script_research_exports_native_values_without_a_trading_engine(self):
        indicator, _ = self.factor("self.cs.rank_unit(self.cs.sqrt(input.volume))")
        original = Path(inspect.getsourcefile(type(indicator))).read_text()
        config = dict(base=dict(start_date="2024-01-02", end_date="2024-01-03"),
                      data=dict(provider="fixture", universe="fixture"), factor={},
                      analysis=dict(groups=3, horizons=[1], decay_steps=1))
        source = "config = " + repr(config) + "\n" + original + '\nif __name__ == "__main__":\n    Factor.run(config)\n'
        with tempfile.TemporaryDirectory() as directory, patch("factortrader.Engine", side_effect=AssertionError("Trading Engine constructed")):
            root = Path(directory)
            (root / "request.json").write_text(json.dumps(dict(name="native extension", source_mode="script", source_code=source)))
            run(directory)
            report = json.loads((root / "result.json").read_text())
            rows = [json.loads(line) for line in (root / "values.jsonl").read_text().splitlines()]
            self.assert_values([row["value"] for row in rows], [0, .5, 1] * 2)
            self.assert_values([row["forward_return"] for row in rows], [.1, 0, -.1] * 2)
            self.assertAlmostEqual(report["summary"]["rank_ic_mean"], -1)
            self.assertEqual(report["metadata"]["indicator_backend"], "pool_native")
            self.assertEqual(report["metadata"]["pool_operator_contract"]["version"], "pool-operators-v1")
            self.assertEqual(report["metadata"]["source_sha256"], hashlib.sha256(source.encode()).hexdigest())
            self.assertEqual((root / "factor.py").read_text(), source)


if __name__ == "__main__":
    unittest.main()
