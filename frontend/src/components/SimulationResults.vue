<script setup>
import { computed, ref, watch } from "vue";
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
  partition = ref("all");
const selected = computed(
  () => props.result?.diagnostics?.horizons?.["1"],
);
const hasTest = computed(() => Boolean(props.result?.diagnostics?.test_period?.start));
function displayHorizon(horizon) {
  const part = horizon?.partitions?.[partition.value];
  if (!hasTest.value || partition.value === "all" || !part) return horizon;
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
const alpha = computed(() => displayed.value?.portfolios.alpha);
const pnlBook = computed(() => ({ size: alpha.value?.pnl?.book_size,
  unit: alpha.value?.pnl?.unit }));
const bookLabel = computed(() => `${Number(pnlBook.value.size).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${pnlBook.value.unit}`);
const visualization = computed(() => props.result?.metadata?.brain_settings?.visualization);
const portfolioDates = computed(() => displayed.value?.portfolio_dates || []);
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
const summaryTitle = computed(() => !hasTest.value || partition.value === "all"
  ? "IS Summary" : partition.value === "train" ? "Train Summary" : "Test Summary");
const num = (x) => (x == null ? "--" : Number(x).toFixed(4));
const pct = (x) => (x == null ? "--" : (Number(x) * 100).toFixed(2) + "%");
watch(() => props.result, () => {
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
        @click="view = key"
      >
        <UiIcon :name="icon" :size="19" />
      </button>
    </aside>
    <section class="result-panel">
      <div class="results-toolbar">
        <span
          ><UiIcon
            :name="sections.find((s) => s[0] === view)?.[1] || 'chart'"
          /><strong>{{
            sections.find((s) => s[0] === view)?.[2]
          }}</strong></span
        ><select v-if="view === 'chart' && result && visualization" v-model="chartMode" aria-label="结果曲线">
          <option value="pnl">PnL</option>
          <option value="nav">Net value · 复利净值</option>
          <option value="returns">Daily returns · 日收益</option>
          <option value="monthly">Monthly returns · 月度收益</option>
          <option value="yearly">Yearly returns · 年度收益</option>
        </select>
      </div>
      <div v-if="submitting" class="empty" role="status">
        <UiIcon name="refresh" :size="42" />
        <h2>正在提交任务</h2>
        <p>提交成功后会自动计算并加载报告。</p>
      </div>
      <template v-else-if="result && supportedReport && selected">
        <label v-if="hasTest && view !== 'trace'" class="validation-period">
          Period
          <select v-model="partition" aria-label="验证区间">
            <option value="train">Train · 训练期</option>
            <option value="test">Test · 测试期</option>
            <option value="all">All · 完整期间</option>
          </select>
        </label>
        <h2 v-if="view === 'summary'" class="summary-title">{{ summaryTitle }}</h2>
        <BrainSummary v-if="alpha && view === 'summary'" :summary="alpha.brain_style" />
        <BrainAnnualStats v-if="view === 'summary'" :rows="alpha?.yearly_stats" />
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
        <div v-if="view === 'chart'" class="simulation-chart-panel">
          <ReportChart
            v-if="selected && visualization"
            :data="displayed"
            :portfolio="alpha"
            :mode="chartMode"
          />
          <p v-else class="report-note" role="status">可视化已由 settings.visualization 关闭。可在 Stats、IC Analysis 和 Groups 查看数值，或导出完整结果。</p>
          <div class="chart-footnote">
            {{ portfolioDates[0] }} —
            {{
              portfolioDates.at(-1)
            }}<span>快照日收益 · 逐日盯市 · 无费用 · 缺失值 --</span>
          </div>
          <p v-if="chartMode === 'nav'" class="pnl-book-note">初始净值 1，按比例权重计算收益并逐日复利；持有期内权重随价格自然漂移。</p>
          <p v-else-if="chartMode === 'pnl'" class="pnl-book-note">固定 book：{{ bookLabel }} · PnL 逐日累加 · 1D 收盘计算</p>
        </div>
        <AlphaTrace v-else-if="view === 'trace'" :data="selected" :constraints="result.diagnostics?.alpha_constraints" />
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
.validation-period { display: flex; align-items: center; gap: 12px; padding: 16px 0 4px; font-size: 12px; color: #687789; }
.summary-title { margin: 24px 0 20px; color: #4a586c; font-size: 18px; font-weight: 400; }
.pnl-book-note { color: #718095; font-size: 11px; line-height: 1.8; margin: 12px 0; overflow-wrap: anywhere; }
</style>
