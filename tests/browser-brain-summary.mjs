// Offline UI regression: route all API calls to deterministic local fixtures.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
const base = process.env.FACTORPOOL_URL
if (!base?.trim()) throw new Error('FACTORPOOL_URL must name an isolated frontend')
const require = createRequire(import.meta.url)
const {chromium} = require(process.env.FACTORPOOL_PLAYWRIGHT_MODULE || 'playwright')
const output = process.env.FACTORPOOL_BROWSER_OUTPUT || '/tmp'
const browser = await chromium.launch({headless:true, executablePath:process.env.FACTORPOOL_CHROMIUM_PATH, args:['--no-sandbox']})
const page = await browser.newPage({viewport:{width:1512,height:1050}})
const errors = []
page.on('pageerror', error => errors.push(error.message))

const brain = {sharpe:1.2, turnover:.12, fitness:1.1, returns:.18, drawdown:.07, margin:.0004}
const year = (year, pnl, long_count, short_count) => ({year, start:`${year}-01-01`, end:`${year}-12-31`,
  ...brain, pnl, long_count, short_count})
const alpha = yearly_stats => ({brain_style:brain, yearly_stats,
  pnl:{book_size:1, unit:'BOOK', cumulative:[.01,.03]},
  nav:[1.01,1.03], drawdown_series:[0,-.01], constant_book_drawdown:[0,-.02], returns:[.01,.02]})
const horizon = (full, train, test) => ({portfolio_dates:['2023-12-29','2024-01-02'],
  portfolios:{alpha:alpha(full)}, partitions:{
    train:{dates:['2023-12-29'], portfolios:{}, alpha:alpha(train)},
    test:{dates:['2024-01-02'], portfolios:{}, alpha:alpha(test)},
  }})
const full = [year('2023',.013,2,3), year('2024',.027,4,5)]
let result = {summary:{ic_mean:.03,rank_ic_mean:.04,icir:.2,coverage:.95},
  metadata:{report_version:3, brain_settings:{visualization:true}, synthetic:true, execution_mode:'native', value_count:0},
  diagnostics:{version:3, test_period:{start:'2024-01-01'}, conventions:{brain_settings:{visualization:true}},
    horizons:{'1':horizon(full,[full[0]],[full[1]]), '5':horizon([year('2024',.05,7,8)],[],[year('2024',.05,7,8)])}}}
const task = {id:'mock',name:'示例因子',status:'SUCCEEDED'}
await page.addInitScript(() => sessionStorage.setItem('factorpool.task','mock'))
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname
  let body = []
  if (path === '/api/tasks/mock') body = task
  else if (path === '/api/tasks/mock/result') body = result
  else if (path === '/api/tasks/mock/logs') body = {text:''}
  await route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(body)})
})

try {
  await page.goto(base,{waitUntil:'networkidle'})
  const table = page.getByRole('table',{name:'年度统计表'})
  await table.waitFor()
  assert.equal(await page.getByLabel('验证区间').inputValue(),'train')
  assert.deepEqual(await table.locator('thead th').allTextContents(),
    ['Year','Sharpe','Turnover','Fitness','Returns','Drawdown','Margin','Long Count','Short Count','PnL'])
  assert.match(await table.locator('tbody').innerText(),/2023/)
  assert.doesNotMatch(await table.locator('tbody').innerText(),/2024/)
  await page.getByLabel('验证区间').selectOption('test')
  assert.match(await table.locator('tbody').innerText(),/2024/)
  assert.match(await table.locator('tbody').innerText(),/0\.027/)
  await page.getByLabel('验证区间').selectOption('all')
  assert.equal(await table.locator('tbody tr').count(),2)
  await page.screenshot({path:`${output}/factorpool-brain-stats.png`,fullPage:true})
  await page.getByRole('button',{name:'5D',exact:true}).click()
  assert.match(await table.locator('tbody').innerText(),/0\.05/)
  await page.getByRole('button',{name:'Chart',exact:true}).click()
  assert.equal(await page.getByLabel('结果曲线').inputValue(),'pnl')
  await page.locator('.simulation-chart-panel .report-chart').waitFor()
  await page.getByRole('button',{name:'Stats',exact:true}).click()
  await page.setViewportSize({width:390,height:844})
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 392))
  assert.ok(await page.locator('.annual-scroll').evaluate(e => e.scrollWidth > e.clientWidth))
  await page.screenshot({path:`${output}/factorpool-brain-stats-mobile.png`,fullPage:true})
  delete result.diagnostics.horizons['1'].partitions.train.alpha.yearly_stats
  await page.reload({waitUntil:'networkidle'})
  await page.getByText('此结果没有年度统计数据；请使用当前版本重新运行。').waitFor()
  assert.deepEqual(errors,[])
  console.log('BRAIN-style annual statistics, periods, PnL chart and mobile layout passed')
} finally { await browser.close() }
