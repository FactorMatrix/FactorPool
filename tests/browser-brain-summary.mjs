// Offline UI regression: API fixtures are local; no research task is created.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
const base = process.env.FACTORPOOL_URL
if (!base?.trim()) throw new Error('FACTORPOOL_URL must name an isolated frontend')
const require = createRequire(import.meta.url)
const {chromium} = require(process.env.FACTORPOOL_PLAYWRIGHT_MODULE || 'playwright')
const output = process.env.FACTORPOOL_BROWSER_OUTPUT || '/tmp'
const browser = await chromium.launch({headless:true, executablePath:process.env.FACTORPOOL_CHROMIUM_PATH, args:['--no-sandbox']})
const page = await browser.newPage({viewport:{width:1512,height:1050}})
const errors = [], requests = []
page.on('pageerror', error => errors.push(error.message))
page.on('request', request => { if (request.url().includes('/api/')) requests.push({method:request.method(),path:new URL(request.url()).pathname}) })

const brain = {sharpe:1.2, turnover:.12, fitness:1.1, returns:.18, drawdown:.07, margin:.0004}
const year = (year, pnl, long_count, short_count) => ({year, start:`${year}-01-01`, end:`${year}-12-31`,
  ...brain, pnl, long_count, short_count})
const alpha = (yearly_stats, length=2) => ({brain_style:brain, yearly_stats,
  pnl:{book_size:1, unit:'BOOK', cumulative:[.01,.03].slice(0,length)},
  nav:[1.01,1.03].slice(0,length), drawdown_series:[0,-.01].slice(0,length),
  constant_book_drawdown:[0,-.02].slice(0,length), returns:[.01,.02].slice(0,length),
  turnover_series:[.12,.15].slice(0,length)})
const horizon = (full, train, test) => ({portfolio_dates:['2023-12-29','2024-01-02'],
  portfolios:{alpha:alpha(full)}, partitions:{
    train:{dates:['2023-12-29'], portfolios:{}, alpha:alpha(train,1)},
    test:{dates:['2024-01-02'], portfolios:{}, alpha:alpha(test,1)},
  }})
const full = [year('2023',.013,2,3), year('2024',.027,4,5)]
let result = {summary:{ic_mean:.03,rank_ic_mean:.04,icir:.2,coverage:.95},
  metadata:{report_version:3, brain_settings:{visualization:true}, synthetic:true, execution_mode:'native', value_count:0},
  diagnostics:{version:3, test_period:{start:'2024-01-01'}, conventions:{brain_settings:{visualization:true}},
    horizons:{'1':horizon(full,[full[0]],[full[1]]),
      '5':horizon([year('2024',.05,7,8)],[],[year('2024',.05,7,8)]),
      '20':horizon([year('2024',.20,9,10)],[],[year('2024',.20,9,10)])}}}
result.diagnostics.horizons['1'].partitions.test.alpha.brain_style={...brain, sharpe:2.5}
result.diagnostics.horizons['1'].portfolios.alpha.brain_style={...brain, sharpe:1.7}
const task = {id:'mock',name:'示例因子',status:'SUCCEEDED'}
await page.addInitScript(() => {
  sessionStorage.setItem('factorpool.task','mock')
  localStorage.setItem('factorpool.layout.brain-simulation-v1','90')
})
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname
  let body = []
  if (path === '/api/tasks/mock') body = task
  else if (path === '/api/tasks/mock/result') body=result
  else if (path === '/api/tasks/mock/logs') body = {text:''}
  await route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(body)})
})

