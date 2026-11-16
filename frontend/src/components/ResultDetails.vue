<script setup>
import { computed } from "vue";

const props = defineProps({
  kind: { type: String, required: true },
  task: Object,
  result: Object,
  savedFactor: Object,
  busy: Boolean,
});
const emit = defineEmits(["save-factor"]);
const day = computed(() => props.result?.diagnostics?.horizons?.["1"]);
const display = value => value == null || value === "" ? "—" : String(value);
const percent = value => typeof value === "number" && Number.isFinite(value)
  ? `${(value * 100).toFixed(2)}%` : "—";
const sourceHash = computed(() => props.result?.metadata?.source_sha256);
const missingCorrelation = computed(() => props.result?.diagnostics?.conventions?.missing_datasets);
const capacities = computed(() => day.value?.alpha_constraints
  || props.result?.diagnostics?.alpha_constraints?.daily || []);
const capacityStatus = computed(() => {
  const rows = Array.isArray(capacities.value) ? capacities.value : [];
  const shortfall = rows.filter(row => row.full_gross_feasible === false);
  const feasible = rows.filter(row => row.full_gross_feasible === true).length;
  return { rows, shortfall, feasible, unknown: rows.length - feasible - shortfall.length };
});
const checks = computed(() => {
  const state = props.task?.status;
  const stopped = ["FAILED", "INTERRUPTED", "CANCELLED"].includes(state);
  const loaded = Boolean(props.result);
  const supported = props.result?.metadata?.report_version === 3
    && props.result?.diagnostics?.version === 3;
  const meta = props.result?.metadata;
  const book = day.value?.portfolios?.alpha?.pnl;
  const validBook = typeof book?.book_size === "number" && Number.isFinite(book.book_size)
    && book.book_size > 0 && typeof book.unit === "string" && book.unit.length > 0;
  return [
    {
      key: "execution", name: "任务执行",
      status: state === "SUCCEEDED" ? "complete" : stopped ? "stopped" : "pending",
      value: state === "SUCCEEDED" ? "已完成" : stopped ? "已停止" : "等待完成",
      detail: props.task?.error || (state ? `本次运行状态：${state}。` : "尚未提交本次研究。"),
    },
    {
      key: "report", name: "研究报告",
      status: !loaded ? "pending" : supported ? "complete" : "unavailable",
      value: !loaded ? "等待报告" : supported ? "已加载" : "不可用",
      detail: !loaded ? "执行完成后读取本次运行的报告。"
        : `报告格式：${display(meta?.report_version)}；诊断格式：${display(props.result?.diagnostics?.version)}。`,
    },
    {
      key: "period", name: "1D 结果",
      status: !loaded ? "pending" : day.value ? "complete" : "unavailable",
      value: !loaded ? "等待报告" : day.value ? "已提供" : "未提供",
      detail: "1D 是收益周期，与 TRAIN / TEST / IS 样本分段不同。这里只检查报告是否提供 1D 数据，不重新计算其他周期。",
    },
    {
      key: "snapshot", name: "数据来源",
      status: meta?.snapshot_id ? "complete" : "unavailable",
      value: meta?.snapshot_id ? meta.synthetic ? "合成数据" : "行情快照" : "未提供",
      detail: meta?.snapshot_id ? `快照：${meta.snapshot_id}。${meta.synthetic ? "本结果使用明确标注的合成数据。" : "本结果使用已发布的行情快照。"}`
        : "本报告没有提供可核对的数据快照标识。",
    },
    {
      key: "book", name: "PnL 单位",
      status: validBook ? "complete" : "unavailable",
      value: validBook ? `${book.book_size.toLocaleString("en-US")} ${book.unit}` : "未提供",
      detail: "使用报告中的固定名义 book 和单位，不做货币换算。BOOK 表示名义单位，不代表某种货币；PnL 与复利净值分开。",
    },
  ];
});
const canSave = computed(() => Boolean(props.result && props.task?.status === "SUCCEEDED"
  && props.task?.snapshot?.source_code && !props.savedFactor));
