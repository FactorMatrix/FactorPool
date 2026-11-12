<script setup>
import { computed, ref, watch } from "vue";
const props = defineProps({ data: Object, constraints: Object });
const signal = ref(""), valuation = ref(""), constraintDate = ref(""), search = ref(""), page = ref(0);
const traces = computed(() => props.data?.alpha_trace || []);
const signalDates = computed(() => [...new Set(traces.value.map(row => row.signal_date))].sort());
const valuationDates = computed(() => traces.value.filter(row => row.signal_date === signal.value)
  .map(row => row.valuation_date).sort());
const selected = computed(() => traces.value.find(row => row.signal_date === signal.value && row.valuation_date === valuation.value));
const constraintRows = computed(() => props.constraints?.daily || []);
const constraint = computed(() => constraintRows.value.find(row => row.date === constraintDate.value));
const unit = computed(() => selected.value?.pnl_unit || props.data?.pnl_book?.unit || "BOOK");
const bookSize = computed(() => selected.value?.book_size ?? props.data?.pnl_book?.size ?? 1);
const skipped = computed(() => props.data?.trace_status?.not_rebalance_dates || []);
const rows = computed(() => (selected.value?.rows || []).filter(row => row.symbol.toLowerCase().includes(search.value.trim().toLowerCase())));
const visibleRows = computed(() => rows.value.slice(page.value * 100, (page.value + 1) * 100));
const number = value => value == null ? "--" : Number(value).toLocaleString("en-US", { maximumFractionDigits: 6 });
const amount = value => value == null ? "--" : Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const percent = value => value == null ? "--" : `${(Number(value) * 100).toFixed(4)}%`;
const member = value => value == null ? "--" : Number(value) > 0 ? "在池" : "池外";
const flag = value => value == null ? "--" : value ? "是" : "否";
const dateLabel = value => typeof value === "string" ? value : value.date || JSON.stringify(value);
watch(signalDates, dates => { if (!dates.includes(signal.value)) signal.value = dates[0] || ""; }, { immediate: true });
watch(valuationDates, dates => { if (!dates.includes(valuation.value)) valuation.value = dates[0] || ""; }, { immediate: true });
watch([signal, constraintRows], () => {
  if (constraintRows.value.some(row => row.date === signal.value)) constraintDate.value = signal.value;
  else if (!constraintRows.value.some(row => row.date === constraintDate.value)) constraintDate.value = constraintRows.value[0]?.date || "";
}, { immediate: true });
watch([selected, search], () => { page.value = 0; });
</script>
<template>
  <section class="alpha-trace" aria-label="权重与PnL追踪">
    <h2>Weights / PnL</h2>
    <p class="trace-note">按请求的信号日期查看完整追踪，不随 Train/Test 切换。信号日生成目标权重，收益日按同一持有期计算盈亏；所有数值来自任务快照。</p>
    <div class="trace-cards">
      <div><span>固定名义 book</span><strong>{{ amount(bookSize) }} {{ unit }}</strong></div>
      <div><span>当日 PnL · {{ unit }}</span><strong>{{ amount(selected?.daily_pnl) }}</strong></div>
      <div><span>调仓目标 gross</span><strong>{{ percent(selected?.target_gross) }}</strong></div>
      <div><span>调仓目标 net</span><strong>{{ percent(selected?.target_net) }}</strong></div>
    </div>
    <p class="trace-note">{{ unit === 'BOOK' ? 'BOOK 表示名义 book 单位。' : '金额单位仅作固定名义规模的显示缩放，不含汇兑计算。' }}无费用、无实际账户或成交模拟。</p>
    <template v-if="traces.length">
      <div class="trace-controls">
        <label>信号日期<select v-model="signal" aria-label="追踪信号日期"><option v-for="day in signalDates" :key="day" :value="day">{{ day }}</option></select></label>
        <label>收益日期<select v-model="valuation" aria-label="追踪收益日期"><option v-for="day in valuationDates" :key="day" :value="day">{{ day }}</option></select></label>
        <label>证券筛选<input v-model="search" aria-label="追踪证券筛选" placeholder="证券代码" /></label>
      </div>
      <p class="trace-note">信号时间：{{ selected?.signal_time || '--' }}；计算状态：{{ selected?.status || '--' }}。漂移敞口是收益发生前相对固定 book 的持有权重，与调仓目标分开。</p>
      <div class="trace-scroll">
        <table aria-label="逐股权重与PnL">
          <thead><tr><th>证券</th><th>成员</th><th>原始 Alpha</th><th>Decay 后</th><th>中性化后</th><th>目标权重</th><th>收益前漂移敞口</th><th>当期收益</th><th>PnL / book</th><th>PnL · {{ unit }}</th><th>状态</th></tr></thead>
          <tbody><tr v-for="(row, index) in visibleRows" :key="`${row.symbol}-${index}`">
            <td>{{ row.symbol }}</td><td>{{ member(row.member) }}</td><td>{{ number(row.raw_alpha) }}</td><td>{{ number(row.decayed_alpha) }}</td><td>{{ number(row.neutralized) }}</td>
            <td>{{ percent(row.target_weight) }}</td><td>{{ percent(row.effective_weight_before_return) }}</td><td>{{ percent(row.return) }}</td><td>{{ percent(row.pnl_fraction) }}</td><td>{{ amount(row.pnl) }}</td><td>{{ row.status || '--' }}<small v-if="row.raw_factor_status != null" class="raw-state">因子：{{ row.raw_factor_status }}；ready {{ flag(row.raw_factor_ready) }} / valid {{ flag(row.raw_factor_valid) }}</small></td>
          </tr></tbody>
        </table>
      </div>
      <p v-if="!rows.length" class="trace-note">当前筛选没有逐股记录；缺失值保留为 --，不补零。</p>
      <div class="trace-pagination"><button :disabled="page === 0" @click="page--">上一页</button><span>{{ rows.length ? page * 100 + 1 : 0 }}–{{ Math.min((page + 1) * 100, rows.length) }} / {{ rows.length }}</span><button :disabled="(page + 1) * 100 >= rows.length" @click="page++">下一页</button></div>
    </template>
    <p v-else class="trace-note" role="status">本次任务未保存逐股追踪。可在 config 的 analysis 字典中加入 <code>"trace_dates": ["2024-01-29"]</code> 后重新运行，最多指定 5 个信号日期；批量运行默认不导出全部中间数据。</p>
    <p v-if="skipped.length" class="trace-note" role="status">当前 {{ data?.horizon }} 期组合在这些请求信号日不调仓：{{ skipped.map(dateLabel).join('、') }}。未将它们替换为其他日期。</p>
    <p v-if="data?.trace_status?.no_future_dates?.length" class="trace-note" role="status">这些请求信号日之后没有可用的收益日期：{{ data.trace_status.no_future_dates.join('、') }}。未生成盈亏，也未自动补行情。</p>
    <section v-if="constraintRows.length" class="trace-constraints" aria-label="截断容量诊断">
      <h3>截断与目标容量</h3>
      <label class="constraint-date">信号日期<input v-model="constraintDate" type="date" aria-label="约束信号日期" :min="constraintRows[0]?.date" :max="constraintRows.at(-1)?.date" /></label>
      <template v-if="constraint">
        <p class="trace-note" :class="{ 'capacity-shortfall': constraint.full_gross_feasible === false }" role="status">{{ constraint.full_gross_feasible === false ? '总容量不足，不能同时容纳 100% gross 与当前单名上限。' : constraint.full_gross_feasible === true ? '总容量足够；此检查不求解双边配额、净零或重新分配。' : '当前截面没有可用的满额容量判定。' }}本地保留未分配比例，不放宽截断上限。</p>
        <div class="trace-scroll"><table aria-label="目标权重容量"><thead><tr><th>有效证券</th><th>单名上限</th><th>总容量</th><th>实际目标 gross</th><th>实际目标 net</th><th>未分配 gross</th><th>状态</th></tr></thead><tbody><tr><td>{{ number(constraint.valid_count) }}</td><td>{{ percent(constraints.truncation) }}</td><td>{{ percent(constraint.capacity) }}</td><td>{{ percent(constraint.target_gross) }}</td><td>{{ percent(constraint.target_net) }}</td><td>{{ percent(constraint.unallocated_gross) }}</td><td>{{ constraint.status || '--' }}</td></tr></tbody></table></div>
      </template>
      <p v-else class="trace-note">该日期没有目标截面。</p>
      <p class="trace-note">截断约束调仓目标；持有期内价格漂移可能改变敞口。max_weight_series 是期内重复的调仓目标浓度，不是漂移后每日持仓上限。</p>
      <details v-if="constraints.policy || constraints.feasibility_scope"><summary>约束口径</summary><p class="trace-note">{{ constraints.policy }} {{ constraints.feasibility_scope }}</p></details>
    </section>
  </section>
