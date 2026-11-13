<script setup>
import { computed } from "vue";
import DocCode from "./DocCode.vue";
import { operatorReference, operatorReferenceConventions } from "../operator-reference";
const props = defineProps({ operator: { type: Object, required: true } });
const reference = computed(() => operatorReference[props.operator.name]);
const connected = computed(() => Boolean(props.operator.native && props.operator.pool_wiring === "connected"));
const show = value => value == null ? "未指定" : typeof value === "string" ? value : JSON.stringify(value);
</script>
<template>
  <div class="inline-reference operator-details" role="region" :aria-label="`${operator.name} 算子说明`">
    <section data-detail-section="signature">
      <h4>调用方式</h4>
      <DocCode v-if="operator.frontend" :code="reference?.signature || operator.frontend" label="算子调用方式" />
      <p v-else>此算子当前没有可调用入口。</p>
      <p class="detail-caption">数据要求：{{ operator.data }}</p>
    </section>
    <section data-detail-section="parameters">
      <h4>参数</h4>
      <div v-if="reference?.parameters?.length" class="detail-table-wrap">
        <table><thead><tr><th>参数</th><th>含义</th></tr></thead><tbody>
          <tr v-for="parameter in reference.parameters" :key="parameter.name"><td><code>{{ parameter.name }}</code></td><td>{{ parameter.description }}</td></tr>
        </tbody></table>
      </div>
      <p v-else>参数按上方调用入口使用。</p>
    </section>
    <section data-detail-section="example">
      <h4>计算示例</h4>
      <template v-if="reference?.example && connected">
        <p class="detail-caption">{{ operatorReferenceConventions[0] }}</p>
        <DocCode :code="reference.example.expression" label="算子示例" />
        <div class="detail-table-wrap"><table><tbody>
          <tr><th>输入</th><td class="example-value">{{ show(reference.example.input) }}</td></tr>
          <tr><th>输出</th><td class="example-value">{{ show(reference.example.output) }}</td></tr>
        </tbody></table></div>
        <p class="detail-caption">时序示例展示所列完整窗口的当前输出。</p>
      </template>
      <p v-else-if="!connected">此项尚未接通，不提供可运行示例。</p>
      <p v-else>调用入口已接通；具体输入与边界按下方说明使用。</p>
    </section>
    <section data-detail-section="boundaries">
      <h4>使用说明</h4>
      <ul v-if="reference?.notes?.length"><li v-for="note in reference.notes" :key="note">{{ note }}</li></ul>
      <p>{{ connected ? '当前调用链可用，仍需满足输入数据和窗口要求。' : '台账中的名称和候选写法不代表该算子已经可用。' }}</p>
      <p class="detail-caption">这里说明 FactorPool 当前计算规则，BRAIN 官方数值一致性尚未逐项核验。</p>
    </section>
  </div>
</template>
<style scoped>
.inline-reference { padding: 2px 20px 22px; min-width: 0; border-left: 2px solid #b9cede; background: #f9fbfd; }
section { margin-top: 23px; min-width: 0; }
h4 { margin: 0 0 12px; font-size: 17px; font-weight: 500; color: #3b5872; }
p, li { color: #627185; font-size: 16px; line-height: 1.9; margin: 10px 0; overflow-wrap: anywhere; }
ul { padding-left: 21px; }
.detail-caption { font-size: 13px; color: #8b99aa; }
.detail-table-wrap { max-width: 100%; overflow-x: auto; margin: 17px 0; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 12px; border-bottom: 1px solid #e1e9f0; font-size: 15px; line-height: 1.75; vertical-align: top; color: #64758a; overflow-wrap: anywhere; }
th { background: #eef3f8; color: #526b84; font-weight: 500; }
code, .example-value { font-family: "Liberation Mono", Consolas, monospace; font-size: 14px; white-space: pre-wrap; }
@media (max-width: 760px) { .inline-reference { padding: 2px 12px 18px; } p, li { font-size: 15px; } table { min-width: 320px; } }
</style>
