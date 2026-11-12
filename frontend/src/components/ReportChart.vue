<script setup>
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from "vue";
import * as echarts from "echarts";
const props = defineProps({
  data: Object,
  mode: String,
  metric: { type: String, default: "rank_ic" },
  portfolio: Object,
});
const host = ref(null);
let chart, observer;
const compactAmount = value => {
  const scale = Math.abs(value) >= 1e6 ? 1e6 : Math.abs(value) >= 1e3 ? 1e3 : 1;
  return `${(value / scale).toLocaleString("en-US", { maximumFractionDigits: 2 })}${scale === 1e6 ? 'M' : scale === 1e3 ? 'K' : ''}`;
};
const escapeHtml = value => String(value).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
function draw() {
  if (!chart || !props.data) return;
  const d = props.data,
    p = props.portfolio,
    k = props.metric,
    portfolioDates = d.portfolio_dates || d.portfolio_signal_dates,
    dailyPortfolio = !!d.portfolio_dates;
  const base = {
    backgroundColor: "transparent",
    color: ["#18bed0", "#efb240", "#74b96a"],
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { color: "#8491a2" } },
    grid: { left: 55, right: 55, top: 45, bottom: 40 },
    xAxis: { type: "category", axisLabel: { color: "#8491a2" } },
    yAxis: {
      type: "value",
      axisLabel: { color: "#8491a2" },
      splitLine: { lineStyle: { color: "#e3e8ef" } },
    },
  };
  let option = { ...base };
  if (props.mode === "monthly") {
    const rows = p ? p.monthly : d.monthly[k],
      years = [...new Set(rows.map((r) => r.period.slice(0, 4)))].sort();
    const values = rows
      .filter((r) => (p ? r.value : r.mean) != null)
      .map((r) => [
        Number(r.period.slice(5)) - 1,
        years.indexOf(r.period.slice(0, 4)),
        p ? r.value : r.mean,
      ]);
    const extent = Math.max(...values.map((r) => Math.abs(r[2])), 0.000001);
    option = {
      ...base,
      tooltip: {
        formatter: (v) =>
          `${years[v.value[1]]}-${String(v.value[0] + 1).padStart(2, "0")}: ${p ? (v.value[2] * 100).toFixed(2) + "%" : v.value[2].toFixed(4)}`,
      },
      legend: undefined,
      grid: { left: 60, right: 20, top: 20, bottom: 65 },
      xAxis: {
        type: "category",
        data: Array.from({ length: 12 }, (_, i) => `${i + 1}月`),
        axisLabel: { color: "#8491a2" },
      },
      yAxis: { type: "category", data: years, axisLabel: { color: "#8491a2" } },
      visualMap: {
        min: -extent,
        max: extent,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        textStyle: { color: "#8491a2" },
        inRange: { color: ["#93c7af", "#f3f6f8", "#d99191"] },
      },
      series: [
        {
          type: "heatmap",
          data: values,
          label: {
            show: values.length < 25,
            formatter: (v) =>
              p ? (v.value[2] * 100).toFixed(1) + "%" : v.value[2].toFixed(3),
          },
          itemStyle: { borderColor: "#fafbfc", borderWidth: 2 },
        },
      ],
    };
  } else if (p) {
    option.xAxis.data = portfolioDates;
    if (props.mode === "nav") {
      option.yAxis = [
        base.yAxis,
        {
          type: "value",
          axisLabel: {
            color: "#b47f88",
            formatter: (v) => (v * 100).toFixed(0) + "%",
          },
          splitLine: { show: false },
        },
      ];
      option.series = [
        {
          name: "复利净值",
          type: "line",
          showSymbol: portfolioDates?.length < 20,
          connectNulls: false,
          data: p.nav,
        },
        {
          name: "回撤",
          type: "line",
          yAxisIndex: 1,
          showSymbol: portfolioDates?.length < 20,
          areaStyle: { opacity: 0.2 },
          data: p.drawdown_series,
        },
      ];
    } else if (props.mode === "pnl") {
      const unit = p.pnl?.unit || d.pnl_book?.unit || "BOOK";
      option.tooltip = { trigger: "axis", formatter: rows => {
        const values = Array.isArray(rows) ? rows : [rows];
        return [escapeHtml(values[0]?.axisValueLabel || ""), ...values.map(row => `${row.marker}${escapeHtml(row.seriesName)}: ${row.value == null ? '--' : row.seriesIndex === 0 ? `${Number(row.value).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})} ${escapeHtml(unit)}` : `${(Number(row.value) * 100).toFixed(2)}%`}`)].join("<br/>");
      }};
      option.yAxis = [
        { ...base.yAxis, name: unit, axisLabel: { ...base.yAxis.axisLabel, formatter: compactAmount } },
        {
          type: "value",
          axisLabel: {
            color: "#b47f88",
            formatter: (v) => (v * 100).toFixed(0) + "%",
          },
          splitLine: { show: false },
        },
      ];
      option.series = [
        {
          name: `累计固定 book PnL (${unit})`,
          type: "line",
          showSymbol: portfolioDates?.length < 20,
          connectNulls: false,
          data: p.pnl?.cumulative || p.constant_book_pnl,
        },
        {
          name: "半 book 回撤",
          type: "line",
          yAxisIndex: 1,
          showSymbol: portfolioDates?.length < 20,
          data: p.constant_book_drawdown,
          areaStyle: { opacity: 0.2 },
        },
      ];
    } else if (props.mode === "turnover")
      option.series = [
        {
          name: "调仓换手（含价格漂移）",
          type: "bar",
          data: p.turnover_series,
        },
      ];
    else if (props.mode === "yearly") {
      option.xAxis.data = p.yearly.map((r) => r.period);
      option.series = [
        {
          name: dailyPortfolio ? "逐日归属年度收益" : "退出年度归属收益",
          type: "bar",
          data: p.yearly.map((r) => r.value),
        },
      ];
    } else
      option.series = [{ name: dailyPortfolio ? "日收益" : "持有期收益", type: "bar", data: p.returns }];
  } else if (props.mode === "yearly") {
    option.xAxis.data = d.yearly[k].map((r) => r.period);
    option.series = [
      { name: "年度均值", type: "bar", data: d.yearly[k].map((r) => r.mean) },
    ];
  } else if (props.mode === "decay") {
    option.xAxis.data = d.decay.map((r) => `${r.entry_lag}期`);
    option.series = [
      { name: "IC", type: "bar", data: d.decay.map((r) => r.ic.mean) },
      {
        name: "Rank IC",
        type: "bar",
        data: d.decay.map((r) => r.rank_ic.mean),
      },
    ];
  } else {
    option.xAxis.data = d.dates;
    option.series =
      props.mode === "cumulative"
        ? [
            {
              name: "累计IC（求和）",
              type: "line",
              showSymbol: d.dates?.length < 20,
              connectNulls: false,
              data: d.series[`${k}_cumulative`],
            },
          ]
        : [
            {
              name: k === "ic" ? "IC" : "Rank IC",
              type: "line",
              showSymbol: d.dates.length < 20,
              connectNulls: false,
              data: d.series[k],
            },
            {
              name: "30期滚动均值",
              type: "line",
              showSymbol: d.dates?.length < 20,
              connectNulls: false,
              data: d.series[`${k}_rolling30`],
            },
          ];
  }
  chart.setOption(option, true);
}
onMounted(() => {
  chart = echarts.init(host.value);
  observer = new ResizeObserver(() => chart.resize());
  observer.observe(host.value);
  draw();
});
watch(
  () => [props.data, props.mode, props.metric, props.portfolio],
  async () => {
    await nextTick();
    draw();
  },
  { deep: true },
);
onBeforeUnmount(() => {
  observer?.disconnect();
  chart?.dispose();
});
</script>
<template><div class="report-chart" ref="host"></div></template>
<style scoped>
.report-chart {
  height: 310px;
  width: 100%;
  min-width: 0;
}
</style>
