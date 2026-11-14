"""Bitwise compatibility oracle captured before moving Alpha arithmetic to C++."""
from datetime import date, timedelta
import json
from pathlib import Path
from script_fixtures import result_values
import tempfile
from unittest.mock import patch
import unittest
import numpy as np
import _factorpool_native as native
from factorpool.settings import normalize_settings
from factorpool.simulation import alpha_targets, _decayed


def published_decay_reference(factors, members, period):
    from factorpool import PythonIndicator
    from factorpool.adapter import IndicatorAdapter

    class LinearDecay(PythonIndicator):
        def __init__(self, period):
            super().__init__()
            self.period = period

        def update(self, input):
            value = self.ts.decay_linear(input.value, self.period)
            self.value = value
            return self.ts.is_ready(value)

    factory = lambda: LinearDecay(period)
    adapter = IndicatorAdapter(factory())
    if adapter.mode != "native":
        raise ValueError("Alpha decay requires the shared native ts.decay_linear operator")
    output = np.full(factors.shape, np.nan, dtype=np.float64)
    symbols = [f"factor-{n}" for n in range(factors.shape[1])]
    lookup = {symbol: n for n, symbol in enumerate(symbols)}
    epoch = date(2000, 1, 1)
    for t in range(factors.shape[0]):
        axis = [symbol for n, symbol in enumerate(symbols) if members[t, n]]
        rows = {symbol: {"value": float(factors[t, n])}
                for n, symbol in enumerate(symbols)
                if members[t, n] and np.isfinite(factors[t, n])}
        # Only ordered observations matter to this count-based shared window.
        frame = dict(date=(epoch + timedelta(days=t)).isoformat(), symbols=axis, rows=rows)
        for item in adapter.update(frame, factory):
            if item["valid"] and item["value"] is not None:
                output[t, lookup[item["symbol"]]] = item["value"]
    return output


def published_targets(full_factors, full_membership, settings, *, details=None, trace_indices=()):
    """Return contiguous target weights, or None for the existing native Alpha."""
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    settings = normalize_settings(settings, {"universe": settings.get("universe", "fixture")})
    factors = np.asarray(full_factors, dtype=np.float64)
    membership = np.asarray(full_membership, dtype=np.float64)
    if factors.ndim != 2 or membership.shape != factors.shape:
        raise ValueError("Alpha factors and membership must have the same date x symbol shape")
    period, neutralization, cap = settings["decay"], settings["neutralization"], settings["truncation"]
    if period <= 1 and neutralization == "MARKET" and cap == 0:
        return None
    members = np.isfinite(membership) & (membership > 0)
    transformed = published_decay_reference(factors, members, period) if period > 1 else factors
    targets = np.full(factors.shape, np.nan, dtype=np.float64, order="C")
    traces = set(trace_indices)
    if details is not None:
        details.update(trace={}, constraints=[])
    for t in range(factors.shape[0]):
        valid = members[t] & np.isfinite(transformed[t])
        values = transformed[t, valid].astype(np.longdouble)
        if details is not None and t in traces:
            details["trace"][t] = dict(decayed=transformed[t].copy(),
                                       neutralized=np.full(factors.shape[1], np.nan))
        if values.size == 0 or (neutralization == "MARKET" and np.all(values == values[0])):
            if details is not None and t in traces and values.size:
                details["trace"][t]["neutralized"][valid] = 0
            if details is not None and cap > 0:
                details["constraints"].append(dict(index=t, valid_count=int(values.size),
                    capacity=float(values.size*cap), full_gross_feasible=bool(values.size*cap >= 1-1e-12),
                    target_gross=None, target_net=None, unallocated_gross=None, status="no_valid_weights"))
            continue
        if neutralization == "MARKET":
            values -= values.mean(dtype=np.longdouble)
        if details is not None and t in traces:
            details["trace"][t]["neutralized"][valid] = values
        norm = np.abs(values).sum(dtype=np.longdouble)
        if not np.isfinite(norm) or norm <= 0:
            if details is not None and cap > 0:
                details["constraints"].append(dict(index=t, valid_count=int(values.size),
                    capacity=float(values.size*cap), full_gross_feasible=bool(values.size*cap >= 1-1e-12),
                    target_gross=None, target_net=None, unallocated_gross=None, status="no_valid_weights"))
            continue
        weights = np.asarray(values / norm, dtype=np.float64)
        if cap > 0:
            weights = np.clip(weights, -cap, cap)
        targets[t] = 0
        targets[t, valid] = weights
        if details is not None and cap > 0:
            gross = float(np.abs(weights).sum())
            feasible = values.size*cap >= 1-1e-12
            details["constraints"].append(dict(index=t, valid_count=int(values.size),
                capacity=float(values.size*cap), full_gross_feasible=bool(feasible),
                target_gross=gross, target_net=float(weights.sum()), unallocated_gross=max(0., 1-gross),
                status="unit_gross_capacity_infeasible" if not feasible else ("unallocated_gross" if gross < 1-1e-12 else "ok")))
    return targets


