// BRAIN layout/interaction regression. All API responses are local test data;
// no production or service research task is created by this suite.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {writeFile,readFile} from 'node:fs/promises'
import {createHash} from 'node:crypto'
import {fixtureScript,setSource} from './browser-script-utils.mjs'
import {reportSection} from './browser-result-utils.mjs'
const base=process.env.FACTORPOOL_URL
if(!base?.trim())throw new Error('FACTORPOOL_URL must name an isolated frontend')
const output=process.env.FACTORPOOL_BROWSER_OUTPUT||'/tmp'
const {chromium}=createRequire(import.meta.url)(process.env.FACTORPOOL_PLAYWRIGHT_MODULE||'playwright')
const browser=await chromium.launch({headless:true,executablePath:process.env.FACTORPOOL_CHROMIUM_PATH,args:['--no-sandbox']})
const context=await browser.newContext({viewport:{width:1512,height:1050},reducedMotion:'reduce'})
const page=await context.newPage(),errors=[],requests=[],checks=[]
page.on('pageerror',e=>errors.push(e.message))
const dates=['2022-12-30','2023-01-03','2023-06-30','2023-07-03','2023-12-29','2024-01-02','2024-06-03','2025-01-02']
const trainDates=dates.slice(0,3),testDates=dates.slice(3,7)
const trainStats={sharpe:1.2,turnover:0,fitness:null,returns:.12,drawdown:0,margin:0}
const testStats={sharpe:2.5,turnover:1.0063,fitness:.9,returns:-.08,drawdown:.2,margin:.00059}
const allStats={sharpe:1.7,turnover:.8183,fitness:.78,returns:.2412,drawdown:.1577,margin:.00059}
const year=(year,start,end,days,metrics,long_count,short_count)=>({year,start,end,days,...metrics,long_count,short_count,pnl:.013})
const zero={sharpe:0,turnover:0,fitness:0,returns:0,drawdown:0,margin:0}
const annual=[year('2022',dates[0],dates[0],1,zero,0,0),
 year('2023',dates[1],dates[4],4,{...allStats,fitness:null},1234,2345),
 year('2024',dates[5],dates[6],2,testStats,4,5),year('2025',dates[7],dates[7],1,allStats,6,7)]
const trainAnnual=[annual[0],year('2023',dates[1],dates[2],2,trainStats,2,3)]
const testAnnual=[year('2023',dates[3],dates[4],2,testStats,3,4),annual[2]]
const pnl=[0,.02,-.01,.03,null,-.02,.04,.01],turnover=[0,.12,.15,1.0063,null,.3,.4,.2]
const portfolio=(stats,rows,indices)=>({brain_style:stats,yearly_stats:rows,
 pnl:{book_size:1,unit:'BOOK',cumulative:indices.map(i=>pnl[i])},
 nav:indices.map((_,i)=>1+i*.01),drawdown_series:indices.map(()=>0),constant_book_drawdown:indices.map(()=>0),
 returns:indices.map(()=>.01),turnover_series:indices.map(i=>turnover[i]),
 monthly:[{period:'2023-01',value:.02}],yearly:[{period:'2023',value:.02}]})
const h1={portfolio_dates:dates,portfolios:{alpha:portfolio(allStats,annual,dates.map((_,i)=>i))},
 partitions:{train:{dates:trainDates,alpha:portfolio(trainStats,trainAnnual,[0,1,2]),portfolios:{}},
 test:{dates:testDates,alpha:portfolio(testStats,testAnnual,[3,4,5,6]),portfolios:{}}}}
let result={summary:{ic_mean:.03,rank_ic_mean:.04,icir:.2,coverage:.95},
 metadata:{report_version:3,brain_settings:{visualization:true},synthetic:true,execution_mode:'native',value_count:0,snapshot_id:'isolated-layout-fixture',source_sha256:'layout-source-sha'},
 diagnostics:{version:3,test_period:{start:'2023-07-01',evaluation_start:'2022-01-01',evaluation_end:'2024-06-03'},
 conventions:{brain_settings:{visualization:true},missing_datasets:{self_correlation:'no selected alpha pool',production_correlation:'no BRAIN production pool access'}},
 unavailable:{self_correlation:null,production_correlation:null},horizons:{'1':h1,'5':structuredClone(h1),'20':structuredClone(h1)}}}
