"""Generic column publications against fresh native programs and scalar oracles."""
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import patch
import os
import gc
import tempfile
from pathlib import Path
import unittest

import numpy as np
import _factorpool_native as native
from factorpool.adapter import IndicatorAdapter, indicator_fields, rows_from_arrays
from factorpool.data_client.contract import FIELDS
from factorpool.factor_matrix import FactorMatrix
from factorpool.runner import load_factor
from test_factor_matrix import payload_from_frames, split_payload


class FactorArrayTests(unittest.TestCase):
    def factory(self, expression, ready='True'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)/'factor.py'
        path.write_text('from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n'
                        '    def update(self, input):\n'
                        f'        self.value = {expression}\n        return {ready}\n')
        return load_factor(path, 'Factor', {})

    def test_stateless_reuse_matches_fresh_program_for_missing_and_reentry(self):
        frames = []
        for day in range(6):
            rows = {s: dict(open=10., high=11., low=9., close=10., prev_close=10.,
                           volume=2., total_turnover=float(n+1), is_st=0., is_suspended=0.)
                    for n, s in enumerate(('A', 'B', 'C'))}
            axis = ['A', 'B', 'C']
            if day == 1:
                del rows['B']  # known active identifier with no actual bar
            if day == 2:
                rows['B']['total_turnover'] = np.nan  # observed bar, missing field
            if day == 3:
                rows['B']['total_turnover'] = 0.  # valid zero is an observation
            if day == 4:
                axis.remove('B')
                del rows['B']
            frames.append(dict(date=f'2024-01-{day+2:02}', symbols=axis, rows=rows))
        payload = payload_from_frames(frames)
        expressions = ['input.total_turnover', 'self.cs.rank_unit(input.total_turnover)', '0',
                       'self.is_nan(input.total_turnover)',
                       '0 if self.is_nan(input.total_turnover) else input.total_turnover']
        for expression in expressions:
            with self.subTest(expression=expression):
                factory = self.factory(expression)
                evaluator = FactorMatrix(payload, factory(), factory, frames[-1]['date'])
                for day, frame in enumerate(frames):
                    expected = IndicatorAdapter(factory()).update(frame, factory)
                    actual = evaluator.evaluate_arrays(day)
                    self.assertEqual(rows_from_arrays(actual), expected)
                    self.assertEqual(actual['columns'].tolist(), [evaluator.columns[s] for s in actual['symbols']])
                # Backwards/repeated stateless queries retain the former fresh-plan semantics.
                self.assertEqual(rows_from_arrays(evaluator.evaluate_arrays(0)),
                                 IndicatorAdapter(factory()).update(frames[0], factory))

    def test_projected_quote_columns_keep_presence_and_temporal_state(self):
        payload = split_payload(25)
        for expression, fields in [('self.ts.mean(input.total_turnover,3)', ('total_turnover',)),
                                   ('self.ts.mean(input.close,3)', ('close','prev_close','volume','total_turnover'))]:
            with self.subTest(expression=expression):
                factory = self.factory(expression)
                full = FactorMatrix(payload, factory(), factory, payload['frames'][-1]['date'])
                projected = deepcopy(payload)
                raw = projected['quotes']['values']
                projected['quotes']['present'] = np.any(np.isfinite(raw[:7]), axis=0)
                projected['quotes']['fields'] = fields
                projected['quotes']['values'] = raw[[FIELDS.index(field) for field in fields]]
                columns = np.arange(len(payload['symbols']), dtype=np.intp)
                for frame in projected['frames']:
                    frame['symbol_columns'] = columns
                selected = FactorMatrix(projected, factory(), factory, projected['frames'][-1]['date'])
                for day in range(25):
                    self.assertEqual(rows_from_arrays(selected.evaluate_arrays(day)), full.evaluate(day))
                self.assertEqual(selected.adjusted, 'close' in fields)

    def test_dynamic_projected_axes_match_legacy_lookup_and_shared_symbol_bridge(self):
        payload = split_payload(25)
        for day, frame in enumerate(payload['frames']):
            frame['symbols'] = ['A', 'C'] if day % 5 in (2, 3) else ['A', 'B', 'C']
        projected = deepcopy(payload)
        for frame in projected['frames']:
            columns = np.array([projected['symbols'].index(s) for s in frame['symbols']], dtype=np.intp)
            columns.setflags(write=False)
            frame['symbol_columns'] = columns
        for expression in ('input.total_turnover', 'self.cs.rank_unit(input.total_turnover)',
                           'self.ts.mean(input.total_turnover,3)', 'self.ts.mean(input.close,3)'):
            with self.subTest(expression=expression):
                factory = self.factory(expression)
                before = FactorMatrix(payload, factory(), factory, payload['frames'][-1]['date'])
                after = FactorMatrix(projected, factory(), factory, payload['frames'][-1]['date'])
                for day in range(25):
                    expected = before.evaluate_arrays(day)
                    if hasattr(before._adapter.binding, 'original'):
                        binding = before._adapter.binding
                        binding.graph_set_symbols = binding.original.graph_set_symbols
                    actual = after.evaluate_arrays(day)
                    self.assertEqual(rows_from_arrays(actual), rows_from_arrays(expected))
                    np.testing.assert_array_equal(actual['columns'], expected['columns'])

    def test_cached_axis_revalidates_mutated_caller_columns(self):
        payload = split_payload(3)
        columns = np.array([0, 1], dtype=np.intp)
        for frame in payload['frames']:
            frame['symbol_columns'] = columns
        factory = self.factory('input.total_turnover')
        evaluator = FactorMatrix(payload, factory(), factory, payload['frames'][-1]['date'])
        self.assertEqual(evaluator.evaluate_arrays(0)['symbols'], ('A', 'B'))
        columns[:] = [0, 2]
        self.assertEqual(evaluator.evaluate_arrays(1)['symbols'], ('A', 'C'))
        columns[:] = [0, 0]
        with self.assertRaisesRegex(ValueError, 'ordered unique quote positions'):
            evaluator.evaluate_arrays(2)

    def assert_batch_matches_daily(self, payload, expression, ready='True'):
        factory = self.factory(expression, ready)
        daily = FactorMatrix(payload, factory(), factory, payload['frames'][-1]['date'])
        count, symbols = len(daily.frames), len(daily.symbols)
        expected = dict(value=np.full((count, symbols), np.nan),
                        valid=np.zeros((count, symbols), dtype=bool),
                        ready=np.zeros((count, symbols), dtype=bool),
                        reason=np.full((count, symbols), 6, dtype=np.uint8))
        for day in range(count):
            result = daily.evaluate_arrays(day)
            for field in expected:
                expected[field][day, result['columns']] = result[field]
        for threads in (1, 4):
            with self.subTest(threads=threads), patch.dict(os.environ, FACTORPOOL_COMPUTE_THREADS=str(threads)):
                batch = FactorMatrix(payload, factory(), factory, payload['frames'][-1]['date'])
                result = batch.evaluate_many(list(range(count)))
                self.assertIsNotNone(result)
                self.assertEqual(result['symbols'], tuple(daily.symbols))
                np.testing.assert_array_equal(result['indices'], np.arange(count))
                for field in expected:
                    np.testing.assert_array_equal(result[field], expected[field])
                    if field == 'value':
                        np.testing.assert_array_equal(result[field].view(np.uint64), expected[field].view(np.uint64))
                    self.assertFalse(result[field].flags.writeable)
                self.assertEqual(batch.evaluations, count)
                self.assertEqual(batch.input_frames, count)
                if not batch.temporal:
                    self.assertEqual(rows_from_arrays(batch.evaluate_arrays(0)),
                                     rows_from_arrays(daily.evaluate_arrays(0)))

    def test_stateless_batch_matches_daily_missing_nan_zero_empty_and_reentry_flags(self):
        frames = deepcopy(split_payload(9)['frames'])
        del frames[1]['rows']['B']
        frames[2]['rows']['B']['total_turnover'] = np.nan
        frames[3]['rows']['B']['total_turnover'] = 0.
        frames[4]['symbols'] = []
        frames[5]['symbols'] = ['A', 'C']
        frames[7]['rows']['B']['total_turnover'] = np.inf
        payload = payload_from_frames(frames)
        for frame in payload['frames']:
            frame['symbol_columns'] = np.array([payload['symbols'].index(s) for s in frame['symbols']], dtype=np.intp)
        for expression, ready in [('input.total_turnover', 'True'), ('0', 'True'),
                                  ('self.is_nan(input.total_turnover)', 'True'),
                                  ('0 if self.is_nan(input.total_turnover) else input.total_turnover', 'True'),
                                  ('self.cs.rank_unit(input.total_turnover)', 'True'),
                                  ('self.cs.zscore(input.total_turnover)', 'True'),
                                  ('self.cs.rank_unit(input.total_turnover) + self.log(input.close)', 'True'),
                                  ('input.total_turnover', 'False')]:
            with self.subTest(expression=expression, ready=ready):
                self.assert_batch_matches_daily(payload, expression, ready)

    def test_stateless_batch_uses_real_parallel_dynamic_axes(self):
        count, width = 160, 513
        symbols = [f'S{n:04}' for n in range(width)]
        raw = np.random.default_rng(20261113).integers(-8, 9, size=(1, count, width)).astype(float)
        raw[0, 3, :7] = [0., np.nan, np.inf, -np.inf, 1., 1., -0.]
        present = np.ones((count, width), dtype=bool)
        present[2, 4:9] = False
        frames = []
        for day in range(count):
            columns = np.array([n for n in range(width) if n != day % width], dtype=np.intp)
            if day == 7:
                columns = np.empty(0, dtype=np.intp)
            frames.append(dict(date=(date(2020, 1, 1) + timedelta(days=day)).isoformat(),
                               symbols=[symbols[n] for n in columns], symbol_columns=columns, rows={}))
        payload = dict(frames=frames, quotes=dict(symbols=symbols, fields=('total_turnover',),
                                                  values=raw, present=present))
        for expression in ('input.total_turnover', 'self.cs.rank_unit(input.total_turnover)'):
            with self.subTest(expression=expression):
                self.assert_batch_matches_daily(payload, expression)

    def test_batch_indices_empty_subset_and_temporal_fallback(self):
        payload = split_payload(9)
        factory = self.factory('input.total_turnover')
        matrix = FactorMatrix(payload, factory(), factory, payload['frames'][-1]['date'])
        for indices in ([1, 1], [2, 1], [-1], [9], [1.], [True], [[1]]):
            with self.subTest(indices=indices), self.assertRaisesRegex(ValueError, 'strictly increasing'):
                matrix.evaluate_many(indices)
        empty = matrix.evaluate_many([])
        self.assertEqual(empty['value'].shape, (0, 3))
        self.assertEqual(matrix.evaluations, 0)
        result = matrix.evaluate_many([0, 3, 8])
        self.assertEqual(result['value'].shape, (3, 3))
        self.assertEqual(matrix.evaluations, 3)
        self.assertEqual(matrix.input_frames, 3)
        factory = self.factory('self.ts.mean(input.close,3)')
        matrix = FactorMatrix(payload, factory(), factory, payload['frames'][-1]['date'])
        self.assertIsNone(matrix.evaluate_many([0, 1]))
        self.assertEqual(matrix.evaluations, 0)
        self.assertEqual(matrix.input_frames, 0)
        self.assertIsNone(matrix._adapter)

    def test_all_core_operator_plans_keep_daily_values_or_explicit_temporal_fallback(self):
        from test_core66 import CORE_EXPRESSIONS
        payload = split_payload(9)
        for name, expression in CORE_EXPRESSIONS.items():
            with self.subTest(operator=name):
                factory = self.factory(expression)
                matrix = FactorMatrix(payload, factory(), factory, payload['frames'][-1]['date'])
                if matrix.temporal and matrix.adjusted:
                    self.assertIsNone(matrix.evaluate_many([0, 3, 8]))
                    self.assertEqual(matrix.evaluations, 0)
                else:
                    self.assert_batch_matches_daily(payload, expression)

    def test_basic_temporal_batch_keeps_sparse_clock_reentry_and_daily_handoff(self):
        frames = deepcopy(split_payload(40)['frames'])
        for day in (5, 6, 22):
            frames[day]['symbols'] = ['A', 'C']
        frames[11]['symbols'] = []
        frames[13]['rows']['B']['total_turnover'] = np.nan
        frames[14]['rows']['B']['total_turnover'] = 0.
        del frames[17]['rows']['C']
        payload = payload_from_frames(frames)
        for frame in payload['frames']:
            frame['symbol_columns'] = np.array([payload['symbols'].index(s) for s in frame['symbols']], dtype=np.intp)
        expressions = ['self.ts.mean(input.total_turnover,3)',
                       'self.ts.mean(input.total_turnover,3,clock="valid_observations")',
                       'self.cs.rank_unit(self.ts.mean(input.total_turnover,3))',
                       'self.ts.mean(self.cs.rank_unit(input.total_turnover),3)',
                       'self.ts.hump(input.total_turnover,hump=0.01)',
                       'self.ts.step(1)',
                       'self.tf.trade_when(input.is_st > 0,input.total_turnover,-1)',
                       'self.gp.backfill(input.total_turnover,input.is_st,3,std=4)']
        requested = [0, 2, 7, 13, 20, 25]
        for expression in expressions:
            with self.subTest(expression=expression):
                factory = self.factory(expression)
                before = FactorMatrix(payload, factory(), factory, frames[-1]['date'])
                after = FactorMatrix(payload, factory(), factory, frames[-1]['date'])
                expected = dict(value=np.full((len(requested), 3), np.nan),
                                valid=np.zeros((len(requested), 3), dtype=bool),
                                ready=np.zeros((len(requested), 3), dtype=bool),
                                reason=np.full((len(requested), 3), 6, dtype=np.uint8))
                for row, day in enumerate(requested):
                    values = before.evaluate_arrays(day)
                    for field in expected:
                        expected[field][row, values['columns']] = values[field]
                actual = after.evaluate_many(requested)
                self.assertIsNotNone(actual)
                for field in expected:
                    np.testing.assert_array_equal(actual[field], expected[field])
                np.testing.assert_array_equal(actual['value'].view(np.uint64), expected['value'].view(np.uint64))
                self.assertEqual(after.evaluations, len(requested))
                self.assertEqual(after.input_frames, 26)
                for day in (29, 35):
                    self.assertEqual(rows_from_arrays(after.evaluate_arrays(day)),
                                     rows_from_arrays(before.evaluate_arrays(day)))
                repeated = after.evaluate_many(requested)
                for field in expected:
                    np.testing.assert_array_equal(repeated[field], expected[field])
                fresh = FactorMatrix(payload, factory(), factory, frames[-1]['date'])
                fresh.evaluate_many(requested)
                self.assertEqual(rows_from_arrays(after.evaluate_arrays(26)),
                                 rows_from_arrays(fresh.evaluate_arrays(26)))

    def test_all_temporal_core_plans_batch_unadjusted_basic_data(self):
        from test_core66 import CORE_EXPRESSIONS
        payload = split_payload(9)
        for name, expression in CORE_EXPRESSIONS.items():
            expression = expression.replace('input.close', 'input.total_turnover').replace(
                'input.high', 'input.total_turnover').replace('input.open', 'input.is_st').replace(
                'input.volume', 'input.total_turnover')
            factory = self.factory(expression)
            matrix = FactorMatrix(payload, factory(), factory, payload['frames'][-1]['date'])
            if matrix.temporal:
                with self.subTest(operator=name):
                    self.assertFalse(matrix.adjusted)
                    self.assert_batch_matches_daily(payload, expression)

    def test_required_fields_are_available_before_matrix_loading(self):
        factory = self.factory('self.cs.rank_unit(self.ts.mean(input.total_turnover, 3))')
        self.assertEqual(set(indicator_fields(factory())), {'total_turnover'})

    def test_array_exports_match_list_contract_and_own_their_lifetime(self):
        factors = np.array([[0.,1.,2.],[np.nan,1.,0.],[2.,1.,0.]], dtype=np.float64)
        returns = np.array([[0.,0.,0.],[.1,np.nan,-.2],[0.,-1.,.1],[.2,.3,.4]], dtype=np.float64)
        mask = np.array([[True,True,True],[True,False,True],[False,True,True]], dtype=bool)
        for dtype in (np.bool_, np.uint8):
            for stride in (1,2):
                with self.subTest(dtype=dtype, stride=stride):
                    prepared = native.prepare_research(factors, returns, mask.astype(dtype))
                    expected = native.analyze(factors, returns, 3, 2, 1, mask.astype(np.float64), 1, stride)
                    actual = native.analyze(prepared, None, 3, 2, 1, None, 1, stride, True)
                    self.assertEqual(actual['summary'], expected['summary'])
                    self.assertEqual(actual['daily'], expected['daily'])
                    for name in ('forward_returns','groups'):
                        np.testing.assert_array_equal(actual[name], np.asarray(expected[name], dtype=np.float64))
                        self.assertFalse(actual[name].flags.writeable)
                    del prepared
                    gc.collect()
                    np.testing.assert_array_equal(actual['groups'], np.asarray(expected['groups'], dtype=np.float64))


if __name__ == '__main__':
    unittest.main()
