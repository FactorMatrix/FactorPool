<script setup>
import { computed, ref } from "vue";
import { api } from "../api";
const catalog = ref(null), error = ref(""), loading = ref(false);
const rows = computed(() => (catalog.value?.fields || []).flatMap(field =>
  (field.options?.length ? field.options : [{ value: field.legal_values }])
    .map(option => ({ field, option, status: option.status || field.status }))));
const show = value => value === undefined ? "未指定" : typeof value === "string" ? value : JSON.stringify(value);
const status = value => catalog.value?.status_labels?.[value] || value;
async function open(event) {
  if (!event.target.open || catalog.value || loading.value) return;
  loading.value = true;
  try { catalog.value = await api("/simulation-settings"); }
  catch (e) { error.value = e.message; }
  finally { loading.value = false; }
}
</script>
<template>
  <details class="config-coverage" @toggle="open">
    <summary>配置覆盖表 · 参数与选项</summary>
    <p v-if="loading">正在读取配置能力…</p>
    <p v-else-if="error" role="status">{{ error }}</p>
    <template v-else-if="catalog">
      <p>项目默认值与 BRAIN 官方默认值分别记录；规则或数据不足的选项会明确拒绝。</p>
      <div class="coverage-scroll">
        <table aria-label="配置覆盖表">
          <thead><tr><th>参数 / 位置</th><th>选项 / 类型</th><th>项目默认</th><th>官方默认</th><th>状态与实际作用</th></tr></thead>
          <tbody><tr v-for="(row, index) in rows" :key="index">
            <td>{{ row.field.field }}<small>{{ row.field.location }}</small></td>
            <td>{{ show(row.option.value) }}<small>{{ row.field.type }}</small></td>
            <td>{{ show(row.field.project_default) }}</td>
            <td>{{ row.field.official_default == null ? '待核实' : show(row.field.official_default) }}</td>
            <td>{{ status(row.status) }}<small>{{ row.option.reason || row.option.meaning || row.field.stage }}</small></td>
          </tr></tbody>
        </table>
      </div>
    </template>
  </details>
</template>
<style scoped>
.config-coverage { margin-top: 18px; font-size: 12px; }
summary { cursor: pointer; font-weight: 600; }
.coverage-scroll { max-height: 310px; overflow: auto; }
table { border-collapse: collapse; min-width: 700px; width: 100%; }
th, td { border-bottom: 1px solid #e3e8ef; padding: 8px; text-align: left; vertical-align: top; }
small { display: block; color: #687789; margin-top: 4px; }
</style>
