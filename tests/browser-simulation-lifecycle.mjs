// Stateful UI regression. Every API request is fulfilled locally; no task is sent to a service.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {writeFile} from 'node:fs/promises'

const base = process.env.FACTORPOOL_URL
if (!base?.trim()) throw new Error('FACTORPOOL_URL must name an explicitly selected frontend')
const require = createRequire(import.meta.url)
const {chromium} = require(process.env.FACTORPOOL_PLAYWRIGHT_MODULE || 'playwright')
const output = process.env.FACTORPOOL_BROWSER_OUTPUT || '/tmp'
const browser = await chromium.launch({headless:true, executablePath:process.env.FACTORPOOL_CHROMIUM_PATH, args:['--no-sandbox']})
const evidence = []
const source = `from factorpool import PythonIndicator
class LifecycleFactor(PythonIndicator):
    def update(self, input):
        self.value = input.volume
        return True
config = {}
if __name__ == "__main__":
    LifecycleFactor.run(config)
`

async function until(check, message, timeout=20000) {
  const deadline = Date.now()+timeout
  while (Date.now()<deadline) {
    if (await check()) return
    await new Promise(resolve => setTimeout(resolve,50))
  }
  throw new Error(message)
}

function report(index) {
  const year = String(2023+index), dates = [`${year}-01-02`,`${year}-01-03`]
  const brain = {sharpe:index*1.2, turnover:.12, fitness:1.1, returns:.18, drawdown:.07, margin:.0004}
  const alpha = {brain_style:brain, pnl:{book_size:1,unit:'BOOK',cumulative:[.01,.03]},
    nav:[1.01,1.03],returns:[.01,.02],
    yearly_stats:[{year,start:dates[0],end:dates[1],...brain,long_count:3,short_count:3,pnl:.03}]}
  return {metadata:{report_version:3,brain_settings:{visualization:true},value_count:1},summary:{},
    diagnostics:{version:3,test_period:{},conventions:{brain_settings:{visualization:true}},
      horizons:{'1':{portfolio_dates:dates,portfolios:{alpha}}}}}
}

async function harness(options={}) {
  const context = await browser.newContext({viewport:{width:1366,height:900}})
  const page = await context.newPage(), tasks = new Map(), requests = [], errors = []
  const counts = {post:0,status:0,logs:0,result:0,values:0,logFailures:0}
  let releaseFirst, heldFirst = false
  const heldResult = new Promise(resolve => { releaseFirst=resolve })
  page.on('pageerror', error => errors.push(error.message))
  if (options.storageBlocked) await page.addInitScript(() => {
    window.lifecycleStorageErrors={getItem:0,setItem:0,removeItem:0}
    for (const method of ['getItem','setItem','removeItem']) {
      const original=Storage.prototype[method]
      Storage.prototype[method]=function(key,...args) {
        if (key==='factorpool.task') {
          window.lifecycleStorageErrors[method]++
          throw new DOMException('Lifecycle storage denied','SecurityError')
        }
        return original.call(this,key,...args)
      }
    }
  })
  await page.route('**/api/**',async route => {
    const request=route.request(),path=new URL(request.url()).pathname
    requests.push({method:request.method(),path})
    let body=[],status=200
    if (path==='/api/examples') body=[{name:'low_volume.py',source_code:source}]
    else if (path==='/api/data-sources') body=[]
    else if (path==='/api/operators'||path==='/api/factors') body=[]
    else if (path==='/api/tasks'&&request.method()==='POST') {
      const index=++counts.post,id=`lifecycle-${index}`
      const submitted=request.postDataJSON()
      tasks.set(id,{index,created:Date.now(),resultCalls:0,valuesCalls:0,logsCalls:0,
        reportFailures:options.reportFailures||0,valueFailures:options.valueFailures||0,
        snapshot:{name:submitted.name,source_code:submitted.source_code,workspace_config:submitted.workspace_config}})
      body={id}
      if (options.postDelay) await new Promise(resolve=>setTimeout(resolve,options.postDelay))
    } else {
      const match=path.match(/^\/api\/tasks\/(lifecycle-\d+)(?:\/(result|values|logs))?$/)
      const task=match&&tasks.get(match[1])
      if (!task) {status=404;body={error:`Unexpected mock request ${request.method()} ${path}`}}
      else if (!match[2]) {
        counts.status++
        const delay=task.index===2&&options.holdFirstReport ? 0 : (options.completeAfter??1400)
        const finished=Date.now()-task.created>=delay
        body={id:match[1],name:task.snapshot.name,snapshot:task.snapshot,
          status:finished?(options.failed?'FAILED':'SUCCEEDED'):'RUNNING',
          error:finished&&options.failed?'Lifecycle computation failed':null}
      } else if (match[2]==='logs') {
        counts.logs++;task.logsCalls++
        if (options.logFailure&&counts.logFailures===0) {
          counts.logFailures++;status=503;body={error:'Lifecycle logs unavailable'}
        } else body={text:'Lifecycle worker completed'}
      } else if (match[2]==='result') {
        counts.result++;task.resultCalls++
        if (options.holdFirstReport&&task.index===1&&!heldFirst) {
          heldFirst=true;await heldResult
        }
        if (task.reportFailures>0) {
          task.reportFailures--;status=503;body={error:'Lifecycle report unavailable'}
        } else body=report(task.index)
      } else {
        counts.values++;task.valuesCalls++
        if (task.valueFailures>0) {
          task.valueFailures--;status=503;body={error:'Lifecycle values unavailable'}
        } else body=[{trading_date:'2024-01-02',symbol:'LIFECYCLE.XSHG',value:10,
          forward_return:.01,factor_group:1,reason:'ok'}]
      }
    }
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)})
  })
  await page.goto(base,{waitUntil:'networkidle'})
  return {page,tasks,counts,errors,requests,releaseFirst,
    async submit() {
      const response=page.waitForResponse(value=>value.request().method()==='POST'&&new URL(value.url()).pathname==='/api/tasks')
      await page.getByRole('button',{name:'运行研究',exact:true}).click()
      assert.equal((await response).status(),200)
    },
    async close(label) {
      releaseFirst()
      assert.deepEqual(errors,[],label)
      const forbidden=requests.filter(request=>request.method!=='GET'&&!(request.method==='POST'&&request.path==='/api/tasks'))
      assert.deepEqual(forbidden,[],`${label}: unexpected API write`)
      evidence.push({scenario:label,counts:{...counts},pageErrors:errors,
        productionRequestsForwarded:0,apiRequestsFulfilledLocally:requests.length})
      await context.close()
    }}
}

