<script setup>
import { computed, reactive, ref, watch } from "vue";
import UiIcon from "./UiIcon.vue";
import SettingsCapabilities from "./SettingsCapabilities.vue";
const props = defineProps({ values: Object, sources: Array, resolved: Object,
  resolveError: String, resolving: Boolean });
const emit = defineEmits(["apply", "close", "resolve"]);
const clone = value => JSON.parse(JSON.stringify(value));
const draft = reactive(clone(props.values)), error = ref("");
const factorParams = ref(JSON.stringify(draft.factor || {}, null, 2));
const periods = ref(draft.analysis.horizons.join(","));
const traceDates = ref((draft.analysis.trace_dates || []).join(","));
const dynamic = ref(draft.data.universe === "all_a_dynamic");
const universe = ref(Array.isArray(draft.data.universe) ? draft.data.universe.join(",") : "");
const previewKey = computed(() => JSON.stringify({ draft, factorParams: factorParams.value,
  periods: periods.value, traceDates: traceDates.value, dynamic: dynamic.value, universe: universe.value }));
const previewRequested = ref(null);
const previewCurrent = computed(() => previewRequested.value === previewKey.value);
const selectedSource = computed(() => props.sources?.find(source => source.id === draft.data.provider));
watch(() => draft.data.provider, () => { delete draft.data.snapshot_id; });
function config() {
  const value = clone(draft);
  value.factor = JSON.parse(factorParams.value);
  if (!value.factor || Array.isArray(value.factor) || typeof value.factor !== "object")
    throw new Error("构造参数应为 JSON 对象");
  value.data.universe = value.data.provider === "fixture" ? "fixture" : dynamic.value ? "all_a_dynamic" : universe.value.split(",").map(s => s.trim()).filter(Boolean);
  value.analysis.horizons = periods.value.split(",").map(s => Number(s.trim()));
  value.analysis.trace_dates = traceDates.value.split(",").map(s => s.trim()).filter(Boolean);
  if (value.base.start_date > value.base.end_date) throw new Error("结束日期不能早于开始日期");
  if (!value.analysis.horizons.length || value.analysis.horizons.length > 6 ||
      new Set(value.analysis.horizons).size !== value.analysis.horizons.length ||
      value.analysis.horizons.some(n => !Number.isInteger(n) || n < 1 || n > 252))
    throw new Error("分析周期应为 1..252 的不重复整数，最多6个");
  return value;
}
function apply() {
  try { emit("apply", config()); emit("close"); }
  catch (e) { error.value = e.message; }
}
function resolve() {
  try { error.value = ""; const value = config(); previewRequested.value = previewKey.value; emit("resolve", value); }
  catch (e) { error.value = e.message; }
}
</script>
<template>
  <section class="settings-panel" aria-label="模拟设置" @keydown.esc.stop="emit('close')">
    <div class="settings-title">
      <strong>模拟设置</strong><button class="icon-button" aria-label="关闭设置" @click="emit('close')"><UiIcon name="close" /></button>
    </div>
    <form @submit.prevent="apply">
      <p class="settings-note">这里设置默认参数。脚本 config 中写出的字段优先，未写出的字段沿用这里的值；config = {} 使用全部设置。</p>
      <div class="settings-grid">
        <label>LANGUAGE <span>语言</span><input value="PythonIndicator" readonly aria-label="因子语言" /></label>
        <label>PRICE / FREQUENCY <span>价格与频率</span><input value="前复权 · 收盘调仓 · 日线" readonly /></label>
        <label class="wide">DATA SOURCE <span>数据源</span><select v-model="draft.data.provider" aria-label="数据源"><option v-for="s in sources" :key="s.id" :value="s.id" :disabled="!s.available">{{ s.name }}{{ s.available ? "" : " · 不可用" }}</option></select></label>
        <label v-if="draft.data.provider !== 'fixture'" class="wide">UNIVERSE <span>股票池</span><span class="checkbox-line"><input v-model="dynamic" type="checkbox" aria-label="历史动态全 A 股" />历史动态全 A 股</span><input v-if="!dynamic" v-model="universe" placeholder="逗号分隔证券代码" aria-label="证券代码" /></label>
        <label v-else class="wide">UNIVERSE <span>股票池</span><input value="合成测试数据" readonly /></label>
        <label>START DATE <span>开始日期</span><input v-model="draft.base.start_date" type="date" aria-label="开始日期" required /></label>
        <label>END DATE <span>结束日期</span><input v-model="draft.base.end_date" type="date" aria-label="结束日期" required /></label>
        <label class="wide">PARAMETERS <span>构造参数 JSON</span><textarea v-model="factorParams" aria-label="构造参数 JSON" rows="3" required /></label>
        <label>PERIODS <span>分析周期</span><input v-model="periods" aria-label="分析周期" placeholder="1,5,20" /></label>
        <label>GROUPS <span>分组数</span><input v-model.number="draft.analysis.groups" type="number" min="1" max="100" aria-label="分组数" required /></label>
        <label>PREDICTION DECAY <span>预测力衰减阶数</span><input v-model.number="draft.analysis.decay_steps" type="number" min="1" max="10" aria-label="衰减阶数" required /></label>
        <label>DECAY <span>权重衰减</span><input v-model.number="draft.settings.decay" type="number" min="0" max="512" aria-label="权重衰减" required /></label>
        <label>NEUTRALIZATION <span>中性化</span><select v-model="draft.settings.neutralization" aria-label="中性化"><option value="NONE">NONE · 无</option><option value="MARKET">MARKET · 市场</option></select></label>
        <label>TRUNCATION <span>单股权重上限</span><input v-model.number="draft.settings.truncation" type="number" min="0" max="1" step="any" aria-label="单股权重上限" required /></label>
        <label>PASTEURIZATION <span>截面范围</span><select v-model="draft.settings.pasteurization" aria-label="截面范围"><option value="ON">ON · 研究池</option><option value="OFF">OFF · 全部存续证券</option></select></label>
        <label>TEST PERIOD <span>测试期</span><input v-model="draft.settings.testPeriod" aria-label="测试期" placeholder="P0Y0M / P1Y6M" required /></label>
        <label>VISUALIZATION <span>图表</span><select v-model="draft.settings.visualization" aria-label="图表"><option :value="true">开启</option><option :value="false">关闭</option></select></label>
      </div>
      <details class="advanced-settings">
        <summary>更多报告参数</summary>
        <div class="settings-grid">
          <label>ANNUAL DAYS <span>年化交易日</span><input v-model.number="draft.analysis.annual_days" type="number" min="1" max="366" aria-label="年化交易日" required /></label>
          <label>MIN IC PAIRS <span>IC 最小配对数</span><input v-model.number="draft.analysis.min_ic_pairs" type="number" min="2" max="2147483647" aria-label="IC 最小配对数" required /></label>
          <label>ICIR DDOF <span>自由度</span><select v-model="draft.analysis.icir_ddof" aria-label="ICIR 自由度"><option :value="0">0</option><option :value="1">1</option></select></label>
          <label>BOOK SIZE <span>名义 book</span><input v-model.number="draft.analysis.book_size" type="number" min="0.0000000001" max="1000000000000000" step="any" aria-label="名义 book" required /></label>
          <label>PNL UNIT <span>PnL 单位标签</span><input v-model="draft.analysis.pnl_unit" pattern="[A-Z][A-Z0-9_]{0,11}" aria-label="PnL 单位标签" required /></label>
          <label>TRACE DATES <span>追踪日期</span><input v-model="traceDates" aria-label="追踪日期" placeholder="逗号分隔，最多5个日期" /></label>
        </div>
      </details>
      <p v-if="draft.data.provider === 'market_matrix'" class="settings-note">
        <template v-if="draft.data.snapshot_id">已固定快照：{{ draft.data.snapshot_id }}。</template>
        <template v-else-if="selectedSource?.snapshot">提交时固定快照：{{ selectedSource.snapshot.id }}；{{ selectedSource.snapshot.symbol_count }} 个证券。</template>
        <template v-else>{{ selectedSource?.message || '暂无已发布行情快照。' }}</template>
      </p>
      <div class="config-preview">
        <button type="button" class="settings-cancel" aria-label="校验源码配置" :disabled="resolving" @click="resolve">{{ resolving ? '正在校验…' : '查看有效配置' }}</button>
        <p v-if="previewCurrent && resolveError" class="settings-error" role="alert">{{ resolveError }}</p>
        <template v-else-if="previewCurrent && resolved"><p>{{ resolved.factor_class }}</p><pre aria-label="有效配置">{{ JSON.stringify(resolved.effective_config, null, 2) }}</pre></template>
      </div>
      <SettingsCapabilities />
      <p v-if="error" class="settings-error" role="alert">{{ error }}</p>
      <div class="settings-actions"><button type="button" class="settings-cancel" @click="emit('close')">取消</button><button class="apply-button" type="submit">应用设置</button></div>
    </form>
  </section>
</template>
<style scoped>
.settings-grid label { font-size: 11px; }
.settings-grid label > span:not(.checkbox-line) { font-size: 10px; }
.settings-note { color: #bdc7d7; font-size: 12px; line-height: 1.7; margin: 0 0 18px; }
.advanced-settings, .config-preview { margin-top: 20px; font-size: 12px; }
.advanced-settings summary { cursor: pointer; margin-bottom: 16px; }
.config-preview pre { max-height: 260px; overflow: auto; padding: 12px; background: #2b2b2b; color: #d5dbe4; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.config-preview button { padding: 7px 12px; }
.settings-grid textarea { resize: vertical; width: 100%; padding: 7px 9px; min-height: 60px; background: #4a5265; color: #eef0f6; border: 1px solid #667086; font: 12px/1.5 Consolas, monospace; }
</style>
