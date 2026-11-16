"""Indicator readiness lowers to current native dependencies, not old Python state."""
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest

import numpy as np

from factorpool import PythonIndicator
from factorpool.adapter import IndicatorAdapter
from factorpool.operator_frontend import DOMAINS, validate_source_frontend
from factorpool.runner import load_factor


class IndicatorNativeReadinessTests(unittest.TestCase):
    def build(self, body, *, constructor='', property_body=''):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)/'factor.py'
        source = 'from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n'
        if constructor:
            source += '    def __init__(self):\n        super().__init__()\n' + constructor
        if property_body:
            source += '    @property\n    def is_ready(self):\n' + property_body
        source += '    def update(self, input):\n' + body
        path.write_text(source)
        factory = load_factor(path, 'Factor', {})
        indicator = factory()
        return IndicatorAdapter(indicator), factory

    @staticmethod
    def frame(day, symbols=('A','B','C','D'), missing=(), values=None):
        values = values or tuple((index+1)*(day+1) for index in range(len(symbols)))
        return dict(date=(date(2024,1,2)+timedelta(days=day)).isoformat(), symbols=list(symbols),
                    rows={symbol:dict(open=value,high=value+1,low=value-1,close=value,
                         prev_close=value,volume=value,total_turnover=value*10)
                         for symbol,value in zip(symbols,values) if symbol not in missing})

    def test_readiness_is_not_a_public_operator_namespace_member(self):
        indicator = PythonIndicator()
        for domain in DOMAINS:
            self.assertNotIn('is_ready', dir(getattr(indicator, domain)))
            with self.assertRaises(AttributeError):
                getattr(getattr(indicator,domain),'is_ready')
        for body in ('return self.ts.is_ready(input.close)',
                     'domain=self.ts\n        return domain.is_ready(input.close)',
                     'ready=self.ts.is_ready\n        return ready(input.close)'):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, 'public operator'):
                validate_source_frontend('class Factor:\n    def update(self,input):\n        '+body+'\n')

    def test_plain_arithmetic_explicit_current_readiness_stays_native(self):
        adapter,factory=self.build('        self.value=input.close+input.volume\n        return self.is_ready\n')
        self.assertEqual(adapter.mode,'native')
        rows=adapter.update(self.frame(0),factory)
        self.assertTrue(all(row['ready'] and row['valid'] for row in rows))
        self.assertEqual([row['value'] for row in rows],[2,4,6,8])

    def test_current_output_readiness_supports_twenty_day_cross_section(self):
        adapter, factory = self.build(
            '        mean=self.ts.mean(input.volume,20)\n'
            '        self.value=self.cs.rank_unit(-mean)\n'
            '        return self.is_ready\n')
        self.assertEqual(adapter.mode,'native')
        for day in range(20):
            rows = adapter.update(self.frame(day),factory)
            self.assertEqual([row['ready'] for row in rows],[day==19]*4)
        np.testing.assert_allclose([row['value'] for row in rows],[1,2/3,1/3,0])

    def test_nested_twenty_then_five_first_becomes_ready_on_twenty_four(self):
        adapter, factory = self.build(
            '        mean=self.ts.mean(input.close,20)\n'
            '        rank=self.cs.rank_unit(mean)\n'
            '        self.value=self.ts.mean(rank,5)\n'
            '        return self.is_ready\n')
        self.assertEqual(adapter.indicator.warm_up_period,24)
        for day in range(24):
            rows=adapter.update(self.frame(day),factory)
            self.assertEqual([row['ready'] for row in rows],[day==23]*4)
        np.testing.assert_allclose([row['value'] for row in rows],[0,1/3,2/3,1])

    def test_pure_scalar_temporal_compiler_also_reads_current_ready(self):
        adapter, factory = self.build(
            '        self.value=self.ts.mean(input.close,3,clock="valid_observations")\n'
            '        return self.is_ready\n')
        self.assertEqual(adapter.mode,'native')
        for day in range(3):
            rows=adapter.update(self.frame(day),factory)
            self.assertEqual([row['ready'] for row in rows],[day==2]*4)
        np.testing.assert_allclose([row['value'] for row in rows],[2,4,6,8])

    def test_explicit_false_does_not_inherit_window_ready(self):
        adapter,factory=self.build('        self.value=self.ts.mean(input.close,2)\n        return False\n')
        for day in range(4):
            rows=adapter.update(self.frame(day),factory)
            self.assertFalse(any(row['ready'] or row['valid'] for row in rows))

    def test_readiness_boolean_conditions_are_per_symbol(self):
        adapter,factory=self.build(
            '        self.value=self.cs.rank_unit(self.ts.mean(input.close,2))\n'
            '        return self.is_ready and not (self.value == 0)\n')
        self.assertFalse(any(row['ready'] for row in adapter.update(self.frame(0),factory)))
        rows=adapter.update(self.frame(1),factory)
        self.assertEqual([row['ready'] for row in rows],[False,True,True,True])

    def test_sample_guard_does_not_turn_a_mature_missing_value_into_unready(self):
        adapter,factory=self.build('        self.value=self.ts.mean(input.close,2)\n        return self.is_ready and self.samples >= 3\n')
        for day in range(3):adapter.update(self.frame(day),factory)
        rows=adapter.update(self.frame(3,values=(np.nan,8,12,16)),factory)
        self.assertTrue(rows[0]['ready'])
        self.assertFalse(rows[0]['valid'])
        self.assertIsNone(rows[0]['value'])

    def test_custom_samples_property_is_current_and_not_global_all_ready(self):
        adapter,factory=self.build(
            '        self.value=self.cs.rank_unit(self.ts.mean(input.close,2))\n'
            '        return self.is_ready\n',
            constructor='        self.warm_up_period=2\n',
            property_body='        return self.samples >= self.warm_up_period + 1\n')
        rows=adapter.update(self.frame(0),factory)
        self.assertFalse(any(row['ready'] for row in rows))
        adapter.update(self.frame(1,missing=('B',)),factory)
        rows=adapter.update(self.frame(2),factory)
        self.assertEqual([row['ready'] for row in rows],[True,False,True,True])
        rows=adapter.update(self.frame(3),factory)
        self.assertTrue(all(row['ready'] for row in rows))

    def test_custom_property_reads_the_inferred_public_warmup(self):
        adapter,factory=self.build('        self.value=self.ts.mean(input.close,2)\n        return self.is_ready\n',
                                  property_body='        return self.samples >= self.warm_up_period * 2\n')
        self.assertEqual(adapter.indicator.warm_up_period,2)
        for day in range(4):
            rows=adapter.update(self.frame(day),factory)
            self.assertEqual([row['ready'] for row in rows],[day==3]*4)

    def test_declared_warmup_is_preserved_without_delaying_dependency_ready(self):
        for cross_section in (False,True):
            with self.subTest(cross_section=cross_section):
                expression='self.ts.mean(input.close,2)'
                if cross_section:expression='self.cs.rank_unit('+expression+')'
                adapter,factory=self.build('        self.value='+expression+'\n        return self.is_ready\n',
                                          constructor='        self.warm_up_period=8\n')
                self.assertEqual(adapter.indicator.warm_up_period,8)
                self.assertFalse(any(row['ready'] for row in adapter.update(self.frame(0),factory)))
                self.assertTrue(all(row['ready'] for row in adapter.update(self.frame(1),factory)))

    def test_native_none_consults_custom_property(self):
        for ending in ('        return None\n',''):
            with self.subTest(ending=ending):
                adapter,factory=self.build(
                    '        self.value=self.ts.mean(input.close,2)\n'+ending,
                    constructor='        self.warm_up_period=2\n',
                    property_body='        return self.samples >= self.warm_up_period + 1\n')
                for day in range(3):
                    rows=adapter.update(self.frame(day),factory)
                    self.assertEqual([row['ready'] for row in rows],[day==2]*4)

    def test_true_update_precedes_false_custom_property_but_not_window_dependencies(self):
        adapter,factory=self.build('        self.value=self.ts.mean(input.close,2)\n        return True\n',
                                  property_body='        return False\n')
        self.assertFalse(any(row['ready'] for row in adapter.update(self.frame(0),factory)))
        self.assertTrue(all(row['ready'] for row in adapter.update(self.frame(1),factory)))

    def test_readiness_short_circuits_unused_invalid_branches(self):
        cases = (
            ('True', '        return self.value > 0\n'),
            ('None', '        return self.samples >= 2 or self.value > 0\n'),
            ('self.is_ready or self.value > 0', ''),
            ('not (False and self.value > 0)', ''),
        )
        for expression in ('self.ts.mean(input.close,2)/0',
                           'self.cs.rank_unit(self.ts.mean(input.close,2))/0'):
            for result, getter in cases:
                with self.subTest(expression=expression, result=result, getter=getter):
                    adapter,factory=self.build('        self.value='+expression+'\n        return '+result+'\n',
                                              property_body=getter)
                    self.assertFalse(any(row['ready'] for row in adapter.update(self.frame(0),factory)))
                    rows=adapter.update(self.frame(1),factory)
                    self.assertTrue(all(row['ready'] for row in rows))
                    self.assertFalse(any(row['valid'] for row in rows))
                    self.assertTrue(all(row['value'] is None for row in rows))

    def test_readiness_and_finite_validity_remain_separate(self):
        adapter,factory=self.build('        self.value=self.cs.rank_unit(self.ts.mean(input.close,1))/0\n        return self.is_ready\n')
        rows=adapter.update(self.frame(0),factory)
        self.assertTrue(all(row['ready'] for row in rows))
        self.assertFalse(any(row['valid'] for row in rows))
        self.assertTrue(all(row['value'] is None for row in rows))

    def test_native_publication_preserves_previous_samples_and_missing_observation(self):
        adapter,factory=self.build('        self.value=self.cs.rank_unit(input.close)\n        return self.is_ready\n')
        adapter.update(self.frame(0),factory)
        adapter.update(self.frame(1,values=(4,3,2,1)),factory)
        adapter.update(self.frame(2,missing=('B',)),factory)
        state=adapter.binding.graph_snapshot(adapter.graph)
        self.assertEqual(state['state_scope'],'streaming')
        self.assertEqual(np.frombuffer(state['samples'],dtype=np.int64).tolist(),[3,2,3,3])
        self.assertEqual(np.frombuffer(state['end_time'],dtype=np.int64)[1],20240103150000)
        self.assertTrue(np.isnan(np.frombuffer(state['current'],dtype=np.float64)[1]))
        self.assertTrue(np.frombuffer(state['publication_ready'],dtype=np.bool_)[1])
        self.assertAlmostEqual(np.frombuffer(state['publication_current'],dtype=np.float64)[1],2/3)
        adapter.binding.graph_reset(adapter.graph)
        state=adapter.binding.graph_snapshot(adapter.graph)
        self.assertFalse(np.frombuffer(state['samples'],dtype=np.int64).any())
        self.assertFalse(np.frombuffer(state['publication_ready'],dtype=np.bool_).any())

    def test_dynamic_axis_retains_members_but_reentry_starts_cold(self):
        adapter,factory=self.build('        self.value=self.cs.rank_unit(input.close)\n        return self.is_ready\n')
        adapter.update(self.frame(0,symbols=('A','B')),factory)
        adapter.update(self.frame(1,symbols=('A','C')),factory)
        state=adapter.binding.graph_snapshot(adapter.graph)
        self.assertEqual(state['symbols'],('A','C'))
        self.assertEqual(np.frombuffer(state['samples'],dtype=np.int64).tolist(),[2,1])
        adapter.update(self.frame(2,symbols=('A','B','C')),factory)
        state=adapter.binding.graph_snapshot(adapter.graph)
        self.assertEqual(np.frombuffer(state['samples'],dtype=np.int64).tolist(),[3,1,2])

    def test_complex_property_with_cross_section_is_rejected_before_execution(self):
        with self.assertRaisesRegex(ValueError,'native|unsupported|Python|unknown'):
            self.build('        self.value=self.cs.rank_unit(input.close)\n        return self.is_ready\n',
                       property_body='        values=[True]\n        return all(values)\n')

    def matrix(self, adapter, *, asof=False, chronological=True):
        symbols=('A','B')
        values=np.array([[day+1,(day+1)*2] for day in range(6)],dtype=np.float64)
        present=np.ones(values.shape,dtype=np.bool_)
        requested=[2,3,4,5]
        steps=range(6) if chronological or asof else requested
        axes=[np.array([0,1],dtype=np.intp) for _ in steps]
        times=[int((date(2024,1,2)+timedelta(days=day)).strftime('%Y%m%d')+'150000') for day in steps]
        graph=adapter.binding.graph_create(adapter.indicator._ir.to_dict(),())
        args=(graph,symbols,{'close':values},present,requested,axes,times)
        if asof:
            return adapter.binding.graph_evaluate_asof(*args,np.ones(values.shape),np.ones(values.shape,dtype=np.int64))
        return adapter.binding.graph_evaluate_many(*args,chronological)

    def test_chronological_matrix_keeps_the_actual_final_program_publication(self):
        adapter,_=self.build('        self.value=self.cs.rank_unit(self.ts.mean(input.close,2))\n        return self.is_ready\n')
        state=self.matrix(adapter)
        publication=state['publication']
        self.assertEqual(publication['state_scope'],'streaming')
        self.assertEqual(np.frombuffer(publication['samples'],dtype=np.int64).tolist(),[6,6])
        np.testing.assert_equal(np.frombuffer(publication['current'],dtype=np.float64),
                                np.frombuffer(state['current'],dtype=np.float64).reshape(-1,2)[-1])

    def test_independent_matrix_publication_is_explicitly_worker_scoped(self):
        adapter,_=self.build('        self.value=self.cs.rank_unit(input.close)\n        return self.is_ready\n')
        state=self.matrix(adapter,chronological=False)
        publication=state['publication']
        self.assertEqual(publication['state_scope'],'matrix_worker')
        self.assertTrue(np.all(np.frombuffer(publication['samples'],dtype=np.int64)>=1))
        np.testing.assert_equal(np.frombuffer(publication['current'],dtype=np.float64),
                                np.frombuffer(state['current'],dtype=np.float64).reshape(-1,2)[-1])

    def test_asof_publication_captures_real_temporal_or_suffix_state_without_replay(self):
        for cross_section in (False,True):
            with self.subTest(cross_section=cross_section):
                expression='self.ts.mean(input.close,2,clock="valid_observations")'
                if cross_section:expression='self.cs.rank_unit('+expression+')'
                adapter,_=self.build('        self.value='+expression+'\n        return self.is_ready\n')
                state=self.matrix(adapter,asof=True)
                publication=state['publication']
                self.assertEqual(publication['state_scope'],'asof_suffix_worker' if cross_section else 'asof_temporal_cohort')
                np.testing.assert_equal(np.frombuffer(publication['current'],dtype=np.float64),
                                        np.frombuffer(state['current'],dtype=np.float64).reshape(-1,2)[-1])
                self.assertTrue(np.all(np.frombuffer(publication['end_time'],dtype=np.int64)==20240107150000))


if __name__=='__main__': unittest.main()
