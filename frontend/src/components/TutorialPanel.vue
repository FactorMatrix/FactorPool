<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount, nextTick } from "vue";
import { api } from "../api";
import UiIcon from "./UiIcon.vue";
import DocCode from "./DocCode.vue";
import { operatorDescriptions, settingHints } from "../tutorial-content";
import { learnGroups, learnArticles } from "../learn-articles";
import { operatorReference, operatorReferenceConventions } from "../operator-reference";

const props = defineProps({ operators: { type: Array, default: () => [] } });
const settingsSnippet = `"settings": {\n    "decay": 0,\n    "neutralization": "MARKET",\n    "truncation": 0,\n    "visualization": True,\n}`;
const pageId = ref("welcome"), operatorId = ref(""), settingId = ref("");
const sidebarOpen = ref(false), query = ref(""), operatorQuery = ref(""), operatorFilter = ref("connected"), operatorCategory = ref("all"), settingsQuery = ref("");
const catalog = ref(null), loading = ref(false), loadError = ref("");
const viewport = ref(null), currentSection = ref(""), searchInput = ref(null);
const connected = item => Boolean(item.native && item.pool_wiring === "connected");
const connectedCount = computed(() => props.operators.filter(connected).length);
const navigation = computed(() => learnGroups.flatMap(group => group.items.map(item => ({ ...item, group: group.title }))));
const activeNav = computed(() => navigation.value.find(item => item.id === pageId.value) || navigation.value[0]);
const article = computed(() => learnArticles[pageId.value] || learnArticles.welcome);
const selectedOperator = computed(() => props.operators.find(item => item.name === operatorId.value));
const reference = computed(() => operatorReference[operatorId.value]);
const selectedSetting = computed(() => catalog.value?.fields.find(item => item.field === settingId.value));
const title = computed(() => pageId.value === "operators" ? operatorId.value || "算子参考" : pageId.value === "settings" ? settingId.value || "模拟设置参考" : article.value.title);
const subtitle = computed(() => pageId.value === "operators" ? operatorId.value ? operatorDescriptions[operatorId.value] || "查看当前调用入口、数据要求和支持状态。" : "从数学运算到时序与截面变换，查找构建因子所需的计算方法。" : pageId.value === "settings" ? settingId.value ? settingHints[settingId.value] || selectedSetting.value?.stage : "配置决定研究范围、Alpha 权重变换与结果展示。每个参数均保留实际支持状态。" : article.value.subtitle);
const sections = computed(() => {
  if (pageId.value === "operators") return operatorId.value ? [{ id: "signature", title: "调用方式" }, { id: "parameters", title: "参数" }, { id: "example", title: "计算示例" }, { id: "boundaries", title: "使用说明" }] : [{ id: "operator-guide", title: "如何使用算子" }, { id: "operator-index", title: "算子索引" }];
  if (pageId.value === "settings") return settingId.value ? [{ id: "setting-overview", title: "参数说明" }, { id: "setting-options", title: "允许值与选项" }, { id: "setting-boundaries", title: "使用说明" }] : [{ id: "settings-guide", title: "配置写在哪里" }, { id: "settings-index", title: "设置索引" }];
  return article.value.sections;
});
const categories = [["all", "全部分类"], ["basic", "数学与逻辑"], ["ts", "时序运算"], ["cs", "截面运算"], ["group", "分组运算"], ["vector", "向量运算"]];
function categoryOf(item) {
  if (operatorReference[item.name]?.category) return operatorReference[item.name].category;
  if (item.name.startsWith("ts_")) return "ts";
  if (item.name.startsWith("group_")) return "group";
  if (/^(vec_|reduce_)/.test(item.name)) return "vector";
  return ["rank", "zscore", "normalize", "scale", "scale_down", "quantile", "winsorize", "bucket"].includes(item.name) ? "cs" : "basic";
}
const filteredOperators = computed(() => props.operators.filter(item =>
  [item.name, item.frontend, operatorDescriptions[item.name], operatorReference[item.name]?.title].join(" ").toLowerCase().includes(operatorQuery.value.trim().toLowerCase())
  && (operatorFilter.value === "all" || connected(item) === (operatorFilter.value === "connected"))
  && (operatorCategory.value === "all" || categoryOf(item) === operatorCategory.value)));
