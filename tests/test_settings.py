"""Local BRAIN-style controls must be explicit, effective or rejected."""
from copy import deepcopy
import unittest

from factorpool.config import effective_config
from factorpool.script import bind_request, parse_source
from factorpool.settings import normalize_settings


DATA = dict(provider='fixture', universe='fixture')


def settings_script(settings=None):
    config = dict(base=dict(start_date='2024-01-04', end_date='2024-01-04'),
                  data=deepcopy(DATA), factor=dict(period=3))
    if settings is not None:
        config['settings'] = deepcopy(settings)
    source = ('from factorpool import PythonIndicator\n\nconfig = '+repr(config)+'''

class Factor(PythonIndicator):
    pass

if __name__ == "__main__":
    Factor.run(config)
''')
    return source, config


class SettingsTests(unittest.TestCase):
    def test_local_defaults_are_complete_and_universe_does_not_alias_input(self):
        data = dict(provider='market_matrix', universe=['A', 'B'])
        supplied = {}
        settings = normalize_settings(supplied, data)
        self.assertEqual(settings, dict(instrumentType='EQUITY', region='CHN', universe=['A', 'B'],
                                        language='PYTHONINDICATOR', delay=1, decay=0, truncation=0,
                                        neutralization='MARKET', pasteurization='ON', unitHandling='OFF',
                                        nanHandling='OFF', testPeriod='P0Y0M', visualization=True,
                                        maxTrade='OFF', maxPosition='OFF', simulationMode='FULL'))
        self.assertEqual(supplied, {})
        settings['universe'].append('C')
        self.assertEqual(data['universe'], ['A', 'B'])

    def test_supported_controls_are_preserved_without_mutating_source_settings(self):
        supplied = dict(decay=3, truncation=.12, neutralization='NONE', pasteurization='OFF',
                        testPeriod='P6M', visualization=False)
        before = deepcopy(supplied)
        normalized = normalize_settings(supplied, DATA)
        for key, value in supplied.items():
            self.assertEqual(normalized[key], value)
        self.assertEqual(supplied, before)
        for duration in ('P0Y0M', 'P1Y6M', 'P30D', 'P2W'):
            self.assertEqual(normalize_settings(dict(testPeriod=duration), DATA)['testPeriod'], duration)

    def test_unavailable_options_fail_with_the_setting_name(self):
        unavailable = [('instrumentType', 'FUTURE'), ('region', 'USA'), ('universe', 'TOP3000'),
                       ('language', 'FASTEXPR'), ('delay', 0), ('neutralization', 'SECTOR'),
                       ('neutralization', 'INDUSTRY'), ('neutralization', 'SUBINDUSTRY'),
                       ('unitHandling', 'VERIFY'), ('nanHandling', 'ON'), ('maxTrade', 'ON'),
                       ('maxPosition', 'ON'), ('simulationMode', 'QUICK')]
        for key, value in unavailable:
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, key):
                    normalize_settings({key: value}, DATA)
        with self.assertRaisesRegex(ValueError, 'universe'):
            normalize_settings({}, dict(provider='market_matrix', universe='TOP3000'))

    def test_invalid_values_do_not_turn_into_silent_defaults(self):
        invalid = [('delay', True), ('delay', 2), ('decay', True), ('decay', -1),
                   ('decay', 513), ('decay', 1.5), ('truncation', True), ('truncation', -.1),
                   ('truncation', 1.1), ('truncation', float('nan')), ('truncation', float('inf')),
                   ('pasteurization', True), ('pasteurization', 'MAYBE'), ('visualization', 'false'),
                   ('testPeriod', 'P'), ('testPeriod', 'P-1Y'), ('testPeriod', 'P0.5Y'),
                   ('testPeriod', 'PT1H'), ('testPeriod', 1)]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    normalize_settings({key: value}, DATA)
        for supplied in (None, [], {'notASetting': 0}):
            with self.subTest(supplied=supplied):
                with self.assertRaises(ValueError):
                    normalize_settings(supplied, DATA)

    def test_recognized_super_and_ml_fields_are_explicitly_unsupported(self):
        for key in ('lookback', 'selectionHandling', 'selectionLimit', 'componentActivation',
                    'universeOverride', 'model', 'lossFunction', 'epochCount', 'earlyStop',
                    'trainingPeriod', 'randomSeed'):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    normalize_settings({key: 0}, DATA)

    def test_source_settings_are_immutable_and_effective_settings_include_defaults(self):
        source, config = settings_script(dict(neutralization='NONE', truncation=.2))
        parsed = parse_source(source)
        self.assertEqual(parsed['source_config'], config)
        self.assertEqual(parsed['source_config']['settings'], dict(neutralization='NONE', truncation=.2))
        self.assertEqual(parsed['brain_settings']['decay'], 0)
        self.assertEqual(parsed['brain_settings']['neutralization'], 'NONE')
        self.assertEqual(parsed['brain_settings']['truncation'], .2)
        effective = effective_config(parsed)
        self.assertEqual(effective['settings'], parsed['brain_settings'])
        effective['settings']['truncation'] = .9
        self.assertEqual(parsed['brain_settings']['truncation'], .2)
        self.assertEqual(parsed['source_config'], config)
        request = dict(name='settings fixture', source_code=source,
                       brain_settings=dict(parsed['brain_settings'], truncation=.9))
        with self.assertRaisesRegex(ValueError, 'brain_settings'):
            bind_request(request)

    def test_omitted_settings_do_not_get_inserted_into_original_config(self):
        source, config = settings_script()
        parsed = parse_source(source)
        self.assertNotIn('settings', parsed['source_config'])
        self.assertEqual(parsed['source_config'], config)
        self.assertEqual(parsed['brain_settings'], normalize_settings({}, parsed['data']))
        bad_source, _ = settings_script(dict(nanHandling='ON'))
        with self.assertRaisesRegex(ValueError, 'line .*nanHandling'):
            parse_source(bad_source)
