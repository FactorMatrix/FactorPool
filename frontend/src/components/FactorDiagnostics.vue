<script setup>
import { ref, computed, watch } from "vue";
import ReportChart from "./ReportChart.vue";
const props = defineProps({
  report: Object,
  initialSection: { type: String, default: "ic" },
  initialPeriod: { type: String, default: "1" },
});
const emit = defineEmits(["period-change"]);
watch(
  () => props.initialPeriod,
  (value) => {
    period.value = value;
  },
);
const period = ref(props.initialPeriod),
  section = ref(props.initialSection),
  metric = ref("rank_ic"),
  view = ref(props.initialSection === "ic" ? "series" : "nav"),
  strategy = ref("high_low");
watch(period, (value) => emit("period-change", value));
const periods = computed(() => Object.keys(props.report.horizons));
watch(
  periods,
  (values) => {
    if (!values.includes(period.value)) period.value = values[0];
  },
  { immediate: true },
);
const selected = computed(
  () =>
    props.report.horizons[period.value] ||
    props.report.horizons[periods.value[0]],
);
const port = computed(
  () =>
    selected.value?.portfolios[strategy.value] ||
    selected.value?.portfolios.high_low,
);
const alpha = computed(() => selected.value?.portfolios.alpha);
const dailyPortfolio = computed(() => (props.report.version || 0) >= 3);
const num = (x) => (x == null ? "--" : Number(x).toFixed(4));
const pct = (x) => (x == null ? "--" : (Number(x) * 100).toFixed(2) + "%");
const pval = (x) => (x == null ? "--" : Number(x).toExponential(3));
const bp = (x) => (x == null ? "--" : (Number(x) * 10000).toFixed(2) + " bp");
const title = (name) =>
  name === "alpha"
    ? "Alpha（去均值 / L1）"
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
const brainCards = computed(() => {
  const b = alpha.value?.brain_style || {};
  return [
    ["Sharpe", num(b.sharpe)],
    ["Fitness", num(b.fitness)],
    ["Returns", pct(b.returns)],
    ["Turnover", pct(b.turnover)],
    ["Drawdown", pct(b.drawdown)],
    ["Margin", bp(b.margin)],
  ];
});
</script>
<template>
  <section class="diagnostics" data-testid="diagnostics">
    <div class="diagnostic-heading">
      <strong>完整因子分析</strong>
      <div class="period-tabs">
        <button
          v-for="h in periods"
          :key="h"
          :class="{ active: period === h }"
          @click="period = h"
        >
          {{ h }} 期
        </button>
      </div>
    </div>
    <div class="diagnostic-tabs">
      <button :class="{ active: section === 'ic' }" @click="section = 'ic'">
        IC / Rank IC</button
      ><button
        :class="{ active: section === 'portfolio' }"
        @click="section = 'portfolio'"
      >
        多空与分组</button
      ><button
        :class="{ active: section === 'brain' }"
        @click="section = 'brain'"
      >
        BRAIN 风格
      </button>
    </div>
    <template v-if="section === 'ic'">
      <div class="report-controls">
        <select v-model="metric" aria-label="相关指标">
          <option value="rank_ic">Rank IC · Spearman</option>
          <option value="ic">IC · Pearson</option></select
        ><select v-model="view" aria-label="诊断图表">
          <option value="series">时间序列 / 30期均值</option>
          <option value="monthly">月度热力图</option>
          <option value="yearly">年度均值</option>
          <option value="cumulative">累计 IC</option>
          <option value="decay">预测力衰减</option>
        </select>
      </div>
      <ReportChart :data="selected" :mode="view" :metric="metric" />
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
        逐日 h 期前瞻 IC 含重叠样本；IID 显著性未修正自相关。HAC 使用
        {{ selected.horizon - 1 }} 阶 Bartlett
        权重，数据不完整时留空。衰减固定当期因子，逐阶后移未来入场时点。
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
        ><select v-model="view" aria-label="组合图表">
          <option value="nav">净值 / 回撤</option>
          <option value="returns">{{ dailyPortfolio ? '日收益' : '持有期收益' }}</option>
          <option value="monthly">月度收益热力图</option>
          <option value="yearly">年度收益</option>
          <option value="turnover">调仓换手</option>
        </select>
      </div>
      <ReportChart :data="selected" :portfolio="port" :mode="view" />
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
              <th>{{ dailyPortfolio ? '有效日数' : '有效期数' }}</th>
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
      <p v-if="dailyPortfolio" class="report-note">
        每 {{ selected.horizon }} 期调仓，持有期内权重自然漂移；收盘价 / 前收盘价 − 1，逐日计算净值与回撤。
        多空两侧各 0.5、总敞口 1；按 {{ report.conventions.annual_days }} 个交易日年化，月/年收益按实际交易日归属。
        缺失收益留空，不补零或重分配权重。所有组合均无费用。
      </p>
      <p v-else class="report-note">
        每 {{ selected.horizon }} 期调仓，按非重叠持有期计算。多空两侧各
        0.5、总敞口 1；年化系数 {{ report.conventions.annual_days }}/{{
          selected.horizon
        }}。月/年收益按退出日期归属，非逐日盯市口径。缺价格不按事后可用样本重分配权重。
      </p>
    </template>
    <template v-else>
      <div class="brain-metrics">
        <div v-for="[name, value] in brainCards" :key="name">
          <span>{{ name }}</span
          ><strong>{{ value }}</strong>
        </div>
      </div>
      <p class="report-note">
        研究 Alpha：因子截面去均值、L1 归一化、Delay 1、每
        {{ selected.horizon }} 期调仓。采用页面下方披露的研究公式，BRAIN
        官方数值一致性尚未核验。
      </p>
      <ReportChart :data="selected" :portfolio="alpha" mode="pnl" />
      <div class="brain-missing">
        <span>Self-correlation <b>--</b></span
        ><span>Production correlation <b>--</b></span
        ><span>市值暴露 <b>--</b></span
        ><span>行业暴露 <b>--</b></span>
      </div>
      <details>
        <summary>计算公式与数据缺口</summary>
        <dl>
          <dt>Sharpe</dt>
          <dd>{{ dailyPortfolio ? '每日固定 book PnL 增量均值 / 样本标准差 × √年交易日' : '持有期 PnL 均值 / 样本标准差 × √(年交易日/持有期)' }}</dd>
          <dt>Returns</dt>
          <dd>{{ dailyPortfolio ? '每日固定 book PnL 增量均值年化 / 0.5；与 CAGR 分开' : '按固定 book size 的 PnL 均值年化 / 0.5；与 CAGR 分开' }}</dd>
          <dt>Turnover</dt>
          <dd>
            目标权重与价格漂移后的原权重之差的绝对值和；含首次建仓，折算日均
          </dd>
          <dt>Drawdown</dt>
          <dd>累计固定 book PnL 的峰谷差 / 0.5</dd>
          <dt>Fitness</dt>
          <dd>Sharpe × √(|Returns| / max(Turnover, 0.125))</dd>
          <dt>Margin</dt>
          <dd>累计 PnL / 累计换手金额；以 bp 展示</dd>
        </dl>
        <p class="report-note">
          没有指定 Alpha 参考池、BRAIN 生产池、合格市值或行业
          Provider，对应指标留空。所有组合均为无费用研究结果。
        </p>
      </details>
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
.period-tabs,
.diagnostic-tabs {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.period-tabs button,
.diagnostic-tabs button {
  font-size: 11px;
  padding: 6px 10px;
}
.diagnostic-tabs {
  padding-bottom: 12px;
  border-bottom: 1px solid #dce2ea;
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
.brain-metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 14px;
}
.brain-metrics > div {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  background: #edf2f6;
  border-radius: 0;
}
.brain-metrics span {
  font-size: 11px;
  color: #8391a5;
}
.brain-metrics strong {
  font-weight: 500;
  font-size: 21px;
}
.brain-missing {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  font-size: 10px;
  color: #8391a5;
  margin: 12px 0;
}
.brain-missing b {
  float: right;
}
dl {
  font-size: 11px;
  line-height: 1.9;
}
dt {
  color: #4794a9;
}
dd {
  margin: 0 0 8px;
  color: #8190a3;
}
@media (max-width: 500px) {
  .brain-metrics strong {
    font-size: 17px;
  }
  .report-controls select {
    max-width: 55%;
  }
}
</style>
