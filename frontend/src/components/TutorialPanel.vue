<script setup>
import { computed, ref, watch } from "vue";
import { api } from "../api";
import { quickStartCode, operatorDescriptions, settingHints } from "../tutorial-content";

const props = defineProps({ operators: { type: Array, default: () => [] } });
const section = ref("start");
const operatorSearch = ref(""), operatorFilter = ref("connected"), category = ref("all");
const settingsSearch = ref(""), catalog = ref(null), loading = ref(false), loadError = ref("");
const copied = ref("");
const tabs = [["start", "快速开始"], ["operators", "算子"], ["settings", "设置"]];
const connected = (operator) => Boolean(operator.native && operator.pool_wiring === "connected");
const connectedCount = computed(() => props.operators.filter(connected).length);
const operatorCategory = (name) => name.startsWith("ts_") ? "ts"
  : name.startsWith("group_") ? "group"
  : /^(vec_|reduce_)/.test(name) ? "vector"
  : ["rank", "zscore", "normalize", "scale", "scale_down", "quantile", "winsorize", "bucket"].includes(name) ? "cs"
  : "basic";
const filteredOperators = computed(() => props.operators.filter(operator => {
  const haystack = [operator.name, operator.frontend, operator.data, operatorDescriptions[operator.name]].join(" ").toLowerCase();
  return haystack.includes(operatorSearch.value.trim().toLowerCase())
    && (operatorFilter.value === "all" || connected(operator) === (operatorFilter.value === "connected"))
    && (category.value === "all" || operatorCategory(operator.name) === category.value);
}));
const filteredSettings = computed(() => (catalog.value?.fields || []).filter(field =>
  JSON.stringify([field.field, field.location, field.stage, field.options, field.properties, settingHints[field.field]])
    .toLowerCase().includes(settingsSearch.value.trim().toLowerCase())));
const show = value => value == null ? "未指定" : typeof value === "string" ? value : JSON.stringify(value);
const readable = value => JSON.stringify(value, null, 2);
const status = value => catalog.value?.status_labels?.[value] || value;
const statusClass = value => value?.startsWith("implemented_") ? "available" : value === "mixed" ? "partial" : "unavailable";
const hasConfigLocation = field => field.location === "config" || field.location === "config.settings";
const fieldPath = field => hasConfigLocation(field) ? `${field.location}.${field.field}` : field.field;
async function loadCatalog() {
  if (loading.value) return;
  loading.value = true;
  loadError.value = "";
  try { catalog.value = await api("/simulation-settings"); }
  catch (error) { loadError.value = error.message; }
  finally { loading.value = false; }
}
watch(section, value => {
  if (value === "settings" && !catalog.value) loadCatalog();
});
async function copyExample() {
  try {
    await navigator.clipboard.writeText(quickStartCode);
    copied.value = "已复制。回到 Simulate 页粘贴到代码框，再运行研究。";
  } catch {
    copied.value = "当前连接不支持自动复制，请选择下方代码后复制。";
  }
}
</script>

