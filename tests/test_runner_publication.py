"""A complete value artifact may precede diagnostics, never the final report."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factorpool import runner
from factorpool.result_matrix import iter_result_chunks
from script_fixtures import example_source


class RunnerPublicationTests(unittest.TestCase):
    def request(self):
        return dict(
            name='early-artifact',
            source_code=example_source('low_volume'),
            workspace_config=dict(base=dict(start_date='2024-01-04', end_date='2024-01-18'),
                factor={'period': 3}, data=dict(provider='fixture', universe='fixture'),
                analysis=dict(groups=3, horizons=[5, 20], decay_steps=3)))

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
                runner.run(directory)
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
                    runner.run(directory)
            self.check_artifact(directory)
            self.assertFalse((directory/'result.json').exists())

    def test_failed_matrix_write_clears_a_stale_marker_without_publishing_another(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare(directory)
            (directory/'values-ready.json').write_text('{"version":1}')
            with patch('factorpool.result_matrix.write_result_matrix', side_effect=OSError('matrix write failed')):
                with self.assertRaisesRegex(OSError, 'matrix write failed'):
                    runner.run(directory)
            self.assertFalse((directory/'values-ready.json').exists())
            self.assertFalse((directory/'result.json').exists())
