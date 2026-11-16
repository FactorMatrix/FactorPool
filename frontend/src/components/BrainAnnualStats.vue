<script setup>
import { computed, ref, useId } from "vue";
import UiIcon from "./UiIcon.vue";

const props = defineProps({
  rows: Array,
  testStart: String,
  showTest: Boolean,
  trainDates: Array,
  testDates: Array,
  collapsible: { type: Boolean, default: true },
});
const expanded = ref(true);
const tableId = useId();
const finite = value => typeof value === "number" && Number.isFinite(value);
const number = value => finite(value) ? value.toFixed(2) : "—";
const scaled = (value, scale, unit) => finite(value) && finite(value * scale)
  ? `${(value * scale).toFixed(2)}${unit}` : "—";
const percent = value => scaled(value, 100, "%");
const margin = value => scaled(value, 10000, " bps");
const count = value => {
  if (!finite(value)) return "—";
  return (Math.round(value) || 0).toLocaleString("en-US");
};
const day = value => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
const partitionDates = computed(() => {
  if (!Array.isArray(props.trainDates) || !Array.isArray(props.testDates)) return null;
  return {
    train: new Set(props.trainDates.filter(day)),
    test: new Set(props.testDates.filter(day)),
  };
});
function period(row) {
  if (!props.showTest || !props.testStart) return null;
  const start = day(row.start), end = day(row.end), dates = partitionDates.value;
  const unassigned = { kind: "unassigned", label: "IS · 未分段", description: "该年度有真实统计，但日期没有归入现有 TRAIN / TEST 数据。" };
  if (!start || !end || start > end || !dates) return unassigned;
  const within = values => [...values].filter(value => value >= start && value <= end);
  const train = within(dates.train), test = within(dates.test);
  if (!train.length && !test.length) return unassigned;
  const assigned = new Set([...train, ...test]);
  const outside = !assigned.has(start) || !assigned.has(end)
    || (finite(row.days) && assigned.size < row.days);
  const kind = train.length && test.length ? "mixed" : train.length ? "train" : "test";
  const label = kind === "mixed" ? "TRAIN / TEST" : kind.toUpperCase();
  return {
    kind,
    outside,
    label: outside ? `${label} + 未分段` : label,
    description: `${label}：按本任务实际分段日期标记。${outside ? "本年度还包含未归入 TRAIN / TEST 的持有收益日期，不将其标作测试期。" : kind === "mixed" ? "完整年度同时包含训练期与测试期，不拆分或拼接年度统计。" : "指标保持后端给出的真实年度值。"}`,
  };
}
const annualRows = computed(() => (Array.isArray(props.rows) ? props.rows : [])
  .map(row => ({ row, period: period(row) })));
</script>

<template>
  <section class="annual-stats" aria-label="年度统计">
    <div class="annual-header">
      <span>Annual Statistics</span>
      <button v-if="collapsible" type="button" class="annual-toggle"
        :aria-label="expanded ? '折叠年度统计' : '展开年度统计'"
        :aria-expanded="expanded" :aria-controls="tableId" @click="expanded = !expanded">
        <span>{{ expanded ? "收起" : "展开" }}</span>
        <UiIcon name="arrow" :size="14" :class="{ expanded }" />
      </button>
    </div>
    <div v-show="!collapsible || expanded" :id="tableId" class="annual-body">
      <div v-if="annualRows.length" class="annual-scroll">
        <table aria-label="年度统计表">
          <thead><tr>
            <th>Year</th><th>Sharpe</th><th>Turnover</th><th>Fitness</th><th>Returns</th>
            <th>Drawdown</th><th>Margin</th><th>Long Count</th><th>Short Count</th>
          </tr></thead>
          <tbody><tr v-for="item in annualRows" :key="item.row.year"
            :class="[item.period && `${item.period.kind}-row`, { 'partial-row': item.period?.outside }]"
            :data-period="item.period?.kind" :data-outside-window="item.period?.outside || undefined">
            <th scope="row" :title="`${item.row.start || '—'} — ${item.row.end || '—'}${item.period ? ` · ${item.period.description}` : ''}`">
              {{ item.row.year }}
              <span v-if="item.period && (item.period.kind === 'mixed' || item.period.kind === 'unassigned' || item.period.outside)"
                class="period-label">{{ item.period.label }}</span>
            </th>
            <td>{{ number(item.row.sharpe) }}</td><td>{{ percent(item.row.turnover) }}</td>
            <td>{{ number(item.row.fitness) }}</td><td>{{ percent(item.row.returns) }}</td>
            <td>{{ percent(item.row.drawdown) }}</td><td>{{ margin(item.row.margin) }}</td>
            <td>{{ count(item.row.long_count) }}</td><td>{{ count(item.row.short_count) }}</td>
          </tr></tbody>
        </table>
      </div>
      <p v-if="annualRows.length" class="scroll-hint">左右滚动查看完整表格</p>
      <p v-else class="annual-empty">当前报告没有可用的年度统计数据。</p>
      <div v-if="showTest && testStart && annualRows.length" class="period-legend" aria-label="年度区间标记">
        <span class="legend-train">TRAIN</span><span class="legend-test">TEST</span>
        <span v-if="annualRows.some(item => item.period?.kind === 'mixed')" class="legend-mixed">TRAIN / TEST</span>
        <span v-if="annualRows.some(item => item.period?.kind === 'unassigned' || item.period?.outside)" class="legend-unassigned">未分段日期</span>
      </div>
      <p class="annual-note">Long / Short Count：每日目标持仓数的均值，四舍五入显示。— 表示缺少有效数据。</p>
    </div>
  </section>
