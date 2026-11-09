import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import _factorpool_native as native
from factortrader import PythonIndicator, NativeVector
from factortrader.binding import engine as engine_binding
from factortrader.binding.loading import _load_native_rows
from factorpool.adapter import IndicatorAdapter
from factorpool.datasource import fixture
from factorpool.runner import run, load_factor

ROOT = Path(__file__).resolve().parents[1]


class ResearchTests(unittest.TestCase):
    def test_hand_calculated_sample_and_missing_price(self):
        out = native.analyze([[1,.5,0]], [[0,0,0],[10,20,30],[11,20,27]], 3,3,1)
        self.assertAlmostEqual(out['summary']['ic_mean'],1)
        self.assertAlmostEqual(out['summary']['rank_ic_mean'],1)
        self.assertIsNone(out['summary']['icir'])
        np.testing.assert_allclose(out['daily'][0]['group_returns'],[-.1,0,.1],atol=1e-15)
        out = native.analyze([[1,.5,0]], [[0,0,0],[10,20,30],[11,20,None]],3,3,1)
        self.assertIsNone(out['summary']['ic_mean'])
        self.assertEqual(out['daily'][0]['pairs'],2)
        self.assertIsNone(out['forward_returns'][0][2])

    def test_icir_ddof_and_ties_against_independent_numpy(self):
        f = [[1,2,3]] * 3
        p = [[10.,10.,10.],[10.,10.,10.]]
        x=np.array([-1.,0.,1.]);x/=np.linalg.norm(x)
        z=np.array([1.,-2.,1.]);z/=np.linalg.norm(z)
        for rho in (.1,.2,.3):
            y = rho*x+math.sqrt(1-rho*rho)*z
            p.append((np.array(p[-1])*(1+y*.01)).tolist())
        out=native.analyze(f,p,3,3,1)
        np.testing.assert_allclose([d['ic'] for d in out['daily']],[.1,.2,.3],atol=1e-13)
        self.assertAlmostEqual(out['summary']['icir'],2,places=10)
        out=native.analyze([[1,1,2]],[[1,1,1],[10,10,10],[11,10,9]],3,3,1)
        reference=np.corrcoef([1.5,1.5,3],[3,2,1])[0,1]
        self.assertAlmostEqual(out['daily'][0]['rank_ic'],reference)

    def test_zero_valid_constant_missing_returns_and_small_groups(self):
        out=native.analyze([[0,None,2]],[[1,1,1],[10,10,0],[11,12,10]],5,3,1)
        self.assertAlmostEqual(out['summary']['coverage'],2/3)
        self.assertEqual(out['daily'][0]['group_members'],[1,0,1,0,0])
        self.assertEqual(out['daily'][0]['pairs'],1)
        self.assertIsNone(out['forward_returns'][0][2])
        self.assertIsNone(native.analyze([[1,1,1]],[[1]*3]*3,3,3,1)['summary']['ic_mean'])
        for f,p,g in (([[1,2]],[[1]],3),([[1],[1,2]],[[1],[1]],3),([[1]],[[1]],0)):
            with self.assertRaises(ValueError):native.analyze(f,p,g,3,1)

    def factor(self, source, params=None):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        path=Path(temporary.name)/'factor.py';path.write_text(source)
        return load_factor(path,'Factor',params or {})

    def assert_parity(self, source, frames):
        factory=self.factor(source)
        adapter=IndicatorAdapter(factory())
        engine_binding._INDICATOR_EXTENSION=adapter.binding
        ft=factory()
        for frame in frames:
            actual=adapter.update(frame,factory)
            ids=tuple(sorted(frame['symbols']))
            if ft._graph is None:ft._activate(ids,batch=True)
            fields=ft._manual_fields
            columns={k:[float('nan') if frame['rows'].get(s,{}).get(k) is None else frame['rows'][s][k] for s in ids] for k in fields}
            stamp=int(frame['date'].replace('-','')+'150000')
            ft._update_native_columns(stamp,columns,symbols=ids,valid=[s in frame['rows'] for s in ids])
            state=ft._binding.graph_snapshot(ft._graph)
            values={k:NativeVector._view(ft._binding,state[k]).to_numpy() for k in ('current','ready','valid')}
            for i,row in enumerate(actual):
                ready=bool(values['ready'][i]);usable=ready and bool(values['valid'][i]) and row['symbol'] in frame['rows']
                self.assertEqual(row['valid'],usable)
                self.assertEqual(row['ready'],ready)
                if usable:self.assertAlmostEqual(row['value'],values['current'][i])
        return actual

    def test_ft_pool_parity_missing_zero_dynamic_and_reset_axes(self):
        source='''from factortrader import PythonIndicator
class Factor(PythonIndicator):
    def update(self,input):
        value=self.ts.mean(input.close,3)
        self.value=value
        return self.ts.is_ready(value)
'''
        frames=fixture()['frames'][:6]
        for i,frame in enumerate(frames):
            for row in frame['rows'].values():row['close']=i+1
        frames[1]['rows'].pop('A')
        frames[3]['rows']['B']['close']=0
        frames[4]['symbols']=['A','C']
        frames[5]['symbols']=['C','B','A']
        self.assert_parity(source,frames)
        source=source.replace('value=self.ts.mean(input.close,3)','value=self.cs.rank(self.ts.mean(input.close,3),pct=True)')
        self.assert_parity(source,frames)

    def test_ts_cs_ts_cs_warmup_24_and_axis_invariance(self):
        source='''from factortrader import PythonIndicator
class Factor(PythonIndicator):
    def update(self,input):
        a=self.ts.mean(input.volume,20)
        b=self.cs.rank(a,pct=True)
        c=self.ts.mean(b,5)
        self.value=self.cs.rank(c,pct=True)
        return self.ts.is_ready(c)
'''
        frames=fixture()['frames']
        self.assertTrue(all(not r['ready'] for r in self.assert_parity(source,frames[:23])))
        final=self.assert_parity(source,frames[:24])
        self.assertTrue(all(r['ready'] for r in final))
        np.testing.assert_allclose([r['value'] for r in final],[1/3,2/3,1])

    def test_python_callback_and_native_agree_after_warmup(self):
        factory=load_factor(ROOT/'examples/factors/close_mean_python.py','CloseMeanPython',{'period':3})
        adapter=IndicatorAdapter(factory());self.assertEqual(adapter.mode,'python')
        frames=fixture()['frames'][:3]
        for i,frame in enumerate(frames):
            for row in frame['rows'].values():row['close']=i+1
            rows=adapter.update(frame,factory)
            self.assertEqual(rows[0]['ready'],i==2)
        self.assertEqual([r['value'] for r in rows],[2,2,2])

    def test_runner_never_constructs_engine_and_future_never_enters_factor(self):
        source=(ROOT/'examples/factors/low_volume.py').read_text()
        request=dict(name='matrix',source_code=source,factor_class='LowVolumeFactor',factor_params={'period':20},
                     data=dict(provider='fixture',universe='fixture',frequency='1d',adjustment='raw',factor_start='2024-01-29',factor_end='2024-01-29'),analysis=dict(groups=3))
        with tempfile.TemporaryDirectory() as d,patch('factortrader.Engine',side_effect=AssertionError('Trading Engine constructed')):
            path=Path(d);(path/'request.json').write_text(json.dumps(request))
            run(d)
            result=json.loads((path/'result.json').read_text())
            self.assertEqual(result['summary']['valid_dates'],1)
            self.assertEqual(result['metadata']['value_count'],3)
            self.assertEqual(len((path/'values.jsonl').read_text().splitlines()),3)
            self.assertNotIn('NaN',(path/'result.json').read_text())


if __name__=='__main__':unittest.main()
