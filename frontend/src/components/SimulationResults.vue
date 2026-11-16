<script setup>
import { computed, nextTick, ref, watch } from "vue";
import UiIcon from "./UiIcon.vue";
import BrainSummary from "./BrainSummary.vue";
import BrainAnnualStats from "./BrainAnnualStats.vue";
import FactorDiagnostics from "./FactorDiagnostics.vue";
import ReportChart from "./ReportChart.vue";
import AlphaTrace from "./AlphaTrace.vue";
const props = defineProps({
  task: Object,
  result: Object,
  logs: String,
  values: Array,
  offset: Number,
  busy: Boolean,
  running: Boolean,
  submitting: Boolean,
  reportLoading: Boolean,
  reportError: String,
  valuesLoading: Boolean,
  valuesError: String,
  states: Object,
});
const emit = defineEmits(["load-snapshot", "page", "retry-report", "load-values", "load-logs"]);
const view = ref("chart"),
  chartMode = ref("pnl"),
  partition = ref("all"),
  showTest = ref(false),
  annualOpen = ref(true),
  panel = ref(null),
  chartSection = ref(null),
  summarySection = ref(null);
const reportView = computed(() => ["chart", "summary"].includes(view.value));
const selected = computed(
  () => props.result?.diagnostics?.horizons?.["1"],
);
const hasTest = computed(() => Boolean(props.result?.diagnostics?.test_period?.start));
function displayHorizon(horizon) {
  const part = horizon?.partitions?.[partition.value];
  if (!hasTest.value || partition.value === "all") return horizon;
  if (!part) return null;
  return { ...horizon, portfolio_dates: part.dates,
    portfolios: { ...part.portfolios, alpha: part.alpha } };
}
const displayed = computed(() => displayHorizon(selected.value));
const displayedReport = computed(() => {
  if (!props.result?.diagnostics) return null;
  return { ...props.result.diagnostics, horizons: { "1": displayed.value } };
});
const supportedReport = computed(() => props.result?.metadata?.report_version === 3
  && props.result?.diagnostics?.version === 3 && props.result.metadata.brain_settings
  && props.result.diagnostics.conventions?.brain_settings);
const alpha = computed(() => displayed.value?.portfolios?.alpha);
const fullAlpha = computed(() => selected.value?.portfolios?.alpha);
const chartData = computed(() => hasTest.value && !showTest.value
  ? displayHorizon(selected.value) : selected.value);
const chartAlpha = computed(() => chartData.value?.portfolios?.alpha);
const annualRows = computed(() => hasTest.value && !showTest.value
  ? selected.value?.partitions?.train?.alpha?.yearly_stats
  : fullAlpha.value?.yearly_stats);
const pnlBook = computed(() => ({ size: chartAlpha.value?.pnl?.book_size,
  unit: chartAlpha.value?.pnl?.unit }));
const bookLabel = computed(() => `${Number(pnlBook.value.size).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${pnlBook.value.unit}`);
const visualization = computed(() => props.result?.metadata?.brain_settings?.visualization);
const portfolioDates = computed(() => chartData.value?.portfolio_dates || []);
const summary = computed(() => props.result?.summary || {});
const statusText = computed(() => {
  if (props.submitting) return "正在提交";
  if (props.task?.status === "SUCCEEDED" && !props.result)
    return props.reportError ? "报告加载失败" : "正在加载报告";
  return props.task ? props.states[props.task.status] || props.task.status : "Ready";
});
const statusClass = computed(() => props.submitting ? "QUEUED"
  : props.task?.status === "SUCCEEDED" && !props.result
    ? props.reportError ? "FAILED" : "RUNNING" : props.task?.status);
