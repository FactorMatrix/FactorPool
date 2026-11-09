import copy
import unittest
import numpy as np
from scipy.stats import spearmanr
import _factorpool_native as native
from factorpool.reporting.diagnostics import build


class PreparedResearchTests(unittest.TestCase):
    def test_masked_ties_and_nonfinite_values_match_independent_scipy(self):
        f = [[0, 0, 1, 2, 2, 4, None], [0, 0, 1, 2, 2, 4, 6]]
        p = [[10]*7, [10]*7, [11, None, 9, 12, 10, 13, 10],
             [10, 12, 11, 9, 13, 10, 14], [9, 10, 12, 11, 14, 13, 10]]
        m = [[1, 1, 1, 1, 1, 0, 1], [1]*7]
        prepared = native.prepare_research(f, p, m)
        for h in (1, 2, 3):
            series = native.ic_series(prepared, h, 3)
            for t in range(len(f)):
                a, b = [], []
                if t+1+h < len(p):
                    for n in range(7):
                        x, entry, exit = f[t][n], p[t+1][n], p[t+1+h][n]
                        if m[t][n] and all(v is not None and np.isfinite(v) for v in (x, entry, exit)) and entry > 0 and exit > 0:
                            a.append(x);b.append(exit/entry-1)
                expected = float(spearmanr(a,b).statistic) if len(a) >= 3 else None
                if expected is None or not np.isfinite(expected):
                    self.assertIsNone(series['rank_ic'][t])
                else:
                    self.assertAlmostEqual(series['rank_ic'][t],expected)
            full = native.analyze(f,p,5,3,1,m,h)
            self.assertEqual(series['ic'],[r['ic'] for r in full['daily']])
            self.assertEqual(series['rank_ic'],[r['rank_ic'] for r in full['daily']])

    def test_task_input_is_owned_and_reuse_has_no_cross_task_state(self):
        f,p,m=[[0,1,2]],[[10]*3,[10]*3,[11,10,9]],[[1]*3]
        prepared=native.prepare_research(f,p,m)
        expected=copy.deepcopy(native.analyze(prepared,None,3,3,1))
        f[0][0]=100;p[2][0]=0;m[0][0]=0
        self.assertEqual(native.analyze(prepared,None,3,3,1),expected)
        other=native.prepare_research(f,p,m)
        self.assertNotEqual(native.analyze(other,None,3,3,1),expected)
        self.assertEqual(native.analyze(prepared,None,3,3,1),expected)
        for matrices in (([[1,2]],[[1]],[[1,1]]), ([[1]],[[1]],[]), ([[1],[1,2]],[[1],[1]],[[1],[1]])):
            with self.assertRaises(ValueError):native.prepare_research(*matrices)
        with self.assertRaises(ValueError):native.analyze(prepared,p,3,3,1,m)
        for h,minimum in ((0,3),(253,3),(1,1)):
            with self.assertRaises(ValueError):native.ic_series(prepared,h,minimum)

    def test_full_report_primary_reuse_and_nonfinite_membership(self):
        f=[[0,0,1,2],[2,1,0,0],[1,1,1,1]]
        p=[[10]*4,[10]*4,[11,12,10,9],[10,11,12,10],[12,9,11,10]]
        m=[[1,1,1,1],[1,float('nan'),1,1],[1]*4]
        dates=['2024-01-30','2024-01-31','2024-02-01']
        pd=dates+['2024-02-02','2024-02-05']
        analysis=dict(groups=5,horizons=[1,2,20],decay_steps=3)
        prepared=native.prepare_research(f,p,m)
        primary=native.analyze(prepared,None,5,3,1)
        expected=build(f,p,m,dates,pd,analysis)
        actual=build(prepared,None,None,dates,pd,analysis,primary=primary)
        self.assertEqual(actual,expected)
        self.assertIsNone(actual['horizons']['1']['series']['ic'][2])
        self.assertEqual(actual['horizons']['20']['ic']['n'],0)
