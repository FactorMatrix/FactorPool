# FactorPool：参考 FTServer 的极简因子研究平台实操方案

> 文档类型：新项目建设说明 / 开发任务书  
> 范围：独立 FactorPool 项目，参考 FTServer 结构和页面，首版用于因子编写、计算、管理与快速研究  
> 日期：2026-11-06  
> 状态：**以下为目标设计和实施要求，不代表 FactorPool 已经开发完成。**

## 1. 项目要做成什么样

**FactorPool 是一套精简的因子管理和研究工具：左边写 Python 因子，右边看测试结果；后台用 Java/Spring 管任务和资源，底层用 C++ 执行因子计算和简化研究。**

用户需要完成的操作：

1. 打开页面，直接进入工作台，无需登录。
2. 在左侧输入或选择一个 `PythonIndicator` 因子类。
3. 选择数据源、股票池、日期、频率、未来收益和分组数。
4. 点击运行，右侧显示计算进度、日志、IC、Rank IC、ICIR、分组表现和因子值。
5. 保存因子、查看历史测试，后续可批量计算和管理。

**项目的主要难点是指标能力和数据口径：**输入对象、窗口、预热、`.ts`、`.cs`、截面同步、缺失值、行业和财务字段；这些能力应复用和完善 FT 的 Indicator，而不是因 FactorPool 是新项目就重新写一套。

### 1.1 第一版边界

| 需要 | 第一版方案 |
|---|---|
| C++ | 极简因子研究执行核心：读取数据、推进时间、调用共享指标核、输出因子和研究结果。 |
| Python | 因子作者使用同一个 `PythonIndicator` 类；Python 承载代码加载和绑定，受支持的计算尽量下沉 C++。 |
| Java | 一个 Spring Boot 后台，负责资源配置、因子与任务管理、启动执行进程、结果入库和 API。 |
| 前端 | Vue 3 + Vite，参考 FTServer 的工作台；左侧代码框、右侧因子测试结果。 |
| 数据库 | PostgreSQL 保存因子、版本、任务、结果和需要保留的因子值。行情源继续按已有 DataSource / Data Mod 接入。 |
| 登录 | 第一版不做注册、登录、角色、权限、用户中心或 user-service；按单机 / 可信使用场景设计。 |
| 缓存 | 先复用实际数据源的读数和缓存能力；Redis 为可选项，第一版不作为启动必需组件。 |
| 回测范围 | 首版是**因子研究回测**：按指定价格算未来收益、IC 和分组收益；不复制 FT 的账户、订单、撮合、费用、风险和完整交易引擎。 |

完整策略交易、委托成交和真实账户需要时，使用 FactorTrader；FactorPool 的分组收益要标注为**按所选价格和权重计算的研究结果**，不能自动称作真实成交收益。

## 2. 已核对的 FTServer：参考哪里

