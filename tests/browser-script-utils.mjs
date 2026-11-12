export function fixtureConfig({period=20, start='2024-01-29', end=start, horizons=[1,5,20], settings, analysis={}}={}) {
  const config = {
    base: {start_date:start, end_date:end, frequency:'1d'},
    data: {provider:'fixture', universe:'fixture'},
    factor: {period},
    analysis: {groups:3, horizons, decay_steps:2, ...structuredClone(analysis)},
  }
  if (settings !== undefined) config.settings = structuredClone(settings)
  return config
}

function pythonLiteral(value, depth=0) {
  if (value === true) return 'True'
  if (value === false) return 'False'
  if (value === null) return 'None'
  if (Array.isArray(value)) return '[' + value.map(item => pythonLiteral(item, depth)).join(', ') + ']'
  if (typeof value === 'object') {
    const indent = '    '.repeat(depth+1), end = '    '.repeat(depth)
    return '{\n' + Object.entries(value).map(([key, item]) => indent + JSON.stringify(key) + ': ' + pythonLiteral(item, depth+1)).join(',\n') + '\n' + end + '}'
  }
  return JSON.stringify(value)
}

export function fixtureScript(options={}) {
  const config = fixtureConfig(options)
  const meanClose = options.meanClose || false
  return `from factorpool import PythonIndicator

# 完整研究脚本：配置和因子共同保存在源码快照中。
config = ${pythonLiteral(config)}


class MatrixFactor(PythonIndicator):
    def __init__(self, period=20):
        super().__init__()
        self.period = period

    def update(self, input):
        mean_value = self.ts.mean(input.${meanClose ? 'close' : 'volume'}, self.period)
        self.value = ${meanClose ? 'mean_value' : 'self.cs.rank_unit(-mean_value)'}
        return self.ts.is_ready(mean_value)


if __name__ == "__main__":
    MatrixFactor.run(config)
`
}

export async function setSource(page, source) {
  const editor = page.locator('.cm-content')
  await editor.waitFor()
  await editor.click()
  await page.keyboard.press('Control+a')
  await page.keyboard.insertText(source)
}

// The editor virtualizes its DOM. Read the complete source through the existing
// read-only AST preview request instead of rendered lines or private view state.
export async function readSource(page) {
  const settings = page.getByRole('region', {name:'模拟设置'})
  const alreadyOpen = await settings.isVisible()
  if (!alreadyOpen) await page.getByRole('button', {name:'Settings', exact:true}).click()
  const request = page.waitForRequest(value => value.method() === 'POST' && value.url().endsWith('/api/script/resolve'))
  const response = page.waitForResponse(value => value.request().method() === 'POST' && value.url().endsWith('/api/script/resolve'))
  await settings.getByRole('button', {name:'校验源码配置', exact:true}).click()
  const [submitted, preview] = await Promise.all([request, response])
  if (!preview.ok()) throw new Error(`Source preview failed (${preview.status()}): ${await preview.text()}`)
  await settings.getByLabel('源码配置', {exact:true}).waitFor()
  if (!alreadyOpen) await settings.getByRole('button', {name:'关闭设置', exact:true}).click()
  return submitted.postDataJSON().source_code
}
