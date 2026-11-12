<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import FactorEditor from "./components/FactorEditor.vue";
import SplitPane from "./components/SplitPane.vue";
import UiIcon from "./components/UiIcon.vue";
import WorkspaceSettings from "./components/WorkspaceSettings.vue";
import SimulationResults from "./components/SimulationResults.vue";
import { api } from "./api";
const source = ref(""),
  name = ref("20日低成交量因子"),
  factorClass = ref("LowVolumeFactor"),
  params = ref('{"period":20}');
const provider = ref("market_matrix"),
  snapshotId = ref(null),
  universe = ref("000001.XSHE,000002.XSHE,600000.XSHG"),
  dynamic = ref(false);
const start = ref("2024-01-29"),
  end = ref("2024-01-29"),
  groups = ref(3);
const horizons = ref("1,5,20"),
  decaySteps = ref(10);
const tab = ref("research"),
  error = ref(""),
  busy = ref(false);
const factors = ref([]),
  history = ref([]),
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
const settings = computed(() => ({
  provider: provider.value,
  snapshotId: snapshotId.value,
  universe: universe.value,
  dynamic: dynamic.value,
  start: start.value,
  end: end.value,
  groups: groups.value,
  horizons: horizons.value,
  decaySteps: decaySteps.value,
  factorClass: factorClass.value,
  params: params.value,
}));
function applySettings(d) {
  snapshotId.value = d.provider === provider.value ? d.snapshotId || null : null;
  provider.value = d.provider;
  universe.value = d.universe;
  dynamic.value = d.dynamic;
  start.value = d.start;
  end.value = d.end;
  groups.value = d.groups;
  horizons.value = d.horizons;
  decaySteps.value = d.decaySteps;
  factorClass.value = d.factorClass;
  params.value = d.params;
}
function captureWorkspace() {
  const current = workspaces.value.find((w) => w.id === workspaceId.value);
  current.draft = {
    ...settings.value,
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
    applySettings(next.draft);
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
  const factorParams = JSON.parse(params.value);
  if (
    !factorParams ||
    Array.isArray(factorParams) ||
    typeof factorParams !== "object"
  )
    throw new Error("因子参数应为 JSON 对象");
  return {
    name: name.value,
    source_code: source.value,
    factor_class: factorClass.value,
    factor_params: factorParams,
    data: {
      provider: provider.value,
      universe:
        provider.value === "fixture"
          ? "fixture"
          : dynamic.value
            ? "all_a_dynamic"
            : universe.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
      frequency: "1d",
      adjustment: "raw",
      factor_start: start.value,
      factor_end: end.value,
      ...(provider.value === "market_matrix" && snapshotId.value
        ? { snapshot_id: snapshotId.value }
        : {}),
    },
    analysis: {
      entry: "signal_close",
      exit: "next_close",
      groups: Number(groups.value),
      horizons: horizons.value.split(",").map((x) => Number(x.trim())),
      decay_steps: Number(decaySteps.value),
      annual_days: 252,
      min_ic_pairs: 3,
      icir_ddof: 1,
      annualize_icir: false,
      group_weight: "equal",
    },
  };
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
  [factors.value, history.value] = await Promise.all([
    api("/factors"),
    api("/tasks"),
  ]);
}
function example(item) {
  source.value = item.source_code;
  factorClass.value = item.source_code.match(/class\s+(\w+)\(/)?.[1] || "";
  params.value = item.name.includes("volume_rank")
    ? '{"volume_period":20,"rank_period":5}'
    : item.name.includes("close_mean")
      ? '{"period":3}'
      : '{"period":20}';
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
    source.value = d.source_code;
    factorClass.value = d.factor_class;
    params.value = JSON.stringify(d.factor_params || {});
    name.value = d.name;
    tab.value = "research";
    notice.value = `已载入因子 v${f.versions[0].version}`;
  });
}
function clearDraft() {
  factorId.value = null;
  provider.value = "market_matrix";
  snapshotId.value = null;
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
    const r = request();
    const batch = await api("/tasks/batch", {
      factors: selected.value,
      data: r.data,
      analysis: r.analysis,
    });
    notice.value = `批次 ${batch.id} · ${batch.tasks.length} 个任务已提交`;
    tab.value = "history";
    await refreshLists();
  });
}
function restoreSnapshot(r) {
  source.value = r.source_code;
  name.value = r.name;
  factorClass.value = r.factor_class;
  params.value = JSON.stringify(r.factor_params || {});
  factorId.value = null;
  if (r.data) {
    const migrated = r.data.provider === "ft_daily_mmap";
    provider.value = migrated ? "market_matrix" : r.data.provider;
    snapshotId.value = migrated ? null : r.data.snapshot_id || null;
    dynamic.value = r.data.universe === "all_a_dynamic";
    if (Array.isArray(r.data.universe))
      universe.value = r.data.universe.join(",");
    start.value = r.data.factor_start;
    end.value = r.data.factor_end;
  }
  if (r.analysis) {
    groups.value = r.analysis.groups;
    horizons.value = (r.analysis.horizons || [1]).join(",");
    decaySteps.value = r.analysis.decay_steps || 10;
  }
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
            ['library', 'alpha', 'Alphas', '因子库'],
            ['learn', 'book', 'Learn', '使用说明'],
            ['operators', 'data', 'Data', '算子兼容'],
            ['history', 'history', 'History', '测试记录'],
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
                <UiIcon name="settings" :size="16" />Settings</button
              ><span
                >{{ provider === "fixture" ? "SYNTHETIC" : "CHN" }} / D1 /
                {{
                  provider === "fixture"
                    ? "A, B, C"
                    : dynamic
                      ? "All A Dynamic"
                      : "Custom universe"
                }}</span
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
                placeholder="Alpha name"
              /><span>PythonIndicator</span>
            </div>
            <div class="editor-stage">
              <FactorEditor
                :key="workspaceId"
                v-model="source"
              /><WorkspaceSettings
                v-if="settingsOpen"
                :values="settings"
                :sources="sources"
                @apply="applySettings"
                @close="settingsOpen = false"
              />
            </div>
            <div class="editor-status">
              <span><span class="status-dot"></span>{{ factorClass }}</span
              ><span
                >{{ provider === "fixture" ? "合成样例" : "行情只读" }} ·
                {{ start }} — {{ end }}</span
              >
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
        批量任务使用工作台当前数据与研究参数；每个子任务保留独立源码快照。
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
    <section v-else-if="tab === 'history'" class="page">
      <div class="page-title">
        <div>
          <span class="eyebrow">RESEARCH HISTORY</span>
          <h1>测试记录</h1>
        </div>
        <button @click="action(refreshLists)">刷新</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>提交时间</th>
            <th>状态</th>
            <th>批次</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in history">
            <td>{{ t.name }}</td>
            <td>{{ t.created_at }}</td>
            <td>
              <span class="badge" :class="t.status">{{
                states[t.status]
              }}</span>
            </td>
            <td class="mono">{{ t.batch_id?.slice(0, 8) || "--" }}</td>
            <td>
              <button
                @click="
                  action(async () => {
                    await openTask(t.id);
                    tab = 'research';
                  })
                "
              >
                查看
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!history.length" class="empty">
        运行后，源码与结果会保存在这里。
      </p>
    </section>
    <section v-else-if="tab === 'learn'" class="page learn-page">
      <span class="eyebrow">LEARN / PYTHON INDICATOR</span>
      <h1>同一份因子定义，直接做矩阵研究</h1>
      <p>
        通过 PythonIndicator 定义输入、预热窗口和计算方法。TS / CS 算子复用
        FactorTrader 共享实现，点击 Data 查看每个算子的兼容状态。
      </p>
      <div class="learn-columns">
        <article>
          <h2>1. 编写因子</h2>
          <p>
            从 Examples 载入示例。Settings
            中的因子类名、构造参数与代码保持一致，支持编译的算子使用原生执行路径。
          </p>
          <pre>{{ examples[0]?.source_code || "正在载入示例" }}</pre>
        </article>
        <article>
          <h2>2. 设置与模拟</h2>
          <p>
            选择数据源、股票池和信号日期。默认读取 PostgreSQL 中已导入的行情矩阵，提交时固定快照；任务不会导入或刷新行情。也可显式选择合成样例。
          </p>
          <p>
            日收益为收盘价 / 前收盘价 − 1，信号使用下一交易日起的收益矩阵。持有期内权重自然漂移，逐日计算净值与回撤；无费用。
          </p>
          <h2>3. 查看与复现</h2>
          <p>
            右侧导航查看 IC、分组、Alpha
            统计、因子值、日志和提交时的源码。保存版本写入因子库，每次任务保留不可变快照。
          </p>
          <p>
            模拟草稿切换保存在本页内存中；刷新后从最近任务快照恢复。未运行、未保存的编辑请先保存版本。
          </p>
          <h2>数据源</h2>
          <ul>
            <li v-for="s in sources" :key="s.id">
              {{ s.name }} · {{ s.available ? "可用" : "不可用" }}
            </li>
          </ul>
        </article>
      </div>
    </section>
    <section v-else class="page">
      <span class="eyebrow">SHARED INDICATOR / COMPATIBILITY</span>
      <h1>算子兼容台账</h1>
      <p class="muted">
        Python → LEAN →
        BRAIN。前端可调用、原生已验证与官方兼容是独立状态。rank_unit 使用明确的
        FT/Pool 端点合同；BRAIN rate=2 尚待核验。
      </p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>目标算子</th>
              <th>共享入口</th>
              <th>原生实现</th>
              <th>数据要求</th>
              <th>BRAIN 对照</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="o in operators">
              <td>{{ o.name }}</td>
              <td class="mono">{{ o.frontend || "--" }}</td>
              <td>
                <span :class="o.native ? 'supported' : 'muted'">{{
                  o.native ? "已接通" : "待实现 / 待核验"
                }}</span>
              </td>
              <td>{{ o.data }}</td>
              <td>{{ o.brain }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <footer v-if="tab !== 'research'">
      <span>FactorPool / Research workspace</span
      ><span>PythonIndicator · FT C++ Core · PostgreSQL</span>
    </footer>
  </div>
</template>