const chart = page => page.locator('.simulation-chart-panel .report-chart')
const noFirstRun = async page => assert.equal(await page.getByText('Run your first simulation',{exact:true}).count(),0)

try {
  // A slow submission must acknowledge the click before the server returns a task id.
  {
    const h=await harness({postDelay:1800,completeAfter:0})
    try {
      await h.page.getByRole('button',{name:'运行研究',exact:true}).click()
      await h.page.getByRole('heading',{name:'正在提交任务',exact:true}).waitFor()
      assert.equal(await h.page.getByRole('button',{name:'运行研究',exact:true}).isDisabled(),true)
      await h.page.screenshot({path:`${output}/simulation-lifecycle-submitting.png`,fullPage:true})
      await chart(h.page).waitFor()
      assert.equal(h.counts.post,1)
    } finally { await h.close('submission feedback appears before the POST completes') }
  }
  // The task has already completed when report loading fails. Retrying must be GET-only.
  {
    const h=await harness({reportFailures:1})
    try {
      await h.submit()
      await h.page.locator('.badge.RUNNING').waitFor()
      await until(()=>h.counts.result>=1,'Completed task did not request its report')
      await noFirstRun(h.page)
      await chart(h.page).waitFor({timeout:20000})
      assert.equal(h.counts.result,2)
      assert.equal(h.counts.post,1,'Automatic report retry must not resubmit research')
      assert.equal(h.counts.values,0,'Value pagination is not a prerequisite for the main report')
      assert.equal(await h.page.getByLabel('结果曲线',{exact:true}).inputValue(),'pnl')
      await h.page.getByRole('button',{name:'Summary',exact:true}).click()
      await h.page.getByRole('table',{name:'年度统计表'}).waitFor()
      await h.page.screenshot({path:`${output}/simulation-lifecycle-recovered.png`,fullPage:true})
    } finally { await h.close('transient report failure recovers without another POST') }
  }
  // Permanent report failure is bounded and exposes a real retry action.
  {
    const h=await harness({reportFailures:Infinity,completeAfter:0})
    try {
      await h.submit()
      await until(()=>h.counts.result===3,'Expected three bounded report attempts')
      await h.page.getByRole('alert').filter({hasText:'Lifecycle report unavailable'}).waitFor()
      await new Promise(resolve=>setTimeout(resolve,1800))
      assert.equal(h.counts.result,3,'A failed report must not poll forever')
      await noFirstRun(h.page)
      h.tasks.get('lifecycle-1').reportFailures=0
      await h.page.getByRole('button',{name:'重试报告',exact:true}).click()
      await chart(h.page).waitFor()
      assert.equal(h.counts.result,4)
      assert.equal(h.counts.post,1,'Manual report retry must not resubmit research')
    } finally { await h.close('bounded report errors and manual retry') }
  }
  // Values and logs fail independently, while the completed report remains usable.
  {
    const h=await harness({completeAfter:0,valueFailures:1,logFailure:true})
    try {
      await h.submit();await chart(h.page).waitFor()
      assert.equal(h.counts.values,0)
      await h.page.getByRole('button',{name:'Factor Values',exact:true}).click()
      await h.page.getByRole('alert').filter({hasText:'Lifecycle values unavailable'}).waitFor()
      assert.equal(await h.page.getByRole('button',{name:'Export Results',exact:true}).isEnabled(),true)
      await h.page.getByRole('button',{name:'重试因子值',exact:true}).click()
      await h.page.getByRole('cell',{name:'LIFECYCLE.XSHG',exact:true}).waitFor()
      assert.equal(h.counts.values,2)
      await h.page.getByRole('button',{name:'Logs',exact:true}).click()
      await until(()=>h.counts.logFailures===1,'The mock log failure was not exercised')
      await h.page.getByRole('button',{name:'Chart',exact:true}).click()
      await chart(h.page).waitFor()
      assert.equal(h.counts.result,1)
      assert.equal(h.counts.post,1)
    } finally { await h.close('on-demand values and logs cannot discard the main report') }
  }
  // Storage is optional: blocked reads, writes and removals cannot stop a long task.
  {
    const h=await harness({storageBlocked:true})
    try {
      await h.submit();await chart(h.page).waitFor({timeout:20000})
      await h.page.getByRole('button',{name:'新建模拟',exact:true}).click()
      assert.equal(await h.page.getByRole('tab',{name:'Simulation 2',exact:true}).getAttribute('aria-selected'),'true')
      await h.page.getByRole('tab',{name:'Simulation 1',exact:true}).click()
      await chart(h.page).waitFor()
      const failures=await h.page.evaluate(()=>window.lifecycleStorageErrors)
      for(const method of ['getItem','setItem','removeItem']) assert.ok(failures[method]>0,method)
      assert.equal(await h.page.getByRole('alert').filter({hasText:'Lifecycle storage denied'}).count(),0)
      assert.equal(h.counts.post,1)
    } finally { await h.close('blocked session storage does not block startup, polling or workspace switching') }
  }
  // A real failed computation must not be described as a first, unstarted run.
  {
    const h=await harness({failed:true})
    try {
      await h.submit()
      await h.page.locator('.badge.FAILED').waitFor()
      await h.page.getByRole('alert').filter({hasText:'Lifecycle computation failed'}).waitFor()
      await noFirstRun(h.page)
      assert.equal(h.counts.result,0)
    } finally { await h.close('failed computation has a distinct terminal state') }
  }
  // A late report from a previous workspace cannot replace the current result.
  {
    const h=await harness({holdFirstReport:true})
    try {
      await h.submit()
      await until(()=>h.tasks.get('lifecycle-1').resultCalls===1,'First report was not requested')
      await h.page.getByRole('button',{name:'新建模拟',exact:true}).click()
      await h.submit();await chart(h.page).waitFor()
      h.releaseFirst()
      await new Promise(resolve=>setTimeout(resolve,600))
      await h.page.getByRole('button',{name:'Summary',exact:true}).click()
      const rows=h.page.getByRole('table',{name:'年度统计表'}).locator('tbody')
      assert.match(await rows.innerText(),/2025/)
      assert.doesNotMatch(await rows.innerText(),/2024/)
      assert.equal(h.counts.post,2)
    } finally { await h.close('late workspace responses do not replace the active result') }
  }
  await writeFile(`${output}/simulation-lifecycle.json`,JSON.stringify({status:'passed',productionTasksCreated:0,scenarios:evidence},null,2)+'\n')
  console.log(JSON.stringify({status:'passed',scenarios:evidence.length,productionTasksCreated:0}))
} finally { await browser.close() }
