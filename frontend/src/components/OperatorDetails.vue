<script setup>
import { computed } from "vue";
import DocCode from "./DocCode.vue";
const props = defineProps({ operator: { type: Object, required: true } });
const reference = computed(() => props.operator.reference);
const connected = computed(() => Boolean(props.operator.native && props.operator.pool_wiring === "connected"));
const basicParameters = computed(() => (reference.value?.parameters || []).filter(parameter => !parameter.advanced));
const advancedParameters = computed(() => (reference.value?.parameters || []).filter(parameter => parameter.advanced));
const show = value => value == null ? "—" : typeof value === "boolean" ? (value ? "True" : "False") : typeof value === "string" ? value : JSON.stringify(value);
const required = parameter => parameter.optional === '二选一' ? '二选一' : parameter.optional ? '可选' : '必填';
</script>
<template>
  <div class="inline-reference operator-details" role="region" :aria-label="`${operator.name} 算子说明`">
    <section data-detail-section="purpose">
      <h4>{{ reference?.title || operator.name }}</h4>
      <p>{{ operator.summary }}</p>
    </section>
    <section data-detail-section="signature">
      <h4>调用方式</h4>
      <DocCode v-if="operator.frontend" :code="operator.frontend" label="算子调用方式" />
      <p v-else>此算子当前没有可调用入口。</p>
    </section>
    <section data-detail-section="parameters">
      <h4>参数</h4>
      <div v-if="basicParameters.length" class="detail-table-wrap">
        <table><thead><tr><th>参数</th><th>类型</th><th>要求</th><th>默认值</th><th>含义</th></tr></thead><tbody>
          <tr v-for="parameter in basicParameters" :key="parameter.name"><td><code>{{ parameter.name }}</code></td><td>{{ parameter.type }}</td><td>{{ required(parameter) }}</td><td><code>{{ show(parameter.default) }}</code></td><td>{{ parameter.description }}</td></tr>
        </tbody></table>
      </div>
      <p v-else>此写法不需要额外参数。</p>
    </section>
    <section data-detail-section="example">
      <h4>输入与输出</h4>
      <template v-if="reference?.example && connected">
        <p class="detail-caption">x、y、g 是输入表达式的占位名；表中数组用于说明数据方向。</p>
        <DocCode :code="reference.example.expression" label="算子示例" />
        <div class="detail-table-wrap"><table><tbody>
          <tr><th>数据方向</th><td>{{ reference.example.direction }}</td></tr>
          <tr><th>输入</th><td class="example-value">{{ show(reference.example.input) }}</td></tr>
          <tr><th>输出</th><td class="example-value">{{ show(reference.example.output) }}</td></tr>
        </tbody></table></div>
      </template>
      <p v-else-if="!connected">此项尚未接通，不提供可运行示例。</p>
      <p v-else>请按参数说明提供同轴输入。</p>
    </section>
    <section data-detail-section="boundaries">
      <h4>预热、缺失与边界</h4>
      <ul v-if="reference?.notes?.length"><li v-for="note in reference.notes" :key="note">{{ note }}</li></ul>
      <p class="detail-caption">未就绪、不可用与有效零值分别处理；不会把不可用结果自动填成零。</p>
    </section>
    <details data-detail-section="advanced" class="advanced-details">
      <summary>高级参数与执行说明</summary>
      <div v-if="advancedParameters.length" class="detail-table-wrap">
        <table><thead><tr><th>参数</th><th>类型</th><th>要求</th><th>默认值</th><th>含义</th></tr></thead><tbody>
          <tr v-for="parameter in advancedParameters" :key="parameter.name"><td><code>{{ parameter.name }}</code></td><td>{{ parameter.type }}</td><td>{{ required(parameter) }}</td><td><code>{{ show(parameter.default) }}</code></td><td>{{ parameter.description }}</td></tr>
        </tbody></table>
      </div>
      <DocCode v-if="reference?.advanced_syntax" :code="reference.advanced_syntax" label="高级参数示例" />
      <p>执行依赖：{{ reference?.execution_dependencies?.join('；') }}</p>
      <p>输入数据：{{ operator.data }}</p>
      <p>{{ connected ? '当前公开写法已接通原生计算链。' : '当前公开写法尚未接通。' }} BRAIN 官方数值一致性尚未逐项核验。</p>
    </details>
  </div>
</template>
<style scoped>
.inline-reference { padding: 2px 20px 22px; min-width: 0; border-left: 2px solid #b9cede; background: #f9fbfd; }
section, .advanced-details { margin-top: 23px; min-width: 0; }
h4, summary { margin: 0 0 12px; font-size: 17px; font-weight: 500; color: #3b5872; }
summary { cursor: pointer; }
p, li { color: #627185; font-size: 16px; line-height: 1.9; margin: 10px 0; overflow-wrap: anywhere; }
ul { padding-left: 21px; }
.detail-caption { font-size: 14px; color: #77889b; }
.detail-table-wrap { max-width: 100%; overflow-x: auto; margin: 17px 0; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 12px; border-bottom: 1px solid #e1e9f0; font-size: 15px; line-height: 1.75; vertical-align: top; color: #64758a; overflow-wrap: anywhere; }
th { background: #eef3f8; color: #526b84; font-weight: 500; }
th:first-child, td:first-child { min-width: 82px; }
td:first-child code { white-space: nowrap; }
code, .example-value { font-family: "Liberation Mono", Consolas, monospace; font-size: 14px; white-space: pre-wrap; }
@media (max-width: 760px) { .inline-reference { padding: 2px 12px 18px; } p, li { font-size: 15px; } table { min-width: 320px; } }
</style>
