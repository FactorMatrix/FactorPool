import {createRequire} from 'node:module'
import assert from 'node:assert/strict'
import {fixtureScript, setSource} from './browser-script-utils.mjs'
const require=createRequire(import.meta.url)
const {chromium}=require(process.env.FACTORPOOL_PLAYWRIGHT_MODULE||'playwright')
const browser=await chromium.launch({headless:true})
const page=await browser.newPage({viewport:{width:1512,height:1050}})
const base=process.env.FACTORPOOL_URL||'http://127.0.0.1:8088'
const output=process.env.FACTORPOOL_BROWSER_OUTPUT||'/tmp'
const errors=[];page.on('pageerror',error=>errors.push(error.message))
try {
 await page.goto(base,{waitUntil:'networkidle'})
 const source=fixtureScript({period:3,start:'2024-01-04',end:'2024-01-08',horizons:[1,5],meanClose:true,
  settings:{truncation:.08,visualization:false,testPeriod:'P2D'},
  analysis:{book_size:20000000,pnl_unit:'USD',trace_dates:['2024-01-04','2024-01-05']}})
 await setSource(page,source)
 await page.getByRole('button',{name:'运行研究',exact:true}).click()
 await page.locator('.badge.SUCCEEDED').waitFor({timeout:40000})
 const task=await page.evaluate(()=>sessionStorage.getItem('factorpool.task'))
 const result=await (await page.request.get(`${base}/api/tasks/${task}/result`)).json()
 assert.equal(result.metadata.effective_config.analysis.book_size,20000000)
 assert.equal(result.metadata.effective_config.analysis.pnl_unit,'USD')
 const horizon=result.diagnostics.horizons['1']
 assert.equal(horizon.alpha_trace.length,2)
 for(const trace of horizon.alpha_trace) {
  assert.ok(trace.valuation_date>trace.signal_date)
  assert.ok(Math.abs(trace.rows.reduce((sum,row)=>sum+row.pnl,0)-trace.daily_pnl)<1e-7)
  assert.ok(trace.rows.every(row=>Math.abs(row.target_weight)<=.080000001))
 }
 assert.equal(result.diagnostics.alpha_constraints.daily[0].capacity,.24)
 assert.equal(result.diagnostics.alpha_constraints.daily[0].full_gross_feasible,false)
 await page.getByRole('button',{name:'Weights / PnL',exact:true}).click()
 const region=page.getByRole('region',{name:'权重与PnL追踪',exact:true})
 await region.waitFor()
 assert.equal(await region.getByLabel('追踪信号日期',{exact:true}).inputValue(),'2024-01-04')
 assert.equal(await region.getByLabel('追踪收益日期',{exact:true}).inputValue(),'2024-01-05')
 const table=region.getByRole('table',{name:'逐股权重与PnL',exact:true})
 assert.equal(await table.locator('tbody tr').count(),3)
 assert.match(await region.innerText(),/20,000,000.00 USD/)
 assert.match(await region.innerText(),/总容量不足/)
 assert.match(await table.innerText(),/ready/)
 assert.equal(await page.getByLabel('验证区间',{exact:true}).count(),0)
 assert.equal(await page.locator('.report-chart').count(),0)
 await region.getByLabel('追踪证券筛选',{exact:true}).fill('A')
 assert.equal(await table.locator('tbody tr').count(),1)
 await region.getByLabel('追踪证券筛选',{exact:true}).fill('')
 await region.getByLabel('追踪信号日期',{exact:true}).selectOption('2024-01-05')
 assert.equal(await region.getByLabel('追踪收益日期',{exact:true}).inputValue(),'2024-01-08')
 await page.getByRole('button',{name:'5D',exact:true}).click()
 await region.getByLabel('追踪信号日期',{exact:true}).selectOption('2024-01-04')
 assert.match(await region.innerText(),/2024-01-05/)
 assert.match(await region.innerText(),/不调仓/)
 await region.getByLabel('追踪收益日期',{exact:true}).selectOption('2024-01-08')
 const actual=result.diagnostics.horizons['5'].alpha_trace.find(row=>row.valuation_date==='2024-01-08')
 assert.ok(actual.rows.some(row=>Math.abs(row.target_weight-row.effective_weight_before_return)>1e-10))
 await page.screenshot({path:`${output}/alpha-trace-desktop.png`,fullPage:true})
 await page.setViewportSize({width:390,height:844})
 await page.waitForTimeout(350)
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=392),'Trace caused mobile body overflow')
 await page.screenshot({path:`${output}/alpha-trace-mobile.png`,fullPage:true})
 assert.deepEqual(errors,[])
 console.log(JSON.stringify({task,trace:'signal/valuation dates + actual native weights/contributions',amounts:'20m USD nominal only',capacity:'3*.08 cannot fill 100%',missing:'explicit status',visualization:'false retains trace table',mobile:'passed',browser_errors:errors}))
} finally {await browser.close()}
