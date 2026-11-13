"""Full research scripts use literal configuration and the shared Indicator."""
import builtins
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from factortrader import PythonIndicator as SharedIndicator
from factorpool import PythonIndicator
from factorpool.config import effective_config
from factorpool.runner import run
from factorpool.script import parse_source


def source_config(period=3):
    return dict(base=dict(start_date='2024-01-04', end_date='2024-01-04', frequency='1d'),
                data=dict(provider='fixture', universe='fixture'), factor=dict(period=period),
                analysis=dict(groups=3, horizons=[1, 5], decay_steps=2))


def full_script(config=None, counters=False):
    config = source_config() if config is None else config
    imports = 'import builtins\nbuiltins.FACTORPOOL_SCRIPT_IMPORTS += 1\n' if counters else ''
    constructor = '        builtins.FACTORPOOL_SCRIPT_INSTANCES += 1\n' if counters else ''
    return ('from factorpool import PythonIndicator\n'+imports+'\nconfig = '+repr(config)+'''\n\n
class MatrixFactor(PythonIndicator):
    def __init__(self, period=3):
        super().__init__()
'''+constructor+'''        self.period = period

    def update(self, input):
        mean_close = self.ts.mean(input.close, self.period)
        self.value = mean_close
        return self.ts.is_ready(mean_close)


if __name__ == "__main__":
    MatrixFactor.run(config)
''')