const task={id:'layout-mock',name:'布局交互测试数据',status:'SUCCEEDED',created_at:'2026-11-16T00:00:00Z'}
await page.addInitScript(()=>{sessionStorage.setItem('factorpool.task','layout-mock');localStorage.setItem('factorpool.layout.brain-simulation-v1','90')})
await page.route('**/api/**',async route=>{
 const r=route.request(),path=new URL(r.url()).pathname;requests.push({method:r.method(),path})
 assert.equal(r.method(),'GET',`Unexpected mutation in layout suite: ${r.method()} ${path}`)
 let body=[]
 if(path==='/api/tasks/layout-mock')body=task
 else if(path==='/api/tasks/layout-mock/result')body=reportSection(result)
 else if(path.startsWith('/api/tasks/layout-mock/result/'))body=reportSection(result,path.split('/').at(-1))
 else if(path==='/api/tasks/layout-mock/logs')body={text:'mock log'}
 await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)})
})
const nav=name=>page.getByRole('button',{name,exact:true})
const section=key=>page.locator(`[data-result-section=${key}]`)
const panel=page.getByRole('region',{name:'回测报告',exact:true})
const chart=page.locator('.simulation-chart-panel .report-chart')
const plotMode=page.getByLabel('结果曲线',{exact:true})
const periods=page.getByRole('group',{name:'汇总区间',exact:true})
const table=page.getByRole('table',{name:'年度统计表',exact:true})
const rangeText=()=>page.getByLabel('图表时间范围',{exact:true}).locator('[aria-live]').innerText()
const cards=()=>page.locator('.summary-measure strong').allTextContents()
const coreOrder=()=>panel.locator(':scope > .report-section').evaluateAll(nodes=>nodes.map(n=>n.dataset.resultSection))
async function eventually(test,message){for(let i=0;i<100;i++){if(await test())return;await page.waitForTimeout(50)}throw new Error(message)}
async function hoverIndex(index,length){await nav('Chart').click();await chart.scrollIntoViewIfNeeded();const box=await chart.boundingBox();await chart.hover({position:{x:60+index*(box.width-84)/(length-1),y:190}});await page.waitForTimeout(180);return chart.innerText()}
try{
 await page.goto(base,{waitUntil:'networkidle'});await chart.waitFor()
 assert.deepEqual(await coreOrder(),['chart','summary','correlation','testing','properties'])
 assert.equal(await section('chart').count(),1);assert.equal(await section('summary').count(),1)
 // The test-period banner precedes Chart; Summary and its period controls share one header.
 const testBanner=nav('Show test period')
 assert.ok((await testBanner.boundingBox()).y < (await section('chart').boundingBox()).y,
  'Test-period controls must appear above the chart')
 const summaryHeader=section('summary').locator('header.results-toolbar')
 assert.equal(await summaryHeader.getByRole('heading',{name:'IS Summary',exact:true}).count(),1)
 assert.equal(await summaryHeader.getByRole('group',{name:'汇总区间',exact:true}).count(),1)
 assert.equal(await plotMode.inputValue(),'pnl');assert.equal(await page.locator('.result-periods').count(),0)
 assert.equal(await nav('5D').count(),0);assert.equal(await nav('20D').count(),0)
 assert.equal(await plotMode.locator('optgroup[label=Performance] option').count(),3)
 assert.equal(await plotMode.locator('option[value=sharpe]').isDisabled(),true)
 assert.equal(await plotMode.locator('option[value=turnover]').isDisabled(),false)
 assert.equal(await periods.getByRole('button',{name:'TRAIN',exact:true}).getAttribute('aria-pressed'),'true')
 for(const name of ['TEST','IS','OS'])assert.equal(await periods.getByRole('button',{name,exact:true}).isDisabled(),true)
 assert.deepEqual(await cards(),['1.20','0.00%','—','12.00%','0.00%','0.00 bps'])
 assert.equal(await table.locator('tbody tr').count(),2)
 assert.deepEqual(await table.locator('tbody tr').first().locator('td').allTextContents(),['0.00','0.00%','0.00','0.00%','0.00%','0.00 bps','0','0'])
 assert.equal(await rangeText(),`${trainDates[0]} — ${trainDates.at(-1)}`)
 assert.match(await hoverIndex(0,trainDates.length),/0\.00 BOOK/)
 checks.push('continuous core report; 1D UI; real zero versus missing; nominal BOOK PnL')
 // Rail navigation only scrolls and focuses; it does not replace report nodes.
 await section('chart').evaluate(node=>{node.dataset.identityProbe='same-chart'})
 await nav('Summary').click();await eventually(async()=>await nav('Summary').getAttribute('aria-current')==='location','Summary scrollspy did not activate')
 assert.equal(await section('chart').getAttribute('data-identity-probe'),'same-chart')
 assert.equal(await section('chart').count(),1)
 await nav('Chart').focus();await page.keyboard.press('ArrowDown');assert.equal(await nav('Summary').evaluate(n=>n===document.activeElement),true)
 await page.keyboard.press('Enter');await eventually(async()=>await nav('Summary').getAttribute('aria-current')==='location','Keyboard navigation did not scroll Summary')
 await panel.evaluate(node=>{const target=node.querySelector('[data-result-section=testing]');node.scrollTop+=target.getBoundingClientRect().top-node.getBoundingClientRect().top-10})
 await eventually(async()=>await nav('Testing Status').getAttribute('aria-current')==='location','Manual scroll did not update current section')
 checks.push('rail scroll navigation, preserved DOM identity, keyboard focus and manual scrollspy')
 await nav('Summary').click();await nav('Show test period').click()
 assert.equal(await periods.getByRole('button',{name:'TEST',exact:true}).getAttribute('aria-pressed'),'true',
  'Showing the test period should select TEST')
 assert.equal(await section('summary').getByRole('heading',{name:'IS Summary',exact:true}).count(),1)
 assert.equal(await periods.getByRole('button',{name:'OS',exact:true}).isDisabled(),true)
 assert.equal(await table.locator('tbody tr').count(),4)
 assert.deepEqual(await table.locator('tbody tr').evaluateAll(rows=>rows.map(row=>row.dataset.period)),['train','mixed','test','unassigned'])
 assert.match(await table.locator('tbody tr').nth(1).innerText(),/TRAIN \/ TEST/)
 assert.match(await table.locator('tbody tr').nth(3).innerText(),/未分段/)
 const fullAnnual=await table.locator('tbody').innerText()
 assert.deepEqual(await cards(),['2.50','100.63%','0.90','-8.00%','20.00%','5.90 bps'])
 await nav('Chart').click();const fullRange=`${dates[0]} — ${dates.at(-1)}`;assert.equal(await rangeText(),fullRange)
 const fullChartIdentity=await section('chart').getAttribute('data-identity-probe')
 await nav('Summary').click();await periods.getByRole('button',{name:'IS',exact:true}).click()
 assert.equal(await section('summary').getByRole('heading',{name:'IS Summary',exact:true}).count(),1)
 assert.deepEqual(await cards(),['1.70','81.83%','0.78','24.12%','15.77%','5.90 bps'])
 await periods.getByRole('button',{name:'TRAIN',exact:true}).click();assert.equal((await cards())[0],'1.20')
 assert.equal(await table.locator('tbody').innerText(),fullAnnual)
 assert.equal(await section('chart').getAttribute('data-identity-probe'),fullChartIdentity)
 assert.equal(await rangeText(),fullRange,'Summary partition must not change the chart range')
 await nav('折叠年度统计').click();assert.equal(await table.isVisible(),false)
 await nav('展开年度统计').click();assert.equal(await table.isVisible(),true)
 await nav('Margin 指标说明').focus();await page.waitForTimeout(100)
 assert.match(await page.getByRole('tooltip').filter({hasText:'1 bp = 0.01%'}).innerText(),/10000/)
 checks.push('TRAIN/TEST/IS affect real summary only; full annual rows, mixed and outside dates, bps and collapse')
 // Actual ECharts tooltip values, plot selection, handles, accessible zoom and reset.
 await nav('Chart').click();await plotMode.selectOption('turnover')
 assert.equal(await rangeText(),fullRange)
 assert.match(await hoverIndex(3,dates.length),/Test Turnover: 100\.63%/)
 assert.match(await hoverIndex(7,dates.length),/IS Turnover: 20\.00%/)
 await nav('缩小时间范围').focus();await page.keyboard.press('Enter')
 await eventually(async()=>await rangeText()!==fullRange,'Keyboard zoom did not alter the actual displayed range')
 await nav('恢复完整区间').click();await eventually(async()=>await rangeText()===fullRange,'Full range was not restored')
 await nav('框选缩放').click();assert.equal(await nav('框选缩放').getAttribute('aria-pressed'),'true')
 await chart.scrollIntoViewIfNeeded();let box=await chart.boundingBox()
 await page.mouse.move(box.x+60+(box.width-84)*.15,box.y+130);await page.mouse.down();await page.mouse.move(box.x+60+(box.width-84)*.7,box.y+350,{steps:15});await page.mouse.up()
 await eventually(async()=>await rangeText()!==fullRange,'Plot drag did not select a smaller range')
 await nav('恢复完整区间').click();await eventually(async()=>await rangeText()===fullRange,'Reset after plot selection failed')
 box=await chart.boundingBox()
 await page.mouse.move(box.x+65,box.y+box.height-20);await page.mouse.down();await page.mouse.move(box.x+60+(box.width-84)*.3,box.y+box.height-20,{steps:12});await page.mouse.up()
 await eventually(async()=>await rangeText()!==fullRange,'Bottom range handle drag did not update the visible range')
 await plotMode.selectOption('pnl');await eventually(async()=>await rangeText()===fullRange,'Switching chart mode did not reset full range')
 assert.match(await hoverIndex(0,dates.length),/Train PnL: 0\.00 BOOK/)
 await page.screenshot({path:`${output}/factorpool-brain-chart.png`,fullPage:true})
 checks.push('real turnover and PnL tooltips, outside-tail label, plot drag, slider handle, keyboard zoom/reset; mode reset')
 await nav('Correlation').click()
 assert.deepEqual(await section('correlation').locator('.correlation-values strong').allTextContents(),['—','—','—'])
 await section('correlation').locator('details summary').click();assert.match(await section('correlation').innerText(),/no selected alpha pool/);assert.match(await section('correlation').innerText(),/no BRAIN production pool access/)
 await nav('Testing Status').click();await section('testing').locator('[data-check=report] summary').click()
 assert.match(await section('testing').innerText(),/报告格式：3/)
 assert.doesNotMatch(await section('testing').innerText(),/5 PASS|2 FAIL|Sharpe.*阈值/)
 await nav('Properties').click();assert.match(await section('properties').innerText(),/layout-mock/)
 assert.equal(await section('properties').getByRole('button',{name:'保存本次因子快照',exact:true}).isDisabled(),true)
 checks.push('unavailable correlation, expandable actual local checks and immutable run properties; no fake submission results')
 // Customization is real sorting with save, reload, reset, cancellation and drag.
 await nav('自定义结果菜单').click();let dialog=page.getByRole('dialog',{name:'Customize Results Menu',exact:true})
 await dialog.getByRole('button',{name:'上移 Summary',exact:true}).click()
 await dialog.getByRole('button',{name:'Apply',exact:true}).click()
 await eventually(async()=>(await coreOrder())[0]==='summary','Applied order did not reorder the report')
 await page.reload({waitUntil:'networkidle'});await chart.waitFor();assert.equal((await coreOrder())[0],'summary')
 await nav('自定义结果菜单').click();await dialog.getByRole('button',{name:'Reset',exact:true}).click();await page.keyboard.press('Escape')
 assert.equal(await dialog.isVisible(),false);assert.equal((await coreOrder())[0],'summary','Esc must cancel pending changes')
 await nav('自定义结果菜单').click();await dialog.getByRole('button',{name:'Reset',exact:true}).click()
 await dialog.locator('li').filter({hasText:'Properties'}).dragTo(dialog.locator('li').first())
 assert.match(await dialog.locator('li').first().innerText(),/^Properties/)
 await dialog.getByRole('button',{name:'Apply',exact:true}).click();assert.equal((await coreOrder())[0],'properties')
 await nav('自定义结果菜单').click();await dialog.getByRole('button',{name:'Reset',exact:true}).click();await dialog.getByRole('button',{name:'Apply',exact:true}).click()
 assert.deepEqual(await coreOrder(),['chart','summary','correlation','testing','properties'])
 await page.reload({waitUntil:'networkidle'});await chart.waitFor();assert.deepEqual(await coreOrder(),['chart','summary','correlation','testing','properties'])
 checks.push('menu up/down and drag sorting, Apply persistence/reload, Reset and Escape cancellation')
 // A supplied series is plotted without synthesis; absent/all-missing/misaligned remains unavailable.
 h1.portfolios.alpha.sharpe_series=[0,-1,null,2,3,4,5,6]
 h1.partitions.train.alpha.sharpe_series=[0,-1,null]
 await page.reload({waitUntil:'networkidle'});await chart.waitFor()
 assert.equal(await plotMode.locator('option[value=sharpe]').isDisabled(),false)
 await plotMode.selectOption('sharpe');assert.match(await hoverIndex(1,trainDates.length),/Train Sharpe: -1\.00/)
 h1.partitions.train.alpha.sharpe_series=[null,null,null]
 await page.reload({waitUntil:'networkidle'});await chart.waitFor();assert.equal(await plotMode.locator('option[value=sharpe]').isDisabled(),true)
 h1.partitions.train.alpha.sharpe_series=[1]
 await page.reload({waitUntil:'networkidle'});await chart.waitFor();assert.equal(await plotMode.locator('option[value=sharpe]').isDisabled(),true)
 delete h1.partitions.train.alpha.sharpe_series;delete h1.portfolios.alpha.sharpe_series
 await page.reload({waitUntil:'networkidle'});await chart.waitFor()
 await nav('Summary').click();await nav('Show test period').click();await periods.getByRole('button',{name:'IS',exact:true}).click()
 await page.screenshot({path:`${output}/factorpool-brain-stats.png`,fullPage:true})
 await nav('Hide test period').click();assert.equal(await table.locator('tbody tr').count(),2)
 assert.equal(await periods.getByRole('button',{name:'TEST',exact:true}).isDisabled(),true)
 assert.equal((await cards())[0],'1.20');assert.equal(await rangeText(),`${trainDates[0]} — ${trainDates.at(-1)}`)
 checks.push('supplied Sharpe series only; absent/all-null/misaligned unavailable; Hide resets training context')
 // Missing data and mobile/extreme split remain visible and honest.
 delete h1.partitions.train.alpha.yearly_stats
 h1.partitions.train.alpha.pnl.cumulative=[null,null,null]
 await page.reload({waitUntil:'networkidle'});await chart.waitFor()
 await page.getByText('暂无有效 PnL 数据',{exact:true}).waitFor()
 await nav('Summary').click();await page.getByText('当前报告没有可用的年度统计数据。',{exact:true}).waitFor()
 h1.partitions.train.alpha.yearly_stats=trainAnnual;h1.partitions.train.alpha.pnl.cumulative=[0,.02,-.01]
 await page.reload({waitUntil:'networkidle'});await chart.waitFor()
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>window.scrollTo(0,0))
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=392))
 assert.ok((await panel.boundingBox()).y<744);assert.ok((await chart.boundingBox()).height>=250)
 await page.screenshot({path:`${output}/factorpool-brain-chart-mobile.png`,fullPage:true})
 await nav('Summary').click();assert.ok(await page.locator('.annual-scroll').evaluate(n=>n.scrollWidth>n.clientWidth))
 await page.screenshot({path:`${output}/factorpool-brain-stats-mobile.png`,fullPage:true})
 // A separate context tests the market-snapshot save request without any market service access.
 const draftContext=await browser.newContext({viewport:{width:1512,height:1050},reducedMotion:'reduce'})
 const draftPage=await draftContext.newPage(),marketRequests=[],unexpected=[]
 draftPage.on('pageerror',error=>errors.push(error.message))
 const originalSnapshot='4193445409362c48c9b2f34bc1648877d4a96c412496275212de8f41b977ec71'
 const latestSnapshot='f'.repeat(64)
 const savedSource=fixtureScript({period:20,start:'2016-01-01',end:'2026-01-01'})
  .replace('"provider": "fixture"','"provider": "market_matrix"').replace('"universe": "fixture"','"universe": "all_a_dynamic"')
 const savedHash=createHash('sha256').update(savedSource).digest('hex')
 const originalWorkspace={base:{start_date:'2015-01-01'},data:{provider:'market_matrix',universe:'all_a_dynamic'},factor:{period:3},settings:{decay:0}}
 const pinnedTask={id:'market-save-mock',name:'已完成的行情快照',status:'SUCCEEDED',snapshot:{name:'已完成的行情快照',source_code:savedSource,workspace_config:originalWorkspace,data:{provider:'market_matrix',snapshot_id:originalSnapshot}}}
 const pinnedReport=structuredClone(result);pinnedReport.metadata.snapshot_id=originalSnapshot;pinnedReport.metadata.source_sha256=savedHash;pinnedReport.metadata.synthetic=false
 const catalog=JSON.parse(await readFile(new URL('../src/factorpool/settings_catalog.json',import.meta.url),'utf8'))
 let savedBody,savedRecord
 await draftPage.addInitScript(()=>sessionStorage.setItem('factorpool.task','market-save-mock'))
 await draftPage.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname;marketRequests.push({method:request.method(),path})
  let body=[],status=200
  if(path==='/api/examples')body=[{name:'low_volume.py',source_code:savedSource}]
  else if(path==='/api/operators')body=[]
  else if(path==='/api/data-sources')body=[{id:'market_matrix',name:'测试行情源',available:true,snapshot:{id:latestSnapshot,symbol_count:5413}}]
  else if(path==='/api/simulation-settings')body=catalog
  else if(path==='/api/tasks/market-save-mock')body=pinnedTask
  else if(path==='/api/tasks/market-save-mock/result')body=reportSection(pinnedReport)
  else if(path.startsWith('/api/tasks/market-save-mock/result/'))body=reportSection(pinnedReport,path.split('/').at(-1))
  else if(path==='/api/factors'&&request.method()==='GET')body=savedRecord?[savedRecord]:[]
  else if(path==='/api/factors'&&request.method()==='POST'){
   savedBody=request.postDataJSON();savedRecord={id:'11111111-1111-4111-8111-111111111111',version_id:'22222222-2222-4222-8222-222222222222',version:1,version_at:'2026-11-16T00:00:09Z',name:savedBody.name,source_hash:savedHash}
   body={id:savedRecord.id,version_id:savedRecord.version_id,version:1}
  }else{unexpected.push({method:request.method(),path});status=500;body={error:'Unexpected mocked request'}}
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)})
 })
 try{
  await draftPage.goto(base,{waitUntil:'networkidle'});await draftPage.locator('.simulation-chart-panel .report-chart').waitFor()
  const changedSource=savedSource+'\n# editor draft must not be used for result saving\n'
  await setSource(draftPage,changedSource);await draftPage.getByLabel('因子名称',{exact:true}).fill('新的未运行草稿')
  await draftPage.getByRole('button',{name:'设置',exact:true}).click()
  const settings=draftPage.getByRole('region',{name:'模拟设置',exact:true})
  await settings.getByLabel('开始日期',{exact:true}).fill('2020-01-01')
  await settings.getByLabel('构造参数 JSON',{exact:true}).fill('{"period":999}')
  await settings.getByRole('button',{name:'应用设置',exact:true}).click()
  await draftPage.getByRole('button',{name:'Properties',exact:true}).click()
  const propsPanel=draftPage.locator('[data-result-section=properties]')
  const reply=draftPage.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/factors')
  await propsPanel.getByRole('button',{name:'保存本次因子快照',exact:true}).click();assert.equal((await reply).status(),200)
  await draftPage.waitForFunction(()=>document.querySelector('[data-result-section=properties]')?.textContent.includes('11111111-1111-4111-8111-111111111111'))
  assert.equal(savedBody.name,pinnedTask.name);assert.equal(savedBody.source_code,savedSource)
  assert.deepEqual(savedBody.workspace_config,{...originalWorkspace,data:{...originalWorkspace.data,snapshot_id:originalSnapshot}})
  assert.notEqual(savedBody.workspace_config.data.snapshot_id,latestSnapshot)
  assert.equal(savedBody.workspace_config.factor.period,3)
  assert.equal(await draftPage.getByLabel('因子名称',{exact:true}).inputValue(),'新的未运行草稿')
  assert.equal(await propsPanel.getByRole('button',{name:'保存本次因子快照',exact:true}).isDisabled(),true)
  assert.equal(marketRequests.filter(r=>r.method==='POST'&&r.path==='/api/factors').length,1)
  assert.equal(marketRequests.filter(r=>r.path==='/api/tasks').length,0)
  assert.deepEqual(unexpected,[])
  await draftPage.screenshot({path:`${output}/properties-market-pinning-mock.png`,fullPage:true})
 }finally{await draftContext.close()}
 const marketPinning={status:'passed',scope:'Fully mocked client save request only; no real market save/backtest or API forwarding',originalSnapshot,latestSnapshotIgnored:latestSnapshot,sourceHash:savedHash,originalWorkspacePreserved:true,editorDraftPreserved:true,factorPosts:1,researchPosts:0,productionRequestsForwarded:0}
 checks.push('mocked market save pins original run snapshot and workspace, ignores edited draft and latest data source')
 assert.deepEqual(errors,[]);assert.ok(requests.every(r=>r.method==='GET'))
 const evidence={status:'passed',scope:'Local HTTP fixtures only; actual browser/ECharts interactions, no research or service mutations',checks,marketPinning,methods:[...new Set(requests.map(r=>r.method))],pageErrors:errors,productionTasksCreated:0}
 await writeFile(`${output}/brain-layout-interactions.json`,JSON.stringify(evidence,null,2)+'\n');console.log(JSON.stringify(evidence))
}catch(error){await page.screenshot({path:`${output}/brain-layout-failure.png`,fullPage:true}).catch(()=>{});throw error}finally{await browser.close()}