const filteredSettings = computed(() => (catalog.value?.fields || []).filter(item => JSON.stringify([item.field, item.location, item.stage, item.options, item.properties, settingHints[item.field]]).toLowerCase().includes(settingsQuery.value.trim().toLowerCase())));
const searchResults = computed(() => {
  const value = query.value.trim().toLowerCase(); if (!value) return [];
  const pages = navigation.value.filter(item => [item.title, item.description, ...(learnArticles[item.id]?.sections || []).map(section => [section.title, ...(section.paragraphs || [])].join(" "))].join(" ").toLowerCase().includes(value)).map(item => ({ label: item.title, context: item.group, page: item.id }));
  const operators = props.operators.filter(item => [item.name, item.frontend, operatorDescriptions[item.name]].join(" ").toLowerCase().includes(value)).map(item => ({ label: item.name, context: "算子参考", page: "operators", operator: item.name }));
  const fields = catalog.value?.fields || Object.keys(settingHints).map(field => ({ field }));
  const settings = fields.filter(item => [item.field, item.stage, settingHints[item.field]].join(" ").toLowerCase().includes(value)).map(item => ({ label: item.field, context: "模拟设置", page: "settings", setting: item.field }));
  return [...pages, ...operators, ...settings].slice(0, 12);
});
const pageIndex = computed(() => navigation.value.findIndex(item => item.id === pageId.value));
const previous = computed(() => navigation.value[pageIndex.value - 1]);
const next = computed(() => navigation.value[pageIndex.value + 1]);
const show = value => value == null ? "未指定" : typeof value === "string" ? value : JSON.stringify(value);
const status = value => catalog.value?.status_labels?.[value] || value;
const hasConfigPath = field => field && ["config", "config.settings"].includes(field.location);
const configPath = field => hasConfigPath(field) ? `${field.location}.${field.field}` : field?.field;
const statusClass = value => value?.startsWith("implemented_") ? "available" : value === "mixed" ? "partial" : "unavailable";
function navigate(page, detail = {}) {
  query.value = ""; sidebarOpen.value = false;
  const suffix = detail.operator || detail.setting;
  const hash = `#learn/${page}${suffix ? "/" + encodeURIComponent(suffix) : ""}`;
  if (window.location.hash === hash) readRoute(); else window.location.hash = hash;
}
function readRoute() {
  const parts = window.location.hash.slice(1).split("/");
  const valid = navigation.value.some(item => item.id === parts[1]);
  pageId.value = parts[0] === "learn" && valid ? parts[1] : "welcome";
  let detail = ""; try { detail = decodeURIComponent(parts[2] || ""); } catch { /* Invalid links open the index. */ }
  operatorId.value = pageId.value === "operators" && props.operators.some(item => item.name === detail) ? detail : "";
  settingId.value = pageId.value === "settings" && (!catalog.value || catalog.value.fields.some(item => item.field === detail)) ? detail : "";
  query.value = ""; sidebarOpen.value = false;
  nextTick(() => { viewport.value?.scrollTo({ top: 0 }); currentSection.value = sections.value[0]?.id || ""; });
}
function scrollToSection(id) {
  const target = viewport.value?.querySelector(`[data-section="${id}"]`);
  if (target && viewport.value) viewport.value.scrollTo({ top: target.offsetTop - 28, behavior: "smooth" });
  currentSection.value = id;
}
let scrollFrame;
function trackScroll() {
  if (scrollFrame) return;
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null; if (!viewport.value) return;
    const top = viewport.value.getBoundingClientRect().top + 100;
    const all = [...viewport.value.querySelectorAll("[data-section]")];
    currentSection.value = (all.filter(item => item.getBoundingClientRect().top <= top).at(-1) || all[0])?.dataset.section || "";
  });
}
async function loadCatalog() {
  if (loading.value) return;
  loading.value = true; loadError.value = "";
  try {
    catalog.value = await api("/simulation-settings");
    if (settingId.value && !catalog.value.fields.some(item => item.field === settingId.value)) settingId.value = "";
  }
  catch (error) { loadError.value = error.message; }
  finally { loading.value = false; }
}
function searchShortcut(event) {
  if (event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey && !event.target.closest("input, textarea, [contenteditable=true]")) {
    event.preventDefault(); sidebarOpen.value = true; nextTick(() => searchInput.value?.focus());
  }
  if (event.key === "Escape") { query.value = ""; sidebarOpen.value = false; }
}
watch(() => props.operators.length, readRoute);
onMounted(() => { readRoute(); loadCatalog(); window.addEventListener("hashchange", readRoute); window.addEventListener("keydown", searchShortcut); });
onBeforeUnmount(() => { window.removeEventListener("hashchange", readRoute); window.removeEventListener("keydown", searchShortcut); if (scrollFrame) cancelAnimationFrame(scrollFrame); });
</script>