本次查看的是 [FactorMatrix/FTServer](https://github.com/FactorMatrix/FTServer) 的 `main`，提交 `47aa87eced06e63bd8ac74f96b8bf84d07f1f02c`；[FactorPool](https://github.com/FactorMatrix/FactorPool) 的 `main` 为 `74526d68a4b1f067f22fef0e7cac91c1047a541d`，当前仓库根目录只看到 `LICENSE`。以上是该时间的仓库状态，不代表线上部署，也不等于新项目已经实现。

| FTServer 实际位置 | 已看到的职责 | FactorPool 具体怎么参考 |
|---|---|---|
| [`frontend/src/components/StrategyEditor.vue`](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/frontend/src/components/StrategyEditor.vue) | CodeMirror 代码编辑器，包含 Python/C++ 高亮、查找、替换、撤销和只读等。 | 参考并提取适用的 Python 编辑器，改成因子代码框；按实际依赖一起接入。当前 FT 用的是 **CodeMirror，不是 Monaco**。 |
| [`frontend/src/components/SplitPane.vue`](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/frontend/src/components/SplitPane.vue) | 可拖动横向 / 纵向分栏，保存分栏比例。 | 复用或参考交互：左代码、右结果；窄屏变上下布局。 |
| [`frontend/src/components/EquityChart.vue`](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/frontend/src/components/EquityChart.vue) | FT 使用 ECharts 展示策略净值 / 基准等。 | 参考图表结构，**改为 IC、分组净值等因子研究数据**；不能把策略曲线直接当作因子结果。 |
| [`frontend/src/components/LogPanel.vue`](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/frontend/src/components/LogPanel.vue) | 展示任务日志。 | 参考日志样式和滚动行为；对接 FactorPool 自己的任务 API。 |
| [`frontend/src/App.vue`](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/frontend/src/App.vue) | 当前 FT 页面组合、左右分栏、任务和结果；还包含登录、策略后台等业务。 | 参考布局、页面交互和视觉风格；抽出需要的部分，不整体搬入登录和交易策略状态。 |
| [`backend`](https://github.com/FactorMatrix/FTServer/tree/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/backend) | Spring Boot / Java 17，对外 HTTP、请求处理和转发。 | 借鉴 HTTP 接口组织、请求/响应和配置方式；FactorPool 首版只建一个 Spring 服务。 |
| [`backtest-platform`](https://github.com/FactorMatrix/FTServer/tree/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/backtest-platform) | FT 的任务队列、Worker、资源配置、状态、日志、结果归档。 | 借鉴任务生命周期、执行和资源边界；**只实现 FactorPool 首版需要的队列与进程管理**。 |
| [`engine-core-cpp/mods/sys_indicator`](https://github.com/FactorMatrix/FTServer/tree/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/engine-core-cpp/mods/sys_indicator) | FT 的 C++ 指标数值、状态、Graph 和截面执行能力。 | **作为同一套指标数值能力复用 / 组件化的目标**；先验证与 Runtime 的实际依赖，不能直接假设已能独立链接。 |
| [`engine-sdk/language/python/.../indicator`](https://github.com/FactorMatrix/FTServer/tree/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/engine-sdk/language/python/src/factortrader/indicator) | `PythonIndicator`、`.ts/.cs` 前端、注册、源码分析等。 | 保持同一个因子类和同一套语义；补共享组件所需的最小研究入口。 |

参考 FT 的**分层与可用组件**，不意味着把 FT 的所有服务、配置、业务模块和目录原样复制。FT 当前的 Backend、Platform、User Service 是分开的；FactorPool 首版按用户要求合成**一个 Java 服务、不做用户服务**。

## 3. 最小架构和职责

| 部件 | 负责什么 | 不负责什么 |
|---|---|---|
| Vue 3 工作台 | 编辑因子、填写研究参数、提交任务、展示结果和历史。 | 不计算真实因子、不直接读数据库、不直接调用 C++。 |
| Spring Boot 后台 | 提供 API；保存因子/版本；管理队列、进程、资源配置、日志、结果和数据库。 | 不在 Java 中重写 `.ts/.cs` 算子，不逐根 Bar 调用 Python / C++。 |
| Python 运行入口 | 加载用户的因子类、连接 FT Indicator 编译与绑定，发起一次研究任务。 | 不复制一套 pandas 计算结果冒充共享 C++ 内核；不承诺任意 Python 都能编译。 |
| C++ 极简研究核心 | 使用选定数据源按时间推进、调用共享指标组件、计算研究统计、输出结果。 | 不实现完整交易账户、委托、撮合、交易风控及 Trader。 |
| 共享 Indicator 组件 | `PythonIndicator` 合同、TS/CS、窗口状态、截面封口、算子图和数值逻辑。 | 不为 FactorPool 维护一份和 FT 长期分叉的副本。 |
| 数据源 / PostgreSQL | 数据源提供真实行情及必要历史；PostgreSQL 管理因子、任务与研究结果。 | 不把数据库或 Redis 当作指标计算引擎。 |

实际调用顺序：**浏览器 → Spring Boot → 研究执行进程 → Python 因子入口和 FT SDK 绑定 → C++ 研究核心 / 共享 Indicator → 数据源；结果再由后台保存并返回页面。**

### 3.1 为什么会有 Python 入口，底层仍然是 C++

用户写的是 Python 因子类，所以需要有 Python 环境加载类。建议首版由 Java 启动**每个任务一个独立研究进程**：Python 负责加载和绑定；受支持的表达式注册一次、编译准备一次；时间推进、滚动、截面和批量计算由 C++ 组件完成。

这只是推荐的首版执行形态，具体进程入口和导入 API **需要开发**：

- 不能把每一只股票、每一根 Bar 都经 Java HTTP 调一次 Python。
- 已由 FT 原生编译器支持的表达式，走共享 C++ Indicator 核；具体可编译子集以源码和测试为准。
- 普通 Python 自定义单股指标可以显式走 Python 回调，但要展示 `python` 执行模式，不能说它自动等同 C++ 速度。
- 包含 `.cs` 的表达式，如无法形成正确截面执行，就在任务准备时给出错误；不能逐只股票回调后冒充全市场截面。
- 每任务进程简化启动、停止和状态隔离；先测启动开销。如果未来批量短任务的进程开销确实明显，再评估复用 Worker，不预设必须建设复杂进程池。

### 3.2 复用 FT 指标的真实边界

FT 当前 [Indicator 合同](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/engine-core-cpp/docs/INDICATOR-ENGINE.md)把 Python 指标前端、编译和 C++ `sys_indicator` 分在不同层；其现有运行方式涉及 SDK Runtime、Host、数据 Mod 和 Core。

**第一项技术验证就是把这条链跑通。**不能现在就断言 `sys_indicator` 已经可以作为一个完全独立的动态库直接安装。按这个顺序做：

1. 固定 FT 提交、Python 包、C++ Core、SDK、Data Mod 的构建版本和 ABI。
2. 盘点因子准备、数据驱动、预热、截面封口和结果导出分别依赖哪些 FT 组件。
3. 优先提取 / 暴露最薄的研究调用入口，让两项目使用同一份 `PythonIndicator`、编译器和 C++ 指标实现。
4. 如果脱离现有 Runtime 会破坏语义，首版先用**实际必需的现有 FT SDK/Host/Runtime 组件**跑通，后续再按真实耦合逐步精简；不能为“目录看起来简单”复制第二套执行器和指标核。
5. 记录构建版本、数据快照和算子语义；同一份因子在两端逐时点、逐股票对账。

本方案把因子研究工作叫“极简研究核心”，并不是声称 FT 当前的 `EngineRunner` 能直接删掉交易依赖后独立运行。这要先做原型验证。

## 4. 推荐目录结构

以下是 **FactorPool 新项目建议目录**，用于给开发者或 AI 落地；不是现有仓库已经有这些文件。

| 目录 / 文件 | 要放什么 |
|---|---|
| `FactorPool/README.md` | 项目目标、最小启动步骤、运行一个因子的方法。 |
| `FactorPool/docs/` | 架构、配置、数据和运行说明；算子细节引用既有 Indicator 规范。 |
| `FactorPool/backend/` | 唯一的 Java / Spring Boot 服务。 |
| `backend/src/main/java/.../factor/` | 因子保存、版本、查询。 |
| `backend/src/main/java/.../task/` | 任务提交、状态、队列、取消、日志与进程管理。 |
| `backend/src/main/java/.../result/` | 结果查询、汇总和批量入库。 |
| `backend/src/main/java/.../config/` | 配置解析、资源限制、数据源配置。 |
| `backend/src/main/resources/application.yml` | PostgreSQL、数据源、执行进程、资源预算等后台配置。 |
| `backend/src/main/resources/db/migration/` | PostgreSQL 表结构和版本迁移。 |
| `FactorPool/frontend/` | Vue 3 + Vite 工作台。 |
| `frontend/src/components/FactorEditor.vue` | 参考 FT `StrategyEditor.vue` 的因子代码框。 |
| `frontend/src/components/SplitPane.vue` | 参考 FT 的可拖动分栏组件。 |
| `frontend/src/components/ResultPanel.vue` | 右侧指标卡片、图表、数据表和日志。 |
| `frontend/src/components/RunConfig.vue` | 数据范围、股票池、收益计算和分组设置。 |
| `frontend/src/api/` | Spring API 调用；错误和任务状态保持原样。 |
| `FactorPool/research-core-cpp/` | 新增的精简 C++ 研究计算与结果输出层。 |
| `research-core-cpp/src/runner/` | 根据真实数据时序驱动研究任务和因子值生成；如依赖现有 FT Runner，则使用必要的既有执行路径，不复制交易 Runner。 |
| `research-core-cpp/src/analysis/` | IC / Rank IC / ICIR、分组统计和未来收益的研究计算。 |
| `research-core-cpp/src/output/` | 按批输出结果；不在每个数值点都创建 Python 对象。 |
| `research-core-cpp/tests/` | 小样本手工结果、无效值、日期与性能对账。 |
| `FactorPool/runner-python/` | 加载 Python 因子、连接 FT SDK 和 C++ 研究入口的最小运行适配。 |
| `runner-python/factorpool_runner/` | 任务启动、因子类定位、准备、调用 C++ 和异常输出；**拟新增**。 |
| `FactorPool/examples/factors/` | 可以被 FT 和 FactorPool 共同使用的 PythonIndicator 因子案例。 |
| `FactorPool/tests/parity/` | 固定输入下 FT / FactorPool 的逐值、状态和时间对账。 |

`research-core-cpp` 不应出现另一份独立维护的 `indicator/ts/cs` 算法目录；Indicator 的代码来源仍是 FT 共享组件。能否最终以动态库、静态库、SDK Host 或现有进程运行，以第 3.2 节技术验证结果确定。

## 5. 前端工作台：左边代码，右边因子测试结果

### 5.1 页面布局

| 左侧：因子代码框，初始约 42% | 右侧：测试结果，初始约 58% |
|---|---|
| 顶部：因子名称、新建、保存、载入、运行、停止。 | 顶部：当前任务名、运行状态、进度和耗时。 |
| 中间：CodeMirror Python 编辑器；语法高亮、行号、查找、替换、撤销、重做。 | 指标卡：IC 均值、Rank IC 均值、ICIR、样本覆盖率、有效日期数。 |
| 底部或弹出面板：数据源、股票池、日期、频率、未来收益、分组数。 | 图表：IC 序列、各组收益 / 累计收益；可切换。 |
| 首版只要求编辑 PythonIndicator 因子；同一个类可在 FT 与 Pool 使用。 | 明细标签：因子值、每日 IC、各组收益、任务日志和错误信息。 |

- 中间分界线可拖动、双击恢复；窄屏时变上下排列。参考 FT 的 `SplitPane.vue`。
- 右侧图表参考 FT 的 ECharts 组织方式，但字段和标题明确改为**因子研究**，不沿用策略账户收益标签。
- 用户编辑代码时，已经提交的任务使用**提交瞬间的源码和参数快照**；后续修改不改变正在运行的任务。
- 草稿与结果分开。新建或修改因子不会冒充重新运行；打开历史只读取历史结果，不重新计算。
- 未运行显示引导；运行中显示状态和日志；失败显示原因；结果缺失显示 `--`，不得拿上一次成功结果冒充本次结果。
- 历史任务列表能查看任务名称、因子版本、时间、状态、耗时、参数和结果；先做列表和打开，不需要做复杂账号权限。

### 5.2 页面最少功能

| 页面 | 内容 | 第一版操作 |
|---|---|---|
| 因子工作台 | 左代码、右结果。 | 新建、编辑、保存、配置、运行、停止、查看日志。 |
| 因子库 | 以列表查看已保存因子。 | 按名称查询，查看版本和最后测试结果，打开因子。 |
| 测试记录 | 已提交和历史任务。 | 查看状态、打开结果；失败和取消也保留可见记录。 |
| 后台配置 | 数据源和服务资源。 | 在后台配置文件中设定并重启 / 按实际支持的方式生效；先不强制制作独立管理页面。 |

前端配置研究任务时只提交该任务的业务参数；机器路径、缓存预算、最大并发等由后端统一管理。需要给使用者开放哪些研究参数，和服务资源由谁控制是两层概念。

## 6. 指标写法：同一份因子类

### 6.1 因子作者看到的目标写法

以下是一个**目标接口示例**：对每只股票求 20 期成交量均值，再对其负值做截面排名。数值高表示相对于当期股票池成交量较低。`self.ts.mean`、`PythonIndicator` 在固定 FT 源码中有入口；**本示例的 `rank(..., rate=2)` 作为 BRAIN 兼容目标尚需实现 / 核验，不能当成当前 FT 已支持的参数。**

```python
from factortrader import PythonIndicator

class LowVolumeFactor(PythonIndicator):
    def __init__(self, period=20):
        super().__init__()
        self.period = period

    def update(self, input):
        avg_volume = self.ts.mean(input.volume, self.period)
        self.value = self.cs.rank(-avg_volume, rate=2)  # BRAIN 目标口径，拟接通
        return self.ts.is_ready(avg_volume)
```

这一个类由页面保存为 Python 源码，FactorTrader 和 FactorPool 都导入它。FactorPool 提供研究数据和任务环境，FT 提供策略运行环境；因子作者不再额外写 `update_batch`、跨股票同步或数据库代码。

**重要区别：**固定 FT 的 `self.cs.rank(values, ascending=True, pct=False)` 当前采用平均并列名次及其 `pct` 口径；上面 `rate=2` 和 BRAIN 目标口径还没有证明已接通。开发要按本项目对齐需求补齐并测试，而不能把目前 FT 的 `pct=True` 直接说成世坤排名。 以 3 只股票的 20 期均量 `[10,20,30]` 为例，现有 FT 的 `rank(-avg_volume, pct=True)` 按源码合同得到 `[1,2/3,1/3]`；本方案目标的无并列端点排名是 `[1,0.5,0]`。前者可用于验证现有链路，后者必须等兼容能力完成后才能作为 BRAIN 目标验收。

### 6.2 指标组件要支持的事情

| 能力 | 具体要求 |
|---|---|
| 真实 input | `TradeBar` 行情用真实 `open/high/low/close/volume`；普通数值点有自己的 `value`。一个当前数值不自动包含历史。 |
| `.ts` | 按每只证券维护多期状态、窗口、预热与有效性；不得不同股票串状态。 |
| `.cs` | 同一时点、同一股票池的证券先结算完整，再做截面计算并回填原股票轴。 |
| `.ts → .cs → .ts` | 一个截面节点完成后才推进下游时序节点；支持多个截面节点的依赖顺序。 |
| 数据类型 | 行情、行业、财务、向量字段由真实 Provider 提供；不能假设它们全部是 Bar 原生字段。 |
| 缺失与零 | 无 Bar、无字段、NaN、未就绪和有效 0 分开；不存在以无效 0 冒充有效因子值。 |
| 来源顺序 | 语义匹配时先复用 Python 自带能力，再核对 LEAN 已有能力，最后补已确认的 BRAIN 需求。 |
| 双方合同 | BRAIN 兼容能力按目标 BRAIN 版本对齐；LEAN 原生专属能力保留 LEAN 合同；来源不明的候选先核实，不因没看到某页面而随意删除。 |
| 实现状态 | 每个算子区分 FT 已有入口、底层已有、拟新增、单测通过、双项目对账通过、官方兼容待核验。 |

具体算子及其参数、数据输入和案例，以已有文档 [FactorPool_项目需求与Indicator统一规范.md](https://chatgpt.com/library?file_id=libfile_e497535a11588191968e4943bd2775d3&account_id=personal) 为准；[WorldQuant_BRAIN_算子页面整理.md](https://chatgpt.com/library?file_id=libfile_d9cafafe23d881918c386806091d681f&account_id=personal) 是此前页面记录的独立表格。两份记录不能自动证明 BRAIN 实际执行器与 FT / Pool 已经一致。

## 7. 一次因子测试从点击到出结果

| 步骤 | 谁做 | 输入和处理 | 输出 |
|---:|---|---|---|
| 1 | 前端 | 编辑因子，填写日期、股票池、数据源、收益区间、组数。 | 一次完整的提交请求。 |
| 2 | Spring | 校验名称、源码、日期、数据源和研究参数；保存不可变源码 / 参数快照。 | `task_id`，初始状态排队。 |
| 3 | Spring | 按后台并发和队列预算启动任务进程。 | 状态运行中，记录进程、开始时间和日志。 |
| 4 | Python 入口 | 加载目标类、构造实例，调用共享 FT 指标准备流程；检测依赖和可执行模式。 | 准备成功，或给出明确的源码位置 / 不支持原因。 |
| 5 | 数据源 + C++ | 读取股票池、行情和所需预热历史；按真实时间推进共享指标。 | 每个时点、每只股票的因子值、有效性和就绪状态。 |
| 6 | C++ 研究分析 | 根据指定的未来入场 / 退出价格计算未来收益，再做 IC、Rank IC、ICIR 和分组研究。 | 汇总、逐日序列、分组数据、覆盖率和缺失原因。 |
| 7 | 执行入口 + Spring | 按批交接结构化结果；后台写 PostgreSQL，并在完成入库后发布任务成功。 | 持久化结果、终态和日志。 |
| 8 | 前端 | 轮询任务状态；成功后读取汇总与分页明细。 | 右侧展示卡片、曲线、表格和日志。 |

**时间规则必须明确：**

- 例如 T 日收盘后形成因子，选择 T+1 开盘入场、T+2 开盘退出，则未来收益为 `open[T+2] / open[T+1] - 1`；这是一种可选研究配置，不是固定的 FT 撮合方式。
- 用户选的开始 / 结束日表示**因子评估日期**，预热需要额外向前取数据；未来收益需要向后多取足够的价格。无法取足未来价格时对应样本无效，不能偷用别的日期或填 0。
- 计算因子的 `input` 只能看到该时点可用的数据；T+1 / T+2 的价格只给事后的研究标签使用，不能参与 T 日因子计算。
- 截面股票池、财务公告和行业分类必须按历史真实可见时点取值；缓存要区分数据源、频率、复权和快照。
- 多股票不必在 Python 侧手工循环做截面，截面封口和批量推进由共享指标执行层负责。

## 8. Java / Spring Boot：资源配置、任务和结果

### 8.1 Java 负责哪些配置

| 配置类别 | 例子 | 放哪里 |
|---|---|---|
| 平台资源 | 最大同时运行任务数、队列长度、任务超时。 | Spring 后台配置。 |
| 数据资源 | FT 数据源类型、可用数据路径、读数缓存预算。 | 由后台向实际 Data Mod / SDK 提供；必须有对应接线才生效。 |
| 连接配置 | PostgreSQL 地址、账号、数据库、结果目录。 | Spring 后台配置 / 部署环境。 |
| 执行程序 | Python 环境、研究入口、共享库 / 构建版本。 | Spring 后台配置；任务运行前检查。 |
| 业务参数 | 因子源码与参数、标的池、时间、频率、收益价格、组数。 | 页面填写并随任务快照保存。 |

下面是**拟定的配置示意**，不是 FT 已支持的原样配置；实际键名和 Data Mod 参数必须在接线时对应验证：

```yaml
factorpool:
  task:
    max-concurrent: 1       # 首版同时运行的任务数量
    max-queued: 20          # 等待队列上限
    timeout-seconds: 3600   # 单任务超时
  runner:
    python: /opt/factorpool/bin/python
    work-dir: /data/factorpool/tasks
  data:
    provider: ft-data-mod   # 需要实际实现的注册名
    root: /data/market      # 示例；以本机配置为准
    cache-mb: 512           # 只有实际接到数据 Mod 才生效
  postgres:
    url: jdbc:postgresql://127.0.0.1:5432/factorpool
```

首版以 **1 个任务并发**作为易验证的起点，配置可以调整；这不是性能上限或已经测得的最优值。配置文件中的线程 / 缓存 / 内存数字只有在对应执行层确实接通后才有实际效果；任务数限制不等于操作系统 CPU 或内存硬限制。

### 8.2 任务状态和控制

| 状态 | 说明 | 用户看到什么 |
|---|---|---|
| `QUEUED` | 参数和快照已保存，等待执行。 | 排队中，可停止。 |
| `RUNNING` | 进程已启动，正在准备、读取、计算或入库。 | 当前阶段、进度、日志和耗时。 |
| `CANCELLING` | 已请求停止，等待进程退出 / 清理。 | 正在停止。 |
| `SUCCEEDED` | 所需结果完整写入，已可读取。 | 显示本次结果。 |
| `FAILED` | 准备、数据、计算或入库失败。 | 显示失败阶段和原因。 |
| `CANCELLED` | 任务停止完成。 | 显示已停止；不能当成功结果。 |
| `INTERRUPTED` | 进程或后台异常中断，结果未完成。 | 显示中断，可重新提交。 |

同一 `task_id` 的源码和参数不能在运行中被覆盖；重跑创建新的任务。停止仅针对当前任务执行进程，先尝试合作退出，必要时终止该进程并在确认退出后收口状态。后台重启时，已持久化的历史继续可查，无法确认仍在运行的任务应恢复或标记中断，不能直接标成功。

### 8.3 建议的最小 API

下面的路径和字段是**待实现合同**，不是现有 FactorPool 的接口；最终可结合实际编码命名。

| 方法 / 路径 | 用途 |
|---|---|
| `GET /api/factors` | 查询因子库。 |
| `POST /api/factors` | 保存因子；生成或更新一个明确的因子版本。 |
| `GET /api/factors/{id}` | 读取因子及版本信息。 |
| `POST /api/tasks` | 用当前编辑器源码快照和研究参数创建任务。 |
| `GET /api/tasks` | 查询历史任务列表。 |
| `GET /api/tasks/{id}` | 状态、进度、执行模式、阶段耗时、错误。 |
| `POST /api/tasks/{id}/cancel` | 请求停止任务。 |
| `GET /api/tasks/{id}/logs` | 获取该任务日志。 |
| `GET /api/tasks/{id}/result` | 汇总、IC 序列、分组结果；大结果按日期 / 股票分页。 |
| `GET /api/data-sources` | 列出后端已配置且可用的数据源与支持频率。 |

一个提交请求的**目标字段示意**如下；`source_code` 实际由左侧编辑器提交完整代码，`provider`、时间和股票池值需映射到真实已配置数据：

```json
{
  "name": "20日低成交量因子",
  "factor_class": "LowVolumeFactor",
  "source_code": "此处为编辑器中的完整 Python 类源码",
  "factor_params": {"period": 20},
  "data": {
    "provider": "ft-data-mod",
    "universe": "all_a_dynamic",
    "frequency": "1d",
    "adjustment": "adjusted",
    "factor_start": "2024-01-01",
    "factor_end": "2024-12-31"
  },
  "analysis": {
    "entry": "next_open",
    "exit": "following_open",
    "groups": 3,
    "group_weight": "equal",
    "min_ic_pairs": 3,
    "icir_ddof": 1,
    "annualize_icir": false
  }
}
```

这些字段为演示而定，不表示当前接口、provider 或数据已经存在。JSON 的不可用数值用 `null`，并配合状态 / 原因；不要返回非标准 JSON 数值 `NaN`，也不要转成 0。

## 9. C++ 极简研究核心：具体做什么

核心只围绕一次研究任务完成以下工作，代码尽量短，复杂逻辑集中在共享 Indicator 和经过定义的统计计算里。

| 能力 | 首版要求 |
|---|---|
| 输入数据 | 按指定 Provider、日期、频率、股票池读取行情；支持查询因子所需预热区间及收益所需的未来价格。 |
| 时间推进 | 按时间与标的对齐数据，调用共享 Indicator 的实际公开能力；处理数据缺失和截面封口。 |
| 因子输出 | 产生 `time / symbol / value / valid / ready / reason`，保留因子与数据版本。 |
| 未来收益 | 根据请求的价格字段、入场日、退出日计算；价格无效或不足时标缺失。 |
| 研究统计 | 计算逐日 IC、Rank IC、ICIR、各组样本数、组收益、累计收益和覆盖率。 |
| 结果输出 | 汇总和小结果用结构化元数据；大量因子值、逐日明细按批输出并持久化，不能每个数值经 Java / Python 单独往返。 |
| 日志和停止 | 输出真实阶段和错误；能检查停止请求；进程退出后释放本任务资源。 |

### 9.1 数据流和计算责任

- 数据读取与原始行情缓存优先沿用 FT 的 DataSource / Data Mod：行情根路径和缓存由服务资源配置提供，具体选源由任务声明。不能因 FactorPool 重复维护一份无界全市场行情缓存。
- Indicator 窗口和截面状态按任务、指标节点、标的隔离；同一批依赖可以合理共享只读输入，但不能串改。
- 研究核心对**因子值**做后续统计；不能把 IC、分组收益或真实持仓塞进 `PythonIndicator.update`，导致因子类只能用于 FactorPool。
- 首版读取可用日线，分钟和其他资产使用同一接口扩展，但必须按实际 Provider、日历和性能验证后标明支持；不声称一开始所有市场都已接通。
- 单因子任务先打通；批量任务可作为一批独立子任务按配置并发执行。若后续多个因子在相同数据上重复读盘，先测量，再评估同轴复用和共享读取，不在首版建设复杂分布式计算框架。

### 9.2 统计指标的明确规则

下面是**FactorPool 首版研究配置**，不是宣称所有 BRAIN 设置都已经对齐。若研究目标与 BRAIN 某个指标完全可比，后续需按目标版本的定义和相同数据验证。

| 指标 | 计算与边界 |
|---|---|
| 未来收益 | `退出价格 / 入场价格 - 1`。入场价为 0、缺失、未到未来日期或其他无效情形，不产生有效收益。价格及复权口径必须记录。 |
| IC | 某一天在同一批股票上，用成对有效的因子值和未来收益算 Pearson 相关。例子配置至少 3 对，样本不足或相关分母为 0，显示无效，不填 0。 |
| Rank IC | 对同一批成对有效样本分别排序，再算 Pearson；本项目研究设置需记录并列排名方法，不能与 BRAIN 因子算子 `rank` 自动混为一谈。 |
| 平均 IC | 对有效的逐日 IC 求均值；无效日不按 0 计入。 |
| ICIR | `平均 IC / IC 的时间序列标准差`；记录 `ddof`、有效日数和是否年化。标准差为 0 或天数不足时为无效。 |
| 分组 | 当期按有效因子值从小到大分为 G1～Gn，记录组数、各组成员数、并列和不足分组数的处理；组数和分组方法随任务保存。 |
| 组收益 | 研究示例采用每组有效股票等权，收益是组内未来收益平均；真实不可交易、成本等是否考虑必须单独说明。 |
| 累计收益 | 对非重叠持有期按所选约定累计；如果是多期重叠持有，需要先定义组合构造，不能直接把重叠区间收益当独立每日收益相乘。 |
| 高低组差 | 可展示高因子组收益减低因子组收益，清楚标注为收益差；不能直接冒充真实账户的多空收益。 |

例如 IC 序列 `[0.1, 0.2, 0.3]`，均值 `0.2`；按样本标准差 `ddof=1` 时标准差 `0.1`，**未年化 ICIR = 2**。只有一个有效 IC 时，`ddof=1` 下 ICIR 不能计算，应显示 `--`。

### 9.3 批量任务

第一版完成单因子闭环后，应支持在因子库中勾选多个因子，使用一组共同的数据和研究参数提交：

1. 每个因子生成自己的不可变源码、参数和任务快照。
2. Java 以一条批量记录关联多个子任务；按并发上限执行，某个因子失败不冒充整批成功。
3. 右侧和历史页展示每个因子的状态、IC / ICIR / 分组摘要；单个失败可查看原因并单独重跑。
4. 初版不要求每个因子都同时运行；默认并发为 1 也可以连续完成批量任务。
5. 后续再根据实测决定是否在 C++ 层一遍读取推进多个因子；性能优化必须保持各因子输入、时点和结果一致。

对应可以补 `POST /api/tasks/batch` 和 `GET /api/batches/{id}`，仍属于本项目拟新增接口。

## 10. PostgreSQL：最小表结构

| 表 | 主要字段 | 用途 |
|---|---|---|
| `factors` | `id`、名称、说明、创建 / 更新时间 | 管理逻辑因子和检索。 |
| `factor_versions` | `id`、`factor_id`、源码、类名、源码 hash、因子参数、创建时间 | 保存可复现的因子版本；任务引用版本或保留独立源码快照。 |
| `research_tasks` | `id`、可选 `batch_id`、因子版本、源码 / 配置快照、状态、进度、执行模式、错误、时间、构建与数据版本 | 记录每次运行及其结果来源。 |
| `research_results` | `task_id`、汇总指标、各期 IC / 分组序列或对应结果位置、状态 | 展示一份任务的研究结果和可用明细。 |
| `factor_values` | `task_id`、`time`、`symbol`、`value`、`valid`、`ready`、原因 | 需要保存明细时，按时间和标的查询因子值。可按规模分区并建立合适索引。 |
| `research_batches` | `id`、公共配置快照、批次状态、创建时间 | 关联一组批量子任务；实现批量提交时增加。 |

第一版可以在同一个 PostgreSQL 实例内完成。任务写入按批执行；大结果避免逐条 HTTP 请求和逐条数据库事务。任务成功状态应在必要结果入库完成后发布；失败、取消或入库不完整不能显示成成功。

**数据量提醒：**5000 只股票 × 250 个交易日 × 400 个因子约为 **5 亿个因子值 / 年**。因此因子值是否全量保存、分区、索引和保留策略应通过真实数据量测试决定；页面默认读取摘要和分页明细，不一次把全部数据传到浏览器。Redis 可以以后用于热点缓存，但不是唯一数据源或第一版强制依赖。

### 10.1 研究结果怎样交给 Java

推荐首版的简单实现：研究进程在该任务目录输出**结果清单、汇总和分块明细文件**；Java 读取清单和汇总，并使用批量导入写 PostgreSQL。不要让 C++ 每算出一个因子值就通过 HTTP 调 Java 一次。日志、错误、任务状态与结果属于同一个 `task_id`。 批量明细可评估使用 [PostgreSQL COPY FROM STDIN](https://www.postgresql.org/docs/current/sql-copy.html)，由 Java 客户端发送数据，按实际吞吐测量；不要求数据库服务器直接读取研究进程所在机器的文件。

- 汇总足够小，可以随一次结果请求返回；大明细按批导入、分页读取。
- 临时结果与正式结果区分；写入失败标 `FAILED`，不发布半成品为成功。
- 如果之后测得文件中转成为瓶颈，可再考虑原生数据库批量写入等办法；是否有收益以实测为准。

## 11. 一个可手工核对的完整案例

### 11.1 研究设置

| 项目 | 样例设置 |
|---|---|
| 因子 | 第 6 节的 `LowVolumeFactor`，20 期均量取负后做截面排名。 |
| 股票池 | A、B、C 三只；示例当期都在池中且数据有效。 |
| 时点 | T 日收盘后形成因子，T+1 开盘到 T+2 开盘计算未来收益。 |
| 分组 | 3 组，按因子从低到高 G1、G2、G3，每组等权。 |
| 特别说明 | 以下 rank 按此前记录的无并列 0～1 端点示例手算；目标参数和实际 BRAIN 行为仍需复核。 |

### 11.2 输入与输出

| 股票 | 已有的 20 期成交量均值 | 取负 | 当日因子值 | T+1 开盘 | T+2 开盘 | 未来收益 | 因子组 |
|---|---:|---:|---:|---:|---:|---:|---|
| A | 10 | -10 | 1 | 10 | 11 | 10% | G3 |
| B | 20 | -20 | 0.5 | 20 | 20 | 0% | G2 |
| C | 30 | -30 | 0 | 30 | 27 | -10% | G1 |

由这一天的数据可以手算：

- 因子 `[1, 0.5, 0]` 与未来收益 `[0.1, 0, -0.1]` 完全正相关，因此 **IC = 1、Rank IC = 1**。
- G1、G2、G3 的组收益分别为 **-10%、0%、10%**；高组减低组为 **20 个百分点**，这是研究收益差。
- 只有一天 IC，按上面的样本标准差口径不能计算 ICIR，右侧应显示 `--`，不能显示 0。
- 若 C 缺未来开盘价，则 C 的未来收益和该样本配对无效；此例要求至少 3 对有效样本，因此 IC 显示 `--`。
- 若某股票的因子为有效 0，仍可参与排名与分组；无 Bar、未就绪或 NaN 才按已确认的指标合同处理。

### 11.3 页面应如何显示

| 页面位置 | 本案例显示 |
|---|---|
| 左侧代码 | `LowVolumeFactor` 的完整 Python 源码。 |
| 左侧配置 | 日线、股票池 A/B/C、20 期、3 组、T+1 开盘入场、T+2 开盘退出。 |
| 右侧摘要 | IC `1.0000`、Rank IC `1.0000`、ICIR `--`、有效股票 `3`。 |
| 右侧分组 | G1 `-10%`、G2 `0%`、G3 `10%`、高低组收益差 `20%`。 |
| 右侧明细 | A/B/C 的因子值、未来收益、分组、有效性。 |
| 右侧日志 | 准备、读取、计算、分析、入库的真实阶段和耗时；没采集到的时间显示未知。 |

## 12. 开发顺序：先做通，再扩展

### P0：从现有 FT 里找准可复用边界

- [ ] 固定当前 FT、LEAN、Python SDK、C++ Core 和 Data Mod 版本，确认本机真实数据和构建环境。
- [ ] 找到 `PythonIndicator`、编译器、`sys_indicator`、Runtime、数据源的实际依赖；明确最小研究调用入口。
- [ ] 用一只股票 / 少量标的跑通现有 FT 指标能力；说明哪些代码是现有、哪些需要新增。
- [ ] 对 `.ts`、`.cs`、Bar 输入和截面同步做一个最小共享组件原型；不能独立运行就先通过所需现有 FT SDK / Host 接通。

### P1：跑通单因子研究核心

- [ ] 新建独立 FactorPool 工程的 C++ 研究层和 Python 运行适配；先使用真实数据源和小样本。
- [ ] 从源码加载一个 `PythonIndicator`，按时间推进，输出因子值、状态及错误。
- [ ] 补最小未来收益、IC、Rank IC、ICIR、分组计算；第 11 节样例能手工对上。
- [ ] 对齐 FT 与 FactorPool 的因子值和有效状态；BRAIN / LEAN 兼容状态分别登记。

### P2：Java 后台与 PostgreSQL

- [ ] Spring Boot 配置数据源、最大并发、任务进程、超时和 PostgreSQL。
- [ ] 因子保存 / 版本、任务提交 / 状态 / 日志 / 停止、结果入库 / 查询，形成一个完整链路。
- [ ] 写入失败、数据源不可用和执行失败能在页面 API 中看见；后台重启后的历史仍可查询。
- [ ] 完成批量提交和关联子任务；不要求一开始多机器运行。

### P3：Vue 3 工作台

- [ ] 参考 FT 的 CodeMirror 编辑器、`SplitPane`、日志和图表组织，完成左代码右结果。
- [ ] 接通配置、提交、进度、结果、因子库和测试记录；刷新页面能重新打开已保存结果。
- [ ] 无登录页面和账号系统；处理空结果、失败、取消、缺失和长表格分页。

### P4：按算子台账补齐并实测

- [ ] 按 Python → LEAN → 已确认 BRAIN 能力的顺序补指标，维护每项输入、参数、边界和实现状态。
- [ ] 保留 LEAN 原生专属能力；未确认来源或权限不足的算子继续标记待核实。
- [ ] 测量单因子及批量耗时：准备、预热、读数、因子计算、统计、入库分别记录；优化实际瓶颈。

### 12.1 本地启动交付要求

完成代码后，项目 `README.md` 要给出可以复制的实际命令和工作目录，至少覆盖以下顺序；当前项目还没有这些实现，所以这里不编造现成命令。

| 顺序 | 实操内容 | 完成标志 |
|---:|---|---|
| 1 | 准备 FT 对应版本的 Python / C++ 组件、构建工具、真实数据源和 PostgreSQL。 | 可确认组件版本、数据路径与数据库可连接。 |
| 2 | 构建共享 Indicator 的实际依赖与 FactorPool 研究核心，安装 / 指定 Python 运行环境。 | 运行入口能加载同版本组件，并报告构建或加载错误。 |
| 3 | 初始化 PostgreSQL 表，配置 Spring 的数据源、结果目录和资源上限，启动后端。 | `/api/data-sources` 和因子列表等实际实现的健康接口可用。 |
| 4 | 启动 / 构建 Vue 前端，连接同一 Spring 后端。 | 打开页面直接进入左侧代码、右侧结果的工作台。 |
| 5 | 导入项目提供的最小因子与小样本数据，运行并查看结果。 | 因子值、IC、分组、日志和历史记录可核对。 |

Python 研究执行入口在提交任务时由 Spring 拉起；C++ 研究核心及共享指标组件随该执行路径加载。首版无需把 Python、C++ 各自做成常驻独立 Web 服务。文档应列出真实需要的组件、端口、路径和停机方法，不能把设计中的命令写成已经可用。

## 13. 验收标准

### 13.1 用户实际操作

1. 打开 FactorPool 页面，不登录即可看到左侧因子代码和右侧结果区域。
2. 打开已有因子或粘贴一个 `PythonIndicator`，配置数据与研究参数。
3. 点击运行，页面看到任务状态和日志，不需要等待整次运行结束才能知道是否出错。
4. 运行成功后，右侧看到 IC、Rank IC、ICIR、分组收益、图表与明细；没有足够数据的指标显示 `--`。
5. 关闭页面再打开历史，能够看到本次源码版本、配置和相同结果；修改代码后重跑生成新任务。
6. 在因子库选择多个因子可批量提交，观察每个子任务的状态和结果。

### 13.2 数值与架构

- [ ] 第 11 节手算样例通过：IC = 1、Rank IC = 1、3 组收益为 -10% / 0% / 10%，单日 ICIR 无效。
- [ ] 同一因子、参数、证券、数据和时间口径下，FactorTrader 与 FactorPool 的数值、日期、标的、有效性和就绪状态对齐；不支持的路径报出原因。
- [ ] 数据窗口、预热、真实 Bar、无 Bar、NaN、有效 0、动态池和截面同步均有最小可重放案例。
- [ ] Java 不在逐 Bar 热路径做数值计算；指标数值实现只有 FT / Pool 共同使用的一份。
- [ ] Python 回调模式和原生执行模式可识别；不支持的 `.cs` 不降级为逐股伪截面。
- [ ] 同时记录实际源码 / 构建版本、因子源码、参数、数据源与快照、研究配置；不会把历史任务结果与当前草稿混淆。
- [ ] 不把完成文档、现有同名接口、源码构建或单个手算样例，当成全部算子 / BRAIN 兼容已经完成。

## 14. 给开发 AI 的执行要求

请先阅读本文件、[既有 Indicator 统一规范](https://chatgpt.com/library?file_id=libfile_e497535a11588191968e4943bd2775d3&account_id=personal)、[FTServer 指标合同](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/engine-core-cpp/docs/INDICATOR-ENGINE.md)和 FTServer / FactorPool 当前仓库约束；再次检查仓库最新状态。

开发目标为：**FT 结构与界面风格 + 极简 C++ 因子研究核心 + 共享 Indicator + Spring Boot 后台资源 / 任务管理 + Vue 3 左代码右结果 + PostgreSQL + 无登录。**

先做 P0 技术验证，找出真正可复用的 FT SDK / Core / Data Mod，形成能跑的最小数据链；然后按 P1～P4 完成。遇到具体组件暂时无法脱离 FT Runtime 时，应保留最小必要依赖并明确后续精简方式，不得另写一套同名不同口径的指标。完成后分别报告**已开发、实际运行验证、未实现和待核验**。
