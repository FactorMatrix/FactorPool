"""Every advertised core capability is executed through the real Indicator adapter."""
from pathlib import Path
import json
import tempfile
import unittest
import numpy as np
from factorpool.adapter import IndicatorAdapter
from factorpool.runner import load_factor

CORE_EXPRESSIONS = {
 'abs':'self.cs.abs(input.close)', 'add':'self.cs.add(input.close, input.high, filter=True)',
 'densify':'self.cs.densify(input.open)', 'divide':'self.cs.divide(input.close,input.high)',
 'inverse':'self.cs.inverse(input.close)', 'log':'self.cs.log(input.close)',
 'max':'self.cs.max(input.close,input.high)', 'min':'self.cs.min(input.close,input.high)',
 'multiply':'self.cs.multiply(input.close,2,filter=True)', 'power':'self.cs.power(input.close,2)',
 'reverse':'self.cs.reverse(input.close)', 'sign':'self.cs.sign(input.close)',
 'signed_power':'self.cs.signed_power(input.close,0.5)', 'sqrt':'self.cs.sqrt(input.close)',
 'subtract':'self.cs.subtract(input.high,input.close,filter=True)',
 'and':'(input.close > 0) & (input.high > input.close)', 'or':'(input.close > 0) | (input.high < 0)',
 'not':'~(input.close < 0)', 'equal':'input.close == input.high', 'not_equal':'input.close != input.high',
 'less':'input.close < input.high','less_than_equal':'input.close <= input.high',
 'greater':'input.high > input.close','greater_than_equal':'input.high >= input.close',
 'if_else':'self.cs.if_else(input.close > 0,input.close,0)', 'is_nan':'self.cs.is_nan(input.close)',
 'days_from_last_change':'self.ts.days_from_last_change(input.close)', 'hump':'self.ts.hump(input.close,hump=0.01)',
 'kth_element':'self.ts.kth_element(input.close,3,1,ignore="NaN")',
 'last_diff_value':'self.ts.last_diff_value(input.close,3)', 'ts_arg_max':'self.ts.arg_max(input.close,3)',
 'ts_arg_min':'self.ts.arg_min(input.close,3)', 'ts_av_diff':'self.ts.av_diff(input.close,3)',
 'ts_backfill':'self.ts.backfill(input.close,lookback=3,k=1)', 'ts_corr':'self.ts.corr(input.close,input.volume,3)',
 'ts_count_nans':'self.ts.count_nans(input.close,3)', 'ts_covariance':'self.ts.covariance(input.close,input.volume,3)',
 'ts_decay_linear':'self.ts.decay_linear(input.close,3,dense=False)', 'ts_delay':'self.ts.delay(input.close,2)',
 'ts_delta':'self.ts.delta(input.close,2)', 'ts_mean':'self.ts.mean(input.close,3)',
 'ts_product':'self.ts.product(input.close,3)', 'ts_quantile':'self.ts.quantile(input.close,3,driver="gaussian")',
 'ts_rank':'self.ts.rank(input.close,3,constant=0.1)',
 'ts_regression':'self.ts.regression(input.close,input.volume,3,lag=0,rettype=2)',
 'ts_scale':'self.ts.scale(input.close,3,constant=0.1)', 'ts_std_dev':'self.ts.std(input.close,3,ddof=0)',
 'ts_step':'self.ts.step(1)', 'ts_sum':'self.ts.sum(input.close,3)', 'ts_zscore':'self.ts.zscore(input.close,3)',
 'normalize':'self.cs.normalize(input.close,useStd=True,limit=3)',
 'quantile':'self.cs.quantile(input.close,driver="gaussian",sigma=1)',
 'rank':'self.cs.rank_unit(input.close,rate=0)', 'scale':'self.cs.scale(input.close,scale=1)',
 'winsorize':'self.cs.winsorize(input.close,std=2)', 'zscore':'self.cs.zscore(input.close)',
 'vec_avg':'self.cs.vec_avg((input.close,input.high))', 'vec_sum':'self.cs.vec_sum([input.close,input.high])',
 'bucket':'self.cs.bucket(input.close,buckets="5,10,20")',
 'trade_when':'self.cs.trade_when(input.close > 0,input.close,-1)',
 'group_backfill':'self.cs.group_backfill(input.close,input.open,3,std=4)',
 'group_mean':'self.cs.group_mean(input.close,input.volume,input.open)',
 'group_neutralize':'self.cs.group_neutralize(input.close,input.open)',
 'group_rank':'self.cs.group_rank(input.close,input.open)', 'group_scale':'self.cs.group_scale(input.close,input.open)',
 'group_zscore':'self.cs.group_zscore(input.close,input.open)',
}

