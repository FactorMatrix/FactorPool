// Wire-level lazy report regression. Every request is fulfilled locally.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFile,writeFile} from 'node:fs/promises'
import {fixtureScript} from './browser-script-utils.mjs'
import {reportSection} from './browser-result-utils.mjs'
const base=process.env.FACTORPOOL_URL
if(!base?.trim())throw new Error('FACTORPOOL_URL must name an isolated frontend')
const output=process.env.FACTORPOOL_BROWSER_OUTPUT||'/tmp'
const {chromium}=createRequire(import.meta.url)(process.env.FACTORPOOL_PLAYWRIGHT_MODULE||'playwright')
const browser=await chromium.launch({headless:true,executablePath:process.env.FACTORPOOL_CHROMIUM_PATH,args:['--no-sandbox']})
const context=await browser.newContext({viewport:{width:1512,height:1050},reducedMotion:'reduce',acceptDownloads:true})
const page=await context.newPage(),requests=[],errors=[],counts=new Map()
const source=fixtureScript(),tasks=new Map(),checks=[]
const dates=['2024-01-02','2024-01-03','2024-01-04']
const metrics={sharpe:1.2,turnover:.1,fitness:.8,returns:.2,drawdown:.1,margin:.004}
const moments={n:3,mean:.1,std:.2,ir:.5,positive_ratio:.6,skewness:0,excess_kurtosis:0,t_iid:1,p_iid:.3,t_hac:1,p_hac:.3}
const portfolio={n:3,annual_return:.2,cagr:.2,annual_volatility:.1,sharpe:2,max_drawdown:.1,win_rate:.6,payoff_ratio:1.5,
  brain_style:metrics,pnl:{book_size:1,unit:'BOOK',cumulative:[0,.01,.02]},nav:[1,1.01,1.02],
  drawdown_series:[0,0,0],returns:[0,.01,.01],turnover_series:[0,.1,.1],
  monthly:[{period:'2024-01',value:.02}],yearly:[{period:'2024',value:.02}],
  yearly_stats:[{year:'2024',start:dates[0],end:dates[2],days:3,...metrics,long_count:1000,short_count:1000}]}
function report(id){return {metadata:{report_version:3,brain_settings:{visualization:true},synthetic:true,value_count:1,
  snapshot_id:`snapshot-${id}`,source_sha256:`hash-${id}`,source_marker:`source-${id}`},
  summary:{ic_mean:.1,rank_ic_mean:.1,icir:.5,coverage:1},large_export_only:'x'.repeat(400000),
  diagnostics:{version:3,test_period:{},conventions:{annual_days:252,brain_settings:{visualization:true},missing_datasets:{}},
    alpha_constraints:{daily:[]},horizons:{'1':{horizon:1,dates,portfolio_dates:dates,
      portfolios:{alpha:portfolio,high_low:portfolio},ic:moments,rank_ic:moments,
      series:{ic:[.1,.1,.1],rank_ic:[.1,.1,.1],ic_rolling30:[.1,.1,.1],rank_ic_rolling30:[.1,.1,.1],ic_cumulative:[.1,.2,.3],rank_ic_cumulative:[.1,.2,.3]},
      monthly:{ic:[{period:'2024-01',mean:.1}],rank_ic:[{period:'2024-01',mean:.1}]},yearly:{ic:[],rank_ic:[]},decay:[],
      pnl_book:{size:1,unit:`BOOK-${id}`},alpha_trace:[],trace_status:{not_rebalance_dates:[],no_future_dates:[]}}}}}}
