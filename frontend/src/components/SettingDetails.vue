<script setup>
import DocCode from "./DocCode.vue";
const props = defineProps({ field: { type: Object, required: true }, statusLabels: { type: Object, default: () => ({}) } });
const show = value => value == null ? "未指定" : typeof value === "string" ? value : JSON.stringify(value);
const status = value => props.statusLabels[value] || value;
const hasConfigPath = field => ["config", "config.settings"].includes(field.location);
const configPath = field => hasConfigPath(field) ? `${field.location}.${field.field}` : "项目配置路径尚未确认";
const statusClass = value => value?.startsWith("implemented_") ? "available" : value === "mixed" ? "partial" : "unavailable";
</script>
<template>
  <div class="inline-reference setting-details" role="region" :aria-label="`${field.field} 设置说明`">
    <section data-detail-section="overview"><h4>参数说明</h4>
      <div class="detail-table-wrap"><table><tbody>
        <tr><th>配置位置</th><td><code>{{ configPath(field) }}</code></td></tr>
        <tr><th>类型</th><td>{{ field.type }}</td></tr>
        <tr><th>项目默认</th><td><code>{{ show(field.project_default) }}</code></td></tr>
        <tr><th>官方默认</th><td>{{ field.official_default == null ? '待核实' : show(field.official_default) }}</td></tr>
      </tbody></table></div><p>{{ field.stage }}</p>
    </section>
    <section data-detail-section="options"><h4>允许值与选项</h4>
      <div v-if="field.properties" class="detail-table-wrap"><table><thead><tr><th>子参数</th><th>默认与可选值</th></tr></thead><tbody>
        <tr v-for="(property, name) in field.properties" :key="name"><td><code>{{ name }}</code><small>{{ property.required ? '必填' : '可选' }}</small></td><td>{{ property.values ? show(property.values) : show(property.project_default) }}<small>{{ property.default_rule }}</small></td></tr>
      </tbody></table></div>
      <div v-if="field.options?.length" class="detail-table-wrap"><table class="option-table"><thead><tr><th>值</th><th>含义</th><th>状态</th></tr></thead><tbody>
        <tr v-for="(option, index) in field.options" :key="index"><td><code>{{ show(option.value) }}</code></td><td>{{ option.meaning }}<small>{{ option.reason }}</small></td><td><span class="detail-status" :class="statusClass(option.status || field.status)">{{ status(option.status || field.status) }}</span></td></tr>
      </tbody></table></div>
      <details v-if="field.legal_values" class="allowed-values"><summary>查看完整范围定义</summary><DocCode :code="JSON.stringify(field.legal_values, null, 2)" language="json" label="设置允许值" /></details>
    </section>
    <section data-detail-section="boundaries"><h4>使用说明</h4><p>{{ field.validation?.scope || '按当前服务返回的状态使用该参数；尚未支持的值会明确拒绝。' }}</p><p v-if="!hasConfigPath(field)">此项为平台请求字段或尚未核实的标签，不能直接写入项目 config。</p><p class="detail-caption">项目默认与 BRAIN 官方默认分别记录；未核实的官方默认不会以项目值代替。</p></section>
  </div>
</template>
<style scoped>
.inline-reference { padding: 2px 20px 22px; min-width: 0; border-left: 2px solid #b9cede; background: #f9fbfd; }
section { margin-top: 23px; min-width: 0; }
h4 { margin: 0 0 12px; font-size: 17px; font-weight: 500; color: #3b5872; }
p { color: #627185; font-size: 16px; line-height: 1.9; margin: 10px 0; overflow-wrap: anywhere; }
.detail-caption { font-size: 13px; color: #8b99aa; }
.detail-table-wrap { max-width: 100%; overflow-x: auto; margin: 17px 0; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 12px; border-bottom: 1px solid #e1e9f0; font-size: 15px; line-height: 1.75; vertical-align: top; color: #64758a; overflow-wrap: anywhere; }
th { background: #eef3f8; color: #526b84; font-weight: 500; }
code { font-family: "Liberation Mono", Consolas, monospace; font-size: 14px; }
small { display: block; color: #8d9cad; margin-top: 5px; font-size: 12px; }
.detail-status { padding: 3px 7px; border-radius: 3px; background: #edf1f5; color: #8795a5; font-size: 12px; white-space: nowrap; }
.detail-status.available { background: #edf5f0; color: #54836b; }
.detail-status.partial { background: #faf3e7; color: #9a8151; }
.allowed-values { color: #7b8fa5; font-size: 15px; margin: 20px 0; }
.allowed-values summary { cursor: pointer; }
@media (max-width: 760px) { .inline-reference { padding: 2px 12px 18px; } p { font-size: 15px; } table { min-width: 320px; } .option-table { min-width: 510px; } }
</style>
