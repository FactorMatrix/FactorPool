<script setup>
import { computed, useId } from "vue";
const props = defineProps({ summary: Object });
const descriptionId = useId();
const finite = value => typeof value === "number" && Number.isFinite(value);
const num = value => finite(value) ? value.toFixed(2) : "—";
const scaled = (value, scale, unit) => finite(value) && finite(value * scale)
  ? `${(value * scale).toFixed(2)}${unit}` : "—";
const pct = value => scaled(value, 100, "%");
const bps = value => scaled(value, 10000, " bps");
const metrics = computed(() => {
  const b = props.summary || {};
  return [
    ["Sharpe", num(b.sharpe), "固定 book 日度 PnL 均值 ÷ 样本标准差 × √年交易日数。FactorPool 本地统计口径。"],
    ["Turnover", pct(b.turnover), "真实日度换手率的区间均值：目标权重减去价格漂移后的原权重，取绝对值之和；包含首次建仓。"],
    ["Fitness", num(b.fitness), "本地公式：Sharpe × √(|Returns| ÷ max(Turnover, 0.125))。不是另行核验的 BRAIN 评分。"],
    ["Returns", pct(b.returns), "固定 book 日均 PnL ÷ 0.5 × 年交易日数；不是复利年化收益。"],
    ["Drawdown", pct(b.drawdown), "累计算术 PnL 的峰谷最大下降 ÷ 0.5。与复利净值回撤分别计算。"],
    ["Margin", bps(b.margin), "累计标准化 PnL ÷ 累计标准化换手额；1 bp = 0.01%，显示值为后端比例 × 10000。"],
  ];
});
</script>
<template>
  <div class="brain-summary" aria-label="模拟统计摘要">
    <span class="aggregate-label">Aggregate Data</span>
    <div class="summary-measure" v-for="[label, value, description] in metrics" :key="label">
      <span class="metric-caption">
        {{ label }}
        <button type="button" class="metric-help" :aria-label="`${label} 指标说明`"
          :aria-describedby="`${descriptionId}-${label}`" :title="description">i</button>
        <span :id="`${descriptionId}-${label}`" class="metric-description" role="tooltip">{{ description }}</span>
      </span>
      <strong>{{ value }}</strong>
    </div>
  </div>
</template>

<style scoped>
.brain-summary {
  display: grid;
  grid-template-columns: minmax(82px, 1.05fr) repeat(6, minmax(0, 1fr));
  align-items: end;
  gap: 6px;
  padding: 9px 0 13px;
  border-bottom: 1px solid #dce1e8;
}
.aggregate-label {
  grid-column: auto;
  align-self: center;
  margin: 0 6px 0 0;
  color: #4f5c70;
  font-size: 12px;
  font-weight: 600;
}
.summary-measure {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: #8a96a6;
  font-size: 12px;
}
.summary-measure strong {
  color: #49596e;
  font-size: 20px;
  font-weight: 400;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.metric-caption { position: relative; display: flex; align-items: center; gap: 4px; }
.metric-help { display: inline-grid; place-items: center; width: 13px; height: 13px; padding: 0;
  border: 1px solid #a9b4c0; border-radius: 50%; background: transparent; color: #8793a2;
  font: 10px Georgia, serif; line-height: 1; cursor: help; }
.metric-help:focus-visible { outline: 2px solid #56a954; outline-offset: 3px; }
.metric-description { display: none; position: absolute; z-index: 5; left: 0; bottom: calc(100% + 9px);
  width: 230px; max-width: 65vw; padding: 10px 12px; border: 1px solid #dce2e9; border-radius: 4px;
  background: #fff; color: #536174; box-shadow: 0 5px 16px #2733431c; font-size: 12px; line-height: 1.7;
  font-weight: 400; white-space: normal; }
.metric-caption:has(.metric-help:hover) .metric-description,
.metric-caption:focus-within .metric-description { display: block; }
.summary-measure:nth-child(n+5) .metric-description { left: auto; right: 0; }
@container (max-width: 760px) {
  .brain-summary { grid-template-columns: repeat(6, minmax(0, 1fr)); }
  .aggregate-label { grid-column: 1 / -1; margin: 0; }
}
@container (max-width: 620px) {
  .brain-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px 10px; }
  .aggregate-label { grid-column: 1 / -1; margin: 0; }
  .summary-measure strong { font-size: 20px; }
  .summary-measure:nth-child(3n+2) .metric-description { left: 0; right: auto; }
  .summary-measure:nth-child(3n+3) .metric-description,
  .summary-measure:nth-child(3n+4) .metric-description { left: auto; right: 0; }
}
</style>
