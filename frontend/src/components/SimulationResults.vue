<script setup>
import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount } from "vue";
import UiIcon from "./UiIcon.vue";
import BrainSummary from "./BrainSummary.vue";
import BrainAnnualStats from "./BrainAnnualStats.vue";
import FactorDiagnostics from "./FactorDiagnostics.vue";
import ReportChart from "./ReportChart.vue";
import ResultDetails from "./ResultDetails.vue";
import AlphaTrace from "./AlphaTrace.vue";
const props = defineProps({
  task: Object, result: Object, logs: String, values: Array, offset: Number,
  busy: Boolean, running: Boolean, submitting: Boolean, reportLoading: Boolean,
  reportError: String, valuesLoading: Boolean, valuesError: String, states: Object,
  savedFactor: Object,
});
const emit = defineEmits(["load-snapshot", "page", "retry-report", "load-values", "load-logs", "save-factor"]);
const core = [
  ["chart", "chart", "Chart"], ["summary", "bars", "Summary"],
  ["correlation", "scatter", "Correlation"], ["testing", "check", "Testing Status"],
  ["properties", "info", "Properties"],
];
const extensions = [
  ["ic", "scatter", "IC Analysis"], ["groups", "table", "Groups"],
  ["trace", "table", "Weights / PnL"], ["values", "table", "Factor Values"],
  ["logs", "logs", "Logs"], ["source", "code", "Source"],
];
const order = ref(core.map(section => section[0]));
const orderedCore = computed(() => order.value.map(key => core.find(section => section[0] === key)));
const sections = computed(() => [...orderedCore.value, ...extensions]);
const activeSection = ref("chart"), chartMode = ref("pnl"), partition = ref("all"), showTest = ref(false);
const opened = ref(new Set());
const selected = computed(() => props.result?.diagnostics?.horizons?.["1"]);
const supportedReport = computed(() => props.result?.metadata?.report_version === 3
  && props.result?.diagnostics?.version === 3 && props.result.metadata.brain_settings
  && props.result.diagnostics.conventions?.brain_settings);
const testStart = computed(() => props.result?.diagnostics?.test_period?.start);
const hasTest = computed(() => Boolean(testStart.value));
const trainDates = computed(() => selected.value?.partitions?.train?.dates || []);
const testDates = computed(() => selected.value?.partitions?.test?.dates || []);
const chartData = computed(() => {
  if (!hasTest.value || showTest.value) return selected.value;
  const train = selected.value?.partitions?.train;
  return train ? { portfolio_dates: train.dates, portfolios: { ...train.portfolios, alpha: train.alpha } } : null;
});
const chartAlpha = computed(() => chartData.value?.portfolios?.alpha);
const fullAlpha = computed(() => selected.value?.portfolios?.alpha);
const alpha = computed(() => partition.value === "all" ? fullAlpha.value : selected.value?.partitions?.[partition.value]?.alpha);
const annualRows = computed(() => hasTest.value && !showTest.value
  ? selected.value?.partitions?.train?.alpha?.yearly_stats : fullAlpha.value?.yearly_stats);
const summaryDates = computed(() => partition.value === "all" ? selected.value?.portfolio_dates : selected.value?.partitions?.[partition.value]?.dates);
const researchReport = computed(() => props.result?.diagnostics
  ? { ...props.result.diagnostics, horizons: { "1": selected.value } } : null);
const visualization = computed(() => props.result?.metadata?.brain_settings?.visualization);
const hasSharpe = computed(() => Array.isArray(chartAlpha.value?.sharpe_series)
  && chartAlpha.value.sharpe_series.length === chartData.value?.portfolio_dates?.length
  && chartAlpha.value.sharpe_series.some(Number.isFinite));
const summary = computed(() => props.result?.summary || {});
const num = x => x == null || !Number.isFinite(Number(x)) ? "—" : Number(x).toFixed(4);
const pct = x => x == null || !Number.isFinite(Number(x)) ? "—" : (Number(x) * 100).toFixed(2) + "%";
const bookLabel = computed(() => chartAlpha.value?.pnl
  ? `${Number(chartAlpha.value.pnl.book_size).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${chartAlpha.value.pnl.unit}` : "—");
