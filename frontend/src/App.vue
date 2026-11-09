<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import FactorEditor from "./components/FactorEditor.vue";
import SplitPane from "./components/SplitPane.vue";
import ResearchChart from "./components/ResearchChart.vue";
import FactorDiagnostics from "./components/FactorDiagnostics.vue";
import { api } from "./api";
const source = ref(""),
  name = ref("20日低成交量因子"),
  factorClass = ref("LowVolumeFactor"),
  params = ref('{"period":20}');
const provider = ref("fixture"),
  universe = ref("000001.XSHE,000002.XSHE,600000.XSHG"),
  dynamic = ref(false);
const start = ref("2024-01-29"),
  end = ref("2024-01-29"),
  groups = ref(3);
const horizons = ref("1,5,20"),
  decaySteps = ref(10);
const tab = ref("research"),
  chartMode = ref("ic"),
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
const numeric = (value) => (value == null ? "--" : Number(value).toFixed(4));
const percent = (value) =>
  value == null ? "--" : `${(Number(value) * 100).toFixed(2)}%`;
const summary = computed(() => result.value?.summary || {});
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
    },
    analysis: {
      entry: "next_open",
      exit: "following_open",
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
function loadSnapshot() {
  const r = task.value?.snapshot;
  if (!r) return;
  source.value = r.source_code;
  name.value = r.name;
  factorClass.value = r.factor_class;
  params.value = JSON.stringify(r.factor_params || {});
  factorId.value = null;
  notice.value = "已将任务源码快照载入草稿";
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
    if (previous) await openTask(previous);
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
    <header>
      <div class="brand">
        <span class="mark">F</span><strong>FactorPool</strong
        ><span class="muted">因子研究工作台</span>
      </div>
      <nav>
        <button
          v-for="[key, title] in [
            ['research', '研究'],
            ['library', '因子库'],
            ['history', '测试记录'],
            ['operators', '算子兼容'],
          ]"
          :key="key"
          :class="{ active: tab === key }"
          @click="tab = key"
        >
          {{ title }}
        </button>
      </nav>
      <span class="version">FT Indicator · 报告 v2</span>
    </header>
    <div v-if="error" class="error" role="alert">
      {{ error }}<button @click="error = ''">关闭</button>
    </div>
    <div v-if="notice" class="notice">{{ notice }}</div>
    <main v-if="tab === 'research'">
      <SplitPane
        storage-key="workbench"
        label="调整代码与研究结果比例"
        :initial="42"
        :breakpoint="900"
      >
        <template #first
          ><section class="code-panel">
            <div class="panel-top">
              <span class="eyebrow">FACTOR / PYTHON</span>
              <div>
                <button @click="clearDraft">新建</button
                ><button :disabled="busy" @click="save">保存版本</button>
              </div>
            </div>
            <div class="factor-name">
              <input v-model="name" aria-label="因子名称" /><select
                aria-label="载入示例"
                @change="example(examples[$event.target.value])"
              >
                <option disabled selected>载入示例</option>
                <option v-for="(e, i) in examples" :value="i">
                  {{ e.name }}
                </option>
              </select>
            </div>
            <FactorEditor v-model="source" />
            <div class="config">
              <div class="config-title">
                研究配置 <span>日线 · 原始价格 · 无费用</span>
              </div>
              <div class="field-grid">
                <label>因子类<input v-model="factorClass" /></label
                ><label>构造参数 JSON<input v-model="params" /></label>
                <label class="wide"
                  >数据源<select v-model="provider">
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
                <label v-if="provider !== 'fixture'" class="wide"
                  >股票池
                  <span
                    ><input v-model="dynamic" type="checkbox" /> 历史动态全 A
                    股</span
                  ><input
                    v-if="!dynamic"
                    v-model="universe"
                    placeholder="逗号分隔证券代码"
                /></label>
                <label>开始日期<input v-model="start" type="date" /></label
                ><label>结束日期<input v-model="end" type="date" /></label>
                <label
                  >分析周期<input
                    v-model="horizons"
                    aria-label="分析周期"
                    placeholder="1,5,20" /></label
                ><label
                  >衰减阶数<input
                    v-model="decaySteps"
                    type="number"
                    min="1"
                    max="10"
                    aria-label="衰减阶数"
                /></label>
                <label
                  >分组数<input
                    v-model="groups"
                    type="number"
                    min="1"
                    max="100" /></label
                ><label
                  >一期明细区间<span class="fixed-field"
                    >T+1 开盘 → T+2 开盘</span
                  ></label
                >
              </div>
            </div>
            <div class="run-bar">
              <span>{{
                provider === "fixture"
                  ? "合成样例 · A / B / C"
                  : "真实行情 · 只读快照"
              }}</span>
              <div>
                <button :disabled="!running || busy" @click="cancel">
                  停止</button
                ><button class="primary" :disabled="busy" @click="run">
                  ▶ 运行研究
                </button>
              </div>
            </div>
          </section></template
        >
        <template #second
          ><section class="result-panel">
            <div class="panel-top">
              <span class="eyebrow">RESEARCH / RESULT</span
              ><span class="badge" :class="task?.status">{{
                task ? states[task.status] || task.status : "等待运行"
              }}</span>
            </div>
            <div class="result-heading">
              <h1>{{ task?.name || "从一个因子开始" }}</h1>
              <button v-if="task?.snapshot" @click="loadSnapshot">
                载入本次源码
              </button>
            </div>
            <div class="result-meta" v-if="task">
              {{ task.id
              }}<span v-if="result">
                · {{ result.metadata.execution_mode }} · 预热
                {{ result.metadata.warm_up_period }} 期 ·
                {{ result.metadata.synthetic ? "合成数据" : "真实行情" }}</span
              >
            </div>
            <p v-if="task?.error" class="error">{{ task.error }}</p>
            <template v-if="result">
              <div class="metrics">
                <div
                  v-for="[title, value] in [
                    ['IC 均值 · 1期', numeric(summary.ic_mean)],
                    ['Rank IC · 1期', numeric(summary.rank_ic_mean)],
                    ['ICIR · 1期', numeric(summary.icir)],
                    ['因子覆盖率', percent(summary.coverage)],
                  ]"
                >
                  <span>{{ title }}</span
                  ><strong>{{ value }}</strong>
                </div>
              </div>
              <FactorDiagnostics
                v-if="result.diagnostics"
                :report="result.diagnostics"
              />
              <template v-if="!result.diagnostics">
                <div class="chart-toolbar">
                  <div>
                    <button
                      :class="{ active: chartMode === 'ic' }"
                      @click="chartMode = 'ic'"
                    >
                      IC 序列</button
                    ><button
                      :class="{ active: chartMode === 'group' }"
                      @click="chartMode = 'group'"
                    >
                      分组收益
                    </button>
                  </div>
                  <span>{{ summary.valid_dates }} 个有效 IC 日期</span>
                </div>
                <ResearchChart :daily="result.daily" :mode="chartMode" />
                <div class="caption">
                  等权研究收益，未计费用、滑点和成交限制。缺失值显示 --；ICIR
                  未年化。
                </div>
                <details>
                  <summary>每日 IC 与分组明细</summary>
                  <div class="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>日期</th>
                          <th>IC</th>
                          <th>Rank IC</th>
                          <th>配对数</th>
                          <th v-for="(_, i) in result.daily[0]?.group_returns">
                            G{{ i + 1 }}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="d in result.daily">
                          <td>{{ d.date }}</td>
                          <td>{{ numeric(d.ic) }}</td>
                          <td>{{ numeric(d.rank_ic) }}</td>
                          <td>{{ d.pairs }}</td>
                          <td v-for="r in d.group_returns">{{ percent(r) }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </details>
              </template>
              <details open class="factor-values">
                <summary>
                  因子值 <span>{{ result.metadata.value_count }} 条</span>
                </summary>
                <div class="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>日期</th>
                        <th>证券</th>
                        <th>因子值</th>
                        <th>未来收益（1期）</th>
                        <th>分组</th>
                        <th>状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(r, i) in values" :key="i">
                        <td>{{ r.trading_date }}</td>
                        <td>{{ r.symbol }}</td>
                        <td>{{ numeric(r.value) }}</td>
                        <td>{{ percent(r.forward_return) }}</td>
                        <td>{{ r.factor_group ?? "--" }}</td>
                        <td>{{ r.reason }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div class="pagination">
                  <button :disabled="offset === 0 || busy" @click="page(-100)">
                    上一页</button
                  ><span>{{ offset + 1 }}–{{ offset + values.length }}</span
                  ><button
                    :disabled="
                      offset + values.length >= result.metadata.value_count ||
                      busy
                    "
                    @click="page(100)"
                  >
                    下一页
                  </button>
                </div>
              </details>
              <details>
                <summary>复现信息与口径</summary>
                <pre>{{ JSON.stringify(result.metadata, null, 2) }}</pre>
              </details>
            </template>
            <div v-else class="empty">
              <div class="empty-symbol">ƒ</div>
              <h2>{{ running ? "正在执行研究任务" : "一份定义，共享计算" }}</h2>
              <p>
                {{
                  running
                    ? "准备、读取与计算进度见下方日志。"
                    : "编写 PythonIndicator，选择股票池与日期，查看因子值、IC 和分组表现。"
                }}
              </p>
              <p v-if="!task" class="muted">
                默认样例可手工核对：IC = 1，分组收益 -10% / 0% / 10%。
              </p>
            </div>
            <details class="logs" :open="running || task?.status === 'FAILED'">
              <summary>任务日志</summary>
              <pre>{{ logs || "暂无日志" }}</pre>
            </details>
          </section></template
        >
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
            <td><button @click="loadFactor(f.id)">打开</button></td>
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
    <footer>
      <span>FactorPool / Research workspace</span
      ><span>PythonIndicator · FT C++ Core · PostgreSQL</span>
    </footer>
  </div>
</template>
