#!/usr/bin/env python3
"""Exercise the deployed HTTP/database contract with explicitly synthetic inputs."""
import argparse
from pathlib import Path
import json
import time
import urllib.request
import urllib.error
p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8088');p.add_argument('--output');a=p.parse_args()
root=Path(__file__).resolve().parents[1]

def api(path,body=None,status=200):
    request=urllib.request.Request(a.base+'/api'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:
            assert response.status==status,(path,response.status)
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code!=status:raise
        return json.load(error)

def wait(id,target=('SUCCEEDED',),timeout=40):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        task=api('/tasks/'+id)
        if task['status'] in target:return task
        if task['status'] in ('FAILED','SUCCEEDED','CANCELLED','INTERRUPTED'):
            raise AssertionError((id,task))
        time.sleep(.25)
    raise TimeoutError(id)

source=(root/'examples/factors/low_volume.py').read_text()
request=dict(name='验收 · 矩阵研究',source_code=source,factor_class='LowVolumeFactor',factor_params={'period':20},
             data=dict(provider='fixture',universe='fixture',frequency='1d',adjustment='raw',factor_start='2024-01-29',factor_end='2024-01-29'),analysis=dict(groups=3,min_ic_pairs=3,icir_ddof=1))
assert api('/data-sources')[0]['synthetic']
assert len(api('/operators'))==179
saved=api('/factors',{k:v for k,v in request.items() if k not in ('data','analysis')})
saved2=api('/factors',dict(request,id=saved['id']))
assert saved2['version']==saved['version']+1
assert len(api('/factors/'+saved['id'])['versions'])==2
id=api('/tasks',request)['id'];wait(id)
result=api('/tasks/'+id+'/result');rows=api('/tasks/'+id+'/values?offset=0&limit=2')
assert len(rows)==2 and rows[0]['symbol']=='A'
assert result['summary']['ic_mean']==1 and result['summary']['rank_ic_mean']==1 and result['summary']['icir'] is None
assert all(abs(a-b)<1e-12 for a,b in zip(result['daily'][0]['group_returns'],[-.1,0,.1]))
assert api('/tasks/'+id)['snapshot']['source_code']==source
bad=dict(request,source_code=source.replace('self.cs.rank_unit(-mean_volume)','self.cs.unknown(-mean_volume)'))
failed=api('/tasks',bad)['id'];wait(failed,('FAILED',));api('/tasks/'+failed+'/result',status=409)
# Ordinary scalar Python is deliberately slow, making queue/stop observable.
slow=dict(request,name='验收 · 停止运行进程',factor_class='Slow',factor_params={},source_code='''import time
from factortrader import PythonIndicator
class Slow(PythonIndicator):
    def update(self,input):
        time.sleep(60)
        self.value=input.close
        return True
''')
running=api('/tasks',slow)['id'];wait(running,('RUNNING',))
queued=api('/tasks',request)['id'];assert api('/tasks/'+queued)['status']=='QUEUED'
api('/tasks/'+queued+'/cancel',{});wait(queued,('CANCELLED',))
api('/tasks/'+running+'/cancel',{});wait(running,('CANCELLED',))
batch=api('/tasks/batch',dict(factors=[saved['id'],saved['id']],data=request['data'],analysis=request['analysis']))
for child in batch['tasks']:wait(child)
assert all(t['status']=='SUCCEEDED' for t in api('/batches/'+batch['id'])['tasks'])
for field,value in (('frequency','1m'),('adjustment','adjusted')):
    invalid=dict(request,data=dict(request['data'],**{field:value}));api('/tasks',invalid,status=400)
report=dict(task=id,factor=saved['id'],batch=batch['id'],failed=failed,cancelled=[running,queued],summary=result['summary'],result=result)
if a.output:Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='result'},ensure_ascii=False))
