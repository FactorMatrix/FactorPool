<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount, nextTick } from "vue";
import * as echarts from "echarts";
const props = defineProps({
  data: Object,
  mode: String,
  metric: { type: String, default: "rank_ic" },
  portfolio: Object,
  showTest: Boolean,
  trainDates: Array,
  testDates: Array,
});
const host = ref(null), labels = ref([]), emptyReason = ref(""), rendering = ref(false);
const range = ref({ start: 0, end: 100 }), selecting = ref(false);
let chart, observer;
const colors = { Train: "#18bed0", Test: "#df8d32", IS: "#8491a2" };
const performance = computed(() => Boolean(props.portfolio) && ["pnl", "turnover", "sharpe"].includes(props.mode));
const zoomable = computed(() => labels.value.length > 1 && !emptyReason.value);
const zoomed = computed(() => range.value.start > 0.01 || range.value.end < 99.99);
const rangeLabel = computed(() => {
  if (!labels.value.length) return "";
  const last = labels.value.length - 1;
  return `${labels.value[Math.round(range.value.start * last / 100)]} — ${labels.value[Math.round(range.value.end * last / 100)]}`;
});
const finite = value => typeof value === "number" && Number.isFinite(value);
const numeric = values => values.map(value => finite(value) ? value : null);
const compactAmount = value => {
  const scale = Math.abs(value) >= 1e6 ? 1e6 : Math.abs(value) >= 1e3 ? 1e3 : 1;
  return `${(value / scale).toLocaleString("en-US", { maximumFractionDigits: 2 })}${scale === 1e6 ? "M" : scale === 1e3 ? "K" : ""}`;
};
const escapeHtml = value => String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const train = computed(() => new Set(props.trainDates || []));
const test = computed(() => new Set(props.testDates || []));
function period(date) {
  if (train.value.has(date)) return "Train";
  if (test.value.has(date)) return "Test";
  // Full-report holding returns can extend outside both evaluation partitions.
  return train.value.size || test.value.size ? "IS" : "";
}
function formatValue(value, kind, unit = "") {
  if (!finite(value)) return "—";
  if (kind === "percent") return `${(value * 100).toFixed(2)}%`;
  if (kind === "amount") return `${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${escapeHtml(unit || "单位未提供")}`;
  return value.toFixed(kind === "sharpe" ? 2 : 4);
}
function axisTooltip(kind, unit, segmented = false) {
  return rows => {
    const points = Array.isArray(rows) ? rows : [rows];
    const date = points[0]?.axisValueLabel || "";
    return [escapeHtml(date), ...points.map(point => {
      const name = `${segmented && period(date) ? `${period(date)} ` : ""}${point.seriesName || ""}`;
      const pointKind = point.seriesName === "净值回撤" ? "percent" : kind;
      return `${point.marker || ""}${escapeHtml(name)}: ${formatValue(point.value, pointKind, unit)}`;
    })].join("<br/>");
  };
}
function line(name, values, length) {
  return { name, type: "line", showSymbol: length < 20, connectNulls: false,
    lineStyle: { width: 1.6 }, data: numeric(values) };
}
function partitionColors(dates) {
  const pieces = [];
  let start = 0;
  for (let i = 1; i <= dates.length; i++) {
    if (i < dates.length && period(dates[i]) === period(dates[start])) continue;
    pieces.push({ gte: start, ...(i < dates.length ? { lt: i } : { lte: i - 1 }),
      color: colors[period(dates[start])] || colors.Train });
    start = i;
  }
  return pieces;
}
function readRange(event = {}) {
  const changed = event.batch?.[0] || event;
  if (finite(changed.start) && finite(changed.end)) {
    range.value = { start: changed.start, end: changed.end };
    return;
  }
  const zoom = chart?.getOption().dataZoom?.find(item => item.type === "inside");
  if (zoom && finite(zoom.start) && finite(zoom.end)) range.value = { start: zoom.start, end: zoom.end };
}
function applyRange(start, end) {
  if (!chart || !zoomable.value) return;
  chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 0, start, end });
  range.value = { start, end };
}
function resizeRange(factor) {
  const span = Math.min(100, Math.max(100 / (labels.value.length - 1), (range.value.end - range.value.start) * factor));
  const start = Math.max(0, Math.min(100 - span, (range.value.start + range.value.end - span) / 2));
  applyRange(start, start + span);
}
function toggleSelection() {
  if (!chart) return;
  chart.dispatchAction({ type: "takeGlobalCursor", key: "dataZoomSelect", dataZoomSelectActive: !selecting.value });
}
function resetRange() {
  applyRange(0, 100);
  if (selecting.value) toggleSelection();
}
function draw() {
  if (!chart) return;
  rendering.value = true;
  emptyReason.value = "";
  range.value = { start: 0, end: 100 };
  selecting.value = false;
  labels.value = [];
  const d = props.data, p = props.portfolio, k = props.metric;
  const axis = { type: "value", scale: true, axisLabel: { color: "#8491a2" },
    splitLine: { lineStyle: { color: "#e3e8ef", width: 0.7 } } };
  const option = {
    animation: false, backgroundColor: performance.value ? "#f7f9fb" : "transparent",
    color: [colors.Train, colors.Test, "#74b96a"],
    tooltip: { trigger: "axis", confine: true, formatter: axisTooltip("number") },
    legend: { top: 0, textStyle: { color: "#8491a2" }, itemWidth: 18, itemHeight: 8 },
    grid: { left: 60, right: 24, top: 40, bottom: 40 },
    xAxis: { type: "category", boundaryGap: false, axisLabel: { color: "#8491a2", hideOverlap: true }, axisLine: { lineStyle: { color: "#d7dee7" } } },
    yAxis: axis, series: [],
  };
  let dates = [], timeSeries = false, segmented = false;
  const unavailable = message => { emptyReason.value = message; };
  const useSeries = (values, name, kind, unit = "", type = "line") => {
    dates = Array.isArray(d?.portfolio_dates) ? d.portfolio_dates : [];
    timeSeries = true;
    if (!Array.isArray(values) || !dates.length) { unavailable(`暂无 ${name} 时间序列`); return; }
    if (values.length !== dates.length) { unavailable(`${name} 时间序列与日期轴不一致`); return; }
    if (!values.some(finite)) { unavailable(`暂无有效 ${name} 数据`); return; }
    option.xAxis.data = dates;
    option.legend = { show: false };
    option.yAxis = { ...axis, scale: type === "line", name: unit,
      axisLabel: { color: "#8491a2", ...(kind === "percent" ? { formatter: value => `${(value * 100).toFixed(0)}%` } : kind === "amount" ? { formatter: compactAmount } : {}) } };
    option.series = [{ ...line(name, values, dates.length), type }];
    segmented = performance.value;
    option.tooltip.formatter = axisTooltip(kind, unit, segmented);
  };
  if (!d) unavailable("暂无图表数据");
  else if (props.mode === "monthly") {
    const rows = (p ? p.monthly : d.monthly?.[k]) || [];
    const years = [...new Set(rows.map(row => row.period.slice(0, 4)))].sort();
    const values = rows.filter(row => finite(p ? row.value : row.mean)).map(row => [
      Number(row.period.slice(5)) - 1, years.indexOf(row.period.slice(0, 4)), p ? row.value : row.mean]);
    if (!values.length) unavailable("暂无有效月度数据");
    const extent = Math.max(...values.map(row => Math.abs(row[2])), 0.000001);
    Object.assign(option, {
      tooltip: { confine: true, formatter: item => `${escapeHtml(`${years[item.value[1]]}-${String(item.value[0] + 1).padStart(2, "0")}`)}: ${formatValue(item.value[2], p ? "percent" : "number")}` },
      legend: { show: false }, grid: { left: 60, right: 20, top: 20, bottom: 65 },
      xAxis: { type: "category", data: Array.from({ length: 12 }, (_, i) => `${i + 1}月`), axisLabel: { color: "#8491a2" } },
      yAxis: { type: "category", data: years, axisLabel: { color: "#8491a2" } },
      visualMap: { min: -extent, max: extent, orient: "horizontal", left: "center", bottom: 0,
        textStyle: { color: "#8491a2" }, inRange: { color: ["#93c7af", "#f3f6f8", "#d99191"] } },
      series: [{ type: "heatmap", data: values, label: { show: values.length < 25,
        formatter: item => formatValue(item.value[2], p ? "percent" : "number") }, itemStyle: { borderColor: "#fafbfc", borderWidth: 2 } }],
    });
  } else if (p) {
    if (props.mode === "pnl") useSeries(p.pnl?.cumulative, "PnL", "amount", typeof p.pnl?.unit === "string" ? p.pnl.unit : "");
    else if (props.mode === "turnover") useSeries(p.turnover_series, "Turnover", "percent");
    else if (props.mode === "sharpe") useSeries(p.sharpe_series, "Sharpe", "sharpe");
    else if (props.mode === "drawdown") useSeries(p.drawdown_series, "净值回撤", "percent");
    else if (props.mode === "yearly") {
      const rows = p.yearly || [];
      option.xAxis.data = rows.map(row => row.period); option.xAxis.boundaryGap = true;
      option.yAxis = { ...axis, scale: false, axisLabel: { color: "#8491a2", formatter: value => `${(value * 100).toFixed(0)}%` } };
      option.tooltip.formatter = axisTooltip("percent");
      option.series = [{ name: "逐日归属年度收益", type: "bar", data: numeric(rows.map(row => row.value)) }];
      if (!rows.some(row => finite(row.value))) unavailable("暂无有效年度数据");
    } else if (props.mode === "nav") {
      useSeries(p.nav, "复利净值", "number");
      if (!emptyReason.value) {
        dates = ["初始", ...dates]; option.xAxis.data = dates;
        option.series[0].data = [1, ...option.series[0].data];
        if (Array.isArray(p.drawdown_series) && p.drawdown_series.length === dates.length - 1) {
          option.grid.right = 60;
          option.legend = { top: 0, textStyle: { color: "#8491a2" } };
          option.yAxis = [axis, { ...axis, splitLine: { show: false }, axisLabel: { color: "#a6777e", formatter: value => `${(value * 100).toFixed(0)}%` } }];
          option.series.push({ ...line("净值回撤", [0, ...p.drawdown_series], dates.length), yAxisIndex: 1, areaStyle: { opacity: 0.12 } });
        }
      }
    } else useSeries(p.returns, "日收益", "percent", "", "bar");
  } else if (["pnl", "turnover", "sharpe", "nav", "returns", "drawdown"].includes(props.mode)) unavailable("暂无组合时间序列");
  else if (props.mode === "yearly") {
    const rows = d.yearly?.[k] || [];
    option.xAxis.data = rows.map(row => row.period); option.xAxis.boundaryGap = true;
    option.series = [{ name: "年度均值", type: "bar", data: numeric(rows.map(row => row.mean)) }];
    if (!rows.some(row => finite(row.mean))) unavailable("暂无有效年度数据");
  } else if (props.mode === "decay") {
    const rows = d.decay || [];
    option.xAxis.data = rows.map(row => `${row.entry_lag}期`); option.xAxis.boundaryGap = true;
    option.series = [{ name: "IC", type: "bar", data: numeric(rows.map(row => row.ic?.mean)) },
      { name: "Rank IC", type: "bar", data: numeric(rows.map(row => row.rank_ic?.mean)) }];
    if (!rows.some(row => finite(row.ic?.mean) || finite(row.rank_ic?.mean))) unavailable("暂无有效衰减数据");
  } else {
    dates = Array.isArray(d.dates) ? d.dates : [];
    option.xAxis.data = dates;
    const values = d.series?.[props.mode === "cumulative" ? `${k}_cumulative` : k];
    if (!Array.isArray(values) || values.length !== dates.length || !values.some(finite)) unavailable("暂无有效 IC 时间序列");
    else {
      option.series = [line(props.mode === "cumulative" ? "累计IC（求和）" : k === "ic" ? "IC" : "Rank IC", values, dates.length)];
      const rolling = d.series?.[`${k}_rolling30`];
      if (props.mode !== "cumulative" && Array.isArray(rolling) && rolling.length === dates.length)
        option.series.push(line("30期滚动均值", rolling, dates.length));
    }
  }
  if (timeSeries && dates.length > 1 && !emptyReason.value) {
    labels.value = dates;
    option.grid.bottom = 80;
    option.grid.top = Math.max(option.grid.top, 36);
    option.dataZoom = [
      { type: "inside", xAxisIndex: 0, start: 0, end: 100, filterMode: "filter", zoomOnMouseWheel: "ctrl", moveOnMouseWheel: false, moveOnMouseMove: true },
      { type: "slider", xAxisIndex: 0, start: 0, end: 100, bottom: 16, height: 20,
        showDataShadow: false, brushSelect: true, borderColor: "#d9e0e9", fillerColor: "rgba(24,190,208,.10)",
        handleStyle: { color: "#f8fafb", borderColor: "#8aa5ad" }, textStyle: { color: "#718095" } },
    ];
    option.toolbox = { top: 0, right: 12, itemSize: 14, feature: {
      dataZoom: { xAxisIndex: 0, yAxisIndex: "none", title: { zoom: "框选缩放", back: "返回上次范围" } },
    } };
  }
  if (segmented && props.showTest && dates.length && !emptyReason.value) {
    const pieces = partitionColors(dates);
    if (pieces.length > 1) option.visualMap = { type: "piecewise", show: false, dimension: 0, seriesIndex: 0,
      outOfRange: { color: colors.IS }, pieces };
    else if (pieces.length) option.series[0].itemStyle = { color: pieces[0].color };
  }
  chart.setOption(option, true);
  if (option.dataZoom) chart.dispatchAction({ type: "takeGlobalCursor", key: "dataZoomSelect", dataZoomSelectActive: false });
}
onMounted(() => {
  chart = echarts.init(host.value);
  chart.on("finished", () => { rendering.value = false; });
  chart.on("datazoom", readRange);
  chart.on("globalcursortaken", event => { selecting.value = event.key === "dataZoomSelect" && Boolean(event.dataZoomSelectActive); });
  observer = new ResizeObserver(() => { if (!chart.isDisposed()) chart.resize(); });
  observer.observe(host.value);
  draw();
});
watch(() => [props.data, props.mode, props.metric, props.portfolio, props.showTest, props.trainDates, props.testDates], async () => {
  await nextTick();
  draw();
});
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose(); });
</script>
<template>
  <div class="report-chart-shell">
    <div class="report-chart" :class="{ 'brain-chart': performance }" ref="host" role="img" :aria-label="`${mode || metric} 图表`"></div>
    <p v-if="rendering" class="chart-loading" role="status">正在绘制图表…</p>
    <p v-if="emptyReason" class="chart-empty" role="status">{{ emptyReason }}</p>
    <div v-if="zoomable" class="chart-range-controls" aria-label="图表时间范围">
      <span aria-live="polite">{{ rangeLabel }}</span>
      <div class="range-buttons" role="group" aria-label="缩放控制">
        <button type="button" @click="toggleSelection" :aria-pressed="selecting" aria-label="框选缩放" title="框选缩放">框选</button>
        <button type="button" @click="resizeRange(0.5)" :disabled="range.end - range.start <= 100 / (labels.length - 1)" aria-label="缩小时间范围" title="缩小时间范围">＋</button>
        <button type="button" @click="resizeRange(2)" :disabled="!zoomed" aria-label="放大时间范围" title="放大时间范围">−</button>
        <button type="button" @click="resetRange" :disabled="!zoomed && !selecting" aria-label="恢复完整区间">显示全部</button>
      </div>
    </div>
    <p v-if="zoomable && selecting" class="selection-hint" role="status">拖动图内选择时间区间；也可拖动下方范围手柄。</p>
  </div>