<template>
  <section class="tutorial" aria-label="使用教程">
    <div class="tutorial-intro">
      <span class="tutorial-eyebrow">FACTORPOOL GUIDE</span>
      <h2>从一个因子开始</h2>
      <p>在 Simulate 页编写 config 和因子，再查看研究结果。这里可以查算子、参数与当前支持范围。</p>
    </div>
    <nav class="tutorial-tabs" aria-label="教程章节">
      <button v-for="[key, label] in tabs" :key="key" :class="{ active: section === key }"
        :aria-pressed="section === key" @click="section = key">{{ label }}</button>
    </nav>

    <div v-if="section === 'start'" class="tutorial-body">
      <article class="tutorial-card">
        <h3>1 · 先跑通合成样例</h3>
        <p>这份脚本用三只虚拟股票演示“低成交量排名”，不读取真实行情。点击复制，回到 <b>Simulate</b> 页粘贴到代码框后运行。</p>
        <button class="copy-example" aria-label="复制完整示例" @click="copyExample">复制完整示例</button>
        <p v-if="copied" class="tutorial-feedback" role="status">{{ copied }}</p>
        <pre class="tutorial-code" tabindex="0" aria-label="入门完整脚本"><code>{{ quickStartCode }}</code></pre>
      </article>
      <article class="tutorial-card">
        <h3>2 · 理解因子结构</h3>
        <ul>
          <li><code>config</code> 是本次研究配置；<code>factor</code> 会传入类的构造函数。</li>
          <li><code>update(input)</code> 用已完成的日线更新因子，<code>self.ts</code> 计算单股时序，<code>self.cs</code> 计算同日截面。</li>
          <li>把因子赋给 <code>self.value</code>，返回 <code>self.ts.is_ready(value)</code> 声明窗口是否准备好。预热不足与缺失不会当作有效零。</li>
          <li>末尾保留 <code>if __name__ == "__main__":</code> 与 <code>类名.run(config)</code>；配置使用字面量，不在顶层启动其他入口。</li>
        </ul>
      </article>
      <article class="tutorial-card">
        <h3>3 · 换成真实行情</h3>
        <pre><code>"data": {
    "provider": "market_matrix",
    "universe": ["000001.XSHE", "600000.XSHG"]
}</code></pre>
        <p>请按研究需要调整日期和分组数；IC 至少需要足够的有效股票配对。全 A 动态股票池可写 <code>"universe": "all_a_dynamic"</code>。研究直接读已发布行情快照，不导入、补数或刷新数据。</p>
        <dl class="tutorial-fields">
          <div><dt>价格</dt><dd><code>input.open</code>、<code>input.high</code>、<code>input.low</code>、<code>input.close</code>、<code>input.prev_close</code></dd></div>
          <div><dt>量额</dt><dd><code>input.volume</code>、<code>input.total_turnover</code></dd></div>
          <div><dt>状态</dt><dd><code>input.is_st</code>、<code>input.is_suspended</code>；须以实际数据为准。</dd></div>
        </dl>
      </article>
      <article class="tutorial-card">
        <h3>窗口、价格与收益</h3>
        <p>默认 TS 窗口按有效观测推进，缺失不占有效观测数。若需要每个交易日都占一格，可显式使用：</p>
        <pre><code>value = self.ts.mean(input.close, 5,
                     clock="trading_days")</code></pre>
        <p>交易日窗口只支持 mean、sum、min、max、std、delay、delta、decay_linear；严格窗口遇 NaN 会传播缺失，缺 Bar 不生成观测。</p>
        <p>因子价格默认 <code>data.factor_price="raw"</code>。显式设为 <code>"quote_adjusted"</code> 时，仅用相邻前收盘价/收盘价推导历史价格倍率；量额与收益矩阵不变。未知、无量或缺行情桥接会重新预热。</p>
        <p class="tutorial-note">报价复权当前只接受支持的有限价格窗口；递归指标、独立价格与非价格历史、复杂价格 TS→CS→TS，以及与新增数学算子/交易日窗口混用会明确拒绝。可先从纯价格均线开始。</p>
        <p>研究收益为 <code>close / prev_close - 1</code>；收盘形成信号，使用下一交易日起的收益，不再把输入额外移一天。组合按比例权重计算。</p>
      </article>
      <article class="tutorial-card">
        <h3>读结果与保存</h3>
        <p><b>Chart / Summary</b> 查看 Alpha 收益与指标，<b>IC Analysis / Groups</b> 查看原始因子与分组表现，<b>Factor Values</b> 核对逐股值，<b>Source</b> 查看提交快照。<b>Save</b> 保存因子版本；<b>Settings</b> 校验源码配置。</p>
        <p>切换模拟草稿会保留本页编辑；刷新后恢复最近任务。未运行或未保存的编辑，请先保存版本。</p>
      </article>
    </div>

    <div v-else-if="section === 'operators'" class="tutorial-body">
      <p class="tutorial-lead">当前台账 {{ operators.length }} 项，已接通 {{ connectedCount }} 项。已接通表示项目调用链可用，不代表 BRAIN 官方数值一致；数据要求也必须满足。</p>
      <label class="tutorial-search"><span>算子搜索</span><input v-model="operatorSearch" aria-label="算子搜索" placeholder="名称、调用写法或用途，例如 mean" type="search" /></label>
      <div class="tutorial-filters">
        <label><span>支持范围</span><select v-model="operatorFilter" aria-label="算子状态"><option value="connected">已接通</option><option value="all">全部台账</option><option value="pending">待接通</option></select></label>
        <label><span>类型</span><select v-model="category" aria-label="算子类型"><option value="all">全部类型</option><option value="basic">数学 / 逻辑 / 其他</option><option value="ts">时序 TS</option><option value="cs">截面 CS</option><option value="group">分组</option><option value="vector">向量 / 归约</option></select></label>
      </div>
      <div class="tutorial-note">
        <b>截面排名优先用 <code>self.cs.rank_unit(x)</code></b>，窗口完成后再返回 ready。分组算子需要与证券轴对齐的真实分组标签；当前行情 Provider 没有行业分类，不能把任意数字当行业。向量财务字段也需实际数据接入。
      </div>
      <p class="tutorial-count" role="status">显示 {{ filteredOperators.length }} 项</p>
      <article v-for="operator in filteredOperators" :key="operator.name" :data-operator="operator.name" class="tutorial-card operator-card">
        <div class="tutorial-card-heading"><h3>{{ operator.name }}</h3><span class="tutorial-tag" :class="connected(operator) ? 'available' : 'unavailable'">{{ connected(operator) ? '已接通' : '待接通' }}</span></div>
        <code class="operator-call">{{ operator.frontend || '暂无可调用入口' }}</code>
        <p>{{ operatorDescriptions[operator.name] || (connected(operator) ? '请按列出的项目调用入口使用，并满足下方数据要求。' : '当前没有已接通的研究调用链；仅作为待实现或待核验项记录。') }}</p>
        <small>数据要求：{{ operator.data }}</small>
        <small v-if="!connected(operator) && operator.frontend">列出的写法是台账记录，当前支持状态以“待接通”为准。</small>
      </article>
      <p v-if="!filteredOperators.length" class="tutorial-empty">没有匹配算子，可清空搜索或改为“全部台账”。</p>
    </div>

    <div v-else class="tutorial-body">
      <p class="tutorial-lead">配置写在 Simulate 页源码的 <code>config</code> 中。这里展示当前服务返回的参数和选项状态；项目默认值与官方默认值分开，未支持选项不会静默生效。</p>
      <div class="tutorial-note">
        <b>常用配置</b>
        <p><code>base</code> 设置日期；<code>data</code> 选择数据与股票池；<code>factor</code> 是构造参数；<code>analysis.groups / horizons</code> 设置分组和持有期；<code>settings</code> 控制 Alpha 权重与展示。</p>
        <p><code>settings.decay</code> 是目标生成前的历史衰减；<code>analysis.decay_steps</code> 是预测力衰减诊断阶数，作用不同。<code>analysis.book_size</code> 只缩放名义 PnL，不引入整手或交易撮合。</p>
      </div>
      <p v-if="loading" role="status">正在读取设置说明…</p>
      <div v-else-if="loadError" class="tutorial-error" role="status"><p>设置说明读取失败：{{ loadError }}</p><button aria-label="重试" @click="loadCatalog">重试读取设置</button></div>
      <template v-else-if="catalog">
        <label class="tutorial-search"><span>设置搜索</span><input v-model="settingsSearch" aria-label="设置搜索" placeholder="例如 decay、日期、行业或 factor_price" type="search" /></label>
        <p class="tutorial-count" role="status">显示 {{ filteredSettings.length }} / {{ catalog.fields.length }} 个配置项</p>
        <details v-for="field in filteredSettings" :key="field.field" :data-setting="field.field" class="tutorial-card setting-card">
          <summary><span class="setting-name">{{ fieldPath(field) }}</span><span class="tutorial-tag" :class="statusClass(field.status)">{{ status(field.status) }}</span></summary>
          <p>{{ settingHints[field.field] || field.stage }}</p>
          <p v-if="!hasConfigLocation(field)" class="tutorial-note">此项属于平台请求字段或待核实标签，当前没有可用的项目 config 路径。</p>
          <p v-if="settingHints[field.field] && field.stage"><small>作用：{{ field.stage }}</small></p>
          <dl class="tutorial-fields">
            <div><dt>项目默认</dt><dd><code>{{ show(field.project_default) }}</code></dd></div>
            <div><dt>官方默认</dt><dd>{{ field.official_default == null ? '待核实' : show(field.official_default) }}</dd></div>
            <div><dt>类型</dt><dd>{{ field.type }}</dd></div>
          </dl>
          <template v-if="field.properties">
            <h4>子参数</h4>
            <div v-for="(property, name) in field.properties" :key="name" class="setting-property">
              <code>{{ fieldPath(field) }}.{{ name }}</code>
              <small>{{ property.required ? '必填' : '可选' }} · 默认 {{ show(property.project_default) }}</small>
              <small v-if="property.default_rule">{{ property.default_rule }}</small>
              <small v-if="property.values">可选：{{ show(property.values) }}</small>
            </div>
          </template>
          <template v-if="field.options?.length">
            <h4>选项与实际状态</h4>
            <div v-for="(option, index) in field.options" :key="index" class="setting-option">
              <div class="tutorial-card-heading"><code>{{ show(option.value) }}</code><span class="tutorial-tag" :class="statusClass(option.status || field.status)">{{ status(option.status || field.status) }}</span></div>
              <p>{{ option.meaning }}</p><small v-if="option.reason">{{ option.reason }}</small>
            </div>
          </template>
          <details v-if="field.legal_values" class="legal-values"><summary>查看范围与允许值</summary><pre>{{ readable(field.legal_values) }}</pre></details>
        </details>
        <p v-if="!filteredSettings.length" class="tutorial-empty">没有匹配设置，请尝试参数名或清空搜索。</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.tutorial { min-width: 0; max-width: 100%; padding: 20px 0 6px; color: #4b596c; }
.tutorial-intro, .tutorial-body { min-width: 0; }
.tutorial-eyebrow { color: #2c899c; font-size: 9px; letter-spacing: 1.5px; }
.tutorial-intro h2 { margin: 8px 0; color: #334457; font-size: 22px; }
.tutorial p, .tutorial li, .tutorial dd { line-height: 1.8; overflow-wrap: anywhere; }
.tutorial-intro p { margin: 7px 0 18px; color: #7b8696; }
.tutorial-tabs { display: flex; gap: 4px; border-bottom: 1px solid #dce2eb; margin-bottom: 16px; }
.tutorial-tabs button { flex: 1; border: 0; border-radius: 0; background: transparent; padding: 10px 6px; color: #68778d; }
.tutorial-tabs button.active { color: #25869d; box-shadow: inset 0 -2px #35a5b8; background: #eff6f8; }
.tutorial-card { padding: 14px; border: 1px solid #e0e5ed; border-radius: 3px; background: #fff; margin: 12px 0; min-width: 0; }
.tutorial h3 { margin: 0 0 8px; font-size: 13px; color: #3d4c61; font-weight: 600; }
.tutorial h4 { margin: 17px 0 8px; font-size: 11px; color: #64738a; }
.tutorial-card p { margin: 8px 0; }
.tutorial-card ul { padding-left: 18px; margin-bottom: 0; }
.tutorial-card li { margin-top: 7px; }
.tutorial code { font-family: Consolas, "Liberation Mono", monospace; font-size: 11px; overflow-wrap: anywhere; word-break: break-word; }
.tutorial pre { max-width: 100%; margin: 12px 0 0; padding: 11px; border: 1px solid #e3e8ee; background: #f5f7fa; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
.tutorial-code { max-height: 360px; overflow-y: auto; }
.tutorial-code code { white-space: pre-wrap; }
.copy-example { color: #27839b; border-color: #bdd6df; font-size: 11px; }
.tutorial-feedback { color: #287383; font-size: 11px; }
.tutorial-note { margin: 12px 0; padding: 12px; border-left: 2px solid #88bdcd; background: #eef5f8; font-size: 11px; line-height: 1.8; overflow-wrap: anywhere; }
.tutorial-note p { margin: 6px 0 0; }
.tutorial-fields { margin: 10px 0 0; font-size: 11px; }
.tutorial-fields > div { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 6px; margin-top: 7px; }
.tutorial dt { color: #8590a1; }
.tutorial dd { margin: 0; min-width: 0; }
.tutorial small { display: block; color: #8490a1; line-height: 1.7; margin-top: 5px; overflow-wrap: anywhere; }
.tutorial-lead { margin: 0 0 14px; font-size: 11px; }
.tutorial-search { display: flex; flex-direction: column; gap: 6px; font-size: 11px; color: #68778b; }
.tutorial-search input { width: 100%; font-size: 11px; }
.tutorial-filters { display: flex; gap: 10px; margin-top: 10px; }
.tutorial-filters label { display: flex; flex: 1; flex-direction: column; gap: 6px; min-width: 0; font-size: 10px; color: #7c8898; }
.tutorial-filters select { width: 100%; font-size: 11px; }
.tutorial-count { color: #8793a3; font-size: 10px; }
.tutorial-card-heading { display: flex; gap: 8px; justify-content: space-between; align-items: flex-start; min-width: 0; }
.tutorial-card-heading h3 { overflow-wrap: anywhere; margin-bottom: 0; }
.tutorial-tag { display: inline-block; flex-shrink: 0; border-radius: 2px; padding: 2px 5px; font-size: 9px; line-height: 1.5; color: #8791a1; background: #f0f2f6; }
.tutorial-tag.available { color: #428046; background: #edf5ed; }
.tutorial-tag.partial { color: #927140; background: #faf3e8; }
.tutorial-tag.unavailable { color: #80889a; background: #f0f2f6; }
.operator-call { display: block; color: #358397; background: #f3f7fa; padding: 8px; margin: 10px 0; }
.setting-card > summary { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; cursor: pointer; }
.setting-name { font-family: Consolas, "Liberation Mono", monospace; font-size: 11px; color: #42677d; overflow-wrap: anywhere; min-width: 0; }
.setting-card > summary::before { content: "+"; flex-shrink: 0; color: #91a3b4; }
.setting-card[open] > summary::before { content: "−"; }
.setting-name { flex: 1; }
.setting-property, .setting-option { border-top: 1px solid #edf0f5; padding: 9px 0; }
.setting-option p { font-size: 11px; }
.setting-option .tutorial-card-heading > code { min-width: 0; }
.legal-values { margin-top: 12px; font-size: 11px; }
.legal-values > summary { color: #537d92; cursor: pointer; }
.tutorial-error { border: 1px solid #e4cfc6; padding: 12px; color: #9c6550; }
.tutorial-empty { color: #8793a3; padding: 24px 0; text-align: center; }
@media (max-width: 550px) {
  .tutorial { padding-top: 16px; }
  .tutorial-card { padding: 11px; }
  .tutorial-intro h2 { font-size: 20px; }
  .tutorial-tag { white-space: normal; max-width: 48%; }
  .tutorial-fields > div { grid-template-columns: 56px minmax(0, 1fr); }
}
</style>