<template>
  <section class="learn-docs" aria-label="使用教程">
    <div class="docs-mobile-bar"><button aria-label="打开文档目录" :aria-expanded="sidebarOpen" @click="sidebarOpen = !sidebarOpen"><UiIcon name="book" :size="17" />文档目录</button><span>{{ activeNav.title }}</span></div>
    <button v-if="sidebarOpen" class="docs-backdrop" aria-label="关闭文档目录" @click="sidebarOpen = false"></button>
    <aside class="docs-sidebar" :class="{ open: sidebarOpen }" aria-label="文档目录">
      <div class="docs-sidebar-heading"><UiIcon name="book" :size="21" /><div><strong>Documentation</strong><span>FactorPool 使用文档</span></div></div>
      <div class="docs-search">
        <span class="search-glyph" aria-hidden="true">⌕</span><input ref="searchInput" v-model="query" aria-label="搜索文档" placeholder="搜索文档…" @keydown.enter.prevent="searchResults[0] && navigate(searchResults[0].page, searchResults[0])" @keydown.esc="query = ''" />
        <button v-if="query" class="clear-search" aria-label="清空文档搜索" @click="query = ''">×</button><kbd v-else>/</kbd>
      </div>
      <div v-if="query.trim()" class="docs-search-results" aria-label="文档搜索结果"><p>搜索结果</p><button v-for="item in searchResults" :key="item.page + item.label" @click="navigate(item.page, item)"><strong>{{ item.label }}</strong><span>{{ item.context }}</span></button><p v-if="!searchResults.length">没有匹配内容</p></div>
      <nav v-else class="docs-navigation" aria-label="学习目录">
        <div v-for="group in learnGroups" :key="group.id" class="docs-nav-group"><h2>{{ group.title }}</h2><button v-for="item in group.items" :key="item.id" :data-doc-page="item.id" :class="{ active: pageId === item.id }" :aria-current="pageId === item.id ? 'page' : undefined" @click="navigate(item.id)">{{ item.title }}<span v-if="pageId === item.id" aria-hidden="true">›</span></button></div>
      </nav>
      <div class="docs-sidebar-foot"><span class="live-dot"></span>PythonIndicator<span>研究文档</span></div>
    </aside>
    <div class="docs-main">
      <div class="docs-breadcrumb"><span>Learn</span><span>›</span><span>{{ activeNav.group }}</span><span>›</span><strong>{{ title }}</strong><span class="docs-reading-time">{{ ['operators', 'settings'].includes(pageId) ? '参考手册' : article.readingTime }}</span></div>
      <div ref="viewport" class="docs-viewport" @scroll.passive="trackScroll">
        <div class="docs-reading-layout">
          <article class="docs-article" :data-page="pageId">
            <header class="docs-article-header"><span class="docs-section-label">{{ activeNav.group }}</span><h1 tabindex="-1">{{ title }}</h1><p>{{ subtitle }}</p></header>
            <template v-if="pageId === 'operators'">
              <template v-if="selectedOperator">
                <button class="docs-back-link" @click="navigate('operators')">← 返回算子索引</button>
                <section data-section="signature" class="doc-section"><h2>调用方式</h2><p><span class="doc-status" :class="connected(selectedOperator) ? 'available' : 'unavailable'">{{ connected(selectedOperator) ? '已接通' : '待接通' }}</span><span class="doc-inline-note">{{ reference?.title || '项目调用状态' }}</span></p><DocCode v-if="selectedOperator.frontend" :code="reference?.signature || selectedOperator.frontend" label="算子调用方式" /><p v-else>此算子当前没有可调用入口。</p><p>数据要求：{{ selectedOperator.data }}</p></section>
                <section data-section="parameters" class="doc-section"><h2>参数</h2><div v-if="reference?.parameters?.length" class="doc-table-wrap"><table class="doc-table"><thead><tr><th>参数</th><th>含义</th></tr></thead><tbody><tr v-for="param in reference.parameters" :key="param.name"><td><code>{{ param.name }}</code></td><td>{{ param.description }}</td></tr></tbody></table></div><p v-else>尚无已验证的项目参数合同，请先选择已接通的算子。</p></section>
                <section data-section="example" class="doc-section"><h2>计算示例</h2><p class="doc-caption">{{ operatorReferenceConventions[0] }}</p><template v-if="reference?.example && connected(selectedOperator)"><DocCode :code="reference.example.expression" label="算子示例" /><div class="doc-table-wrap"><table class="doc-table"><tbody><tr><th>输入</th><td class="example-value">{{ show(reference.example.input) }}</td></tr><tr><th>输出</th><td class="example-value">{{ show(reference.example.output) }}</td></tr></tbody></table></div><p class="doc-caption">示例用于解释当前项目的计算规则；时序示例展示所列完整窗口的当前输出。</p></template><p v-else>此项尚未接通，不提供可运行示例。</p></section>
                <section data-section="boundaries" class="doc-section"><h2>使用说明</h2><ul v-if="reference?.notes?.length"><li v-for="note in reference.notes" :key="note">{{ note }}</li></ul><p>{{ connected(selectedOperator) ? '当前调用链可用，仍需满足输入数据和窗口要求。' : '台账中的名称和候选写法不代表该算子已经可用。' }}</p><p class="doc-caption">这里说明 FactorPool 本地计算规则，BRAIN 官方数值一致性尚未核验。</p></section>
              </template>
              <template v-else>
                <section data-section="operator-guide" class="doc-section"><h2>如何使用算子</h2><p>通过 <code>self.ts</code> 处理单只股票的历史序列，通过 <code>self.cs</code> 处理同一天的股票截面。将结果写入 <code>self.value</code>，窗口准备好后返回 ready。</p><DocCode :code="'mean_volume = self.ts.mean(input.volume, 20)\nself.value = self.cs.rank_unit(-mean_volume)\nreturn self.ts.is_ready(mean_volume)'" /><p class="doc-caption">当前台账 {{ operators.length }} 项，其中 {{ connectedCount }} 项已接通。点击算子查看参数、示例和使用边界。</p></section>
                <section data-section="operator-index" class="doc-section"><h2>算子索引</h2><div class="docs-reference-filters"><input v-model="operatorQuery" type="search" aria-label="算子搜索" placeholder="搜索名称、用途或调用写法" /><select v-model="operatorFilter" aria-label="算子状态"><option value="connected">已接通</option><option value="all">全部台账</option><option value="pending">待接通</option></select><select v-model="operatorCategory" aria-label="算子类型"><option v-for="[value, label] in categories" :key="value" :value="value">{{ label }}</option></select></div><p class="doc-caption" role="status">{{ filteredOperators.length }} 个结果</p><div class="operator-index"><button v-for="item in filteredOperators" :key="item.name" :data-operator="item.name" @click="navigate('operators', { operator: item.name })"><div><strong>{{ item.name }}</strong><span class="doc-status" :class="connected(item) ? 'available' : 'unavailable'">{{ connected(item) ? '已接通' : '待接通' }}</span></div><code>{{ item.frontend || '暂无可调用入口' }}</code><p>{{ operatorDescriptions[item.name] || '此项暂未接通，点击查看当前状态。' }}</p></button></div><p v-if="!filteredOperators.length">没有匹配算子，请调整筛选条件。</p></section>
              </template>
            </template>
            <template v-else-if="pageId === 'settings'">
              <div v-if="loading" class="doc-notice" role="status">正在读取当前设置…</div><div v-else-if="loadError" class="doc-notice error-notice" role="status">设置读取失败：{{ loadError }}<button aria-label="重试" @click="loadCatalog">重新读取</button></div>
              <template v-else-if="selectedSetting">
                <button class="docs-back-link" @click="navigate('settings')">← 返回设置索引</button>
                <section data-section="setting-overview" class="doc-section"><h2>参数说明</h2><p><span class="doc-status" :class="statusClass(selectedSetting.status)">{{ status(selectedSetting.status) }}</span></p><div class="doc-table-wrap"><table class="doc-table"><tbody><tr><th>配置位置</th><td><code>{{ hasConfigPath(selectedSetting) ? configPath(selectedSetting) : '项目配置路径尚未确认' }}</code></td></tr><tr><th>类型</th><td>{{ selectedSetting.type }}</td></tr><tr><th>项目默认</th><td><code>{{ show(selectedSetting.project_default) }}</code></td></tr><tr><th>官方默认</th><td>{{ selectedSetting.official_default == null ? '待核实' : show(selectedSetting.official_default) }}</td></tr></tbody></table></div><p>{{ selectedSetting.stage }}</p></section>
                <section data-section="setting-options" class="doc-section"><h2>允许值与选项</h2><div v-if="selectedSetting.properties" class="doc-table-wrap"><table class="doc-table"><thead><tr><th>子参数</th><th>默认与可选值</th></tr></thead><tbody><tr v-for="(property, name) in selectedSetting.properties" :key="name"><td><code>{{ name }}</code><small>{{ property.required ? '必填' : '可选' }}</small></td><td>{{ property.values ? show(property.values) : show(property.project_default) }}<small>{{ property.default_rule }}</small></td></tr></tbody></table></div><div v-if="selectedSetting.options?.length" class="doc-table-wrap"><table class="doc-table option-table"><thead><tr><th>值</th><th>含义</th><th>状态</th></tr></thead><tbody><tr v-for="(option, index) in selectedSetting.options" :key="index"><td><code>{{ show(option.value) }}</code></td><td>{{ option.meaning }}<small>{{ option.reason }}</small></td><td><span class="doc-status" :class="statusClass(option.status || selectedSetting.status)">{{ status(option.status || selectedSetting.status) }}</span></td></tr></tbody></table></div><details v-if="selectedSetting.legal_values" class="doc-allowed-values"><summary>查看完整范围定义</summary><DocCode :code="JSON.stringify(selectedSetting.legal_values, null, 2)" language="json" label="设置允许值" /></details></section>
                <section data-section="setting-boundaries" class="doc-section"><h2>使用说明</h2><p>{{ selectedSetting.validation?.scope || '按当前服务返回的状态使用该参数；尚未支持的值会明确拒绝。' }}</p><p v-if="!hasConfigPath(selectedSetting)">此项为平台请求字段或尚未核实的标签，不能直接写入项目 config。</p><p class="doc-caption">项目默认与 BRAIN 官方默认分别记录；未核实的官方默认不会以项目值代替。</p></section>
              </template>
              <template v-else-if="catalog">
                <section data-section="settings-guide" class="doc-section"><h2>配置写在哪里</h2><p>研究配置直接写在脚本的 <code>config</code> 中。日期与数据源放在 <code>base / data</code>；构造参数放在 <code>factor</code>；权重变换与展示选项放在 <code>settings</code>。</p><DocCode :code="settingsSnippet" /><p>点击参数查看默认值、可选值和实际作用。支持范围会随当前服务更新。</p></section>
                <section data-section="settings-index" class="doc-section"><h2>设置索引</h2><input v-model="settingsQuery" type="search" aria-label="设置搜索" placeholder="搜索参数，例如 decay、股票池、factor_price" class="settings-search" /><p class="doc-caption" role="status">{{ filteredSettings.length }} / {{ catalog.fields.length }} 个参数</p><div class="setting-index"><button v-for="field in filteredSettings" :key="field.field" :data-setting="field.field" @click="navigate('settings', { setting: field.field })"><div><strong>{{ field.field }}</strong><span class="doc-status" :class="statusClass(field.status)">{{ status(field.status) }}</span></div><p>{{ settingHints[field.field] || field.stage }}</p><small>{{ hasConfigPath(field) ? configPath(field) : '项目路径待核实' }}</small></button></div><p v-if="!filteredSettings.length">没有匹配设置。</p></section>
              </template>
            </template>
            <template v-else>
              <section v-for="section in article.sections" :key="section.id" :data-section="section.id" class="doc-section"><h2>{{ section.title }}</h2><p v-for="(paragraph, index) in section.paragraphs" :key="index">{{ paragraph }}</p><ul v-if="section.bullets?.length"><li v-for="item in section.bullets" :key="item">{{ item }}</li></ul><DocCode v-if="section.code" :code="section.code" :label="section.codeLabel || '代码示例'" /><div v-if="section.table" class="doc-table-wrap"><table class="doc-table"><thead><tr><th v-for="column in section.table.columns" :key="column">{{ column }}</th></tr></thead><tbody><tr v-for="(row, index) in section.table.rows" :key="index"><td v-for="(cell, n) in row" :key="n">{{ cell }}</td></tr></tbody></table></div><aside v-if="section.note" class="doc-note"><strong>{{ section.note.title }}</strong><p>{{ section.note.text }}</p></aside></section>
              <div v-if="pageId === 'welcome'" class="docs-start-links"><button @click="navigate('quick-start')"><span>开始使用</span><strong>创建你的第一个因子 <span>→</span></strong></button><button @click="navigate('operators')"><span>参考手册</span><strong>查找算子与计算方法 <span>→</span></strong></button></div>
            </template>
            <footer class="docs-page-navigation"><button v-if="previous" @click="navigate(previous.id)"><span>← 上一篇</span><strong>{{ previous.title }}</strong></button><button v-if="next" class="docs-next" @click="navigate(next.id)"><span>下一篇 →</span><strong>{{ next.title }}</strong></button></footer>
          </article>
          <aside class="docs-toc" aria-label="页内目录"><strong>本页内容</strong><button v-for="section in sections" :key="section.id" :class="{ active: currentSection === section.id }" @click="scrollToSection(section.id)">{{ section.title }}</button><div class="docs-toc-note">配置与算子采用<br />FactorPool 当前计算规则。</div></aside>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.learn-docs { display: grid; grid-template-columns: 248px minmax(0, 1fr); width: 100%; min-width: 0; height: 100%; min-height: 0; color: #344254; background: #fff; font-size: 14px; }
.docs-sidebar { display: flex; flex-direction: column; min-height: 0; background: #f8fafc; border-right: 1px solid #e3e8ee; overflow-y: auto; }
.docs-sidebar-heading { display: flex; gap: 12px; align-items: center; padding: 30px 24px 22px; color: #3a4b61; }
.docs-sidebar-heading strong { display: block; font-size: 16px; font-weight: 600; letter-spacing: -.3px; }
.docs-sidebar-heading span { display: block; margin-top: 5px; font-size: 11px; color: #8894a4; }
.docs-search { display: flex; align-items: center; position: relative; margin: 0 19px 19px; border: 1px solid #dfe5ec; border-radius: 5px; background: #fff; }
.docs-search input { padding: 9px 25px 9px 29px; width: 100%; border: 0; background: transparent; font-size: 12px; }
.search-glyph { position: absolute; left: 9px; font-size: 20px; color: #8c98a6; }
.docs-search kbd { position: absolute; right: 8px; border: 1px solid #e1e6ed; border-radius: 3px; padding: 0 4px; color: #a2acb9; font-size: 10px; }
.clear-search { position: absolute; right: 1px; border: 0; background: none; padding: 4px 7px; }
.docs-nav-group { margin: 7px 0 23px; }
.docs-nav-group h2 { font-size: 11px; font-weight: 600; letter-spacing: .4px; color: #8b96a5; margin: 0 25px 9px; }
.docs-nav-group > button { display: flex; justify-content: space-between; width: calc(100% - 24px); margin: 2px 12px; padding: 9px 13px; font-size: 12px; color: #5a6779; text-align: left; border: 0; background: transparent; border-radius: 4px; }
.docs-nav-group > button.active { background: #eaf1f8; color: #28629a; font-weight: 600; }
.docs-nav-group > button:hover { background: #edf2f7; color: #345877; }
.docs-sidebar-foot { margin-top: auto; padding: 18px 23px; border-top: 1px solid #e7ebf0; color: #8491a3; font-size: 10px; display: flex; gap: 7px; align-items: center; flex-wrap: wrap; }
.docs-sidebar-foot > span:last-child { margin-left: auto; color: #a2aab7; }
.live-dot { width: 5px; height: 5px; border-radius: 50%; background: #8bb39b; }
.docs-search-results { padding: 0 14px 20px; }
.docs-search-results > p { padding: 0 7px; font-size: 11px; color: #8996a6; }
.docs-search-results > button { display: block; width: 100%; border: 0; background: none; text-align: left; padding: 10px 9px; }
.docs-search-results strong, .docs-search-results span { display: block; overflow-wrap: anywhere; }
.docs-search-results strong { color: #3f638b; font-size: 12px; font-weight: 500; }
.docs-search-results span { margin-top: 4px; color: #95a0ae; font-size: 10px; }
.docs-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.docs-breadcrumb { display: flex; align-items: center; gap: 11px; height: 60px; flex-shrink: 0; padding: 0 42px; font-size: 11px; border-bottom: 1px solid #eef1f5; color: #96a1af; }
.docs-breadcrumb strong { font-weight: 400; color: #5c6f85; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.docs-reading-time { margin-left: auto; white-space: nowrap; color: #a0a9b5; }
.docs-viewport { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; position: relative; scroll-behavior: smooth; }
.docs-reading-layout { display: grid; grid-template-columns: minmax(0, 780px) 156px; gap: 56px; max-width: 1110px; margin: 0 auto; padding: 45px 42px 35px; }
.docs-article { min-width: 0; }
.docs-article-header { margin: 0 0 30px; border-bottom: 1px solid #e8edf2; padding-bottom: 25px; }
.docs-section-label { text-transform: uppercase; font-size: 11px; font-weight: 500; letter-spacing: .8px; color: #8d9baa; }
.docs-article h1 { margin: 13px 0 16px; color: #273b51; font-size: 32px; font-weight: 500; letter-spacing: -.8px; line-height: 1.35; overflow-wrap: anywhere; }
.docs-article-header > p { color: #8591a1; font-size: 14px; line-height: 1.9; margin: 0; }
.doc-section { margin-top: 36px; }
.doc-section h2 { font-size: 20px; color: #334960; font-weight: 500; margin: 0 0 15px; letter-spacing: -.3px; line-height: 1.5; }
.doc-section p, .doc-section li { line-height: 1.95; color: #627185; font-size: 14px; margin: 12px 0; overflow-wrap: anywhere; }
.doc-section ul { padding-left: 21px; }
.doc-section code, .operator-index code, .setting-index small { font-family: "Liberation Mono", Consolas, monospace; font-size: 11px; }
.doc-section p > code { background: #f1f4f8; padding: 2px 5px; border-radius: 3px; color: #466a8e; }
.doc-note { margin: 24px 0; padding: 17px 19px; border-left: 3px solid #84a9d0; background: #f4f8fc; border-radius: 0 4px 4px 0; }
.doc-note strong { font-size: 12px; color: #577898; font-weight: 600; }
.doc-note p { font-size: 12px; margin: 7px 0 0; }
.doc-table-wrap { max-width: 100%; overflow-x: auto; margin: 20px 0; }
.doc-table { width: 100%; border-collapse: collapse; text-align: left; }
.doc-table th, .doc-table td { border-bottom: 1px solid #e8edf2; padding: 12px 13px; line-height: 1.75; font-size: 12px; vertical-align: top; overflow-wrap: anywhere; color: #64758a; }
.doc-table th { background: #f7f9fb; color: #5c6d82; font-weight: 500; }
.doc-table th:first-child, .doc-table td:first-child { padding-left: 12px; }
.doc-table td:first-child { color: #44617b; }
.doc-table small { display: block; color: #95a0ae; font-size: 10px; margin-top: 5px; }
.doc-table .example-value { white-space: pre-wrap; font-family: monospace; }
.doc-status { padding: 3px 7px; border-radius: 3px; background: #f0f3f6; color: #8c97a5; font-size: 10px; white-space: nowrap; font-weight: 400; }
.doc-status.available { background: #edf5f0; color: #54836b; }
.doc-status.partial { background: #faf3e7; color: #9a8151; }
.doc-inline-note { font-size: 12px; color: #8a96a5; margin-left: 12px; }
.doc-section .doc-caption { font-size: 11px; color: #98a2af; line-height: 1.8; }
.docs-back-link { font-size: 12px; color: #4e7ca7; border: 0; padding: 0; background: none; margin-bottom: 2px; }
.docs-reference-filters { display: flex; flex-wrap: wrap; gap: 9px; margin: 18px 0 8px; }
.docs-reference-filters input { flex: 1 1 190px; }
.docs-reference-filters input, .docs-reference-filters select, .settings-search { padding: 9px 11px; border: 1px solid #dfe5ec; border-radius: 4px; font-size: 11px; }
.docs-reference-filters select { flex: 0 1 auto; }
.operator-index { border-top: 1px solid #e6ebf0; }
.operator-index > button, .setting-index > button { display: block; width: 100%; border: 0; border-radius: 0; border-bottom: 1px solid #e8edf2; background: transparent; text-align: left; padding: 17px 3px; }
.operator-index > button:hover, .setting-index > button:hover { background: #f8fafc; }
.operator-index > button > div, .setting-index > button > div { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
.operator-index strong, .setting-index strong { color: #416b94; font-family: "Liberation Mono", Consolas, monospace; font-weight: 500; font-size: 13px; }
.operator-index code { display: block; color: #8d98a6; margin: 8px 0 0; overflow-wrap: anywhere; }
.operator-index p, .setting-index p { color: #8591a2; font-size: 12px; margin: 6px 0 0; }
.settings-search { width: 100%; }
.setting-index { border-top: 1px solid #e6ebf0; }
.setting-index small { color: #a1aab6; display: block; margin-top: 8px; }
.doc-notice { padding: 18px; font-size: 12px; background: #f5f8fb; color: #7a8b9f; line-height: 1.8; }
.error-notice { background: #fbf5f2; color: #ad795e; }
.doc-notice button { display: block; margin-top: 12px; font-size: 11px; }
.doc-allowed-values { font-size: 12px; margin: 20px 0; color: #7b8fa5; }
.doc-allowed-values summary { cursor: pointer; }
.docs-start-links { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 34px; }
.docs-start-links button { display: block; text-align: left; padding: 20px; border: 1px solid #e1e7ee; border-radius: 5px; background: #fff; }
.docs-start-links button > span { color: #9aa5b3; font-size: 10px; }
.docs-start-links strong { display: block; margin-top: 10px; color: #54789a; font-size: 12px; font-weight: 500; line-height: 1.7; }
.docs-start-links strong > span { float: right; margin-left: 10px; }
.docs-page-navigation { display: flex; gap: 20px; justify-content: space-between; border-top: 1px solid #e8edf2; margin-top: 52px; padding: 24px 0 5px; background: #fff; }
.docs-page-navigation button { display: block; border: 0; padding: 0; text-align: left; background: none; }
.docs-page-navigation button > span { display: block; color: #9aa5b2; font-size: 10px; }
.docs-page-navigation strong { display: block; margin-top: 8px; font-size: 12px; font-weight: 500; color: #52779a; }
.docs-page-navigation .docs-next { margin-left: auto; text-align: right; }
.docs-toc { position: sticky; top: 30px; align-self: start; border-left: 1px solid #e7ecf2; padding: 0 0 0 20px; margin-top: 5px; }
.docs-toc > strong { display: block; font-size: 11px; color: #687d91; font-weight: 500; margin-bottom: 15px; }
.docs-toc > button { display: block; text-align: left; padding: 6px 0; font-size: 11px; line-height: 1.7; border: 0; background: none; color: #98a3b1; white-space: normal; }
.docs-toc > button.active { color: #3975a8; }
.docs-toc-note { border-top: 1px solid #e9edf2; padding-top: 18px; margin-top: 22px; font-size: 10px; color: #adb5bf; line-height: 1.8; }
.docs-mobile-bar, .docs-backdrop { display: none; }
@media (min-width: 1600px) { .docs-reading-layout { padding-top: 54px; } }
@media (max-width: 1150px) { .learn-docs { grid-template-columns: 220px minmax(0, 1fr); } .docs-toc { display: none; } .docs-reading-layout { grid-template-columns: minmax(0, 780px); max-width: 850px; gap: 0; padding: 35px; } .docs-breadcrumb { padding: 0 35px; } }
@media (max-width: 760px) {
  .learn-docs { display: flex; flex-direction: column; position: relative; }
  .docs-mobile-bar { display: flex; padding: 10px 18px; align-items: center; gap: 15px; border-bottom: 1px solid #e7ecf2; background: #f9fbfd; }
  .docs-mobile-bar > button { font-size: 12px; color: #5c7591; border: 0; background: none; padding: 4px 0; }
  .docs-mobile-bar > span { color: #9aa6b4; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .docs-sidebar { display: none; position: absolute; top: 47px; left: 0; bottom: 0; width: 275px; z-index: 7; box-shadow: 8px 0 30px #20385215; }
  .docs-sidebar.open { display: flex; }
  .docs-backdrop { display: block; position: absolute; inset: 47px 0 0; z-index: 6; border: 0; background: #17263640; padding: 0; border-radius: 0; }
  .docs-main { flex: 1; }
  .docs-breadcrumb { height: 49px; padding: 0 22px; gap: 8px; font-size: 10px; }
  .docs-reading-time { display: none; }
  .docs-reading-layout { padding: 28px 23px 30px; }
  .docs-article h1 { font-size: 27px; }
  .docs-article-header > p { font-size: 13px; }
  .doc-section h2 { font-size: 18px; }
  .doc-section p, .doc-section li { font-size: 13px; }
  .docs-start-links { grid-template-columns: 1fr; }
  .doc-table { min-width: 360px; }
  .option-table { min-width: 510px; }
  .docs-reference-filters select { flex: 1; }
}
</style>
