<script setup>
import { ref, computed, watch } from "vue";
import ReportChart from "./ReportChart.vue";
const props = defineProps({
  report: Object,
  initialSection: { type: String, default: "ic" },
});
const section = computed(() => props.initialSection),
  metric = ref("rank_ic"),
  view = ref(props.initialSection === "ic" ? "series" : "nav"),
  strategy = ref("high_low");
const periods = ["1"];
const selected = computed(() => props.report.horizons["1"]);
const port = computed(
  () =>
    selected.value?.portfolios?.[strategy.value] ||
    selected.value?.portfolios?.high_low,
);
const visualization = computed(() => props.report.conventions.brain_settings.visualization);
const num = (x) => (x == null ? "--" : Number(x).toFixed(4));
const pct = (x) => (x == null ? "--" : (Number(x) * 100).toFixed(2) + "%");
const pval = (x) => (x == null ? "--" : Number(x).toExponential(3));
const title = (name) =>
  name === "alpha"
    ? "Alpha（连续因子权重）"
    : name === "high_low"
      ? "最高组 − 最低组（各半仓）"
      : name === "inner_high_low"
        ? "次高组 − 次低组（各半仓）"
        : name.startsWith("long_")
          ? name.replace("long_", "多头 ")
          : name.replace("short_", "空头 ");
watch(section, () => {
  view.value = section.value === "ic" ? "series" : "nav";
});
</script>
<template>
  <section class="diagnostics" data-testid="diagnostics">
    <div class="diagnostic-heading">
      <strong>完整因子分析</strong>
    </div>
    <p v-if="!visualization" class="report-note" role="status">可视化已由 settings.visualization 关闭，以下展示数值结果。</p>
    <template v-if="section === 'ic'">
      <div class="report-controls">
        <select v-model="metric" aria-label="相关指标">
          <option value="rank_ic">Rank IC · Spearman</option>
          <option value="ic">IC · Pearson</option></select
        ><select v-if="visualization" v-model="view" aria-label="诊断图表">
          <option value="series">时间序列 / 30期均值</option>
          <option value="monthly">月度热力图</option>
          <option value="yearly">年度均值</option>
          <option value="cumulative">累计 IC</option>
          <option value="decay">预测力衰减</option>
        </select>
      </div>
      <ReportChart v-if="visualization" :data="selected" :mode="view" :metric="metric" />
      <div class="table-scroll">
        <table class="ic-statistics">
          <thead>
            <tr>
              <th>持有期</th>
              <th>均值</th>
              <th>标准差</th>
              <th>ICIR</th>
              <th>正值比例</th>
              <th>偏度</th>
              <th>超额峰度</th>
              <th>t (IID)</th>
              <th>p (IID)</th>
              <th>t (HAC)</th>
              <th>p (HAC)</th>
              <th>有效日期</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="h in periods" :key="h">
              <td>{{ h }}</td>
              <td>{{ num(report.horizons[h][metric].mean) }}</td>
              <td>{{ num(report.horizons[h][metric].std) }}</td>
              <td>{{ num(report.horizons[h][metric].ir) }}</td>
              <td>{{ pct(report.horizons[h][metric].positive_ratio) }}</td>
              <td>{{ num(report.horizons[h][metric].skewness) }}</td>
              <td>{{ num(report.horizons[h][metric].excess_kurtosis) }}</td>
              <td>{{ num(report.horizons[h][metric].t_iid) }}</td>
              <td>{{ pval(report.horizons[h][metric].p_iid) }}</td>
              <td>{{ num(report.horizons[h][metric].t_hac) }}</td>
              <td>{{ pval(report.horizons[h][metric].p_hac) }}</td>
              <td>{{ report.horizons[h][metric].n }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="report-note">
        各持有期按对应的调仓日取样；当前 HAC 使用 0 阶，数据不完整时留空。
        IID 显著性未修正自相关。衰减固定当期因子，逐阶后移未来入场时点。
      </p>
    </template>
    <template v-else-if="section === 'portfolio'">
      <div class="report-controls">
        <select v-model="strategy" aria-label="研究组合">
          <option
            v-for="(_, key) in selected.portfolios"
            :key="key"
            :value="key"
          >
            {{ title(key) }}
          </option></select
        ><select v-if="visualization" v-model="view" aria-label="组合图表">
          <option value="nav">净值 / 回撤</option>
          <option value="returns">日收益</option>
          <option value="monthly">月度收益热力图</option>
          <option value="yearly">年度收益</option>
          <option value="turnover">调仓换手</option>
        </select>
      </div>
      <ReportChart v-if="visualization" :data="selected" :portfolio="port" :mode="view" />
      <div class="table-scroll portfolio-statistics">
        <table>
          <thead>
            <tr>
              <th>组合</th>
              <th>年化均值收益</th>
              <th>CAGR</th>
              <th>年化波动</th>
              <th>Sharpe</th>
              <th>最大回撤</th>
              <th>胜率</th>
              <th>盈亏比</th>
              <th>有效日数</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(p, key) in selected.portfolios"
              :key="key"
              :class="{ chosen: key === strategy }"
              @click="strategy = key"
            >
              <td>{{ title(key) }}</td>
              <td>{{ pct(p.annual_return) }}</td>
              <td>{{ pct(p.cagr) }}</td>
              <td>{{ pct(p.annual_volatility) }}</td>
              <td>{{ num(p.sharpe) }}</td>
              <td>{{ pct(p.max_drawdown) }}</td>
              <td>{{ pct(p.win_rate) }}</td>
              <td>{{ num(p.payoff_ratio) }}</td>
              <td>{{ p.n }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="report-note">
        每 {{ selected.horizon }} 期调仓，持有期内权重自然漂移，按快照日收益计算净值与回撤。
        高低组多空两侧各 0.5；Alpha 按实际设置生成连续权重，可有净敞口与未分配 gross。按 {{ report.conventions.annual_days }} 个交易日年化，月/年收益按实际交易日归属。
        缺失收益留空，不补零或重分配权重。所有组合均无费用。
      </p>
    </template>

  </section>
</template>
<style scoped>
.diagnostics {
  border: 1px solid #dce2ea;
  border-radius: 0;
  margin: 20px 0;
  background: transparent;
  padding: 12px;
  min-width: 0;
}
.diagnostic-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.diagnostic-heading strong {
  font-size: 13px;
}
.report-controls {
  display: flex;
  gap: 8px;
  margin: 12px 0;
}
.report-controls select {
  font-size: 11px;
  width: auto;
  max-width: 60%;
}
.report-note {
  font-size: 10px;
  color: #8a97a8;
  line-height: 1.8;
  margin: 10px 0;
}
.portfolio-statistics {
  max-height: 330px;
}
.chosen {
  background: #e5f2f5;
}
@media (max-width: 500px) {
  .report-controls select {
    max-width: 55%;
  }
}
</style>
