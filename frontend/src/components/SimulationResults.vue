<script setup>
import { computed, ref, watch } from "vue";
import UiIcon from "./UiIcon.vue";
import BrainSummary from "./BrainSummary.vue";
import FactorDiagnostics from "./FactorDiagnostics.vue";
import ReportChart from "./ReportChart.vue";
import ResearchChart from "./ResearchChart.vue";
import AlphaTrace from "./AlphaTrace.vue";
const props = defineProps({
  task: Object,
  result: Object,
  logs: String,
  values: Array,
  offset: Number,
  busy: Boolean,
  running: Boolean,
  states: Object,
});
const emit = defineEmits(["load-snapshot", "page"]);
const view = ref("chart"),
  period = ref("1"),
  chartMode = ref("pnl"),
  partition = ref("all");
const periods = computed(() =>
  Object.keys(props.result?.diagnostics?.horizons || {}),
);
const selected = computed(
  () => props.result?.diagnostics?.horizons[period.value],
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
  return { ...props.result.diagnostics, horizons: Object.fromEntries(
    Object.entries(props.result.diagnostics.horizons).map(([key, horizon]) => [key, displayHorizon(horizon)])) };
});
const alpha = computed(() => displayed.value?.portfolios.alpha);
const pnlBook = computed(() => ({ size: alpha.value?.pnl?.book_size ?? displayed.value?.pnl_book?.size ?? 1,
  unit: alpha.value?.pnl?.unit || displayed.value?.pnl_book?.unit || "BOOK" }));
const bookLabel = computed(() => `${Number(pnlBook.value.size).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${pnlBook.value.unit}`);
const dailyPortfolio = computed(() => (props.result?.diagnostics?.version || 0) >= 3);
const visualization = computed(() => props.result?.metadata?.brain_settings?.visualization
  ?? props.task?.snapshot?.brain_settings?.visualization ?? true);