</template>

<style scoped>
.annual-stats { margin: 14px 0 4px; }
.annual-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 0 6px; color: #778597; font-size: 12px; }
.annual-toggle { display: inline-flex; align-items: center; gap: 4px; padding: 3px 5px; border: 0; border-radius: 3px; background: transparent; color: #6f7e8e; font-size: 12px; cursor: pointer; }
.annual-toggle:hover { background: #eef2f5; color: #4c7345; }
.annual-toggle:focus-visible { outline: 2px solid #56a954; outline-offset: 2px; }
.annual-toggle svg.expanded { transform: rotate(90deg); }
.annual-scroll { max-width: 100%; overflow-x: auto; border-radius: 3px; border-bottom: 1px solid #d5dce5; }
table { width: 100%; min-width: 620px; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 12px; }
th, td { padding: 9px 5px; text-align: right; white-space: nowrap; border-bottom: 1px solid #dce2e9; font-size: 12px; }
thead th { color: #fff; background: #58697d; font-size: 12px; font-weight: 500; }
tbody th { position: relative; color: #59687a; font-weight: 500; }
tbody tr:nth-child(even) { background: #e8ebef; }
tbody tr:nth-child(odd) { background: #f6f7f9; }
tbody tr.train-row > th:first-child { box-shadow: inset 3px 0 #56c8cf; }
tbody tr.test-row > th:first-child { box-shadow: inset 3px 0 #df8d32; }
tbody tr.mixed-row > th:first-child::before,
tbody tr.partial-row > th:first-child::before { content: ""; position: absolute; top: 0; bottom: 0; left: 0; width: 3px; background: linear-gradient(#56c8cf 0 50%, #df8d32 50%); }
tbody tr.partial-row > th:first-child::before { background: linear-gradient(#56c8cf 0 33%, #df8d32 33% 66%, #a5b0bd 66%); }
tbody tr.train-row.partial-row > th:first-child::before { background: linear-gradient(#56c8cf 0 50%, #a5b0bd 50%); }
tbody tr.test-row.partial-row > th:first-child::before { background: linear-gradient(#df8d32 0 50%, #a5b0bd 50%); }
tbody tr.unassigned-row > th:first-child { box-shadow: inset 3px 0 #a5b0bd; }
td { color: #536174; }
th:first-child { text-align: left; }
.period-label { display: block; margin-top: 4px; color: #8a95a3; font-size: 9px; font-weight: 400; }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
.annual-empty, .annual-note { color: #8995a5; font-size: 11px; line-height: 1.7; }
.annual-empty { padding: 12px 0; }
.scroll-hint { display: none; margin: 5px 0 0; color: #8995a5; font-size: 10px; }
.period-legend { display: flex; flex-wrap: wrap; gap: 12px; margin: 9px 0; color: #8995a5; font-size: 10px; }
.period-legend span::before { content: ""; display: inline-block; width: 7px; height: 7px; margin-right: 5px; background: #a5b0bd; }
.period-legend .legend-train::before { background: #56c8cf; }
.period-legend .legend-test::before { background: #df8d32; }
.period-legend .legend-mixed::before { background: linear-gradient(90deg, #56c8cf 0 50%, #df8d32 50%); }
@container (max-width: 620px) { .scroll-hint { display: block; } }
</style>
