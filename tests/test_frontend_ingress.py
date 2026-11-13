"""Retired operator syntax is rejected before executing or saving user code."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from factortrader import PythonIndicator as SharedIndicator
from factorpool.adapter import IndicatorAdapter
from factorpool.runner import load_factor
from factorpool.script import ScriptError, parse_source


def source(expression):
    return ('from factorpool import PythonIndicator\n'
            'config={"base":{"start_date":"2024-01-02","end_date":"2024-01-03"},'
            '"data":{"provider":"fixture","universe":"fixture"}}\n'
            'class Factor(PythonIndicator):\n'
            '    def update(self,input):\n'
            f'        self.value={expression}\n'
            '        return True\n'
            'if __name__ == "__main__":\n'
            '    Factor.run(config)\n')


class FrontendIngressTests(unittest.TestCase):
    def test_script_preview_and_class_preflight_share_migration_errors(self):
        for expression, replacement in (
            ('self.cs.group_mean(input.close,input.volume,input.open)', 'self.gp.mean'),
            ('self.cs.vec_sum((input.open,input.close))', 'self.vc.sum'),
            ('self.cs.trade_when(input.open,input.close,-1)', 'self.tf.trade_when'),
            ('self.cs.log(input.close)', 'self.log'),
        ):
            with self.subTest(expression=expression):
                text = source(expression)
                with self.assertRaises(ScriptError) as caught:
                    parse_source(text)
                self.assertIn(replacement, str(caught.exception))
                self.assertIn('line', str(caught.exception))
                child = subprocess.run([sys.executable, '-m', 'factorpool.script'],
                    input=json.dumps({'source_code':text, 'frontend_only':True}),
                    text=True, capture_output=True)
                self.assertEqual(child.returncode, 2)
                self.assertIn(replacement, child.stderr)
                self.assertNotIn('Traceback', child.stderr)
                self.assertEqual(child.stdout, '')

    def test_new_class_frontend_preflight_does_not_require_config_or_execute_code(self):
        text = ('from factorpool import PythonIndicator\n'
                'raise RuntimeError("user module must not execute")\n'
                'class Factor(PythonIndicator):\n'
                '    def update(self,input):\n'
                '        self.value=self.log(self.max(input.close,input.open))\n'
                '        return True\n')
        child = subprocess.run([sys.executable, '-m', 'factorpool.script'],
            input=json.dumps({'source_code':text, 'frontend_only':True}),
            text=True, capture_output=True)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout), {'valid':True})

    def test_retired_class_is_rejected_before_any_module_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            path, marker = Path(directory)/'factor.py', Path(directory)/'executed'
            text = ('from pathlib import Path\n'
                    f'Path({str(marker)!r}).write_text("executed")\n'
                    + source('self.cs.sqrt(input.close)'))
            path.write_text(text)
            with self.assertRaisesRegex(ValueError, 'self.sqrt'):
                load_factor(path, 'Factor', {})
            self.assertFalse(marker.exists())

    def test_shared_trader_class_cannot_bypass_the_pool_operator_frontend(self):
        class OldFactor(SharedIndicator):
            def update(self, input):
                self.value = input.close
                return True
        with self.assertRaisesRegex(TypeError, 'factorpool'):
            IndicatorAdapter(OldFactor())


if __name__ == '__main__':
    unittest.main()
