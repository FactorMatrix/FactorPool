<script setup>
import { computed } from "vue";
const props = defineProps({ summary: Object });
const num = (x) => (x == null ? "--" : Number(x).toFixed(2));
const pct = (x) => (x == null ? "--" : (Number(x) * 100).toFixed(2) + "%");
const metrics = computed(() => {
  const b = props.summary || {};
  return [
    ["Sharpe", num(b.sharpe)],
    ["Turnover", pct(b.turnover)],
    ["Fitness", num(b.fitness)],
    ["Returns", pct(b.returns)],
    ["Drawdown", pct(b.drawdown)],
    ["Margin", b.margin == null ? "--" : (b.margin * 10000).toFixed(2) + " bp"],
  ];
});
</script>
<template>
  <div class="brain-summary" aria-label="模拟统计摘要">
    <span class="aggregate-label">Aggregate Data</span>
    <div class="summary-measure" v-for="[label, value] in metrics" :key="label">
      <span>{{ label }}</span
      ><strong>{{ value }}</strong>
    </div>
  </div>
</template>
