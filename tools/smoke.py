#!/usr/bin/env python3
"""Exercise deployed scripts, legacy factors, snapshots and cancellation on fixtures."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--base', default='http://127.0.0.1:8088')
parser.add_argument('--output')
args = parser.parse_args()


def api(path, body=None, status=200):
    request = urllib.request.Request(args.base+'/api'+path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            assert response.status == status, (path, response.status)
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code != status:
            raise
        return json.load(error)


def wait(task_id, target=('SUCCEEDED',), timeout=40):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        task = api('/tasks/'+task_id)
        if task['status'] in target:
            return task
        if task['status'] in ('FAILED', 'SUCCEEDED', 'CANCELLED', 'INTERRUPTED'):
            raise AssertionError((task_id, task))
        time.sleep(.25)
    raise TimeoutError(task_id)


def research_script(period):
    config = dict(base=dict(start_date='2024-01-04', end_date='2024-01-04', frequency='1d'),
                  data=dict(provider='fixture', universe='fixture'), factor=dict(period=period),
                  analysis=dict(groups=3, horizons=[1, 5], decay_steps=2))
    source = '''from factorpool import PythonIndicator

# 源码中的 config 是当前研究的配置。
config = '''+repr(config)+'''

class MatrixFactor(PythonIndicator):
    def __init__(self, period=3):
        super().__init__()
        self.period = period

    def update(self, input):
        mean_close = self.ts.mean(input.close, self.period)
        self.value = mean_close
        return self.ts.is_ready(mean_close)

if __name__ == "__main__":
    MatrixFactor.run(config)
'''
    return source, config


sources = api('/data-sources')
assert next(source for source in sources if source['id'] == 'fixture')['synthetic']
operators = api('/operators')
assert len(operators) == 66 and len({row['name'] for row in operators}) == 66
assert all(row['native'] and row['pool_wiring'] == 'connected' for row in operators)
catalog = api('/simulation-settings')
assert catalog['official_schema_complete'] is False and catalog['official_numerical_parity'] is False
capabilities = {entry['field']: entry for entry in catalog['fields']}
assert {'decay', 'neutralization', 'truncation', 'pasteurization', 'nanHandling', 'unitHandling',
        'testPeriod', 'maxTrade', 'maxPosition', 'visualization'}.issubset(capabilities)
assert all(entry['official_default'] is None for entry in catalog['fields'])
script_source, config = research_script(3)
resolved = api('/script/resolve', dict(source_code=script_source))
assert resolved['source_mode'] == 'script' and resolved['factor_class'] == 'MatrixFactor'
assert resolved['factor_params'] == dict(period=3) and resolved['source_config'] == config
for setting in ('nanHandling', 'unitHandling', 'maxTrade'):
    value = 'VERIFY' if setting == 'unitHandling' else 'ON'
    unsupported = script_source.replace(repr(config), repr(dict(config, settings={setting: value})))
    rejected = api('/script/resolve', dict(source_code=unsupported), status=400)
    assert setting in rejected['error']
invalid = api('/script/resolve', dict(source_code=script_source+'\ndef bad(:\n'), status=400)
assert 'line' in invalid['error'].lower() or '行' in invalid['error']
script_request = dict(name='验收 · 完整研究脚本', source_mode='script', source_code=script_source)
script_factor = api('/factors', script_request)
script_factor2 = api('/factors', dict(script_request, id=script_factor['id']))
assert script_factor2['version'] == script_factor['version']+1
api('/tasks', dict(script_request, factor_params=dict(period=999)), status=400)
script_id = api('/tasks', script_request)['id']
script_task = wait(script_id)
script_result = api('/tasks/'+script_id+'/result')
script_values = api('/tasks/'+script_id+'/values')
assert script_task['snapshot']['source_code'] == script_source
assert script_task['snapshot']['source_config'] == config
assert script_task['snapshot']['factor_params'] == dict(period=3)
assert script_result['metadata']['source_sha256'] == hashlib.sha256(script_source.encode()).hexdigest()
assert script_result['metadata']['source_mode'] == 'script'
assert script_result['metadata']['warm_up_period'] == 3
assert script_result['metadata']['report_horizons'] == [1, 5]
assert script_result['metadata']['effective_config']['factor']['period'] == 3
assert script_result['metadata']['effective_config']['base']['start_date'] == '2024-01-04'
assert abs(script_values[0]['value']-(10+11+12.1)/3) < 1e-12
one_source, one_config = research_script(1)
one_request = dict(name='验收 · 脚本参数1', source_mode='script', source_code=one_source)
one_factor = api('/factors', one_request)
one_id = api('/tasks', one_request)['id']
wait(one_id)
one_values = api('/tasks/'+one_id+'/values')
assert abs(one_values[0]['value']-12.1) < 1e-12
assert abs(one_values[0]['value']-script_values[0]['value']) > 1e-3
assert api('/tasks/'+script_id)['snapshot'] == script_task['snapshot']
script_batch = api('/tasks/batch', dict(factors=[script_factor['id'], one_factor['id']]))
for index, child in enumerate(script_batch['tasks']):
    child_task = wait(child)
    assert child_task['snapshot']['source_code'] == [script_source, one_source][index]
    assert child_task['snapshot']['factor_params'] == dict(period=[3, 1][index])
    assert child_task['snapshot']['source_config'] == [config, one_config][index]
api('/tasks/batch', dict(factors=[script_factor['id']], data=resolved['data'], analysis=resolved['analysis']), status=400)

# Class-only submissions use the same Pool frontend as complete scripts.
source = '''from factorpool import PythonIndicator
class LowVolumeFactor(PythonIndicator):
    def __init__(self, period=20):
        super().__init__()
        self.period=period
    def update(self,input):
        mean_volume=self.ts.mean(input.volume,self.period)
        self.value=self.cs.rank_unit(-mean_volume)
        return self.ts.is_ready(mean_volume)
'''
request = dict(name='验收 · 旧版矩阵研究', source_code=source, factor_class='LowVolumeFactor', factor_params={'period': 20},
               data=dict(provider='fixture', universe='fixture', frequency='1d', adjustment='raw',
                         factor_start='2024-01-29', factor_end='2024-01-29'),
               analysis=dict(groups=3, min_ic_pairs=3, icir_ddof=1))
saved = api('/factors', {key: value for key, value in request.items() if key not in ('data', 'analysis')})
saved2 = api('/factors', dict(request, id=saved['id']))
assert saved2['version'] == saved['version']+1
assert len(api('/factors/'+saved['id'])['versions']) == 2
task_id = api('/tasks', request)['id']
wait(task_id)
result = api('/tasks/'+task_id+'/result')
rows = api('/tasks/'+task_id+'/values?offset=0&limit=2')
assert len(rows) == 2 and rows[0]['symbol'] == 'A'
assert result['metadata']['report_version'] == 3 and result['diagnostics']['version'] == 3
assert result['summary']['ic_mean'] == 1 and result['summary']['rank_ic_mean'] == 1 and result['summary']['icir'] is None
assert all(abs(actual-expected) < 1e-12 for actual, expected in zip(result['daily'][0]['group_returns'], [-.1, 0, .1]))
assert api('/tasks/'+task_id)['snapshot']['source_code'] == source
for horizon in result['diagnostics']['horizons'].values():
    for portfolio in horizon['portfolios'].values():
        assert len(horizon['portfolio_dates']) == len(portfolio['returns']) == len(portfolio['nav'])
bad = dict(request, source_code=source.replace('self.cs.rank_unit(-mean_volume)', 'self.cs.unknown(-mean_volume)'))
api('/tasks', bad, status=400)
missing_data = dict(request, source_code=source.replace('self.cs.rank_unit(-mean_volume)', 'self.ts.mean(input.unavailable_market_field,20)'))
failed = api('/tasks', missing_data)['id']
wait(failed, ('FAILED',))
api('/tasks/'+failed+'/result', status=409)
# An ordinary Python callback makes queueing/cancellation observable.
slow = dict(request, name='验收 · 停止运行进程', factor_class='Slow', factor_params={}, source_code='''import time
from factorpool import PythonIndicator
class Slow(PythonIndicator):
    def update(self,input):
        time.sleep(60)
        self.value=input.close
        return True
''')
running = api('/tasks', slow)['id']
wait(running, ('RUNNING',))
queued = api('/tasks', request)['id']
assert api('/tasks/'+queued)['status'] == 'QUEUED'
api('/tasks/'+queued+'/cancel', {})
wait(queued, ('CANCELLED',))
api('/tasks/'+running+'/cancel', {})
wait(running, ('CANCELLED',))
batch = api('/tasks/batch', dict(factors=[saved['id'], saved['id']], data=request['data'], analysis=request['analysis']))
for child in batch['tasks']:
    wait(child)
assert all(task['status'] == 'SUCCEEDED' for task in api('/batches/'+batch['id'])['tasks'])
api('/tasks/batch', dict(factors=[saved['id'], script_factor['id']], data=request['data'], analysis=request['analysis']), status=400)
for field, value in (('frequency', '1m'), ('adjustment', 'adjusted')):
    api('/tasks', dict(request, data=dict(request['data'], **{field: value})), status=400)
report = dict(task=task_id, factor=saved['id'], batch=batch['id'], failed=failed, cancelled=[running, queued],
              script_tasks=[script_id, one_id], script_batch=script_batch['id'], script_summary=script_result['summary'],
              summary=result['summary'], result=result)
if args.output:
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({key: value for key, value in report.items() if key != 'result'}, ensure_ascii=False))
