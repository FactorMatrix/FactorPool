<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import FactorEditor from "./components/FactorEditor.vue";
import SplitPane from "./components/SplitPane.vue";
import UiIcon from "./components/UiIcon.vue";
import WorkspaceSettings from "./components/WorkspaceSettings.vue";
import SimulationResults from "./components/SimulationResults.vue";
import TutorialPanel from "./components/TutorialPanel.vue";
import { api } from "./api";
const source = ref(""), name = ref("20日低成交量因子");
const defaults = () => ({
  type: "REGULAR",
  base: { start_date: "2016-01-01", end_date: "2026-01-01", frequency: "1d" },
  data: { provider: "market_matrix", universe: "all_a_dynamic", adjustment: "raw", factor_price: "quote_adjusted" },
  factor: {},
  analysis: { groups: 3, horizons: [1, 5, 20], decay_steps: 10, annual_days: 252,
    min_ic_pairs: 3, icir_ddof: 1, book_size: 1, pnl_unit: "BOOK", trace_dates: [] },
  settings: { decay: 0, truncation: 0, neutralization: "MARKET", pasteurization: "ON",
    testPeriod: "P0Y0M", visualization: true },
});
const clone = value => JSON.parse(JSON.stringify(value));
function workspaceDefaults(value = {}) {
  const result = defaults();
  for (const key of ["base", "data", "analysis", "settings"])
    result[key] = { ...result[key], ...clone(value[key] || {}) };
  result.type = value.type ?? result.type;
  result.factor = clone(value.factor || {});
  return result;
}
const settings = ref(defaults());
const resolvedScript = ref(null), resolveError = ref(""), resolving = ref(false);
let resolveRevision = 0;
const tab = ref(window.location.hash.startsWith("#learn/") ? "learn" : "research"),
  error = ref(""),
  busy = ref(false);
watch(tab, next => {
  if (next !== "learn" && window.location.hash.startsWith("#learn/"))
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
});
function followLearnLink() {
  if (window.location.hash.startsWith("#learn/")) tab.value = "learn";
}
const factors = ref([]),
  examples = ref([]),
  operators = ref([]),
  sources = ref([]),
  selected = ref([]),
  factorId = ref(null);
const task = ref(null),
  result = ref(null),
  values = ref([]),
  logs = ref(""),
  offset = ref(0),
  notice = ref("");
let timer,
  active = true,
  polling = false,
  revision = 0;
const running = computed(() =>
  ["QUEUED", "RUNNING", "CANCELLING"].includes(task.value?.status),
);
const states = {
  QUEUED: "排队中",
  RUNNING: "研究中",
  CANCELLING: "正在停止",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCELLED: "已停止",
  INTERRUPTED: "已中断",
};
const settingsOpen = ref(false),
  showCode = ref(true),
  showResults = ref(true);