</template>
<style scoped>
.report-chart-shell { position: relative; min-width: 0; }
.report-chart { height: 310px; width: 100%; min-width: 0; }
.report-chart.brain-chart { height: 520px; }
.chart-loading { position: absolute; top: 8px; left: 16px; color: #718095; font-size: 12px; pointer-events: none; }
.chart-empty { position: absolute; top: 44%; left: 24px; right: 24px; margin: 0; text-align: center; color: #8491a2; font-size: 13px; pointer-events: none; }
.chart-range-controls { display: flex; gap: 12px; align-items: center; justify-content: space-between; padding: 6px 0; color: #718095; font-size: 11px; font-variant-numeric: tabular-nums; flex-wrap: wrap; }
.range-buttons { display: flex; gap: 4px; margin-left: auto; }
.range-buttons button { border: 1px solid #dce3e9; background: #fff; color: #5f7580; min-width: 28px; min-height: 28px; padding: 3px 8px; cursor: pointer; font: inherit; }
.range-buttons button:hover:not(:disabled), .range-buttons button[aria-pressed="true"] { color: #23897f; border-color: #79b9af; background: #eef8f5; }
.range-buttons button:focus-visible { outline: 2px solid #479e92; outline-offset: 2px; }
.range-buttons button:disabled { opacity: .45; cursor: default; }
.selection-hint { margin: 0; font-size: 11px; color: #718095; }
@media (max-width: 600px) { .report-chart.brain-chart { height: 390px; } }
</style>
