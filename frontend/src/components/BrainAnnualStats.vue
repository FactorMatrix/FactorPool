<script setup>
defineProps({ rows: Array, testStart: String });
const number = value => value == null ? "--" : Number(value).toFixed(2);
const percent = value => value == null ? "--" : `${(Number(value) * 100).toFixed(2)}%`;
const margin = value => value == null ? "--" : `${(Number(value) * 10000).toFixed(2)} ‱`;
const count = value => value == null ? "--" : Math.round(Number(value)).toLocaleString("en-US");
</script>

<template>
  <section class="annual-stats" aria-label="年度统计">
    <div v-if="rows?.length" class="annual-scroll">
      <table aria-label="年度统计表">
        <thead><tr>
          <th>Year</th><th>Sharpe</th><th>Turnover</th><th>Fitness</th><th>Returns</th>
          <th>Drawdown</th><th>Margin</th><th>Long Count</th><th>Short Count</th>
        </tr></thead>
        <tbody><tr v-for="row in rows" :key="row.year" :class="testStart && row.start && row.end ? (row.start >= testStart ? 'test-row' : row.end < testStart ? 'train-row' : 'mixed-row') : ''">
          <th scope="row" :title="`${row.start} — ${row.end}`">{{ row.year }}</th>
          <td>{{ number(row.sharpe) }}</td><td>{{ percent(row.turnover) }}</td>
          <td>{{ number(row.fitness) }}</td><td>{{ percent(row.returns) }}</td>
          <td>{{ percent(row.drawdown) }}</td><td>{{ margin(row.margin) }}</td>
          <td>{{ count(row.long_count) }}</td><td>{{ count(row.short_count) }}</td>
        </tr></tbody>
      </table>
    </div>
    <p v-if="rows?.length" class="scroll-hint">左右滚动查看完整表格</p>
    <p v-else-if="rows" class="annual-empty">当前区间没有年度统计数据。</p>
    <p v-else class="annual-empty">此结果没有年度统计数据；请使用当前版本重新运行。</p>
    <p class="annual-note">Long / Short Count：每日目标持仓数的均值。</p>
  </section>
</template>

<style scoped>
.annual-stats { margin: 20px 0 4px; }
.annual-scroll { max-width: 100%; overflow-x: auto; border-top: 1px solid #dce2e9; border-bottom: 1px solid #dce2e9; }
table { width: 100%; min-width: 700px; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 12px; }
th, td { padding: 12px 10px; text-align: right; white-space: nowrap; border-bottom: 1px solid #edf0f3; }
thead th { color: #778597; background: #f5f7fa; font-size: 12px; font-weight: 400; }
tbody th { color: #59687a; font-weight: 500; }
tbody tr.train-row { border-left: 3px solid #56c8cf; }
tbody tr.test-row { border-left: 3px solid #df8d32; }
tbody tr.mixed-row { border-left: 3px solid #adb8c4; }
td { color: #536174; }
th:first-child { text-align: left; }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
.annual-empty, .annual-note { color: #8995a5; font-size: 11px; line-height: 1.7; }
.annual-empty { padding: 12px 0; }
.scroll-hint { display: none; margin: 5px 0 0; color: #8995a5; font-size: 10px; }
@container (max-width: 640px) { .scroll-hint { display: block; } }
</style>