try {
  await page.goto(base,{waitUntil:'networkidle'})
  const chart = page.locator('.simulation-chart-panel .report-chart')
  await chart.waitFor()
  assert.equal(await page.getByRole('region',{name:'Chart',exact:true}).count(),1)
  assert.equal(await page.getByRole('region',{name:'IS Summary',exact:true}).count(),1)
  assert.equal(await page.getByLabel('结果曲线',{exact:true}).inputValue(),'pnl')
  assert.equal(await page.locator('option[value=sharpe]').isDisabled(),true)
  assert.equal(await page.locator('option[value=turnover]').isDisabled(),false)
  await page.getByLabel('结果曲线',{exact:true}).selectOption('turnover')
  assert.equal(await page.getByLabel('结果曲线',{exact:true}).inputValue(),'turnover')
  await page.getByLabel('结果曲线',{exact:true}).selectOption('pnl')
  assert.equal(await page.locator('.result-periods').count(),0)
  assert.equal(await page.locator('.dataset-chip').count(),0)
  assert.equal(await page.locator('.result-factor-name').count(),0)
  assert.equal(await page.getByRole('button',{name:'5D',exact:true}).count(),0)
  assert.equal(await page.getByRole('button',{name:'20D',exact:true}).count(),0)
  assert.ok((await page.locator('.split-second').boundingBox()).width >= 419)
  const size = await chart.boundingBox()
  assert.ok(size.width > 300 && size.height >= 250,'Restored split must leave a usable PnL chart')
  await page.screenshot({path:`${output}/factorpool-brain-chart.png`,fullPage:true})
  await page.getByRole('button',{name:'Stats',exact:true}).click()
  const table = page.getByRole('table',{name:'年度统计表'})
  await table.waitFor()
  const periods = page.getByLabel('验证区间')
  assert.match(await periods.getByRole('button',{name:'TRAIN',exact:true}).getAttribute('class'),/active/)
  assert.equal(await periods.getByRole('button',{name:'TEST',exact:true}).isDisabled(),true)
  assert.equal(await periods.getByRole('button',{name:'IS',exact:true}).isDisabled(),true)
  assert.equal(await periods.getByRole('button',{name:'OS',exact:true}).isDisabled(),true)
  assert.equal(await page.getByText('IS Summary',{exact:true}).count(),1)
  assert.equal(await page.locator('.summary-measure').count(),6)
  assert.equal(await page.locator('.factor-metrics').count(),0)
  assert.deepEqual(await table.locator('thead th').allTextContents(),
    ['Year','Sharpe','Turnover','Fitness','Returns','Drawdown','Margin','Long Count','Short Count'])
  assert.match(await table.locator('tbody').innerText(),/2023/)
  assert.doesNotMatch(await table.locator('tbody').innerText(),/2024/)
  assert.deepEqual(await table.locator('tbody tr').first().locator('td').allTextContents(),
    ['1.20','12.00%','1.10','18.00%','7.00%','4.00 ‱','2','3'])
  await page.getByRole('button',{name:'Show test period',exact:true}).click()
  assert.match(await periods.getByRole('button',{name:'TEST',exact:true}).getAttribute('class'),/active/)
  assert.match(await table.locator('tbody').innerText(),/2024/)
  assert.match(await table.locator('tbody').innerText(),/2023/)
  assert.equal(await table.locator('tbody tr').count(),2)
  assert.equal(await page.locator('.summary-measure').first().locator('strong').innerText(),'2.50')
  assert.deepEqual(await table.locator('tbody tr').first().locator('td').allTextContents(),
    ['1.20','12.00%','1.10','18.00%','7.00%','4.00 ‱','2','3'])
  await periods.getByRole('button',{name:'IS',exact:true}).click()
  assert.equal(await page.getByText('IS Summary',{exact:true}).count(),1)
  assert.equal(await page.locator('.summary-measure').first().locator('strong').innerText(),'1.70')
  assert.equal(await table.locator('tbody tr').count(),2,'UI must use 1D even when API also contains 5D/20D')
  await periods.getByRole('button',{name:'TRAIN',exact:true}).click()
  assert.equal(await page.locator('.summary-measure').first().locator('strong').innerText(),'1.20')
  assert.equal(await table.locator('tbody tr').count(),2,'Period switching must not hide the annual table once test period is shown')
  await page.getByRole('button',{name:'收起年度统计',exact:true}).click()
  assert.equal(await table.count(),0)
  await page.getByRole('button',{name:'展开年度统计',exact:true}).click()
  assert.equal(await table.count(),1)
  assert.equal(result.diagnostics.horizons['1'].portfolios.alpha.yearly_stats[0].pnl,.013,'PnL stays in the API result')
  await page.screenshot({path:`${output}/factorpool-brain-stats.png`,fullPage:true})
  await page.getByRole('button',{name:'Chart',exact:true}).click()
  await page.setViewportSize({width:390,height:844})
  await page.evaluate(() => window.scrollTo(0,0))
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 392))
  assert.ok((await page.locator('.result-panel').boundingBox()).y < 744,'Mobile results must enter the first viewport')
  assert.ok((await chart.boundingBox()).height >= 250)
  await page.screenshot({path:`${output}/factorpool-brain-chart-mobile.png`,fullPage:true})
  await page.getByRole('button',{name:'Stats',exact:true}).click()
  assert.ok(await page.locator('.annual-scroll').evaluate(e => e.scrollWidth > e.clientWidth))
  await page.screenshot({path:`${output}/factorpool-brain-stats-mobile.png`,fullPage:true})
  delete result.diagnostics.horizons['1'].partitions.train.alpha.yearly_stats
  await page.reload({waitUntil:'networkidle'})
  await chart.waitFor()
  await page.getByRole('button',{name:'Stats',exact:true}).click()
  await page.getByText('此结果没有年度统计数据；请使用当前版本重新运行。').waitFor()
  assert.deepEqual(errors,[])
  assert.ok(requests.every(request => request.method === 'GET'))
  console.log('1D-only BRAIN-style Chart/Stats, nine-column annual table, API multi-period retention and extreme/mobile layouts passed')
} finally { await browser.close() }