const portfolioDates = computed(() => displayed.value?.portfolio_dates || displayed.value?.portfolio_signal_dates || []);
const summary = computed(() => props.result?.summary || {});
const num = (x) => (x == null ? "--" : Number(x).toFixed(4));
const pct = (x) => (x == null ? "--" : (Number(x) * 100).toFixed(2) + "%");
watch(
  periods,
  (all) => {
    if (!all.includes(period.value)) period.value = all[0] || "1";
  },
  { immediate: true },
);
watch(() => props.result, () => {
  partition.value = hasTest.value ? "train" : "all";
}, { immediate: true });
watch(
  () => props.task?.id,
  () => {
    view.value = "chart";
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
  ["summary", "bars", "Summary"],
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
        ><span class="badge" :class="task?.status">{{
          task ? states[task.status] || task.status : "Ready"
        }}</span>
      </div>
      <template v-if="result">
        <div class="result-context">
          <span class="dataset-chip"
            >{{ result.metadata.synthetic ? "SYNTHETIC DATA" : "CHN EQUITY"
            }}<span> · {{ result.metadata.execution_mode }}</span></span
          >
          <div class="result-periods">
            <span>Period</span
            ><button
              v-for="h in periods"
              :key="h"
              :class="{ active: period === h }"
              @click="period = h"
            >
              {{ h }}D
            </button>
          </div>
        </div>
        <label v-if="hasTest && view !== 'trace'" class="validation-period">
          验证区间
          <select v-model="partition" aria-label="验证区间">
            <option value="train">Train · 训练期</option>
            <option value="test">Test · 测试期</option>
            <option value="all">All · 完整期间</option>
          </select>
          <span>Train/Test 仅统计配置日期内的组合收益；All 保留未来持有收益尾部。原始因子 IC 保留完整研究期间。</span>
        </label>
        <h1 class="result-factor-name">{{ task.name }}</h1>
        <BrainSummary v-if="alpha && view !== 'trace'" :summary="alpha.brain_style" />
        <div class="metrics legacy-metrics" aria-label="一期因子统计">
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
          <div class="section-title">
            <span><UiIcon name="chart" />Chart</span
            ><select v-if="visualization" v-model="chartMode" aria-label="结果曲线">
              <option value="pnl">PnL · 固定 book</option>
              <option value="nav">Net value · 复利净值</option>
              <option value="returns">{{ dailyPortfolio ? 'Daily returns · 日收益' : 'Period returns · 期收益' }}</option>
              <option value="monthly">Monthly returns · 月度收益</option>
              <option value="yearly">Yearly returns · 年度收益</option>
            </select>
          </div>
          <ReportChart
            v-if="selected && visualization"
            :data="displayed"
            :portfolio="alpha"
            :mode="chartMode"
          /><ResearchChart v-else-if="visualization" :daily="result.daily" mode="ic" />
          <p v-else class="report-note" role="status">可视化已由 settings.visualization 关闭。可在 Summary、IC Analysis 和 Groups 查看数值，或导出完整结果。</p>
          <div class="chart-footnote">
            {{ portfolioDates[0] || result.daily[0]?.date }} —
            {{
              portfolioDates.at(-1) ||
              result.daily.at(-1)?.date
            }}<span>{{ dailyPortfolio ? '快照日收益 · 逐日盯市' : '历史报告 · 原任务收益口径' }} · 无费用 · 缺失值 --</span>
          </div>
          <p class="pnl-book-note">固定名义 book：{{ bookLabel }}。PnL 逐日累加，净值另按收益复利；仅名义缩放，无汇兑或实际成交。</p>
        </div>
        <AlphaTrace v-else-if="view === 'trace'" :data="selected" :constraints="result.diagnostics?.alpha_constraints" />
        <FactorDiagnostics
          v-else-if="
            ['summary', 'ic', 'groups'].includes(view) && result.diagnostics
          "
          :key="view"
          :report="displayedReport"
          :initial-period="period"
          @period-change="period = $event"
          :initial-section="
            view === 'summary'
              ? 'brain'
              : view === 'groups'
                ? 'portfolio'
                : 'ic'
          "
        />
        <div
          v-else-if="['summary', 'ic', 'groups'].includes(view)"
          class="legacy-result"
        >
          <p class="report-note">历史任务 · 一期研究结果</p>
          <ResearchChart
            v-if="visualization"
            :daily="result.daily"
            :mode="view === 'groups' ? 'group' : 'ic'"
          />
          <div class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>IC</th>
                  <th>Rank IC</th>
                  <th>Pairs</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="d in result.daily" :key="d.date">
                  <td>{{ d.date }}</td>
                  <td>{{ num(d.ic) }}</td>
                  <td>{{ num(d.rank_ic) }}</td>
                  <td>{{ d.pairs }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <section v-else-if="view === 'values'" class="factor-values">
          <div class="section-title">
            <span>Factor Values</span
            ><small>{{ result.metadata.value_count }} rows</small>
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
              :disabled="offset === 0 || busy"
              @click="emit('page', -100)"
            >
              上一页</button
            ><span>{{ offset + 1 }}–{{ offset + values.length }}</span
            ><button
              :disabled="
                offset + values.length >= result.metadata.value_count || busy
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
        v-if="result && ['chart', 'summary'].includes(view)"
        class="results-method-note"
      >
        研究口径 · BRAIN 官方数值一致性尚未核验。<button
          @click="view = 'source'"
        >
          查看来源与参数
        </button>
      </p>
    </section>
    <div class="results-actions">
      <span>{{
        task?.id ? task.id.slice(0, 8) : "No simulation selected"
      }}</span
      ><button :disabled="!result" @click="exportResult">
        <UiIcon name="download" />Export Results
      </button>
    </div>
  </div>
</template>
<style scoped>
.validation-period { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 0 20px 12px; font-size: 12px; }
.validation-period span { color: #687789; }
.pnl-book-note { color: #718095; font-size: 11px; line-height: 1.8; margin: 12px 0; overflow-wrap: anywhere; }
</style>