</script>

<template>
  <div v-if="kind === 'correlation'" class="result-details correlation-details">
    <div class="correlation-row">
      <h3>Self Correlation</h3>
      <div class="correlation-values" aria-label="自相关结果">
        <div v-for="label in ['Maximum', 'Minimum', 'Last Run']" :key="label">
          <span>{{ label }}</span><strong>—</strong>
        </div>
      </div>
    </div>
    <p class="detail-note" role="status">本次报告未提供本地因子池的相关性结果。</p>
    <details>
      <summary>查看数据来源</summary>
      <p class="detail-note">{{ missingCorrelation?.self_correlation || '尚未选择可比较的本地因子池。' }}</p>
      <p class="detail-note">{{ missingCorrelation?.production_correlation || '未接入 BRAIN 生产因子库。' }} 不将 IC 或其他统计替代自相关结果。</p>
    </details>
  </div>

  <div v-else-if="kind === 'testing'" class="result-details testing-details" aria-label="本地研究运行状态">
    <p class="detail-note">以下状态来自本次任务和报告，用于核对研究是否可查看。BRAIN 提交检查及其阈值尚未接入。</p>
    <details v-for="check in checks" :key="check.key" class="local-check" :class="'status-' + check.status" :data-check="check.key">
      <summary><span>{{ check.name }}</span><span class="check-state" :class="check.status">{{ check.value }}</span></summary>
      <p class="detail-note">{{ check.detail }}</p>
    </details>
    <details class="local-check" :class="capacityStatus.shortfall.length ? 'status-stopped' : !capacityStatus.rows.length || capacityStatus.unknown ? 'status-unavailable' : 'status-complete'" data-check="capacity">
      <summary><span>实际目标容量</span><span class="check-state" :class="capacityStatus.shortfall.length ? 'stopped' : !capacityStatus.rows.length || capacityStatus.unknown ? 'unavailable' : 'complete'">{{ capacityStatus.rows.length ? '已保存诊断' : '未提供' }}</span></summary>
      <template v-if="capacityStatus.rows.length">
        <p class="detail-note">已记录 {{ capacityStatus.rows.length }} 个截面：总容量充足 {{ capacityStatus.feasible }} 个、不足 {{ capacityStatus.shortfall.length }} 个、未判定 {{ capacityStatus.unknown }} 个。这是报告中的目标容量判定，不是 BRAIN 提交审核。</p>
        <ul v-if="capacityStatus.shortfall.length" class="capacity-examples">
          <li v-for="(row, index) in capacityStatus.shortfall.slice(0, 8)" :key="`${row.date}-${index}`">{{ display(row.date) }}：目标 gross {{ percent(row.target_gross) }}，未分配 gross {{ percent(row.unallocated_gross) }}。</li>
        </ul>
        <p class="detail-note">这里最多展示 8 个不足截面；完整已保存的逐日诊断可在 Weights / PnL 中查看。</p>
      </template>
      <p v-else class="detail-note">本次报告没有已保存的截断容量诊断；不把未检查显示为通过。</p>
    </details>
  </div>

  <div v-else-if="kind === 'properties'" class="result-details properties-details">
    <dl>
      <div><dt>Run ID</dt><dd>{{ display(task?.id) }}</dd></div>
      <div><dt>Name</dt><dd>{{ display(task?.name || task?.snapshot?.name) }}</dd></div>
      <div><dt>Created</dt><dd>{{ display(task?.created_at) }}</dd></div>
      <div><dt>Completed</dt><dd>{{ display(task?.finished_at) }}</dd></div>
      <div class="property-wide"><dt>Source SHA256</dt><dd class="source-hash">{{ display(sourceHash) }}</dd></div>
      <div><dt>Factor ID</dt><dd>{{ display(savedFactor?.id) }}</dd></div>
      <div><dt>Saved Version</dt><dd>{{ savedFactor?.version == null ? '—' : `v${savedFactor.version}` }}</dd></div>
      <div><dt>Last Saved</dt><dd>{{ display(savedFactor?.version_at) }}</dd></div>
      <div class="property-wide"><dt>Description</dt><dd>{{ display(savedFactor?.description || task?.snapshot?.description) }}</dd></div>
    </dl>
    <p class="detail-note">类别、标签和颜色尚未接入因子存储。本区显示本次运行的快照，不跟随编辑器中未运行的修改。</p>
    <button class="save-result-factor" aria-label="保存本次因子快照" :disabled="busy || !canSave" @click="emit('save-factor')">{{ savedFactor ? '已保存本次快照' : busy ? '正在保存…' : '保存本次快照' }}</button>
    <p v-if="!savedFactor" class="detail-note">保存成功后才显示关联的 Factor ID、版本和保存时间。</p>
  </div>