class ScriptParserTests(unittest.TestCase):
    def test_literal_config_normalizes_dates_constructor_and_analysis(self):
        config = source_config()
        parsed = parse_source(full_script(config))
        self.assertEqual(parsed['source_mode'], 'script')
        self.assertEqual(parsed['factor_class'], 'MatrixFactor')
        self.assertEqual(parsed['factor_params'], {'period': 3})
        self.assertEqual(parsed['data']['provider'], 'fixture')
        self.assertEqual(parsed['data']['universe'], 'fixture')
        self.assertEqual(parsed['data']['factor_start'], '2024-01-04')
        self.assertEqual(parsed['data']['factor_end'], '2024-01-04')
        self.assertEqual(parsed['data']['frequency'], '1d')
        self.assertEqual(parsed['data']['adjustment'], 'raw')
        self.assertEqual(parsed['data']['factor_price'], 'quote_adjusted')
        self.assertEqual(parsed['analysis']['groups'], 3)
        self.assertEqual(parsed['analysis']['horizons'], [1, 5])
        self.assertEqual(parsed['source_config'], config)
        parsed['factor_params']['period'] = 999
        self.assertEqual(parsed['source_config']['factor']['period'], 3)
        self.assertEqual(config['factor']['period'], 3)
        mixed = source_config()
        mixed['base'].update(start_date='20240104', end_date='2024-01-05')
        parsed = parse_source(full_script(mixed))
        self.assertEqual(parsed['source_config'], mixed)
        self.assertEqual(parsed['data']['factor_start'], '2024-01-04')
        self.assertEqual(parsed['data']['factor_end'], '2024-01-05')
        wide = source_config()
        wide['analysis']['min_ic_pairs'] = 2147483647
        self.assertEqual(parse_source(full_script(wide))['analysis']['min_ic_pairs'], 2147483647)
        wide['analysis']['min_ic_pairs'] += 1
        with self.assertRaisesRegex(ValueError, 'min_ic_pairs'):
            parse_source(full_script(wide))

    def test_forward_adjustment_is_default_and_the_only_factor_price_mode(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                config = source_config()
                if explicit:
                    config['data']['factor_price'] = 'quote_adjusted'
                original = copy.deepcopy(config)
                parsed = parse_source(full_script(config))
                self.assertEqual(parsed['data']['factor_price'], 'quote_adjusted')
                self.assertEqual(parsed['data']['adjustment'], 'raw')
                self.assertEqual(effective_config(parsed)['data']['factor_price'], 'quote_adjusted')
                self.assertEqual(parsed['source_config'], original)
                self.assertEqual(config, original)
        for basis in ('raw', 'forward', 'backward', '', None, False, 1, []):
            with self.subTest(basis=basis):
                config = source_config()
                config['data']['factor_price'] = basis
                with self.assertRaisesRegex(ValueError, 'factor_price'):
                    parse_source(full_script(config))

    def test_config_extraction_uses_ast_without_executing_imports_or_code(self):
        source = full_script()
        with patch('builtins.exec', side_effect=AssertionError('AST resolution must not execute code')), \
                patch('builtins.eval', side_effect=AssertionError('AST resolution must not evaluate code')):
            parsed = parse_source(source)
        self.assertEqual(parsed['factor_class'], 'MatrixFactor')
        # A helper class and a misleading string must not change entry selection.
        source = source.replace('class MatrixFactor', 'class Helper:\n    note = "Other.run(config)"\n\nclass MatrixFactor')
        self.assertEqual(parse_source(source)['factor_class'], 'MatrixFactor')

    def test_missing_or_multiple_guarded_entries_and_nonliteral_config_have_line_errors(self):
        source = full_script()
        invalid = {
            'missing entry': source.replace('    MatrixFactor.run(config)', '    pass'),
            'unguarded entry': source.replace('if __name__ == "__main__":\n    MatrixFactor.run(config)', 'MatrixFactor.run(config)'),
            'duplicate entry': source.replace('    MatrixFactor.run(config)', '    MatrixFactor.run(config)\n    MatrixFactor.run(config)'),
            'unknown class': source.replace('    MatrixFactor.run(config)', '    MissingFactor.run(config)'),
            'dynamic config': source.replace('config = '+repr(source_config()), 'config = make_config()'),
            'invalid config type': source.replace('config = '+repr(source_config()), 'config = []'),
            'duplicate config key': source.replace("config = {", "config = {'factor': {'period': 9}, ", 1),
            'syntax error': source+'\ndef broken(:\n',
        }
        for name, candidate in invalid.items():
            with self.subTest(case=name):
                with self.assertRaisesRegex(ValueError, r'(?i)(line|行)'):
                    parse_source(candidate)

    def test_unsupported_data_config_is_rejected_without_running_a_factor(self):
        for section, key, value in (('base', 'frequency', '1m'), ('data', 'provider', 'ft_daily_mmap'),
                                    ('data', 'adjustment', 'adjusted'), ('analysis', 'horizons', [1, 1])):
            with self.subTest(section=section, key=key):
                config = source_config()
                config[section][key] = value
                with self.assertRaisesRegex(ValueError, r'(?i)(line|行)'):
                    parse_source(full_script(config))


class ScriptExecutionTests(unittest.TestCase):
    def execute(self, source, **extra):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        request = dict(name='full script fixture', source_mode='script', source_code=source, **extra)
        (directory/'request.json').write_text(json.dumps(request))
        with patch('factortrader.Engine', side_effect=AssertionError('Trading Engine constructed')):
            run(directory)
        result = json.loads((directory/'result.json').read_text())
        values = [json.loads(line) for line in (directory/'values.jsonl').read_text().splitlines()]
        self.assertEqual((directory/'factor.py').read_text(), source)
        self.assertEqual(result['metadata']['source_sha256'], hashlib.sha256(source.encode()).hexdigest())
        return result, values

    def test_full_source_config_drives_real_factor_params_and_dates(self):
        self.assertTrue(issubclass(PythonIndicator, SharedIndicator))
        result, values = self.execute(full_script())
        self.assertEqual(result['metadata']['value_count'], 3)
        self.assertEqual(result['metadata']['warm_up_period'], 3)
        self.assertEqual(result['metadata']['report_horizons'], [1, 5])
        self.assertEqual(result['metadata']['provider'], 'fixture')
        self.assertEqual(result['metadata']['source_mode'], 'script')
        self.assertEqual(result['metadata']['source_config'], source_config())
        effective = result['metadata']['effective_config']
        self.assertEqual(effective['factor']['period'], 3)
        self.assertEqual(effective['base']['start_date'], '2024-01-04')
        self.assertEqual(effective['data']['provider'], 'fixture')
        self.assertEqual(effective['data']['factor_price'], 'quote_adjusted')
        self.assertEqual([row['date'] for row in values], ['2024-01-04']*3)
        self.assertAlmostEqual(values[0]['value'], (10+11+12.1)/3)
        one_day, one_day_values = self.execute(full_script(source_config(period=1)))
        self.assertEqual(one_day['metadata']['warm_up_period'], 1)
        self.assertAlmostEqual(one_day_values[0]['value'], 12.1)
        self.assertNotAlmostEqual(one_day_values[0]['value'], values[0]['value'])
        self.assertEqual(one_day['metadata']['return_policy']['version'], 'synthetic-price-ratio-v1')
        mixed = source_config()
        mixed['base'].update(start_date='20240104', end_date='2024-01-05')
        aligned, aligned_values = self.execute(full_script(mixed))
        self.assertEqual(aligned['metadata']['source_config'], mixed)
        self.assertEqual(aligned['metadata']['effective_config']['base']['start_date'], '2024-01-04')
        self.assertEqual([row['date'] for row in aligned_values], ['2024-01-04']*3+['2024-01-05']*3)
        self.assertAlmostEqual(aligned_values[0]['value'], (10+11+12.1)/3)

    def test_conflicting_external_config_is_rejected_instead_of_overriding_source(self):
        for extra in (dict(factor_params=dict(period=999)),
                      dict(data=dict(provider='fixture', universe='fixture', frequency='1d', adjustment='raw',
                                     factor_start='2024-01-03', factor_end='2024-01-03')),
                      dict(analysis=dict(groups=99))):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    self.execute(full_script(), **extra)

    def test_worker_import_and_native_factory_run_once_without_reentering_main(self):
        source = full_script(counters=True)
        with patch.object(builtins, 'FACTORPOOL_SCRIPT_IMPORTS', 0, create=True), \
                patch.object(builtins, 'FACTORPOOL_SCRIPT_INSTANCES', 0, create=True):
            self.execute(source)
            self.assertEqual(builtins.FACTORPOOL_SCRIPT_IMPORTS, 1)
            self.assertEqual(builtins.FACTORPOOL_SCRIPT_INSTANCES, 1)

    def test_standalone_class_run_reuses_the_loaded_class_without_reimport(self):
        source = full_script(counters=True)
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(builtins, 'FACTORPOOL_SCRIPT_IMPORTS', 0, create=True), \
                patch.object(builtins, 'FACTORPOOL_SCRIPT_INSTANCES', 0, create=True), \
                patch('factortrader.Engine', side_effect=AssertionError('Trading Engine constructed')):
            path = Path(temporary)/'standalone_factor.py'
            path.write_text(source)
            module_name = 'factorpool_standalone_test'
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                config_before = copy.deepcopy(module.config)
                result = module.MatrixFactor.run(module.config)
                self.assertEqual(module.config, config_before)
                self.assertEqual(result['metadata']['source_sha256'], hashlib.sha256(source.encode()).hexdigest())
                self.assertEqual(result['metadata']['source_mode'], 'script')
                self.assertEqual(result['metadata']['value_count'], 3)
                self.assertEqual(result['metadata']['source_config'], config_before)
                self.assertEqual(builtins.FACTORPOOL_SCRIPT_IMPORTS, 1)
                self.assertEqual(builtins.FACTORPOOL_SCRIPT_INSTANCES, 1)
            finally:
                sys.modules.pop(module_name, None)
