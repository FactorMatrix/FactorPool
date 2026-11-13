"""A complete value artifact may precede diagnostics, never the final report."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factorpool import runner
from factorpool.result_matrix import iter_result_chunks


ROOT = Path(__file__).resolve().parents[1]


class RunnerPublicationTests(unittest.TestCase):
    def request(self):
        return dict(
            name='early-artifact',
            source_code=(ROOT/'src/examples/factors/low_volume.py').read_text(),
            factor_class='LowVolumeFactor', factor_params={'period': 3},
            data=dict(provider='fixture', universe='fixture', frequency='1d',
                      adjustment='raw', factor_start='2024-01-04', factor_end='2024-01-18'),
            analysis=dict(groups=3, horizons=[5, 20], decay_steps=3))

    def prepare(self, directory):
        (directory/'request.json').write_text(json.dumps(self.request()))

    def check_artifact(self, directory):
        marker = json.loads((directory/'values-ready.json').read_text())
        self.assertEqual(marker['version'], 1)
        self.assertEqual(marker['artifact'], 'values.matrix')
        digest, count = hashlib.sha256(), 0
        for _, rows, _, checksum in iter_result_chunks(directory/'values.matrix'):
            digest.update(bytes.fromhex(checksum))
            count += rows
        self.assertEqual(count, marker['value_count'])
        self.assertEqual(digest.hexdigest(), marker['sha256'])
        self.assertFalse((directory/'values.matrix.tmp').exists())
        self.assertFalse((directory/'values-ready.tmp').exists())
        return marker

    def test_closed_artifact_precedes_diagnostics_and_keeps_the_primary_daily_axis(self):
        diagnostics = runner.build_diagnostics
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare(directory)

            def inspect(*args, **kwargs):
                marker = self.check_artifact(directory)
                self.assertFalse((directory/'result.json').exists())
                self.assertEqual(len(kwargs['primary']['daily']), 11)
                self.assertEqual(marker['value_count'], 9)
                return diagnostics(*args, **kwargs)

            with patch.object(runner, 'build_diagnostics', side_effect=inspect):
                runner.run(directory, values_format='matrix')
            report = json.loads((directory/'result.json').read_text())
            self.assertEqual(len(report['daily']), 3)
            self.assertEqual(report['metadata']['value_count'], 9)
            self.assertEqual(report['metadata']['value_artifact']['sha256'],
                             self.check_artifact(directory)['sha256'])

    def test_diagnostics_failure_leaves_no_success_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare(directory)
            with patch.object(runner, 'build_diagnostics', side_effect=RuntimeError('diagnostics failed')):
                with self.assertRaisesRegex(RuntimeError, 'diagnostics failed'):
                    runner.run(directory, values_format='matrix')
            self.check_artifact(directory)
            self.assertFalse((directory/'result.json').exists())

    def test_failed_export_clears_a_stale_marker_without_publishing_another(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare(directory)
            (directory/'values-ready.json').write_text('{"version":1}')
            with patch('factorpool.result_matrix.write_result_matrix', side_effect=OSError('export failed')):
                with self.assertRaisesRegex(OSError, 'export failed'):
                    runner.run(directory, values_format='matrix')
            self.assertFalse((directory/'values-ready.json').exists())
            self.assertFalse((directory/'result.json').exists())

    def test_explicit_jsonl_retains_the_complete_report_and_serial_fallback(self):
        reports = []
        with tempfile.TemporaryDirectory() as temporary:
            for format in ('matrix', 'jsonl'):
                directory = Path(temporary)/format
                directory.mkdir()
                self.prepare(directory)
                runner.run(directory, values_format=format)
                report = json.loads((directory/'result.json').read_text())
                report.pop('metadata')
                reports.append(report)
                self.assertEqual((directory/'values-ready.json').exists(), format == 'matrix')
                if format == 'jsonl':
                    self.assertEqual(len((directory/'values.jsonl').read_text().splitlines()), 9)
            self.assertEqual(reports[0], reports[1])
