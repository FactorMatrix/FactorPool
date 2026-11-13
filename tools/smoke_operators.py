#!/usr/bin/env python3
"""Verify Pool operators and quote-only price windows against the deployed API."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base', default='http://127.0.0.1:8088')
parser.add_argument('--market', action='store_true', help='Also read the existing market snapshot; never imports data')
parser.add_argument('--output')
args = parser.parse_args()
http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, body=None):
    request = urllib.request.Request(args.base+'/api'+path,
        data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with http.open(request, timeout=30) as response:
        return json.load(response)


def wait(task):
    end = time.monotonic()+120
    while time.monotonic() < end:
        row = api('/tasks/'+task)
        if row['status'] == 'SUCCEEDED':
            return row
        assert row['status'] not in ('FAILED', 'INTERRUPTED', 'CANCELLED'), row
        time.sleep(.25)
    raise TimeoutError(task)


def source(config, body):
    return ('from factorpool import PythonIndicator\n\nconfig = '+repr(config)
        +'\n\nclass Factor(PythonIndicator):\n    def update(self,input):\n'+body
        +'\n\nif __name__ == "__main__":\n    Factor.run(config)\n')


def run(config, body, label):
    code = source(config, body)
    task = api('/tasks', dict(name=label, source_mode='script', source_code=code))['id']
    row = wait(task)
    report = api('/tasks/'+task+'/result')
    values = api('/tasks/'+task+'/values?limit=1000')
    assert row['snapshot']['source_code'] == code and row['snapshot']['source_config'] == config
    assert report['metadata']['source_sha256'] == hashlib.sha256(code.encode()).hexdigest()
    assert report['metadata']['effective_config'] == row['snapshot']['effective_config']
    assert report['metadata']['operator_contract'] == 'pool-indicator-2'
    assert report['metadata']['value_count'] == len(values)
    return dict(task=task, report=report, values=values)


def price_contract(result):
    contract = result['report']['metadata']['price_contract']
    assert result['report']['metadata']['factor_price'] == 'quote_adjusted'
    assert contract['mode'] == 'forward_adjusted'
    assert contract['price_basis'] == 'rebalance session close'
    assert contract['price'] == 'raw historical price * Q[signal] / Q[history]'
    assert contract['volume'] == 'raw historical volume * Q[history] / Q[signal]'
    assert contract['other_fields'] == 'unchanged'
    assert contract['returns'] == 'independent stored close returns R'
    return contract


def reject_raw(config, body):
    raw = dict(config, data=dict(config['data'], factor_price='raw'))
    try:
        api('/tasks', dict(name='验收 · 原始因子价明确拒绝', source_mode='script', source_code=source(raw, body)))
    except urllib.error.HTTPError as error:
        message = error.read().decode()
        assert error.code == 400 and ('quote_adjusted' in message or 'forward-adjusted' in message), message
        return dict(status=400, error=json.loads(message)['error'])
    raise AssertionError('Raw factor prices must be rejected before enqueue')


config = dict(base=dict(start_date='2024-01-04', end_date='2024-01-08'),
              data=dict(provider='fixture', universe='fixture'),
              analysis=dict(groups=3, horizons=[1], decay_steps=1))
extended = run(config, '''        safe = (self.sqrt(input.close) if input.close > 0 else self.log(-1))
        ranked = self.cs.rank_unit(safe)
        value = self.ts.mean(ranked, 2, clock='trading_days')
        self.value = value
        return self.ts.is_ready(value)''', '验收 · Pool原生算子与交易日窗口')
assert extended['report']['metadata']['indicator_backend'] == 'pool_native'
last = [row for row in extended['values'] if row['trading_date'] == '2024-01-08']
assert [row['value'] for row in last] == [0., .75, .75], last
assert all(row['ready'] and row['valid'] for row in last), last
price = dict(config, data=dict(provider='fixture', universe='fixture', factor_price='quote_adjusted'))
body = '''        value = self.ts.mean(input.close, 2)
        self.value = value
        return self.ts.is_ready(value)'''
quoted = run(price, body, '验收 · 基础报价复权窗口')
contract = price_contract(quoted)
assert contract['factor_evaluations'] == 3 and contract['native_input_frames'] == 5, contract
# Independent fixture quotes: bases 10/20/30, daily growth 1.1/1/.9.
# Adjacent prev_close equals yesterday close, so each Q bridge is exactly one.
indices = {'2024-01-04': 2, '2024-01-05': 3, '2024-01-08': 4}
parameters = {'A': (10, 1.1), 'B': (20, 1.), 'C': (30, .9)}
assert len(quoted['values']) == 9
for row in quoted['values']:
    index = indices[row['trading_date']]
    base, growth = parameters[row['symbol']]
    expected = base*(growth**(index-1)+growth**index)/2
    assert row['ready'] and row['valid'] and row['reason'] == 'ok', row
    assert math.isclose(row['value'], expected, rel_tol=1e-12, abs_tol=1e-12), (row, expected)
# More future returns and later signals must not change an earlier factor value
# or its state. The shorter task sees no input bars beyond its own signal end.
short = dict(price, base=dict(start_date='2024-01-04', end_date='2024-01-04'),
             analysis=dict(groups=3, horizons=[1, 20], decay_steps=1))
short_result = run(short, body, '验收 · 因子窗口隔离未来收益')
short_contract = price_contract(short_result)
assert short_contract['factor_evaluations'] == 1 and short_contract['native_input_frames'] == 3, short_contract
states = lambda rows: {row['symbol']: tuple(row[name] for name in ('value', 'ready', 'valid', 'reason')) for row in rows}
assert states(short_result['values']) == states([row for row in quoted['values'] if row['trading_date'] == '2024-01-04'])
raw_rejection = reject_raw(price, body)
market = None
if args.market:
    market_config = dict(base=dict(start_date='2023-05-08', end_date='2023-05-09'),
        data=dict(provider='market_matrix', universe=['000100.XSHE', '000001.XSHE', '600000.XSHG'], factor_price='quote_adjusted'),
        analysis=dict(groups=3, horizons=[1], decay_steps=1))
    market = run(market_config, body, '验收 · 真实基础行情推导价格倍率')
    assert market['report']['metadata']['synthetic'] is False
    price_contract(market)
    pinned = market['report']['metadata']['snapshot_id']
    assert pinned == market['report']['metadata']['effective_config']['data']['snapshot_id']
    reference_config = dict(market_config, data=dict(market_config['data'], snapshot_id=pinned))
    # On a valid adjacent pair, Q[t]/Q[t-1]=prev_close[t]/close[t-1].
    # Thus the adjusted two-day mean equals the same-day basic quote midpoint;
    # these references never request the removed raw factor mode.
    reference = run(reference_config, '''        self.value = (input.close + input.prev_close) / 2
        return True''', '验收 · 基础行情独立均价参考')
    expected = {(row['trading_date'], row['symbol']): row for row in reference['values']}
    actual = next(row for row in market['values'] if row['trading_date'] == '2023-05-08' and row['symbol'] == '000100.XSHE')
    counterpart = expected[(actual['trading_date'], actual['symbol'])]
    assert actual['ready'] and actual['valid'] and counterpart['ready'] and counterpart['valid']
    assert math.isclose(actual['value'], counterpart['value'], rel_tol=1e-12, abs_tol=1e-10)
    quote_config = dict(reference_config, base=dict(start_date='2023-05-05', end_date='2023-05-09'))
    closes = run(quote_config, '''        self.value = input.close
        return True''', '验收 · 基础行情相邻收盘独立参考')
    raw_closes = {(row['trading_date'], row['symbol']): row for row in closes['values']}
    before = raw_closes[('2023-05-05', '000100.XSHE')]
    current = raw_closes[('2023-05-08', '000100.XSHE')]
    assert before['ready'] and before['valid'] and current['ready'] and current['valid']
    bridge = (2*counterpart['value']-current['value'])/before['value']
    assert math.isfinite(bridge) and bridge > 0 and not math.isclose(bridge, 1., rel_tol=1e-12), bridge
    assert all(result['report']['metadata']['snapshot_id'] == pinned for result in (reference, closes))
    market['independent_reference_task'] = reference['task']
    market['independent_close_task'] = closes['task']
    market['independent_reference_value'] = counterpart['value']
    market['independent_q_bridge'] = bridge
summary = dict(operator_task=extended['task'], fixture_price_task=quoted['task'], future_isolation_task=short_result['task'],
               market_task=market['task'] if market else None, math='native if_else/sqrt/log + CS rank + calendar mean',
               factor_prices='adjacent basic quotes only; forward-adjusted only', raw_rejection=raw_rejection,
               source_and_effective_config='verified', scope='Pool research API; existing basic quote snapshots only')
if args.output:
    Path(args.output).write_text(json.dumps(dict(summary=summary, extended=extended, quoted=quoted,
        future_isolation=short_result, market=market), ensure_ascii=False, indent=2)+'\n')
print(json.dumps(summary, ensure_ascii=False))