</template>
<style scoped>
.alpha-trace { min-width: 0; padding-top: 18px; }
h2 { font-size: 15px; font-weight: 500; }
h3 { margin: 22px 0 10px; font-size: 13px; font-weight: 500; }
.trace-note { color: #718095; font-size: 11px; line-height: 1.8; overflow-wrap: anywhere; }
.trace-note code { font-size: 11px; }
.trace-cards { display: grid; grid-template-columns: repeat(auto-fit,minmax(125px,1fr)); gap: 8px; }
.trace-cards > div { min-width: 0; padding: 12px; background: #edf2f6; }
.trace-cards span { display: block; color: #718095; font-size: 10px; margin-bottom: 8px; }
.trace-cards strong { font-size: 14px; font-weight: 500; overflow-wrap: anywhere; }
.trace-controls { display: flex; flex-wrap: wrap; gap: 10px; margin: 18px 0 10px; }
.trace-controls label, .constraint-date { display: flex; flex-direction: column; gap: 5px; color: #718095; font-size: 10px; min-width: 0; }
.trace-controls input, .trace-controls select, .constraint-date input { max-width: 100%; width: 165px; font-size: 11px; }
.trace-scroll { max-width: 100%; overflow: auto; max-height: 420px; }
table { border-collapse: collapse; min-width: 900px; width: 100%; font-size: 11px; }
th, td { padding: 8px; text-align: right; white-space: nowrap; border-bottom: 1px solid #dce2ea; }
th { position: sticky; top: 0; background: #f5f8fa; z-index: 1; font-weight: 500; }
th:first-child, td:first-child { text-align: left; }
.trace-pagination { display: flex; justify-content: center; align-items: center; gap: 10px; margin: 12px 0; font-size: 11px; }
.trace-pagination button { padding: 5px 9px; }
.trace-constraints table { min-width: 680px; }
.raw-state { display: block; max-width: 180px; white-space: normal; text-align: left; color: #718095; font-size: 9px; }
.capacity-shortfall { color: #b15f49; }
@media(max-width:500px) { .trace-controls { flex-direction: column; } .trace-controls input, .trace-controls select { width: 100%; } }
</style>