class Core66Tests(unittest.TestCase):
 def build(self, expression, statements=""):
  directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
  path=Path(directory.name)/'factor.py'
  path.write_text('from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n    def update(self,input):\n'+statements+'        value='+expression+'\n        self.value=value\n        return self.ts.is_ready(value)\n')
  factory=load_factor(path,'Factor',{});indicator=factory();return IndicatorAdapter(indicator),factory
 def frame(self,t):
  names=['A','B','C','D','E']
  rows={}
  for j,name in enumerate(names):
   x=(t+2)*(j+1)+j*j
   rows[name]=dict(close=x,high=x+1,low=x-1,open=j%2,volume=2*x+1,prev_close=x-1,total_turnover=100)
  return dict(date=f'2024-01-{t+2:02}',symbols=names,rows=rows)
 def test_every_core_name_runs_native_with_actual_values_and_readiness(self):
  catalog=json.loads((Path(__file__).resolve().parents[1]/'src/factorpool/operators.json').read_text())
  self.assertEqual({item['name'] for item in catalog},set(CORE_EXPRESSIONS))
  self.assertEqual(len(CORE_EXPRESSIONS),66)
  self.assertTrue(all(item['native'] and item['pool_wiring']=='connected' for item in catalog))
  for name,expression in CORE_EXPRESSIONS.items():
   with self.subTest(operator=name,expression=expression):
    adapter,factory=self.build(expression)
    self.assertEqual(adapter.mode,'native')
    for t in range(6):result=adapter.update(self.frame(t),factory)
    self.assertEqual(len(result),5)
    self.assertTrue(all(row['ready'] and row['valid'] and np.isfinite(row['value']) for row in result),result)
 def test_paired_regression_rettypes_and_lag_reach_public_adapter(self):
  for rettype in range(10):
   with self.subTest(rettype=rettype):
    adapter,factory=self.build(f'self.ts.regression(input.close,input.volume,4,lag=1,rettype={rettype})')
    for t in range(7):result=adapter.update(self.frame(t),factory)
    self.assertTrue(all(row['valid'] for row in result),result)
    if rettype==2:np.testing.assert_allclose([row['value'] for row in result],.5,atol=1e-12)
 def test_explicit_sample_zscore_is_not_silently_population_zscore(self):
  adapter,factory=self.build('self.ts.zscore(input.close,3,ddof=1,clock="trading_days")')
  for t in range(3):result=adapter.update(self.frame(t),factory)
  np.testing.assert_allclose([r['value'] for r in result],1,atol=1e-12)
 def test_temporal_and_cross_section_order_is_preserved(self):
  adapter,factory=self.build('self.ts.mean(self.cs.densify(input.close),3)')
  for t in range(4):result=adapter.update(self.frame(t),factory)
  np.testing.assert_allclose([r['value'] for r in result],[0,1,2,3,4])
  adapter,factory=self.build('self.cs.rank_unit(self.ts.corr(input.close,input.volume,3))')
  for t in range(4):result=adapter.update(self.frame(t),factory)
  np.testing.assert_allclose([r['value'] for r in result],[.5]*5,atol=1e-10)
 def test_prepared_core_warmup_is_used_for_history_loading(self):
  cases=[('self.ts.product(input.close,20)',20),
         ('self.ts.product(self.ts.product(input.close,3),5)',7),
         ('self.ts.regression(input.close,input.volume,4,lag=2)',6),
         ('self.cs.group_backfill(input.close,input.open,5)',5),
         ('self.ts.mean(self.ts.product(input.close,3),5)',7),
         ('self.ts.product(self.ts.mean(input.close,3,clock="trading_days"),5)',7),
         ('self.ts.step(2)',1)]
  for expression,expected in cases:
   with self.subTest(expression=expression):
    adapter,factory=self.build(expression)
    self.assertEqual(adapter.indicator.warm_up_period,expected)
 def test_unused_nodes_do_not_corrupt_compacted_core_warmup(self):
  adapter,factory=self.build('self.ts.product(input.close,3)', '        unused=self.ts.mean(input.volume,100)\n')
  self.assertEqual(adapter.indicator.warm_up_period,3)
 def test_option_types_are_rejected_instead_of_silently_using_defaults(self):
  for expression in ["self.cs.scale(input.close,scale='2')", "self.cs.winsorize(input.close,std='bad')", "self.ts.hump(input.close,hump='bad')", "self.ts.kth_element(input.close,3,1,ignore=0)", "self.cs.quantile(input.close,sigma='bad')"]:
   with self.subTest(expression=expression):
    with self.assertRaises(ValueError):self.build(expression)
 def test_prewarmed_valid_observation_window_does_not_hide_unready_selected_input(self):
  expression='self.cs.is_nan(self.ts.product(self.cs.if_else(input.close > 0,input.close,self.ts.mean(input.volume,10)),2,clock="valid_observations"))'
  adapter,factory=self.build(expression)
  outputs=[]
  for t,x in enumerate([1,2,-1]):
   frame=self.frame(t)
   for row in frame['rows'].values():row['close']=x
   outputs.append(adapter.update(frame,factory))
  self.assertTrue(all(row['valid'] and row['value']==0 for row in outputs[1]))
  self.assertTrue(all(not row['ready'] and not row['valid'] for row in outputs[2]),outputs[2])
 def test_group_backfill_reuses_each_group_mean_for_multiple_missing_members(self):
  adapter,factory=self.build('self.cs.group_backfill(input.close,input.open,2,std=100)')
  first=self.frame(0)
  for row in first['rows'].values():row['close']=2;row['open']=0
  adapter.update(first,factory)
  second=self.frame(1)
  for row in second['rows'].values():row['close']=np.nan;row['open']=0
  result=adapter.update(second,factory)
  np.testing.assert_allclose([r['value'] for r in result],2)
 def test_window_missing_observations_have_real_numeric_outputs(self):
  for expression,expected in [('self.ts.count_nans(input.close,3)',1),('self.ts.backfill(input.close,3)',2),('self.ts.kth_element(input.close,3,1)',2)]:
   adapter,factory=self.build(expression)
   for t,value in enumerate([1,2,np.nan]):
    frame=self.frame(t)
    for row in frame['rows'].values():row['close']=value
    result=adapter.update(frame,factory)
   self.assertTrue(all(r['valid'] for r in result),result)
   np.testing.assert_allclose([r['value'] for r in result],expected)