const workspaces = ref([
  { id: 1, label: "Simulation 1", draft: null, taskId: null },
]);
const workspaceId = ref(1);
let workspaceSequence = 1;
watch([source, settings, workspaceId], () => {
  ++resolveRevision;
  resolvedScript.value = null;
  resolveError.value = "";
  resolving.value = false;
});
function applySettings(value) {
  settings.value = clone(value);
}
function captureWorkspace() {
  const current = workspaces.value.find((w) => w.id === workspaceId.value);
  current.draft = {
    settings: clone(settings.value),
    source: source.value,
    name: name.value,
    factorId: factorId.value,
  };
  current.taskId = task.value?.id || null;
}
function clearResult() {
  ++revision;
  task.value = null;
  result.value = null;
  values.value = [];
  logs.value = "";
  offset.value = 0;
  sessionStorage.removeItem("factorpool.task");
}
async function selectWorkspace(id) {
  if (id === workspaceId.value || busy.value) return;
  await action(async () => {
    captureWorkspace();
    const next = workspaces.value.find((w) => w.id === id);
    workspaceId.value = id;
    applySettings(next.draft.settings);
    source.value = next.draft.source;
    name.value = next.draft.name;
    factorId.value = next.draft.factorId;
    settingsOpen.value = false;
    notice.value = "";
    clearResult();
    if (next.taskId) await openTask(next.taskId);
  });
}
function newWorkspace(copy = false) {
  captureWorkspace();
  const id = ++workspaceSequence;
  workspaces.value.push({
    id,
    label: `Simulation ${id}`,
    draft: null,
    taskId: null,
  });
  workspaceId.value = id;
  clearResult();
  settingsOpen.value = false;
  notice.value = "";
  factorId.value = null;
  if (copy) name.value += " · 副本";
  else clearDraft();
  captureWorkspace();
  tab.value = "research";
  showCode.value = true;
}
function request() {
  return { name: name.value, source_code: source.value,
    workspace_config: clone(settings.value) };
}
async function resolveScript(workspaceConfig = settings.value) {
  const token = ++resolveRevision,
    text = source.value,
    workspace = workspaceId.value;
  resolving.value = true;
  resolveError.value = "";
  resolvedScript.value = null;
  try {
    const resolved = await api("/script/resolve", { source_code: text, workspace_config: clone(workspaceConfig) });
    if (token === resolveRevision && source.value === text && workspaceId.value === workspace)
      resolvedScript.value = resolved;
  } catch (e) {
    if (token === resolveRevision && source.value === text && workspaceId.value === workspace)
      resolveError.value = e.message;
  } finally {
    if (token === resolveRevision) resolving.value = false;
  }
}
async function action(fn) {
  error.value = "";
  busy.value = true;
  try {
    await fn();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function refreshLists() {
  factors.value = await api("/factors");
}
function example(item) {
  source.value = item.source_code;
  name.value = item.name.includes("volume_rank")
    ? "成交量多阶段截面"
    : item.name.includes("close_mean")
      ? "收盘均值 · Python"
      : "20日低成交量因子";
  factorId.value = null;
}
async function save() {
  await action(async () => {
    const body = request();
    if (factorId.value) body.id = factorId.value;
    const saved = await api("/factors", body);
    factorId.value = saved.id;
    notice.value = `已保存版本 v${saved.version}`;
    await refreshLists();
  });
}
async function loadFactor(id) {
  await action(async () => {
    const f = await api(`/factors/${id}`);
    const d = f.latest;
    newWorkspace();
    restoreSnapshot(d);
    factorId.value = id;
    tab.value = "research";
    notice.value = `已载入因子 v${f.versions[0].version}`;
  });
}
function clearDraft() {
  factorId.value = null;
  settings.value = defaults();
  name.value = "新因子";
  notice.value = "";
  if (examples.value.length)
    example(
      examples.value.find((e) => e.name === "low_volume.py") ||
        examples.value[0],
    );
}
async function run() {
  await action(async () => {
    const submitted = await api("/tasks", request());
    await openTask(submitted.id);
    tab.value = "research";
    await refreshLists();
  });
}
async function openTask(id) {
  const token = ++revision;
  task.value = { id, status: "QUEUED" };
  result.value = null;
  values.value = [];
  logs.value = "";
  offset.value = 0;
  const current = await api(`/tasks/${id}`);
  if (token !== revision || !active) return;
  task.value = current;
  await refreshTask(token);
}
async function refreshTask(token = revision) {
  const id = task.value?.id;
  if (!id) return;
  const [current, log] = await Promise.all([
    api(`/tasks/${id}`),
    api(`/tasks/${id}/logs`),
  ]);
  if (token !== revision || !active) return;
  task.value = current;
  logs.value = log.text;
  if (current.status === "SUCCEEDED" && !result.value) {
    const [research, rows] = await Promise.all([
      api(`/tasks/${id}/result`),
      api(`/tasks/${id}/values?offset=0&limit=100`),
    ]);
    if (token !== revision || !active) return;
    result.value = research;
    values.value = rows;
  }
}
async function page(delta) {
  await action(async () => {
    const next = Math.max(0, offset.value + delta),
      id = task.value?.id,
      token = revision;
    const rows = await api(`/tasks/${id}/values?offset=${next}&limit=100`);
    if (token === revision) {
      values.value = rows;
      offset.value = next;
    }
  });
}
async function cancel() {
  await action(async () => {
    await api(`/tasks/${task.value.id}/cancel`, {});
    await refreshTask();
  });
}
async function runBatch() {
  await action(async () => {
    const body = { factors: selected.value, workspace_config: clone(settings.value) };
    const batch = await api("/tasks/batch", body);
    notice.value = `批次 ${batch.id} · ${batch.tasks.length} 个任务已提交`;
    await refreshLists();
  });
}
function restoreSnapshot(r) {
  source.value = r.source_code;
  name.value = r.name;
  settings.value = workspaceDefaults(r.workspace_config);
  factorId.value = null;
}
function loadSnapshot() {
  const r = task.value?.snapshot;
  if (!r) return;
  newWorkspace(true);
  restoreSnapshot(r);
  captureWorkspace();
  notice.value = "已从任务快照新建模拟；原任务源码与结果保持在原模拟中";
}
onMounted(async () => {
  window.addEventListener("hashchange", followLearnLink);
  await action(async () => {
    [examples.value, sources.value, operators.value] = await Promise.all([
      api("/examples"),
      api("/data-sources"),
      api("/operators"),
    ]);
    if (examples.value.length)
      example(
        examples.value.find((e) => e.name === "low_volume.py") ||
          examples.value[0],
      );
    await refreshLists();
    const previous = sessionStorage.getItem("factorpool.task");
    if (previous) {
      await openTask(previous);
      if (task.value?.snapshot) restoreSnapshot(task.value.snapshot);
    }
    captureWorkspace();
  });
  timer = setInterval(async () => {
    if (polling) return;
    polling = true;
    try {
      if (task.value?.id)
        sessionStorage.setItem("factorpool.task", task.value.id);
      if (running.value) {
        await refreshTask();
        if (!running.value) await refreshLists();
      }
    } catch (e) {
      error.value = e.message;
    } finally {
      polling = false;
    }
  }, 1200);
});
onBeforeUnmount(() => {
  window.removeEventListener("hashchange", followLearnLink);
  active = false;
  clearInterval(timer);
});
</script>
<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-symbol">F<span>·</span></span
        ><strong>FactorPool</strong>
      </div>
      <nav aria-label="主导航">
        <button
          v-for="[key, icon, title, label] in [
            ['research', 'terminal', 'Simulate', '研究'],
            ['library', 'factor', 'Factors', '因子库'],
            ['learn', 'book', 'Learn', '使用说明'],
            ['data', 'data', 'Data', '数据'],
          ]"
          :key="key"
          :class="{ active: tab === key }"
          :aria-label="label"
          @click="tab = key"
        >
          <UiIcon :name="icon" :size="19" /><span>{{ title }}</span>
        </button>
      </nav>
      <span class="version">PythonIndicator <span>· Research</span></span>
    </header>
    <div v-if="error" class="error" role="alert">
      {{ error }}<button @click="error = ''">关闭</button>
    </div>
    <div v-if="notice" class="notice" role="status">
      {{ notice
      }}<button class="icon-button" aria-label="关闭提示" @click="notice = ''">
        <UiIcon name="close" :size="15" />
      </button>
    </div>
    <main v-if="tab === 'research'" class="simulation-main">
      <div class="workspace-bar">
        <div class="workspace-tabs" role="tablist" aria-label="模拟草稿">
          <button
            v-for="w in workspaces"
            :key="w.id"
            role="tab"
            :aria-selected="workspaceId === w.id"
            :disabled="busy"
            :class="{ active: workspaceId === w.id }"
            @click="selectWorkspace(w.id)"
          >
            <span
              class="simulation-dot"
              :class="{ running: workspaceId === w.id && running }"
            ></span
            >{{ w.label }}
          </button>
          <button
            class="new-simulation icon-button"
            aria-label="新建模拟"
            title="New simulation"
            :disabled="busy"
            @click="newWorkspace()"
          >
            <UiIcon name="plus" :size="18" />
          </button>
        </div>
        <div class="pane-toggles">
          <label
            ><input
              v-model="showCode"
              type="checkbox"
              :disabled="showCode && !showResults"
            />Code</label
          ><label
            ><input
              v-model="showResults"
              type="checkbox"
              :disabled="showResults && !showCode"
            />Results</label
          >
        </div>
      </div>
      <SplitPane
        storage-key="brain-simulation-v1"
        label="调整代码与研究结果比例"
        :initial="50"
        :breakpoint="900"
        :class="{ 'code-only': !showResults, 'results-only': !showCode }"
      >
        <template #first
          ><section class="code-panel">
            <div class="editor-toolbar">
              <button
                class="settings-trigger"
                :class="{ active: settingsOpen }"
                :aria-expanded="settingsOpen"
                @click="settingsOpen = !settingsOpen"
              >
                <UiIcon name="settings" :size="16" />设置</button
              ><span>PythonIndicator · config 覆盖设置中的同名字段</span
              ><button
                class="icon-button"
                aria-label="复制为新模拟"
                title="Clone simulation"
                :disabled="busy"
                @click="newWorkspace(true)"
              >
                <UiIcon name="clone" :size="16" />
              </button>
            </div>
            <div class="factor-name">
              <input
                v-model="name"
                aria-label="因子名称"
                placeholder="Factor name"
              />
            </div>
            <div class="editor-stage">
              <FactorEditor
                :key="workspaceId"
                v-model="source"
                :operators="operators"
              /><WorkspaceSettings
                v-if="settingsOpen"
                :values="settings"
                :sources="sources"
                :resolved="resolvedScript"
                :resolve-error="resolveError"
                :resolving="resolving"
                @apply="applySettings"
                @resolve="resolveScript"
                @close="settingsOpen = false"
              />
            </div>
            <div class="editor-status">
              <span><span class="status-dot"></span>{{ resolvedScript?.factor_class || 'PythonIndicator' }}</span
              ><span>未写入 config 的参数使用设置值</span>
            </div>
            <div class="editor-actions">
              <select
                aria-label="载入示例"
                @change="
                  example(examples[$event.target.value]);
                  $event.target.value = '';
                "
                :disabled="busy"
              >
                <option value="">Examples</option>
                <option v-for="(e, i) in examples" :key="e.name" :value="i">
                  {{ e.name }}
                </option>
              </select>
              <div>
                <button :disabled="busy" @click="save" aria-label="保存版本">
                  <UiIcon name="save" :size="15" /><span>Save</span></button
                ><button
                  v-if="running"
                  :disabled="busy"
                  @click="cancel"
                  aria-label="停止"
                >
                  <UiIcon name="stop" :size="14" />Stop</button
                ><button
                  class="simulate-button"
                  :disabled="busy"
                  @click="run"
                  aria-label="运行研究"
                >
                  <UiIcon name="play" :size="15" />Simulate
                </button>
              </div>
            </div>
          </section></template
        >
        <template #second
          ><SimulationResults
            :task="task"
            :result="result"
            :logs="logs"
            :values="values"
            :offset="offset"
            :busy="busy"
            :running="running"
            :states="states"
            @load-snapshot="loadSnapshot"
            @page="page"
        /></template>
      </SplitPane>
    </main>
    <section v-else-if="tab === 'library'" class="page">
      <div class="page-title">
        <div>
          <span class="eyebrow">FACTOR LIBRARY</span>
          <h1>因子库</h1>
        </div>
        <button
          class="primary"
          :disabled="!selected.length || busy"
          @click="runBatch"
        >
          批量研究 · {{ selected.length }}
        </button>
      </div>
      <p class="muted">
        批量任务以当前设置为默认值，各脚本 config 按字段覆盖；每个子任务保留独立源码与配置快照。
      </p>
      <table>
        <thead>
          <tr>
            <th></th>
            <th>名称</th>
            <th>版本</th>
            <th>源码 SHA</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in factors">
            <td><input v-model="selected" type="checkbox" :value="f.id" /></td>
            <td>{{ f.name }}</td>
            <td>v{{ f.version }}</td>
            <td class="mono">{{ f.source_hash.slice(0, 12) }}</td>
            <td>
              <button :disabled="busy" @click="loadFactor(f.id)">打开</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!factors.length" class="empty">在工作台保存第一个因子版本。</p>
    </section>
    <section v-else-if="tab === 'learn'" class="documentation-page">
      <TutorialPanel :operators="operators" />
    </section>
    <section v-else-if="tab === 'data'" class="page" aria-label="数据"></section>
    <footer v-if="!['research', 'learn', 'data'].includes(tab)">
      <span>FactorPool / Research workspace</span
      ><span>PythonIndicator · FT C++ Core · PostgreSQL</span>
    </footer>
  </div>
</template>
