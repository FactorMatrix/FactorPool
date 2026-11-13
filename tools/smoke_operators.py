#!/usr/bin/env python3
"""Verify Pool operators and quote-only price windows against the deployed API."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base',default='http://127.0.0.1:8088')
parser.add_argument('--market',action='store_true',help='Also read the existing market snapshot; never imports data')
parser.add_argument('--output')
args=parser.parse_args()
http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(path,body=None):
 request=urllib.request.Request(args.base+'/api'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with http.open(request,timeout=30) as response:return json.load(response)
def wait(task):
 end=time.monotonic()+120
 while time.monotonic()<end:
  row=api('/tasks/'+task)
  if row['status']=='SUCCEEDED':return row
  assert row['status'] not in ('FAILED','INTERRUPTED','CANCELLED'), row
  time.sleep(.25)
 raise TimeoutError(task)
def source(config,body):
 return 'from factorpool import PythonIndicator\n\nconfig = '+repr(config)+'\n\nclass Factor(PythonIndicator):\n    def update(self,input):\n'+body+'\n\nif __name__ == "__main__":\n    Factor.run(config)\n'
def run(config,body,label):
 code=source(config,body)
 task=api('/tasks',dict(name=label,source_mode='script',source_code=code))['id']
 row=wait(task); report=api('/tasks/'+task+'/result');values=api('/tasks/'+task+'/values?limit=1000')
 assert row['snapshot']['source_code']==code and row['snapshot']['source_config']==config
 assert report['metadata']['source_sha256']==hashlib.sha256(code.encode()).hexdigest()
 assert report['metadata']['effective_config']==row['snapshot']['effective_config']
 assert report['metadata']['operator_contract']=='pool-indicator-2'
 return dict(task=task,report=report,values=values)
config=dict(base=dict(start_date='2024-01-04',end_date='2024-01-08'),
            data=dict(provider='fixture',universe='fixture'),analysis=dict(groups=3,horizons=[1],decay_steps=1))
extended=run(config,'''        safe = (self.sqrt(input.close) if input.close > 0 else self.log(-1))
        ranked = self.cs.rank_unit(safe)
        value = self.ts.mean(ranked, 2, clock='trading_days')
        self.value = value
        return self.ts.is_ready(value)''','验收 · Pool原生算子与交易日窗口')
assert extended['report']['metadata']['indicator_backend']=='pool_native'
last=[row['value'] for row in extended['values'] if row['trading_date']=='2024-01-08']
assert last==[0.,.75,.75], last
price=dict(config,data=dict(provider='fixture',universe='fixture',factor_price='quote_adjusted'))
body='''        value = self.ts.mean(input.close, 2)
        self.value = value
        return self.ts.is_ready(value)'''
quoted=run(price,body,'验收 · 基础报价复权窗口')
contract=quoted['report']['metadata']['price_contract']
assert contract['mode']=='quote_adjusted' and contract['replay_count']==0
assert contract['execution_mode']=='homogeneous_scalar_incremental'
assert abs(quoted['values'][0]['value']-(11+12.1)/2)<1e-12
market=None
if args.market:
 market_config=dict(base=dict(start_date='2023-05-08',end_date='2023-05-09'),
                    data=dict(provider='market_matrix',universe=['000100.XSHE','000001.XSHE','600000.XSHG'],factor_price='quote_adjusted'),
                    analysis=dict(groups=3,horizons=[1],decay_steps=1))
 market=run(market_config,body,'验收 · 真实基础行情推导价格倍率')
 assert market['report']['metadata']['synthetic'] is False
 assert market['report']['metadata']['price_contract']['non_unit_bridge_count']>0
 assert market['report']['metadata']['price_contract']['replay_count']==0
 assert market['report']['metadata']['snapshot_id']==market['report']['metadata']['effective_config']['data']['snapshot_id']
 reference_config=dict(market_config,data=dict(market_config['data'],factor_price='raw'))
 reference=run(reference_config,'''        self.value = (input.close + input.prev_close) / 2
        return True''','验收 · 基础行情独立均价参考')
 expected={ (row['trading_date'],row['symbol']):row['value'] for row in reference['values'] }
 actual=next(row for row in market['values'] if row['trading_date']=='2023-05-08' and row['symbol']=='000100.XSHE')
 assert actual['valid'] and abs(actual['value']-expected[(actual['trading_date'],actual['symbol'])])<1e-10
 assert reference['report']['metadata']['snapshot_id']==market['report']['metadata']['snapshot_id']
 market['independent_reference_task']=reference['task']
 market['independent_reference_value']=expected[(actual['trading_date'],actual['symbol'])]
summary=dict(operator_task=extended['task'],fixture_price_task=quoted['task'],market_task=market['task'] if market else None,
             math='native if_else/sqrt/log + CS rank + calendar mean',factor_prices='adjacent basic quotes only',
             source_and_effective_config='verified',scope='Pool research API; existing basic quote snapshots only')
if args.output:Path(args.output).write_text(json.dumps(dict(summary=summary,extended=extended,quoted=quoted,market=market),ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False))