</template>

<style scoped>
.result-details { min-width: 0; color: #445163; }
h3 { margin: 0; font-size: 13px; font-weight: 600; }
.detail-note { margin: 8px 0; color: #758396; font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.correlation-row { display: grid; grid-template-columns: minmax(130px, 1fr) minmax(0, 2fr); align-items: center; gap: 14px; padding: 8px 0 12px; }
.correlation-values { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; border-left: 1px solid #e5e9ec; padding-left: 18px; }
.correlation-values span { display: block; color: #8490a1; font-size: 11px; }
.correlation-values strong { display: block; margin-top: 8px; font-size: 17px; font-weight: 400; }
details > summary { cursor: pointer; color: #66778a; font-size: 12px; }
details > summary:focus-visible, button:focus-visible { outline: 2px solid #7bad42; outline-offset: 3px; }
.local-check { margin: 1px 0; border-bottom: 2px solid #b9c4ca; background: #eef0f2; }
.local-check.status-complete { border-color: #6cb659; }
.local-check.status-stopped { border-color: #c96d71; }
.local-check > summary { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 12px; list-style: none; }
.local-check > summary::-webkit-details-marker { display: none; }
.local-check > summary::after { content: '⌄'; color: #8496a4; }
.local-check[open] > summary::after { content: '⌃'; }
.local-check > summary > span:first-child { flex: 1; }
.local-check > .detail-note, .local-check > .capacity-examples { margin-left: 12px; margin-right: 12px; }
.check-state { font-size: 11px; text-align: right; overflow-wrap: anywhere; }
.check-state.complete { color: #6d9a43; }
.check-state.stopped { color: #b46d54; }
.check-state.pending, .check-state.unavailable { color: #929dac; }
.capacity-examples { margin: 12px 0; padding-left: 22px; font-size: 12px; line-height: 1.8; }
dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 20px; margin: 0; }
dl > div { min-width: 0; font-size: 12px; line-height: 1.5; }
dl > .property-wide { grid-column: 1 / -1; }
dt { margin-bottom: 5px; color: #526173; font-weight: 600; }
dd { min-height: 32px; margin: 0; padding: 7px 9px; border: 1px solid #dbe3e9; border-radius: 3px; background: #f8fafb; color: #667487; overflow-wrap: anywhere; }
.source-hash { font-family: ui-monospace, monospace; font-size: 11px; }
.save-result-factor { margin-top: 16px; padding: 8px 14px; border: 1px solid #b6caa7; background: #f8fbf5; color: #6b9344; font-size: 12px; cursor: pointer; }
.save-result-factor:disabled { color: #9ba6af; border-color: #dce2e8; background: #f5f7f9; cursor: default; }
@container (max-width: 460px) {
  .correlation-row { grid-template-columns: 1fr; gap: 10px; }
  .correlation-values { padding-left: 0; border-left: 0; }
  dl { grid-template-columns: 1fr; gap: 10px; }
  dl > .property-wide { grid-column: auto; }
}
</style>
