"""Base quote fields, scalar availability and unchanged proportional returns."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import numpy as np
from factorpool import PythonIndicator
from factorpool.adapter import IndicatorAdapter
from factorpool.config import normalize_config,effective_config
from factorpool.runner import returns_matrix


class PreviousCloseReturn(PythonIndicator):
    def update(self,input):
        self.value=input.close/input.prev_close-1
        return True


class TurnoverMean(PythonIndicator):
    def update(self,input):
        value=self.ts.mean(input.total_turnover,2)
        self.value=value
        return self.ts.is_ready(value)


class ScalarMean(PythonIndicator):
    def update(self,input):
        value=self.ts.mean(input.close,2)
        self.value=value
        return self.ts.is_ready(value)


class PythonPreviousClose(PythonIndicator):
    def update(self,input):
        pair=[input.close,input.prev_close]
        self.value=pair[0]/pair[1]-1
        return True


def frame(day,close=10.,previous=10.,amount=100.):
    return dict(date=f'2024-01-{day:02}',session_index=day-2,symbols=['A'],
                rows={'A':dict(open=close,high=close,low=close,close=close,value=close,
                               prev_close=previous,total_turnover=amount,volume=10.)})


class IndicatorInputTests(unittest.TestCase):
    def test_previous_close_and_turnover_are_real_native_input_fields(self):
        source=frame(2,12.,10.)
        before=deepcopy(source)
        adapter=IndicatorAdapter(PreviousCloseReturn())
        row=adapter.update(source,PreviousCloseReturn)[0]
        self.assertEqual(adapter.mode,'native')
        self.assertTrue(row['valid'])
        self.assertAlmostEqual(row['value'],.2)
        self.assertEqual(source,before)
        adapter=IndicatorAdapter(TurnoverMean())
        self.assertFalse(adapter.update(frame(2,amount=0.),TurnoverMean)[0]['valid'])
        self.assertAlmostEqual(adapter.update(frame(3,amount=100.),TurnoverMean)[0]['value'],50.)

    def test_bad_scalar_inputs_do_not_enter_graph_or_advance_observation_clock(self):
        for bad in (None,np.nan,np.inf,-np.inf):
            with self.subTest(value=bad):
                adapter=IndicatorAdapter(ScalarMean())
                self.assertFalse(adapter.update(frame(2,10.),ScalarMean)[0]['valid'])
                missing=adapter.update(frame(3,bad),ScalarMean)[0]
                self.assertFalse(missing['valid'])
                self.assertEqual(missing['reason'],'source_missing')
                restored=adapter.update(frame(4,0.),ScalarMean)[0]
                self.assertTrue(restored['valid'])
                self.assertAlmostEqual(restored['value'],5.)

    def test_zero_denominator_is_invalid_not_a_valid_zero_return(self):
        adapter=IndicatorAdapter(PreviousCloseReturn())
        row=adapter.update(frame(2,10.,0.),PreviousCloseReturn)[0]
        self.assertFalse(row['valid'])
        self.assertIsNone(row['value'])
        self.assertEqual(row['reason'],'native_unavailable')

    def test_explicit_python_callback_receives_basic_previous_close(self):
        adapter=IndicatorAdapter(PythonPreviousClose())
        self.assertEqual(adapter.mode,'python')
        row=adapter.update(frame(2,12.,10.),PythonPreviousClose)[0]
        self.assertTrue(row['valid'])
        self.assertAlmostEqual(row['value'],.2)

    def test_factor_price_contract_is_separate_from_raw_return_matrix(self):
        config=dict(base=dict(start_date='2024-01-02',end_date='2024-01-03'),
                    data=dict(provider='fixture',universe='fixture',factor_price='quote_adjusted'))
        normalized=normalize_config(config)
        effective=effective_config(normalized)
        self.assertEqual(effective['data']['adjustment'],'raw')
        self.assertEqual(effective['data']['factor_price'],'quote_adjusted')
        source=[frame(2,102.,100.),frame(3,51.,51.)]
        before=deepcopy(source)
        np.testing.assert_allclose(returns_matrix(source,['A']),[[.02],[0.]],atol=1e-15)
        self.assertEqual(source,before)
        legacy=deepcopy(config);legacy['data'].pop('factor_price')
        self.assertEqual(normalize_config(legacy)['data']['factor_price'],'quote_adjusted')
        bad=deepcopy(config);bad['data']['factor_price']='external_dividends'
        with self.assertRaisesRegex(ValueError,'factor_price'):normalize_config(bad)

    def test_quote_bridges_equal_current_basis_cumulative_close_previous_close_returns(self):
        from factorpool.factor_matrix import FactorMatrix
        from factorpool.runner import load_factor
        closes=np.array([100.,102.,51.,52.])
        previous=np.array([100.,100.,51.,51.])
        cumulative=np.cumprod(closes/previous)
        source=[frame(i+2,float(close),float(prev)) for i,(close,prev) in enumerate(zip(closes,previous))]
        before=deepcopy(source)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'factor.py'
            path.write_text('from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n    def update(self,input):\n        value=self.ts.mean(input.close,2)\n        self.value=value\n        return self.ts.is_ready(value)\n')
            factory=load_factor(path,'Factor',{})
            matrix=FactorMatrix(dict(frames=source),factory(),factory,source[-1]['date'])
            for basis in range(len(source)):
                actual=[matrix._frame(i,basis)['rows']['A']['close'] for i in range(basis+1)]
                expected=cumulative[:basis+1]*(closes[basis]/cumulative[basis])
                np.testing.assert_allclose(actual,expected,rtol=1e-12,atol=1e-12)
        self.assertEqual(source,before)
