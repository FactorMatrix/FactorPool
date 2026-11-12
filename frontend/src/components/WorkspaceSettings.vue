<script setup>
import { computed, reactive, ref } from "vue";
import UiIcon from "./UiIcon.vue";
const props = defineProps({ values: Object, sources: Array });
const emit = defineEmits(["apply", "close"]);
const draft = reactive({ ...props.values }),
  error = ref("");
const selectedSource = computed(() => props.sources?.find((source) => source.id === draft.provider));
function apply() {
  try {
    const params = JSON.parse(draft.params);
    if (!params || Array.isArray(params) || typeof params !== "object")
      throw new Error("构造参数应为 JSON 对象");
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(draft.factorClass))
      throw new Error("请输入有效的 Indicator 类名");
    const periods = draft.horizons.split(",").map((s) => Number(s.trim()));
    if (
      !periods.length ||
      periods.length > 6 ||
      new Set(periods).size !== periods.length ||
      periods.some((n) => !Number.isInteger(n) || n < 1 || n > 252)
    )
      throw new Error("分析周期应为 1..252 的不重复整数，最多6个");
    if (draft.start > draft.end) throw new Error("结束日期不能早于开始日期");
    if (
      !Number.isInteger(Number(draft.groups)) ||
      draft.groups < 1 ||
      draft.groups > 100
    )
      throw new Error("分组数应为1..100的整数");
    if (
      !Number.isInteger(Number(draft.decaySteps)) ||
      draft.decaySteps < 1 ||
      draft.decaySteps > 10
    )
      throw new Error("衰减阶数应为1..10的整数");
    emit("apply", { ...draft });
    emit("close");
  } catch (e) {
    error.value = e.message;
  }
}
</script>
<template>
  <section
    class="settings-panel"
    aria-label="模拟设置"
    @keydown.esc.stop="emit('close')"
  >
    <div class="settings-title">
      <strong>Simulation Settings</strong
      ><button class="icon-button" aria-label="关闭设置" @click="emit('close')">
        <UiIcon name="close" />
      </button>
    </div>
    <form @submit.prevent="apply">
      <div class="settings-grid">
        <label
          >LANGUAGE <span>语言</span
          ><input value="PythonIndicator" readonly aria-label="因子语言"
        /></label>
        <label
          >INSTRUMENT TYPE <span>品种</span><input value="Equity" readonly
        /></label>
        <label class="wide"
          >DATA SOURCE <span>数据源</span
          ><select v-model="draft.provider" aria-label="数据源">
            <option
              v-for="s in sources"
              :key="s.id"
              :value="s.id"
              :disabled="!s.available"
            >
              {{ s.name }}{{ s.available ? "" : " · 不可用" }}
            </option>
          </select></label
        >
        <label
          >REGION <span>市场</span
          ><input
            :value="draft.provider === 'fixture' ? 'SYNTHETIC' : 'CHN'"
            readonly
        /></label>
        <label
          >DELAY <span>信号延迟</span><input value="1 · 下一交易日收益" readonly
        /></label>
        <label v-if="draft.provider !== 'fixture'" class="wide"
          >UNIVERSE <span>股票池</span
          ><span class="checkbox-line"
            ><input v-model="draft.dynamic" type="checkbox" />历史动态全 A
            股</span
          ><input
            v-if="!draft.dynamic"
            v-model="draft.universe"
            placeholder="逗号分隔证券代码"
            aria-label="证券代码"
        /></label>
        <label v-else class="wide"
          >UNIVERSE <span>股票池</span
          ><input value="A / B / C · 合成样例" readonly
        /></label>
        <label
          >START DATE <span>开始日期</span
          ><input
            v-model="draft.start"
            type="date"
            aria-label="开始日期"
            required
        /></label>
        <label
          >END DATE <span>结束日期</span
          ><input
            v-model="draft.end"
            type="date"
            aria-label="结束日期"
            required
        /></label>
        <label
          >FACTOR CLASS <span>因子类</span
          ><input v-model="draft.factorClass" aria-label="因子类" required
        /></label>
        <label
          >PARAMETERS <span>构造参数 JSON</span
          ><input v-model="draft.params" aria-label="构造参数 JSON" required
        /></label>
        <label
          >PERIODS <span>分析周期</span
          ><input
            v-model="draft.horizons"
            aria-label="分析周期"
            placeholder="1,5,20"
        /></label>
        <label
          >GROUPS <span>分组数</span
          ><input
            v-model.number="draft.groups"
            type="number"
            min="1"
            max="100"
            aria-label="分组数"
        /></label>
        <label
          >PREDICTION DECAY <span>预测力衰减阶数</span
          ><input
            v-model.number="draft.decaySteps"
            type="number"
            min="1"
            max="10"
            aria-label="衰减阶数"
        /></label>
        <label
          >PRICE / FREQUENCY <span>价格与频率</span
          ><input value="收盘价 / 前收盘价 − 1 · 日线" readonly
        /></label>
      </div>
      <p v-if="draft.provider === 'market_matrix'" class="report-note">
        <template v-if="draft.snapshotId && draft.snapshotId !== selectedSource?.snapshot?.id">
          已固定快照：{{ draft.snapshotId }}。提交时沿用此快照，任务只读数据库，不自动导入或刷新。
        </template>
        <template v-else-if="selectedSource?.snapshot">
          快照：{{ draft.snapshotId || selectedSource.snapshot.id }}；
          {{ selectedSource.snapshot.date_start }} — {{ selectedSource.snapshot.date_end }}，
          {{ selectedSource.snapshot.symbol_count }} 个证券。
          {{ draft.snapshotId ? '沿用已选快照。' : '提交时固定最新快照。' }}任务只读数据库，不自动导入或刷新。
        </template>
        <template v-else>{{ selectedSource?.message || '暂无行情矩阵快照，请先显式导入。' }}</template>
      </p>
      <p v-if="error" class="settings-error" role="alert">{{ error }}</p>
      <div class="settings-actions">
        <button type="button" class="settings-cancel" @click="emit('close')">
          Cancel</button
        ><button class="apply-button" type="submit">Apply</button>
      </div>
    </form>
  </section>
</template>