const num = (x) => (x == null ? "--" : Number(x).toFixed(4));
const pct = (x) => (x == null ? "--" : (Number(x) * 100).toFixed(2) + "%");
watch(() => props.result, () => {
  showTest.value = false;
  annualOpen.value = true;
  partition.value = hasTest.value ? "train" : "all";
  if (props.result && view.value === "values") emit("load-values");
}, { immediate: true });
watch(view, value => {
  if (value === "values" && props.result) emit("load-values");
  if (value === "logs") emit("load-logs");
});
watch(
  () => props.task?.id,
  () => {
    view.value = "chart";
    chartMode.value = "pnl";
  },
);
async function navigate(key) {
  view.value = key;
  if (key === "chart" || key === "summary") {
    await nextTick();
    (key === "chart" ? chartSection.value : summarySection.value)?.scrollIntoView({ behavior: "smooth", block: "start" });
  } else if (panel.value) panel.value.scrollTop = 0;
}
function trackSection() {
  if (!reportView.value || !chartSection.value || !summarySection.value || !panel.value) return;
  const top = panel.value.getBoundingClientRect().top;
  view.value = summarySection.value.getBoundingClientRect().top <= top + 90 ? "summary" : "chart";
}
function toggleTest() {
  showTest.value = !showTest.value;
  partition.value = showTest.value && selected.value?.partitions?.test ? "test" : "train";
}
function exportResult() {
  const blob = new Blob(
    [JSON.stringify({ task: props.task, result: props.result }, null, 2)],
    { type: "application/json" },
  );
  const url = URL.createObjectURL(blob),
    link = document.createElement("a");
  link.href = url;
  link.download = `factorpool-${props.task.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
const sections = [
  ["chart", "chart", "Chart"],
  ["summary", "bars", "Stats"],
  ["ic", "scatter", "IC Analysis"],
  ["groups", "table", "Groups"],
  ["trace", "table", "Weights / PnL"],
  ["values", "table", "Factor Values"],
  ["logs", "logs", "Logs"],
  ["source", "code", "Source"],
];
</script>
<template>
  <div class="results-workspace">
    <aside class="results-rail" aria-label="结果导航">
      <button
        v-for="[key, icon, label] in sections"
        :key="key"
        :class="{ active: view === key }"
        :aria-label="label"
        :title="label"
        @click="navigate(key)"
      >
        <UiIcon :name="icon" :size="19" />
      </button>
    </aside>
    <section ref="panel" class="result-panel" @scroll.passive="trackSection">
      <div v-if="!reportView || !result" class="results-toolbar">
        <span
          ><UiIcon
            :name="sections.find((s) => s[0] === view)?.[1] || 'chart'"
          /><strong>{{
            sections.find((s) => s[0] === view)?.[2]
          }}</strong></span
        >
      </div>
      <div v-if="submitting" class="empty" role="status">
        <UiIcon name="refresh" :size="42" />
        <h2>正在提交任务</h2>
        <p>提交成功后会自动计算并加载报告。</p>
      </div>
      <template v-else-if="result && supportedReport && selected">
        <div v-if="reportView && hasTest" class="test-period-notice">
          <button @click="toggleTest">{{ showTest ? 'Hide test period' : 'Show test period' }}</button>
          <span>指定测试期时，测试期及整体统计默认隐藏。</span>
        </div>
        <template v-if="reportView">
          <section ref="chartSection" class="brain-report-section simulation-chart-panel" aria-label="Chart">
            <div class="report-section-heading">
              <h2><UiIcon name="chart" />Chart</h2>
              <select v-if="visualization" v-model="chartMode" aria-label="结果曲线">
                <optgroup label="Performance">
                  <option value="pnl">PnL</option>
                  <option value="sharpe" :disabled="!chartAlpha?.sharpe_series?.length">Sharpe</option>
                  <option value="turnover" :disabled="!chartAlpha?.turnover_series?.length">Turnover</option>
                </optgroup>
                <optgroup label="FactorPool 扩展">
                  <option value="nav">Net value · 复利净值</option>
                  <option value="returns">Daily returns · 日收益</option>
                  <option value="monthly">Monthly returns · 月度收益</option>
                  <option value="yearly">Yearly returns · 年度收益</option>
                </optgroup>
              </select>
            </div>
            <ReportChart v-if="chartData && visualization" :data="chartData" :portfolio="chartAlpha" :mode="chartMode"
              :test-start="showTest ? result.diagnostics?.test_period?.start : null" />
            <p v-else class="report-note" role="status">{{ visualization ? '当前区间没有图表数据。' : '可视化已由 settings.visualization 关闭。' }}</p>
            <div class="chart-footnote">{{ portfolioDates[0] || '--' }} — {{ portfolioDates.at(-1) || '--' }}
              <span>快照日收益 · 逐日盯市 · 无费用 · 缺失值 --</span>
            </div>
            <p v-if="chartMode === 'nav'" class="pnl-book-note">初始净值 1，按比例权重计算收益并逐日复利；持有期内权重随价格自然漂移。</p>
            <p v-else-if="chartMode === 'pnl' && chartAlpha?.pnl" class="pnl-book-note">固定 book：{{ bookLabel }} · PnL 逐日累加 · 1D 收盘计算</p>
          </section>
          <section ref="summarySection" class="brain-report-section" aria-label="IS Summary">
            <div class="report-section-heading summary-heading">
              <h2><UiIcon name="bars" />IS Summary</h2>
              <div class="period-buttons" aria-label="验证区间">
                <span>Period</span>
                <button v-if="hasTest" :class="{ active: partition === 'train' }" :disabled="!selected?.partitions?.train" @click="partition = 'train'">TRAIN</button>
                <button v-if="hasTest" :class="{ active: partition === 'test' }" :disabled="!showTest || !selected?.partitions?.test" @click="partition = 'test'">TEST</button>
                <button :class="{ active: partition === 'all' }" :disabled="hasTest && !showTest" @click="partition = 'all'">IS</button>
                <button disabled title="当前报告没有样本外数据">OS</button>
              </div>
            </div>
            <BrainSummary :summary="alpha?.brain_style" />
            <button class="annual-toggle" :aria-expanded="annualOpen" @click="annualOpen = !annualOpen">{{ annualOpen ? '收起年度统计' : '展开年度统计' }}</button>
            <BrainAnnualStats v-if="annualOpen" :rows="annualRows" :test-start="showTest ? result.diagnostics?.test_period?.start : null" />
          </section>
        </template>
        <div v-if="view === 'ic'" class="metrics factor-metrics" aria-label="一期因子统计">
          <div
            v-for="[label, value] in [
              ['1D IC', num(summary.ic_mean)],
              ['1D Rank IC', num(summary.rank_ic_mean)],
              ['1D ICIR', num(summary.icir)],
              ['Coverage', pct(summary.coverage)],
            ]"
            :key="label"
          >
            <span>{{ label }}</span
            ><strong>{{ value }}</strong>
          </div>
        </div>
        <AlphaTrace v-if="view === 'trace'" :data="selected" :constraints="result.diagnostics?.alpha_constraints" />
        <FactorDiagnostics
          v-else-if="
            ['ic', 'groups'].includes(view)
          "
          :key="view"
          :report="displayedReport"
          :initial-section="
            view === 'groups' ? 'portfolio' : 'ic'
          "
        />
        <section v-else-if="view === 'values'" class="factor-values">
          <div class="section-title">
            <span>Factor Values</span
            ><small>{{ result.metadata.value_count }} rows</small>
          </div>
          <p v-if="valuesLoading" class="report-note" role="status">正在加载因子值…</p>
          <div v-if="valuesError" class="report-load-error">
            <p role="alert">{{ valuesError }}</p>
            <button aria-label="重试因子值" :disabled="valuesLoading" @click="emit('load-values')">重试因子值</button>
          </div>
          <div class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Symbol</th>
                  <th>Factor</th>
                  <th>Return · 1D</th>
                  <th>Group</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in values" :key="i">
                  <td>{{ r.trading_date }}</td>
                  <td>{{ r.symbol }}</td>
                  <td>{{ num(r.value) }}</td>
                  <td>{{ pct(r.forward_return) }}</td>
                  <td>{{ r.factor_group ?? "--" }}</td>
                  <td>{{ r.reason }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="pagination">
            <button
              :disabled="offset === 0 || busy || valuesLoading"
              @click="emit('page', -100)"
            >
              上一页</button
            ><span>{{ offset + 1 }}–{{ offset + values.length }}</span
            ><button
              :disabled="
                offset + values.length >= result.metadata.value_count || busy || valuesLoading
              "
              @click="emit('page', 100)"
            >
              下一页
            </button>
          </div>
        </section>
        <section v-else-if="view === 'source'" class="source-inspector">
          <div class="section-title">
            <span>Source snapshot</span
            ><button :disabled="busy" @click="emit('load-snapshot')">
              <UiIcon name="clone" />载入为新模拟
            </button>
          </div>
          <pre>{{ task.snapshot?.source_code }}</pre>
          <details>
            <summary>参数与复现信息</summary>
            <pre>{{
              JSON.stringify(
                { parameters: task.snapshot, metadata: result.metadata },
                null,
                2,
              )
            }}</pre>
          </details>
        </section>
      </template>
      <p v-else-if="result && supportedReport" class="report-note" role="alert">此任务没有 1D 报告；请使用 analysis.horizons = [1] 运行。其他周期仍可通过接口读取。</p>
      <p v-else-if="result" class="report-note" role="alert">此结果格式已不再支持，请使用当前脚本重新运行。</p>
      <div v-else-if="task?.status === 'SUCCEEDED' && !['logs', 'source'].includes(view)" class="empty" role="status">
        <UiIcon :name="reportError ? 'logs' : 'refresh'" :size="42" />
        <h2>{{ reportError ? '报告加载失败' : '计算已完成，正在加载报告' }}</h2>
        <p v-if="reportError" role="alert">{{ reportError }}</p>
        <p v-else>正在读取本次结果，完成后会自动显示。</p>
        <button v-if="reportError" aria-label="重试报告" :disabled="reportLoading" @click="emit('retry-report')">重试报告</button>
      </div>
      <div v-else-if="['FAILED', 'INTERRUPTED', 'CANCELLED'].includes(task?.status) && !['logs', 'source'].includes(view)" class="empty" role="status">
        <UiIcon name="logs" :size="42" />
        <h2>{{ states[task.status] || task.status }}</h2>
        <button @click="view = 'logs'">查看任务日志</button>
      </div>
      <div v-else-if="view !== 'logs' && view !== 'source'" class="empty">
        <UiIcon :name="running ? 'refresh' : 'chart'" :size="42" />
        <h2>
          {{ running ? "Simulation in progress" : "Run your first simulation" }}
        </h2>
        <p>
          {{
            running
              ? "正在准备并计算本次研究，进度和错误会显示在日志中。"
              : "使用 PythonIndicator 编写因子，设置股票池和日期，然后点击 Simulate。"
          }}
        </p>
        <small v-if="!task">同一份 Indicator 定义，统一的 TS / CS 计算</small>
      </div>
      <p v-if="task?.error" class="task-error" role="alert">{{ task.error }}</p>
      <section v-if="view === 'logs'" class="simulation-logs">
        <div class="section-title">Task Logs</div>
        <pre>{{ logs || "暂无日志" }}</pre>
      </section>
      <section v-if="view === 'source' && !result" class="source-inspector">
        <pre>{{
          task?.snapshot?.source_code || "运行后可查看提交时的源码快照"
        }}</pre>
      </section>
      <p
        v-if="result && supportedReport && ['chart', 'summary'].includes(view)"
        class="results-method-note"
      >
        <button
          @click="view = 'source'"
        >
          查看来源与参数
        </button>
      </p>
    </section>
    <div class="results-actions">
      <span class="badge" :class="statusClass">{{ statusText }}</span
      ><button :disabled="!result" @click="exportResult">
        <UiIcon name="download" />Export Results
      </button>
    </div>
  </div>
</template>
<style scoped>
.test-period-notice { display: flex; align-items: center; gap: 12px; margin: 0 -22px; padding: 12px 22px; background: #ffdb75; color: #5f5541; font-size: 12px; }
.test-period-notice button { flex-shrink: 0; background: #7a8698; color: white; border: 0; padding: 8px 12px; }
.brain-report-section { padding: 18px 0 26px; border-bottom: 1px solid #e1e5ec; scroll-margin-top: 0; }
.report-section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.report-section-heading h2 { display: flex; align-items: center; gap: 8px; margin: 0; color: #4a586c; font-size: 19px; font-weight: 400; white-space: nowrap; }
.report-section-heading h2 svg { color: #69b756; }
.report-section-heading select { width: 48%; max-width: 260px; font-size: 12px; border-color: #b4d2eb; }
.period-buttons { display: flex; align-items: center; gap: 4px; font-size: 11px; color: #687789; }
.period-buttons button { border: 0; border-radius: 14px; padding: 5px 9px; color: #8793a4; background: #e9edf2; font-size: 10px; }
.period-buttons button.active { background: #319bd4; color: white; }
.period-buttons button:disabled { opacity: .4; cursor: not-allowed; }
.annual-toggle { margin-top: 10px; padding: 4px 0; border: 0; background: transparent; color: #6e9aac; font-size: 11px; }
.pnl-book-note { color: #718095; font-size: 11px; line-height: 1.8; margin: 12px 0; overflow-wrap: anywhere; }
@container (max-width: 500px) {
  .test-period-notice, .summary-heading { flex-wrap: wrap; }
  .period-buttons { flex-wrap: wrap; }
}
</style>