def zero_exposure_reference(factors, membership, settings, *, details=None, trace_indices=()):
    """Keep the published arithmetic oracle; correct only its known-cash rows.

    The historical implementation conflated finite zero norm with unavailable
    input. This explicit contract difference must not hide changes to any
    nonzero target, missing sample, trace or floating-point reduction.
    """
    result = published_targets(factors, membership, settings, details=details,
                               trace_indices=trace_indices)
    if result is None:
        return None
    members = np.isfinite(membership) & (membership > 0)
    transformed = (published_decay_reference(factors, members, settings['decay'])
                   if settings['decay'] > 1 else factors)
    for index, row in enumerate(transformed):
        eligible = row[members[index] & np.isfinite(row)]
        known_cash = eligible.size and (np.all(eligible == 0) or
            settings['neutralization'] == 'MARKET' and np.all(eligible == eligible[0]))
        if not known_cash:
            continue
        result[index] = 0
        if details is not None and settings['truncation'] > 0:
            constraint = details['constraints'][index]
            constraint.update(target_gross=0., target_net=0., unallocated_gross=1.,
                status='unallocated_gross' if constraint['full_gross_feasible']
                       else 'unit_gross_capacity_infeasible')
    return result


class NativeAlphaTests(unittest.TestCase):
    def assert_bits(self, actual, expected):
        self.assertEqual(actual.shape, expected.shape)
        np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))

    def compare(self, factors, members, neutralization, cap, *, decay=0):
        settings = normalize_settings(dict(neutralization=neutralization,
                                          truncation=cap, decay=decay),
                                      dict(provider='fixture', universe='fixture'))
        indices = sorted(set([0, max(0, len(factors)-1)])) if len(factors) else []
        expected_details, actual_details = {}, {}
        expected = zero_exposure_reference(factors, members, settings, details=expected_details,
                                     trace_indices=indices)
        actual = alpha_targets(factors, members, settings, details=actual_details,
                               trace_indices=indices)
        if expected is None:
            self.assertIsNone(actual)
            self.assertEqual(actual_details, expected_details)
            return
        self.assert_bits(actual, expected)
        self.assertTrue(actual.flags.c_contiguous)
        self.assertFalse(actual.flags.writeable)
        self.assertEqual(actual_details['constraints'], expected_details['constraints'])
        self.assertEqual(set(actual_details['trace']), set(expected_details['trace']))
        for index in indices:
            for column in ('neutralized', 'decayed'):
                self.assert_bits(actual_details['trace'][index][column],
                                 expected_details['trace'][index][column])

    def test_wide_pairwise_cancellation_and_pathological_magnitudes_are_bit_exact(self):
        random = np.random.default_rng(5537891)
        for width in (1, 7, 8, 9, 63, 64, 127, 128, 129, 5329, 8193, 20001):
            values = random.standard_normal((6, width))
            values *= np.power(10., random.integers(-300, 301, values.shape))
            values[1] = np.where(np.arange(width) % 2, 1e308, -1e308)
            values[2] = 0
            values[3] = 1e308
            values[4, ::3] = np.nan
            values[5, ::7] = -0.
            members = np.ones(values.shape)
            members[4, ::5] = 0
            members[4, ::11] = np.nan
            members[5, ::13] = np.inf
            for mode, cap in (('NONE', 0), ('NONE', .003), ('MARKET', .2)):
                with self.subTest(width=width, neutralization=mode, cap=cap):
                    self.compare(values, members, mode, cap)

    def test_random_masked_and_strided_matrices_match_all_constraint_and_trace_fields(self):
        random = np.random.default_rng(531)
        factors = random.normal(size=(37, 74))
        members = random.choice([0., -1., 1., 2., np.nan, np.inf], size=factors.shape)
        factors[random.random(factors.shape) < .15] = np.nan
        factors[0] = 3
        factors[1] = 0
        before = [x.tobytes() for x in (factors, members)]
        for value in (factors, members):
            value.flags.writeable = False
        for cap in (0, .005, .01, .25, 1):
            for mode in ('NONE', 'MARKET'):
                with self.subTest(cap=cap, mode=mode):
                    self.compare(factors[::2, ::-2], members[::2, ::-2], mode, cap)
        self.assertEqual(before, [x.tobytes() for x in (factors, members)])

    def test_decay_matrix_bridge_matches_published_per_day_graph_with_axis_resets(self):
        random = np.random.default_rng(3107)
        values = random.normal(size=(29, 17))
        members = np.ones(values.shape, dtype=bool)
        values[4:7, 2] = np.nan
        members[9:11, 2] = False
        members[5:8, 15] = False
        members[14] = False
        values[23:25] = 0
        for period in (2, 5, 20):
            with self.subTest(period=period):
                expected = published_decay_reference(values, members, period)
                with patch('factorpool.adapter.IndicatorAdapter.update',
                           side_effect=AssertionError('Per-day Python update invoked')):
                    actual = _decayed(values, members, period)
                self.assert_bits(actual, expected)
                self.compare(values, members.astype(float), 'MARKET', .1, decay=period)
                self.compare(values, members.astype(float), 'NONE', 0, decay=period)

    def test_empty_axes_and_zero_weights_retain_the_published_contract(self):
        for shape in ((0, 0), (0, 3), (3, 0), (2, 3)):
            factors, members = np.zeros(shape), np.ones(shape)
            self.compare(factors, members, 'NONE', .1)
            self.compare(factors, members, 'MARKET', .1)

    def test_native_validation_and_owned_buffers(self):
        factors, members = np.array([[1., 2, 3]]), np.ones((1, 3))
        result = native.alpha_targets(factors, members, 'NONE', .2, [0], True)
        before = result['targets'].copy()
        factors[:] = 100
        members[:] = 0
        self.assert_bits(result['targets'], before)
        for mode, cap, traces in (('INDUSTRY', 0, []), ('NONE', np.nan, []),
                                  ('NONE', -1, []), ('NONE', 2, []),
                                  ('NONE', 0, [True]), ('NONE', 0, [-1]),
                                  ('NONE', 0, [1])):
            with self.subTest(mode=mode, cap=cap, traces=traces):
                with self.assertRaises(ValueError):
                    native.alpha_targets(factors, members, mode, cap, traces, True)
        with self.assertRaisesRegex(ValueError, 'shape'):
            native.alpha_targets(factors, members[:, :2], 'NONE', 0, [], False)

    def test_complete_report_and_values_match_the_frozen_alpha_pipeline(self):
        from factorpool.runner import run
        for mode, cap, decay in (('MARKET', 0, 0), ('MARKET', .2, 3), ('NONE', .1, 2)):
            with self.subTest(mode=mode, cap=cap, decay=decay):
                config = dict(base=dict(start_date='2024-01-02', end_date='2024-02-29'),
                    data=dict(provider='fixture', universe='fixture'),
                    analysis=dict(groups=3, horizons=[1, 5], min_ic_pairs=2,
                                  decay_steps=2, trace_dates=['2024-01-09']),
                    settings=dict(neutralization=mode, truncation=cap, decay=decay))
                source = ('from factorpool import PythonIndicator\n'
                          f'config = {config!r}\n'
                          'class Factor(PythonIndicator):\n'
                          '    def update(self, input):\n'
                          '        self.value = self.cs.rank_unit(input.close + input.volume)\n'
                          '        return True\n'
                          'if __name__ == "__main__":\n'
                          '    Factor.run(config)\n')
                results, values = [], []
                for function in (zero_exposure_reference, alpha_targets):
                    with tempfile.TemporaryDirectory() as directory, \
                            patch('factorpool.simulation.alpha_targets', function):
                        path = Path(directory)
                        (path/'request.json').write_text(json.dumps(dict(
                            name='native-alpha-full-report', source_code=source)))
                        run(path)
                        result = json.loads((path/'result.json').read_text())
                        result.pop('metadata')
                        results.append(result)
                        values.append(json.dumps(result_values(path), sort_keys=True, allow_nan=False).encode())
                self.assertEqual(results[0], results[1])
                self.assertEqual(values[0], values[1])
