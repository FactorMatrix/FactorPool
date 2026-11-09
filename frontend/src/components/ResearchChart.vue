<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from "vue";
import * as echarts from "echarts";
const props = defineProps({ daily: Array, mode: String });
const host = ref(null);
let chart, observer;
function draw() {
  if (!chart) return;
  const dates = (props.daily || []).map((d) => d.date);
  const series =
    props.mode === "ic"
      ? ["ic", "rank_ic"].map((key) => ({
          name: key === "ic" ? "IC" : "Rank IC",
          type: "line",
          connectNulls: false,
          showSymbol: dates.length < 30,
          data: props.daily.map((d) => d[key]),
        }))
      : Array.from(
          { length: props.daily?.[0]?.group_returns.length || 0 },
          (_, i) => ({
            name: `G${i + 1}`,
            type: "line",
            connectNulls: false,
            data: props.daily.map((d) => d.group_returns[i]),
          }),
        );
  chart.setOption(
    {
      backgroundColor: "transparent",
      color: ["#18bed0", "#74b96a", "#efb240", "#d49bfc", "#f68080"],
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: "#8491a2" }, top: 5 },
      grid: { left: 60, right: 25, top: 50, bottom: 35 },
      xAxis: { type: "category", data: dates, axisLabel: { color: "#8491a2" } },
      yAxis: {
        type: "value",
        axisLabel: {
          color: "#8491a2",
          formatter:
            props.mode === "ic" ? "{value}" : (v) => `${(v * 100).toFixed(1)}%`,
        },
        splitLine: { lineStyle: { color: "#e3e8ef" } },
      },
      series,
    },
    true,
  );
}
onMounted(() => {
  chart = echarts.init(host.value);
  observer = new ResizeObserver(() => chart.resize());
  observer.observe(host.value);
  draw();
});
watch(
  () => [props.daily, props.mode],
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
<template><div ref="host" class="chart"></div></template>
<style scoped>
.chart {
  width: 100%;
  height: 270px;
}
</style>
