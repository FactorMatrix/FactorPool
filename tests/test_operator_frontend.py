"""The public operator surface is singular, native and numerically stable."""
import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
import numpy as np

from factorpool import PythonIndicator, TradeBar
from factorpool.adapter import IndicatorAdapter
from factorpool.operator_frontend import DOMAINS, validate_source_frontend
from factorpool.runner import load_factor


class OperatorFrontendTests(unittest.TestCase):
    def build(self, expression, *, ready=None, statements='', imports='', body=None):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)/'factor.py'
        if body is None:
            body=statements+'        value='+expression+'\n        self.value=value\n        return '+(ready or 'self.is_ready')+'\n'
        path.write_text(imports+'from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n    def update(self,input):\n'+body)
        factory=load_factor(path,'Factor',{});return IndicatorAdapter(factory()),factory

    def frame(self,t,values=(1,2,3,4)):
        return dict(date=f'2024-01-{t+1:02}',symbols=list('ABCD'),rows={
            s:dict(open=(n%2),close=x,high=x+1,low=x-1,prev_close=x,
                   volume=(n+1)*(t+1),total_turnover=100.)
            for n,(s,x) in enumerate(zip('ABCD',values))})

    def series(self,expression,values):
        adapter,factory=self.build(expression)
        return [adapter.update(self.frame(t,row),factory) for t,row in enumerate(values)]

    def test_plain_data_and_arithmetic_factors_stay_native(self):
        for expression in ('input.close', 'input.close + input.volume', '(input.close-input.open)/2'):
            with self.subTest(expression=expression):
                adapter,factory=self.build(expression,ready='True')
                self.assertEqual(adapter.mode,'native')
                self.assertTrue(all(row['valid'] for row in adapter.update(self.frame(0),factory)))

    def test_all_runtime_namespaces_expose_only_current_operators(self):
        indicator=PythonIndicator()
        for domain,names in DOMAINS.items():
            self.assertEqual(set(dir(getattr(indicator,domain))),set(names))
        for name in ('log','sqrt','abs','max','min','is_nan','sign','signed_power'):
            self.assertTrue(callable(getattr(indicator,name)))
        for name in ('group_zscore','group_rank','group_mean','vec_avg','bucket','trade_when','log','if_else','rank'):
            with self.assertRaisesRegex(AttributeError,'public operator'):
                getattr(indicator.cs,name)

    def test_domain_readiness_and_aliases_are_rejected_by_static_and_runtime_interfaces(self):
        indicator = PythonIndicator()
        for domain in ('ts', 'cs', 'vc', 'gp', 'tf'):
            for statements, call in (
                ('', f'self.{domain}.is_ready(input.close)'),
                (f'        domain = self.{domain}\n', 'domain.is_ready(input.close)'),
                (f'        ready = self.{domain}.is_ready\n', 'ready(input.close)'),
                ('', f'getattr(self.{domain}, "is_ready")(input.close)'),
            ):
                source = ('from factorpool import PythonIndicator\n'
                          'class Factor(PythonIndicator):\n'
                          '    def update(self, input):\n' + statements +
                          f'        self.value = {call}\n        return self.is_ready\n')
                with self.subTest(domain=domain, call=call), self.assertRaisesRegex(ValueError, 'public operator|removed'):
                    validate_source_frontend(source)
            namespace = getattr(indicator, domain)
            self.assertNotIn('is_ready', dir(namespace))
            with self.subTest(domain=domain, mode='runtime'), self.assertRaisesRegex(AttributeError, 'public operator|removed'):
                getattr(namespace, 'is_ready')

    def test_indicator_readiness_keeps_explicit_sample_guards_and_invalid_values_distinct(self):
        adapter, factory = self.build('self.ts.mean(input.close, 2)', ready='self.is_ready and self.samples >= 3')
        for t in range(3):
            result = adapter.update(self.frame(t, tuple((t+1)*n for n in (1, 2, 3, 4))), factory)
            self.assertEqual([row['ready'] for row in result], [t == 2]*4)
            self.assertEqual([row['valid'] for row in result], [t == 2]*4)
        self.assertEqual([row['value'] for row in result], [2.5, 5., 7.5, 10.])
        result = adapter.update(self.frame(3, (np.nan, 8, 12, 16)), factory)
        self.assertTrue(result[0]['ready'])
        self.assertFalse(result[0]['valid'])
        self.assertIsNone(result[0]['value'])
        self.assertEqual([row['value'] for row in result[1:]], [7., 10.5, 14.])

    def test_obsolete_calls_and_aliases_fail_before_native_or_python_execution(self):
        for call,statements,imports in (
            ('self.cs.group_zscore(input.close,input.open)','',''),
            ('group.group_zscore(input.close,input.open)','        group=self.cs\n',''),
            ('op(input.close)','        op=self.cs.log\n',''),
            ('abs(input.close)','',''), ('max(input.close,2)','',''),
            ('pow(input.close,2)','',''), ('m.log(input.close)','','import math as m\n'),
            ('root(input.close)','','from math import sqrt as root\n'),
            ('m.sqrt(input.close)','        import math as m\n',''),
            ('getattr(self.cs,"group_rank")(input.close,input.open)','',''),
        ):
            with self.subTest(call=call),self.assertRaisesRegex(ValueError,'removed|public operator'):
                self.build(call,statements=statements,imports=imports)

    def test_source_preflight_needs_no_import_or_native_runtime(self):
        for source in (
            'from factorpool import Indicator\nclass Factor(Indicator):\n    pass\n',
            'from factortrader import PythonIndicator as Base\nclass Factor(Base):\n    pass\n',
            'import factortrader as ft\nclass Factor(ft.Indicator):\n    pass\n',
            'from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n    def helper(self,x):\n        return self.cs.group_rank(x,x)\n',
        ):
            with self.assertRaises(ValueError):validate_source_frontend(source)
        validate_source_frontend('from factortrader import TradeBar\nclass Data: pass\n')

    def test_plain_ternary_uses_only_selected_branch_and_separates_condition_validity(self):
        adapter,factory=self.build('self.log(input.close) if input.open > 0 else self.sqrt(input.close)')
        frame=self.frame(0,(4,-1,9,0))
        for s,v in zip('ABCD',(0,1,np.nan,0)):frame['rows'][s]['open']=v
        actual=adapter.update(frame,factory)
        self.assertEqual([r['value'] for r in actual],[2.,None,None,0.])
        self.assertEqual(adapter.mode,'native')

    def test_group_mean_weight_is_keyword_only_and_defaults_to_constant_one(self):
        equal,factory=self.build('self.gp.mean(input.close,input.open)')
        frame=self.frame(0,(10,20,30,40));actual=equal.update(frame,factory)
        self.assertEqual([r['value'] for r in actual],[20.,30.,20.,30.])
        weighted,factory=self.build('self.gp.mean(input.close,input.open,weight=input.volume)')
        out=weighted.update(frame,factory)
        np.testing.assert_allclose([r['value'] for r in out],[25,100/3,25,100/3])
        with self.assertRaisesRegex(ValueError,'keyword-only'):
            self.build('self.gp.mean(input.close,input.volume,input.open)')

    def test_rank_normalizations_keep_distinct_tie_contracts(self):
        frame=self.frame(0,(10,20,20,40))
        for mode,expected in (('unit',[0,.5,.5,1]),('percentile',[.25,.625,.625,1]),('ordinal',[1,2.5,2.5,4])):
            adapter,factory=self.build(f'self.cs.rank_unit(input.close,normalization="{mode}")')
            self.assertEqual([r['value'] for r in adapter.update(frame,factory)],expected)
        with self.assertRaisesRegex(ValueError,'default rate'):
            self.build('self.cs.rank_unit(input.close,normalization="ordinal",rate=0)')

    def test_explicit_default_options_use_identical_clocks_values_and_readiness(self):
        values=[(1.,2,3,4),(np.nan,4,6,8),(3.,6,9,12),(4.,8,12,16),(5.,10,15,20)]
        for plain,explicit in (
            ('self.ts.mean(input.close,3)','self.ts.mean(input.close,3,clock="valid_observations")'),
            ('self.ts.std(input.close,3)','self.ts.std(input.close,3,ddof=0)'),
            ('self.ts.zscore(input.close,3)','self.ts.zscore(input.close,3,ddof=0)'),
            ('self.ts.scale(input.close,3)','self.ts.scale(input.close,3,constant=0)'),
            ('self.ts.decay_linear(input.close,3)','self.ts.decay_linear(input.close,3,dense=False)'),
            ('self.cs.normalize(input.close)','self.cs.normalize(input.close,useStd=False,limit=0)'),
            ('self.cs.scale(input.close)','self.cs.scale(input.close,scale=1)'),
        ):
            with self.subTest(plain=plain):self.assertEqual(self.series(plain,values),self.series(explicit,values))
        zeros=[(0.,0,0,0)]
        self.assertEqual(self.series('self.cs.scale(input.close)',zeros),self.series('self.cs.scale(input.close,scale=1)',zeros))

    def test_non_clock_options_do_not_change_valid_observation_default(self):
        values=[(1.,2,3,4),(np.nan,4,6,8),(3.,6,9,12),(4.,8,12,16),(5.,10,15,20)]
        for expression in ('self.ts.scale(input.close,3,constant=.1)','self.ts.zscore(input.close,3,ddof=1)',
                           'self.ts.decay_linear(input.close,3,dense=True)'):
            with self.subTest(expression=expression):
                explicit=expression[:-1]+',clock="valid_observations")'
                self.assertEqual(self.series(expression,values),self.series(explicit,values))

    def test_decay_clock_dense_and_explicit_zero_fill_have_documented_values(self):
        observations=[(x,x,x,x) for x in (1.,np.nan,3.)]
        for expression,expected,ready in (
            ('self.ts.decay_linear(input.close,3)',None,False),
            ('self.ts.decay_linear(input.close,3,dense=False)',None,False),
            ('self.ts.decay_linear(input.close,3,dense=True)',None,False),
            ('self.ts.decay_linear(input.close,3,clock="valid_observations",dense=False)',None,False),
            ('self.ts.decay_linear(input.close,3,clock="valid_observations",dense=True)',None,False),
            ('self.ts.decay_linear(input.close,3,clock="trading_days")',None,True),
            ('self.ts.decay_linear(input.close,3,clock="trading_days",dense=False)',None,True),
            ('self.ts.decay_linear(input.close,3,clock="trading_days",dense=True)',2.5,True),
            ('self.ts.decay_linear(0 if self.is_nan(input.close) else input.close,3,clock="trading_days")',10/6,True),
        ):
            with self.subTest(expression=expression):
                actual=self.series(expression,observations)[-1][0]
                self.assertEqual(actual['ready'],ready)
                self.assertEqual(actual['valid'],expected is not None)
                if expected is None:self.assertIsNone(actual['value'])
                else:self.assertAlmostEqual(actual['value'],expected,places=12)

    def test_old_parameter_aliases_are_not_silently_accepted(self):
        for expression in ('self.ts.mean(input.close,d=3)','self.cs.normalize(input.close,use_std=True)',
                           'self.cs.scale(input.close,long_scale=1)','self.tf.bucket(input.close,buckets="1,2",skip_begin=True)',
                           'self.ts.backfill(input.close,period=3)'):
            with self.subTest(expression=expression),self.assertRaises(ValueError):self.build(expression)

    def test_document_classes_can_read_only_the_output_assigned_in_this_update(self):
        for body in (
            '        x=input.volume\n        mean=self.ts.mean(x,20)\n        self.value=-self.cs.rank_unit(mean)\n        return self.is_ready\n',
            '        x=self.vc.avg((input.open,input.close))\n        y=self.ts.mean(x,3)\n        group=self.tf.bucket(self.cs.rank_unit(input.volume),buckets="0.33,0.66")\n        self.value=self.gp.zscore(y,group)\n        return self.is_ready\n',
        ):
            adapter,factory=self.build('',body=body)
            for t in range(22):actual=adapter.update(self.frame(t),factory)
            self.assertEqual(adapter.mode,'native')
            self.assertTrue(any(r['valid'] for r in actual))
            json.dumps(actual,allow_nan=False)
        for expression in ('self.ts.mean(prior+input.close,2)', 'self.cs.rank_unit(prior+input.close)'):
            with self.subTest(expression=expression), self.assertRaisesRegex(ValueError, 'state|native|assigned|output'):
                self.build('', body=f'        prior=self.value\n        self.value={expression}\n        return self.is_ready\n')

    def test_recursive_python_state_uses_the_managed_readiness_contract(self):
        body = '        prior=self.value\n        self.value=prior+input.close\n        return self.is_ready\n'
        for custom_property in (False, True):
            source_body = body
            if custom_property:
                source_body += '\n    @property\n    def is_ready(self):\n        return self.samples >= 2\n'
            with self.subTest(custom_property=custom_property):
                adapter, factory = self.build('', body=source_body)
                self.assertEqual(adapter.mode, 'python')
                # The adapter's instance owns its batch publication; use an
                # independent instance for this managed single-input stream.
                indicator = factory()
                for n, (close, expected, previous) in enumerate(((2., 2., 0.), (3., 5., 2.)), start=1):
                    bar = TradeBar(datetime(2024, 1, n+1), 'A', close, close, close, close, 100., timedelta(days=1))
                    self.assertEqual(indicator._receive(bar), custom_property and n == 2)
                    self.assertEqual(indicator.samples, n)
                    self.assertEqual(indicator.current.value, expected)
                    self.assertEqual(indicator.previous.value, previous)
                    self.assertEqual(indicator.is_ready, custom_property and n == 2)