const statusText = computed(() => {
  if (props.submitting) return "正在提交";
  if (props.task?.status === "SUCCEEDED" && !props.result) return props.reportError ? "报告加载失败" : "正在加载报告";
  return props.task ? props.states[props.task.status] || props.task.status : "Ready";
});
const statusClass = computed(() => props.submitting ? "QUEUED" : props.task?.status === "SUCCEEDED" && !props.result
  ? props.reportError ? "FAILED" : "RUNNING" : props.task?.status);
function toggleTest() {
  showTest.value = !showTest.value;
  partition.value = showTest.value && selected.value?.partitions?.test ? "test" : "train";
}
const panel = ref(null), rail = ref(null), menu = ref(null);
const nodes = new Map();
let scrollFrame;
function remember(key, element) { if (element) nodes.set(key, element); else nodes.delete(key); }
function syncActive() {
  if (!panel.value) return;
  const top = panel.value.getBoundingClientRect().top + 38;
  let current = sections.value[0][0];
  for (const [key] of sections.value) {
    const node = nodes.get(key);
    if (node && node.getBoundingClientRect().top <= top) current = key;
  }
  activeSection.value = current;
}
function scheduleActive() {
  if (!panel.value) return;
  if (scrollFrame) cancelAnimationFrame(scrollFrame);
  scrollFrame = requestAnimationFrame(syncActive);
}
async function navigate(key) {
  if (extensions.some(section => section[0] === key)) opened.value = new Set([key]);
  await nextTick();
  const node = nodes.get(key);
  if (!node || !panel.value) return;
  activeSection.value = key;
  panel.value.scrollTo({ top: panel.value.scrollTop + node.getBoundingClientRect().top - panel.value.getBoundingClientRect().top - 12,
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  node.focus({ preventScroll: true });
}
function railKey(event, index) {
  const buttons = rail.value?.querySelectorAll(".nav-button");
  if (!buttons?.length) return;
  const next = event.key === "ArrowDown" ? (index + 1) % buttons.length
    : event.key === "ArrowUp" ? (index + buttons.length - 1) % buttons.length
      : event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : null;
  if (next !== null) { event.preventDefault(); buttons[next].focus(); }
}
function toggleExtension(key, event) {
  const isOpen = event.target.open;
  if (opened.value.has(key) !== isOpen) {
    const next = new Set(opened.value);
    if (isOpen) next.add(key); else next.delete(key);
    opened.value = next;
  }
  if (isOpen && key === "values" && props.result) emit("load-values");
  if (isOpen && key === "logs") emit("load-logs");
  scheduleActive();
}
const pendingOrder = ref([]), preferenceMessage = ref("");
let dragged;
function openMenu() { pendingOrder.value = [...order.value]; preferenceMessage.value = ""; menu.value.showModal(); }
function move(key, target) {
  const next = [...pendingOrder.value], index = next.indexOf(key);
  if (index < 0 || target < 0 || target >= next.length) return;
  next.splice(index, 1); next.splice(target, 0, key); pendingOrder.value = next;
}
function drop(key) { if (dragged) move(dragged, pendingOrder.value.indexOf(key)); dragged = null; }
async function applyMenu() {
  order.value = [...pendingOrder.value];
  try { localStorage.setItem("factorpool.report.sections", JSON.stringify(order.value)); }
  catch { preferenceMessage.value = "排序已应用到当前页面；浏览器未允许保存偏好。"; }
  menu.value.close();
  await nextTick(); panel.value?.scrollTo({ top: 0 }); syncActive();
}
onMounted(() => {
  try {
    const stored = JSON.parse(localStorage.getItem("factorpool.report.sections"));
    if (Array.isArray(stored) && stored.length === core.length && new Set(stored).size === core.length
      && stored.every(key => core.some(section => section[0] === key))) order.value = stored;
  } catch { /* Display preferences must not gate research. */ }
  nextTick(scheduleActive);
});
onBeforeUnmount(() => { if (scrollFrame) cancelAnimationFrame(scrollFrame); });
watch(() => props.task?.id, async () => {
  activeSection.value = order.value[0]; chartMode.value = "pnl"; opened.value = new Set();
  await nextTick(); panel.value?.scrollTo({ top: 0 });
});
watch(() => props.result, () => {
  showTest.value = false; partition.value = hasTest.value ? "train" : "all";
  if (props.result && opened.value.has("values")) emit("load-values");
  nextTick(scheduleActive);
}, { immediate: true });
watch(hasSharpe, available => { if (!available && chartMode.value === "sharpe") chartMode.value = "pnl"; });
function exportResult() {
  const blob = new Blob([JSON.stringify({ task: props.task, result: props.result }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = `factorpool-${props.task.id}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
</script>
<template>
  <div class="results-workspace">
    <aside ref="rail" class="results-rail" aria-label="结果导航">
      <button v-for="([key, icon, label], index) in sections" :key="key" class="nav-button"
        :class="{ active: activeSection === key }" :aria-current="activeSection === key ? 'location' : undefined"
        :aria-label="label" :title="label" @click="navigate(key)" @keydown="railKey($event, index)">
        <UiIcon :name="icon" :size="19" />
      </button>
      <button class="customize-button" aria-label="自定义结果菜单" title="Customize Results Menu" @click="openMenu"><UiIcon name="settings" :size="19" /></button>
    </aside>
    <section ref="panel" class="result-panel" aria-label="回测报告" tabindex="0" @scroll.passive="scheduleActive">
      <p v-if="preferenceMessage" class="report-note" role="status">{{ preferenceMessage }}</p>
      <div v-if="submitting" class="empty" role="status"><UiIcon name="refresh" :size="42" /><h2>正在提交任务</h2><p>提交成功后会自动计算并加载报告。</p></div>
      <template v-else-if="result && supportedReport && selected">
        <div v-if="hasTest" class="test-period-banner">
          <button class="test-toggle" :aria-pressed="showTest" @click="toggleTest">{{ showTest ? 'Hide test period' : 'Show test period' }}</button>
          <span>指定测试期时，默认隐藏测试期和整体统计。</span>
        </div>
        <section v-for="[key, icon, label] in orderedCore" :key="key" :ref="element => remember(key, element)"
          :data-result-section="key" class="report-section" tabindex="-1" :aria-labelledby="`report-${key}-title`">
          <header class="results-toolbar">
            <span><UiIcon :name="icon" /><h2 :id="`report-${key}-title`">{{ key === 'summary' ? 'IS Summary' : key === 'testing' ? 'IS Testing Status' : label }}</h2></span>
            <select v-if="key === 'chart' && visualization" v-model="chartMode" aria-label="结果曲线">
              <optgroup label="Performance"><option value="pnl">PnL</option><option value="sharpe" :disabled="!hasSharpe">Sharpe · {{ hasSharpe ? '真实序列' : '暂无真实时间序列' }}</option><option value="turnover">Turnover</option></optgroup>
              <optgroup label="FactorPool 扩展"><option value="nav">Net value · 复利净值</option><option value="returns">Daily returns · 日收益</option><option value="drawdown">Drawdown · 净值回撤</option><option value="monthly">Monthly returns · 月度收益</option><option value="yearly">Yearly returns · 年度收益</option></optgroup>
            </select>
            <div v-if="key === 'summary'" class="period-control" role="group" aria-label="汇总区间"><span>Period</span>
              <button v-for="[value, title] in [['train','TRAIN'],['test','TEST'],['all','IS'],['os','OS']]" :key="value"
                :disabled="value === 'os' || (!hasTest && value !== 'all') || (hasTest && !showTest && ['test','all'].includes(value)) || (hasTest && ['train','test'].includes(value) && !selected?.partitions?.[value])"
                :aria-pressed="partition === value" :title="value === 'os' ? '没有真实 OS 结果' : title" @click="partition = value">{{ title }}</button>
            </div>
          </header>
          <div v-if="key === 'chart'" class="simulation-chart-panel">
            <ReportChart v-if="visualization && chartData && chartAlpha" :data="chartData" :portfolio="chartAlpha" :mode="chartMode"
              :show-test="showTest" :train-dates="trainDates" :test-dates="testDates" />
            <p v-else class="report-note" role="status">{{ visualization ? '当前训练区间没有可用图表数据。' : '可视化已由 settings.visualization 关闭；统计与导出仍可使用。' }}</p>
            <details class="chart-metadata"><summary>查看图表口径</summary>
              <p class="chart-meta-note">{{ chartData?.portfolio_dates?.[0] || '—' }} — {{ chartData?.portfolio_dates?.at(-1) || '—' }} · 1D · 收盘计算 · 无费用</p>
              <p v-if="chartMode === 'pnl'" class="pnl-book-note">固定 book：{{ bookLabel }} · 真实累计 PnL；BOOK 为名义单位，不代表货币兑换。</p>
              <p v-else-if="chartMode === 'nav'" class="pnl-book-note">初始净值 1，按比例收益逐日复利。</p>
              <p class="chart-meta-note">缩放只改变图表显示。</p>
            </details>
          </div>
          <template v-else-if="key === 'summary'">
            <p class="summary-range">统计区间：{{ summaryDates?.[0] || '—' }} — {{ summaryDates?.at(-1) || '—' }}</p>
            <BrainSummary :summary="alpha?.brain_style" />
            <BrainAnnualStats :key="task?.id" :rows="annualRows" :test-start="testStart" :show-test="showTest" :train-dates="trainDates" :test-dates="testDates" />
          </template>
          <ResultDetails v-else :kind="key" :task="task" :result="result" :saved-factor="savedFactor" :busy="busy" @save-factor="emit('save-factor')" />
        </section>
      </template>
      <p v-else-if="result && supportedReport" class="report-note" role="alert">此任务没有 1D 报告；请使用 analysis.horizons = [1] 运行。其他周期仍可通过接口读取。</p>
      <p v-else-if="result" class="report-note" role="alert">此结果格式已不再支持，请使用当前脚本重新运行。</p>
      <div v-else-if="task?.status === 'SUCCEEDED'" class="empty" role="status">
        <UiIcon :name="reportError ? 'logs' : 'refresh'" :size="42" /><h2>{{ reportError ? '报告加载失败' : '计算已完成，正在加载报告' }}</h2>
        <p v-if="reportError" role="alert">{{ reportError }}</p><p v-else>正在读取本次结果，完成后会自动显示。</p>
        <button v-if="reportError" aria-label="重试报告" :disabled="reportLoading" @click="emit('retry-report')">重试报告</button>
      </div>
      <div v-else-if="['FAILED','INTERRUPTED','CANCELLED'].includes(task?.status)" class="empty" role="status"><UiIcon name="logs" :size="42" /><h2>{{ states[task.status] || task.status }}</h2><button @click="navigate('logs')">查看任务日志</button></div>
      <div v-else class="empty"><UiIcon :name="running ? 'refresh' : 'chart'" :size="42" /><h2>{{ running ? 'Simulation in progress' : 'Run your first simulation' }}</h2><p>{{ running ? '正在准备并计算本次研究，进度和错误会显示在日志中。' : '使用 PythonIndicator 编写因子，设置股票池和日期，然后点击 Simulate。' }}</p></div>
      <p v-if="task?.error" class="task-error" role="alert">{{ task.error }}</p>
      <div class="extensions-heading">FactorPool Research · 研究扩展</div>
      <details v-for="[key, icon, label] in extensions" :key="key" :ref="element => remember(key, element)" class="research-extension"
        name="factorpool-research" :data-result-section="key" :open="opened.has(key)" tabindex="-1" @toggle="toggleExtension(key, $event)">
        <summary><UiIcon :name="icon" :size="16" />{{ label }}</summary>
        <template v-if="opened.has(key)">
          <template v-if="['ic','groups'].includes(key) && supportedReport && selected">
            <p class="report-note">完整研究区间 · 仅 1D；不随 Summary 汇总选区变化。</p>
            <div v-if="key === 'ic'" class="metrics factor-metrics" aria-label="一期因子统计"><div v-for="[title,value] in [['1D IC',num(summary.ic_mean)],['1D Rank IC',num(summary.rank_ic_mean)],['1D ICIR',num(summary.icir)],['Coverage',pct(summary.coverage)]]" :key="title"><span>{{ title }}</span><strong>{{ value }}</strong></div></div>
            <FactorDiagnostics :report="researchReport" :initial-section="key === 'groups' ? 'portfolio' : 'ic'" />
          </template>
          <AlphaTrace v-else-if="key === 'trace' && supportedReport && selected" :data="selected" :constraints="result.diagnostics?.alpha_constraints" />
          <section v-else-if="key === 'values' && result" class="factor-values">
            <div class="section-title"><span>Factor Values</span><small>{{ result.metadata.value_count }} rows</small></div>
            <p v-if="valuesLoading" class="report-note" role="status">正在加载因子值…</p>
            <div v-if="valuesError" class="report-load-error"><p role="alert">{{ valuesError }}</p><button aria-label="重试因子值" :disabled="valuesLoading" @click="emit('load-values')">重试因子值</button></div>
            <div class="table-scroll"><table><thead><tr><th>Date</th><th>Symbol</th><th>Factor</th><th>Return · 1D</th><th>Group</th><th>Status</th></tr></thead><tbody><tr v-for="(row,index) in values" :key="index"><td>{{ row.trading_date }}</td><td>{{ row.symbol }}</td><td>{{ num(row.value) }}</td><td>{{ pct(row.forward_return) }}</td><td>{{ row.factor_group ?? '—' }}</td><td>{{ row.reason }}</td></tr></tbody></table></div>
            <div class="pagination"><button :disabled="offset === 0 || busy || valuesLoading" @click="emit('page', -100)">上一页</button><span>{{ values.length ? offset + 1 : 0 }}–{{ offset + values.length }}</span><button :disabled="offset + values.length >= result.metadata.value_count || busy || valuesLoading" @click="emit('page', 100)">下一页</button></div>
          </section>
          <section v-else-if="key === 'logs'" class="simulation-logs"><div class="section-title">Task Logs</div><pre>{{ logs || '暂无日志' }}</pre></section>
          <section v-else-if="key === 'source'" class="source-inspector"><div class="section-title"><span>Source snapshot</span><button :disabled="busy || !task?.snapshot?.source_code" @click="emit('load-snapshot')"><UiIcon name="clone" />载入为新模拟</button></div><pre>{{ task?.snapshot?.source_code || '运行后可查看提交时的源码快照' }}</pre><details v-if="result"><summary>参数与复现信息</summary><pre>{{ JSON.stringify({ parameters: task.snapshot, metadata: result.metadata }, null, 2) }}</pre></details></section>
          <p v-else class="report-note">报告完成后可查看此研究区块。</p>
        </template>
      </details>
    </section>
    <div class="results-actions"><span class="badge" :class="statusClass">{{ statusText }}</span><button :disabled="!result" @click="exportResult"><UiIcon name="download" />Export Results</button></div>
    <dialog ref="menu" class="results-menu" aria-labelledby="results-menu-title">
      <h2 id="results-menu-title">Customize Results Menu</h2><p>拖动或使用上下按钮排序；Apply 后保存到当前浏览器。</p>
      <ol><li v-for="(key,index) in pendingOrder" :key="key" draggable="true" @dragstart="dragged = key; $event.dataTransfer.setData('text/plain', key)" @dragover.prevent @drop.prevent="drop(key)"><span>{{ core.find(section => section[0] === key)[2] }}</span><button :disabled="index === 0" :aria-label="`上移 ${core.find(section => section[0] === key)[2]}`" @click="move(key,index-1)">↑</button><button :disabled="index === pendingOrder.length-1" :aria-label="`下移 ${core.find(section => section[0] === key)[2]}`" @click="move(key,index+1)">↓</button></li></ol>
      <div class="menu-actions"><button @click="pendingOrder = core.map(section => section[0])">Reset</button><button @click="menu.close()">Cancel</button><button @click="applyMenu">Apply</button></div>
    </dialog>
  </div>
</template>
<style scoped>
.results-rail { background: #d7e1e7; overflow-y: auto; scrollbar-width: thin; }
.results-rail .nav-button { color: #7b8798; border-bottom: 1px solid #dde4e9; }
.results-rail .nav-button.active { color: #55aa49; box-shadow: none; background: #f7f8fa; }
.customize-button { margin-top: auto; color: #435168; }
.result-panel { position: relative; padding: 0; background: #fff; scroll-padding-top: 0; }
.test-period-banner { display: flex; align-items: center; gap: 15px; padding: 13px 22px; background: #ffcc4e; color: #514c3d; font-size: 12px; line-height: 1.5; }
.test-toggle { flex-shrink: 0; padding: 9px 18px; border: 0; border-radius: 3px; background: #8995a4; color: #fff; font-size: 12px; font-weight: 600; }
.test-toggle:hover { background: #758394; }
.report-section { border-bottom: 1px solid #e3e7eb; padding: 20px 22px 24px; scroll-margin-top: 0; outline-offset: -2px; }
.report-section[data-result-section="chart"], .report-section[data-result-section="correlation"] { background: #f5f7f9; }
.result-panel > .report-note, .result-panel > .empty, .result-panel > .task-error { margin: 20px 22px; }
.results-toolbar { position: static; height: auto; min-height: 30px; margin: 0 0 18px; padding: 0; gap: 12px; background: transparent; }
.results-toolbar h2 { margin: 0; font-size: 20px; font-weight: 400; color: #344052; white-space: nowrap; }
.results-toolbar select { max-width: 300px; width: 52%; font-size: 12px; border-color: #71b7e6; background: #fff; }
.period-control { display: flex; align-items: center; gap: 4px; font-size: 11px; color: #526071; white-space: nowrap; }
.period-control > span { margin-right: 8px; font-weight: 600; }
.period-control button { background: #edf0f3; border: 0; padding: 5px 10px; color: #98a2ad; border-radius: 15px; font-size: 10px; }
.period-control button[aria-pressed=true] { color: #fff; background: #2999d6; }
.period-control button:disabled { color: #c7ced5; }
.pnl-book-note, .chart-meta-note { color: #778594; font-size: 11px; line-height: 1.6; margin: 5px 0; overflow-wrap: anywhere; }
.summary-range { margin: -8px 0 2px; color: #98a3b0; font-size: 10px; text-align: right; }
.chart-metadata { padding-top: 8px; color: #8997a5; font-size: 10px; }
.chart-metadata summary { cursor: pointer; width: fit-content; }
.extensions-heading { padding: 24px 22px 12px; color: #879397; font-size: 11px; letter-spacing: .04em; }
.research-extension { border-bottom: 1px solid #e2e6e9; margin: 0 22px; padding: 15px 0; min-width: 0; }
.research-extension > summary { display: flex; align-items: center; gap: 10px; color: #5d6d7a; font-size: 13px; cursor: pointer; list-style: none; }
.research-extension > summary::after { content: '＋'; margin-left: auto; color: #7b9d69; }
.research-extension[open] > summary::after { content: '−'; }
.results-menu { width: min(450px,calc(100vw - 36px)); border: 1px solid #d6dce1; border-radius: 4px; padding: 22px; color: #5a6877; box-shadow: 0 12px 60px #23334238; }
.results-menu::backdrop { background: #1c293d55; }
.results-menu h2 { margin: 0 0 12px; font-size: 18px; font-weight: 400; }
.results-menu p { font-size: 12px; line-height: 1.6; }
.results-menu ol { list-style: none; padding: 0; }
.results-menu li { display: flex; align-items: center; gap: 8px; padding: 9px 0; border-bottom: 1px solid #e7ebee; cursor: grab; font-size: 13px; }
.results-menu li > span { flex: 1; }
.results-menu li button { padding: 4px 9px; }
.menu-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
@container (max-width: 500px) {
  .results-toolbar { flex-wrap: wrap; align-items: center; }
  .results-toolbar select { width: 55%; font-size: 11px; }
  .period-control { margin-left: auto; }
  .period-control button { padding: 5px 7px; }
  .test-period-banner { flex-wrap: wrap; gap: 7px; }
}
</style>
