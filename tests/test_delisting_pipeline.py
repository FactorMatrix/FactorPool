"""Lifecycle selection reaches each real horizon without changing factor history."""
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import _factorpool_native as native

from factorpool import runner


class DelistingPipelineTests(unittest.TestCase):
    def payload(self):
        # Deliberately disagree with the sorted research axis. A ends at
        # source row7; factor_start is source row3 after real warmup history.
        symbols = ['C', 'A', 'B', 'D']
        frames = []
        returns = np.tile([.03, .01, -.02, .04], (14, 1))
        members = np.ones((14, 4), dtype=bool)
        for t in range(14):
            values = dict(A=10+2*t, B=20+t, C=30-t, D=40+2*t)
            active = [s for s in symbols if s != 'A' or t < 7]
            rows = {s: dict(open=100., high=100., low=100., close=100., prev_close=100.,
                            volume=1., total_turnover=values[s], is_suspended=0.)
                    for s in active}
            frames.append(dict(date=(date(2024, 1, 1)+timedelta(days=t)).isoformat(),
                               symbols=active, research_symbols=active, rows=rows))
        returns[7:, 1] = np.nan
        members[7:, 1] = False
        return dict(provider='fixture', synthetic=True, adjustment='raw',
                    snapshot_id='delisting-pipeline', symbols=symbols, frames=frames,
                    returns=returns, membership=members, terminal_exit_rows=[-1, 7, -1, -1])

    def test_warmup_column_alignment_and_horizon_targets_reach_reports_and_traces(self):
        config = dict(
            base=dict(start_date='2024-01-04', end_date='2024-01-09'),
            data=dict(provider='fixture', universe='fixture'),
            analysis=dict(groups=3, horizons=[1, 5], decay_steps=1,
                          trace_dates=['2024-01-04']),
            settings=dict(decay=2, truncation=.3))
        source = '''from factorpool import PythonIndicator
config = CONFIG
class MeanTurnover(PythonIndicator):
    def update(self, input):
        value = self.ts.mean(input.total_turnover, 2)
        self.value = value
        return self.ts.is_ready(value)
if __name__ == "__main__":
    MeanTurnover.run(config)
'''.replace('CONFIG', repr(config))
        prepare = native.prepare_research
        portfolio = native.portfolio_report
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/'request.json').write_text(json.dumps(dict(
                name='delisting-pipeline', source_code=source)))
            with patch.object(runner, 'fixture', return_value=self.payload()), \
                    patch.object(native, 'prepare_research', wraps=prepare) as prepared, \
                    patch.object(native, 'portfolio_report', wraps=portfolio) as reports:
                runner.run(directory, values_format='matrix')
            result = json.loads((directory/'result.json').read_text())
        # Source A's absolute7 becomes relative4, and its position moves from
        # source column1 to sorted column0. Future boundaries are not clamped.
        self.assertEqual(prepared.call_args_list[0].args[4], [4, -1, -1, -1])
        self.assertEqual(result['metadata']['warm_up_period'], 2)
        self.assertEqual(result['metadata']['simulation_warm_up_period'], 1)
        self.assertIn('delisting_filter', result['metadata'])
        horizons = result['diagnostics']['horizons']
        # Independent two-point mean, then linear decay [1,2] over that
        # original history at Jan4, sorted A/B/C/D. Selection occurs later.
        transformed = np.array([43/3, 133/6, 167/6, 133/3])
        expected = {}
        for h, select in ((1, np.array([1, 1, 1, 1], dtype=bool)),
                          (5, np.array([0, 1, 1, 1], dtype=bool))):
            centered = transformed[select] - np.mean(transformed[select])
            weights = np.zeros(4)
            weights[select] = np.clip(centered/np.sum(np.abs(centered)), -.3, .3)
            expected[h] = weights
            horizon = horizons[str(h)]
            first = horizon['alpha_trace'][0]
            self.assertEqual(first['signal_date'], '2024-01-04')
            self.assertEqual([row['symbol'] for row in first['rows']], ['A', 'B', 'C', 'D'])
            np.testing.assert_allclose([row['target_weight'] for row in first['rows']],
                                       weights, atol=3e-14, rtol=0)
            np.testing.assert_allclose([row['decayed_alpha'] for row in first['rows']],
                                       transformed, atol=3e-14, rtol=0)
            self.assertAlmostEqual(first['pnl_fraction'],
                                   float(np.dot(weights, [.01, -.02, .03, .04])), places=14)
            alpha = horizon['portfolios']['alpha']
            for key in ('returns', 'nav', 'constant_book_pnl', 'drawdown_series',
                        'turnover_series'):
                self.assertEqual(len(alpha[key]), len(horizon['portfolio_dates']))
                self.assertTrue(all(value is not None for value in alpha[key]), (h, key))
            self.assertTrue(all(value is not None for value in alpha['brain_style'].values()))
        self.assertNotEqual(expected[1][0], 0)
        self.assertEqual(horizons['5']['alpha_trace'][0]['rows'][0]['status'],
                         'delisting_next_period')
        # The secondary report receives its own normalized target matrix,
        # rather than removing A from already-normalized primary weights.
        overrides = [call.args for call in reports.call_args_list
                     if len(call.args) == 8 and call.args[4] == 5]
        self.assertEqual(len(overrides), 1)
        np.testing.assert_allclose(overrides[0][7][0], expected[5], atol=3e-14, rtol=0)
        primary_constraint = result['diagnostics']['alpha_constraints']['daily'][0]
        secondary_constraint = horizons['5']['alpha_constraints'][0]
        self.assertEqual(primary_constraint['valid_count'], 4)
        self.assertEqual(secondary_constraint['valid_count'], 3)
        self.assertEqual(secondary_constraint['date'], '2024-01-04')
        self.assertEqual(secondary_constraint['index'], 0)
        self.assertAlmostEqual(secondary_constraint['target_gross'], np.abs(expected[5]).sum())