function addTask(id,status='SUCCEEDED'){tasks.set(id,{id,status,name:id,snapshot:{name:id,source_code:source,workspace_config:{},data:{provider:'fixture'}}})}
addTask('load-A')
let submissions=0,holdSource=false,releaseSource,failExport=true
const heldSource=new Promise(resolve=>{releaseSource=resolve})
page.on('pageerror',e=>errors.push(e.message))
await page.addInitScript(()=>sessionStorage.setItem('factorpool.task','load-A'))
await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  requests.push({method:request.method(),path});counts.set(path,(counts.get(path)||0)+1)
  let body=[],status=200
  if(path==='/api/examples')body=[{name:'low_volume.py',source_code:source}]
  else if(path==='/api/tasks'&&request.method()==='POST'){
    const id=`load-${String.fromCharCode(66+submissions++)}`;addTask(id,id==='load-D'?'RUNNING':'SUCCEEDED');body={id}
  } else if(path.startsWith('/api/tasks/')){
    const [,id,resource,section]=path.slice(5).split('/'),task=tasks.get(id)
    if(!task){status=404;body={error:`Unexpected mock task ${id}`}}
    else if(!resource)body=task
    else if(resource==='logs')body={text:`logs-${id}-${task.status}`}
    else if(resource==='values')body=[{trading_date:dates[0],symbol:'MOCK',value:1,forward_return:.01,factor_group:1,reason:'ok'}]
    else if(resource==='result'){
      if(section==='ic'&&id==='load-A'&&counts.get(path)===1){status=503;body={error:'IC section temporarily unavailable'}}
      else if(section==='export'&&failExport){failExport=false;status=503;body={error:'Export temporarily unavailable'}}
      else {if(section==='source'&&id==='load-B'&&holdSource)await heldSource;body=reportSection(report(id),section||'core')}
    } else {status=404;body={error:`Unexpected resource ${path}`}}
  }
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)})
})
const nav=name=>page.getByRole('button',{name,exact:true})
const section=key=>page.locator(`[data-result-section=${key}]`)
const chart=()=>page.locator('.simulation-chart-panel .report-chart')
const count=(id,section)=>counts.get(`/api/tasks/${id}/${section}`)||0
async function until(check,message){for(let i=0;i<100;i++){if(await check())return;await page.waitForTimeout(50)}throw new Error(message)}
async function reopen(name,key){await section(key).locator(':scope > summary').click();await nav(name).click()}
async function newRun(){await nav('新建模拟').click();await nav('运行研究').click();await chart().waitFor()}
try{
  await page.goto(base,{waitUntil:'networkidle'});await chart().waitFor()
  const coreBytes=Buffer.byteLength(JSON.stringify(reportSection(report('load-A'))))
  assert.ok(coreBytes<=300000);assert.ok(Buffer.byteLength(JSON.stringify(report('load-A')))>400000)
  for(const name of ['Summary','Correlation','Testing Status','Properties','Chart'])await nav(name).click()
  assert.equal(requests.filter(r=>/\/result\//.test(r.path)||/\/(values|logs)$/.test(r.path)).length,0)
  assert.equal(counts.get('/api/tasks/load-A'),1,'Opening a completed task must fetch status once')
  assert.equal(counts.get('/api/factors'),1,'Report loading must not refresh the factor library')
  checks.push('core <=300k and Chart/Summary/Testing/Properties require no research payload; one task GET')
  await nav('IC Analysis').click();await page.getByRole('alert').filter({hasText:'IC section temporarily unavailable'}).waitFor()
  assert.equal(await chart().count(),1)
  await nav('重试 IC Analysis').click();await section('ic').locator('.ic-statistics').waitFor()
  await reopen('IC Analysis','ic');assert.equal(count('load-A','result/ic'),2)
  for(const [name,key,selector] of [['Groups','groups','.portfolio-statistics'],['Weights / PnL','trace','.alpha-trace'],['Source','source','.source-inspector']]){
    await nav(name).click();await section(key).locator(selector).waitFor();await reopen(name,key)
    assert.equal(count('load-A',`result/${key}`),1)
  }
  await nav('Factor Values').click();await page.getByRole('cell',{name:'MOCK',exact:true}).waitFor();await reopen('Factor Values','values')
  assert.equal(count('load-A','values'),1)
  await nav('Logs').click();await page.getByText('logs-load-A-SUCCEEDED',{exact:true}).waitFor();await reopen('Logs','logs')
  assert.equal(count('load-A','logs'),1,'Terminal logs must reuse the loaded immutable text')
  checks.push('IC retry is independent; IC/Groups/Trace/Source/Values/terminal Logs cache until task changes')
  await nav('Export Results').click();await page.getByRole('alert').filter({hasText:'Export temporarily unavailable'}).waitFor()
  for(let i=0;i<2;i++){
    const downloaded=page.waitForEvent('download');await nav(i===0?'Retry Export Results':'Export Results').click()
    const download=await downloaded,path=`${output}/report-loading-export-${i}.json`;await download.saveAs(path)
    const value=JSON.parse(await readFile(path,'utf8'));assert.equal(value.task.id,'load-A');assert.equal(value.result.large_export_only.length,400000)
  }
  assert.equal(count('load-A','result/export'),2,'Successful full export must be cached after one failed attempt')
  assert.equal(submissions,0,'Research reads/exports must not create another simulation')
  checks.push('explicit complete export, accessible failure/retry, complete data and successful cache')
  await newRun();holdSource=true;await nav('Source').click()
  await until(()=>count('load-B','result/source')===1,'Late section request did not start')
  await newRun();await nav('Source').click();await section('source').locator('.source-inspector').waitFor()
  await section('source').getByText('参数与复现信息',{exact:true}).click()
  assert.match(await section('source').innerText(),/source-load-C/)
  releaseSource();await page.waitForTimeout(250)
  assert.match(await section('source').innerText(),/source-load-C/);assert.doesNotMatch(await section('source').innerText(),/source-load-B/)
  checks.push('late section response from previous workspace cannot replace current section')
  await nav('新建模拟').click();await nav('运行研究').click();await page.locator('.badge.RUNNING').waitFor()
  await nav('Logs').click();await page.getByText('logs-load-D-RUNNING',{exact:true}).waitFor()
  await section('logs').locator(':scope > summary').click();const closedCount=count('load-D','logs')
  await page.waitForTimeout(1600);assert.equal(count('load-D','logs'),closedCount,'Closed Logs must stop polling')
  tasks.get('load-D').status='SUCCEEDED';await chart().waitFor();await nav('Logs').click()
  await page.getByText('logs-load-D-SUCCEEDED',{exact:true}).waitFor();const terminalCount=count('load-D','logs')
  await reopen('Logs','logs');assert.equal(count('load-D','logs'),terminalCount)
  checks.push('running Logs stop reads when closed; terminal Logs refresh once and then cache')
  assert.equal(counts.get('/api/factors'),1,'Task submit/completion must not refresh the factor library')
  assert.deepEqual(errors,[])
  await writeFile(`${output}/report-loading.json`,JSON.stringify({status:'passed',coreBytes,checks,requests,productionTasksCreated:0},null,2)+'\n')
  console.log(JSON.stringify({status:'passed',checks:checks.length,coreBytes,productionTasksCreated:0}))
}finally{releaseSource();await browser.close()}
