# FactorPool 项目需求与 Indicator 统一规范

**文档类型：**独立项目需求、接口设计与开发验收任务  
**文档日期：**2026-11-06  
**适用项目：**FactorPool、FactorTrader / FTServer 的 Indicator 公共能力  
**文档状态：**需求与目标方案；标记“拟新增”的内容不能当成已经实现。

---

## 1. 一句话目标

建立一个独立的 **FactorPool 量化因子管理与研究项目**：沿用 FactorTrader 的 `PythonIndicator`、一个 `update(self, input)`、`self.ts` 和 `self.cs` 表达因子；同一份因子代码可以在 FactorTrader 和 FactorPool 中使用，在相同输入及相同口径下得到一致的因子值。

FactorTrader 负责策略和交易回测；FactorPool 负责因子注册、批量计算、入库、查询、IC / ICIR、分组分析和研究结果管理。因子计算应尽量复用 FactorTrader 现有的 C++ 指标数值内核和编译执行能力。

> **核心约束：一份因子定义、一套明确的数值合同、一份可识别版本的核心计算实现；两个项目分别承担交易和因子研究。**

## 1.1 算子来源优先级：Python → LEAN → WorldQuant

这是本项目必须遵守的**实现选择顺序**，用于每个算子和每个因子示例：

| 顺序 | 来源 | 怎样选择 |
|---|---|---|
| ① | Python 自带运算、内置函数和标准库 | 对输入类型、作用维度及数值语义已经满足要求的，优先沿用 Python 写法；例如 `+`、`-`、`abs()`、`min()`、`max()`、`math.log()` |
| ② | 固定版本的 LEAN 原生指标 / 指标组合 | Python 自带能力不能直接完成目标合同的，先查固定 LEAN；例如 SMA、Sum、StandardDeviation、Minimum、Maximum、Delay、Correlation、Covariance |
| ③ | WorldQuant BRAIN 补充能力 | Python 和 LEAN 经核对都不能完整覆盖的部分，再按 BRAIN 目标文档补充；例如截面/分组及其特殊边界、BRAIN 特有信号算子 |

**逐算子执行顺序：**先定输入与作用维度、公式和边界 → 检查 Python 内置/标准库能否完整表达 → 检查固定 LEAN 类和 FT 是否已有等价实现 → 只为剩余差异补 BRAIN 兼容算子 → 两个项目使用同一实现与验收样本。若 LEAN 有名称相似但预热、精度、缺失、参数或窗口不同的能力，先复用可复用部分，并把差异作为明确扩展，不能直接宣称等价。

**Python 自带是优先的表达方式与语义来源，不代表每只股票都要逐个调用 Python。**能安全编译的 `abs`、数值表达式及标准数学函数，应由现有编译器识别并下沉到同一 C++ 指标核，保持对应 Python 语义和因子有效性合同；未支持编译的合法普通 Python 在允许的单股路径中明确回调。不能将 `abs(input.close)` 改造成必须写 `self.ts.abs()`，也不能为了速度改变语义。

普通 `sum()`、`statistics.mean()` 作用于实际可迭代对象；`input.close` 是当前一个数字，并非历史窗口。对于滚动均值这类“持续维护单股窗口”的能力，应优先参考 LEAN 的 SMA / RollingWindow 和 FT 现有 `self.ts.mean`，再用原生增量核实现；不能把 `sum(input.close)` 当成有效时序接口。相似地，对全市场股票做 `rank` 需要真实截面轴和同步，不能拿单个数字调用 Python 排序冒充截面计算。

**LEAN 原生支持 ≠ FT 当前已实现。**例如固定上游有 `Correlation`、`Covariance` 和 `LinearWeightedMovingAverage`，但本次没有看到它们从 FT `self.ts` 全链路暴露；需要核验语义、移植/复用并连接，完成双项目验证后才可标已支持。`self.ts`、`self.cs` 是 FT 扩展，并非 LEAN 原生接口；普通 LEAN 指标仍按现有兼容合同保留。LEAN、BRAIN 和 Python 的 NaN/Inf 行为也不能预设为一样。

下面是选择实例，括号内均是**来源候选**，不是已经全量实现的声明：

| 功能 | 优先方案 | 应用时要核对什么 |
|---|---|---|
| 单值绝对值 | Python `abs(x)` | 缺失、非数和编译支持 |
| 单值对数/平方根 | Python 标准库 `math.log(x)` / `math.sqrt(x)` | 定义域、异常/无效值以及 C++ 与 Python 数值语义 |
| 一个向量的求和 | Python `sum(vector)` | 是真实向量，不是标量行情；NaN 和空向量 |
| 20 期均值 | LEAN `SimpleMovingAverage`；FT 前端 `self.ts.mean(x, 20)` | 预热、窗口有效样本、空缺和时间轴 |
| 20 期滚动和 | LEAN `Sum`；FT 前端 `self.ts.sum(x, 20)` | 边界和缺失规则 |
| 20 期标准差 | LEAN `StandardDeviation`；FT 前端 `self.ts.std(x, 20)` | LEAN 总体标准差、FT `ddof`、BRAIN 口径 |
| 20 期最小/最大 | LEAN `Minimum` / `Maximum`；FT 对应 `self.ts.min/max` | 并列、窗口和状态 |
| 滞后 / 变化率 | LEAN `Delay`、`Momentum`、`RateOfChange` 等 | 周期、预热、相对收益是否乘 100 |
| 两序列相关 / 协方差 | 固定 LEAN `Correlation` / `Covariance` | 双输入对齐、样本数和缺失；FT 前端目前需补 |
| 线性加权均值 | 固定 LEAN `LinearWeightedMovingAverage` | 与 BRAIN `dense`、窗口和权重的差异；FT 前端目前需补 |
| 全市场截面 / 行业分组排名 | 先核查固定 LEAN；缺失的截面和分组语义补 FT/BRAIN 扩展 | 轴、同步、缺失、并列和归一化，不能称作 LEAN 原生 API |
| BRAIN `trade_when` / `hump` | 先核查上两层的真实能力，再实现缺口 | 状态、阈值和退出 NaN，不能混成真实订单 |

参考：[Python 内置函数](https://docs.python.org/3/library/functions.html)、[Python math](https://docs.python.org/3/library/math.html)、[LEAN 指标列表](https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/supported-indicators)、[固定 LEAN 源码 Correlation](https://github.com/QuantConnect/Lean/blob/f49bb9623a3e8782bf22bbd2cb51f691058cfdc3/Indicators/Correlation.cs)、[Covariance](https://github.com/QuantConnect/Lean/blob/f49bb9623a3e8782bf22bbd2cb51f691058cfdc3/Indicators/Covariance.cs)。

## 2. 当前情况：已经确认什么

截至本规范形成时检查到的源码：

| 对象 | 事实 | 本规范的判断 |
|---|---|---|
| FactorPool | [FactorMatrix/FactorPool](https://github.com/FactorMatrix/FactorPool) 的 `main` 在 `74526d68a4b1f067f22fef0e7cac91c1047a541d`；初始提交只有 Apache 2.0 许可证 | 这是新项目；因子管理、研究 API、数据库、页面和服务均不能写成已实现 |
| FactorTrader | [FTServer@47aa87e](https://github.com/FactorMatrix/FTServer/commit/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c) | 本次检查的是该源码版本，不等于线上已经部署 |
| 指标类 | 已导出 `PythonIndicator`、`BaseData`、`TradeBar` | FactorPool 的共同因子前端以该类和真实输入对象为基准 |
| `self.ts` | 现有前端包含 `mean/sum/min/max/std/delay/ema/rsi/roc/pct_change/atr/is_ready` | 仅说明源码有入口，不说明与 BRAIN 的全部边界语义已相同 |
| `self.cs` | 现有前端包含 `rank/where/bottomk/scatter/count_nonzero` | `zscore` 虽有底层数组算子，尚未从 `self.cs.zscore` 打通 |
| 运行时 | FT 将数字计算、指标状态和截面算法放在 `sys_indicator`，SDK 负责编译/绑定 | FactorPool 能否直接脱离交易宿主使用现有组件，需要先验证并补齐；不能假装现成 |
| 数据 | FT 行情通过 DataSource / DataProxy / 数据 Mod 提供；`extra` 等部分数据能力尚不完整 | 行业、财务、新闻向量等没有合格 Provider 时明确报告缺失，不造数据 |

依据：[Indicator 当前合同](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/engine-core-cpp/docs/INDICATOR-ENGINE.md)、[Python 域入口](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/engine-sdk/language/python/src/factortrader/indicator/domains.py)、[Python 公开导出](https://github.com/FactorMatrix/FTServer/blob/47aa87eced06e63bd8ac74f96b8bf84d07f1f02c/engine-sdk/language/python/src/factortrader/__init__.py)。

## 3. 两个项目的职责

| 能力 | FactorTrader | FactorPool | 共同要求 |
|---|---|---|---|
| 因子写法 | 策略中注册同一个 Indicator 类 | 注册、读取和批量计算同一个类 | 因子源码和 `update` 不为项目分别改写 |
| TS / CS 数值计算 | 现有 `sys_indicator` / SDK | 调用同版本指标组件 | 公式、数据时间、有效性、排序等一致 |
| 数据提供 | 交易系统使用自己的 DataSource / Mod | 允许独立配置数据源 | 通过同一数据合同对齐；相同测试输入才比较结果 |
| 因子研究 | 提供策略消费因子结果 | 计算因子值、IC、ICIR、分组等 | 因子值与研究指标分开 |
| 因子存储 | 可读取计算结果，但不强制依赖 FactorPool | 管理定义、版本、任务、结果 | 记录可复现信息 |
| 交易 | 账户、订单、撮合、费用、成交及 Trader | 因子分组研究可独立分析；如需事件驱动交易，调用 FactorTrader | 不在 FactorPool 复制一套交易内核 |

### 3.1 核心组件怎么复用

**目标方案：**FactorPool 通过正式组件接口依赖同版本的 FactorTrader Indicator 类、编译器、C++ `sys_indicator` 数值核及必要绑定。FactorPool 提供研究场景需要的输入适配、时间轴与结果输出；不再独立抄一套数学算子。

必须先检查现有组件与 Runtime、Provider、任务租约及构建系统的耦合，形成可行的打包/嵌入方案。现有源码并未证明指标核已经能以轻量独立库直接安装使用。若需要拆出可复用组件，须保持现有 Core/Mod/SDK 的职责和行为，提出可审查的方案并按既有架构边界处理；不复制第二套指标实现、Runner、Runtime 或交易语义。

- 两边部署相同 `factortrader` 公共类身份和同版本 C++ 指标组件；可通过包版本、构建指纹与 ABI 校验。
- 可以为研究模式补一个**薄适配入口**，读取日线或分钟数据并驱动已有指标服务；不伪造交易订单或账户。
- FactorPool 不能在 C++ 内核缺失时静默使用不同 pandas 算法，仍声称两个项目同口径。
- 普通 LEAN 风格单股 Python 指标可保留明确的 Python 回调路径；有 `self.cs` 且不能正确原生准备或没有正确全轴实现时，准备阶段明确报错。
- 历史行情和缓存属于实际 Data Provider / Data Mod；指标层保存有界派生状态，不持有第二份无界全市场行情。

## 4. 因子类的统一合同

### 4.1 因子作者只写一个 `update`

1. 继承同一个 `factortrader.PythonIndicator`。
2. `update(self, input)` 接收一次真实数据输入；`input.close`、`input.volume` 等是对应时点的真实字段。
3. `self.ts` 表达单标的多期；`self.cs` 表达同一时间点截面；组内计算归 `self.cs.group_*`。
4. 用 `self.value` 输出单因子值，用返回值和依赖的有效性确定本次结果是否就绪。
5. 同一个类可以注册单标的、明确列表、动态股票池；作者不编写 `update_batch`、股票同步器或缓存管理。
6. FT 保留已有指标生命周期和注册/预热接口；FactorPool 只是增加独立的研究入口，不修改这个类的主体。
7. 两边的因子文件、参数、频率、数据快照和语义版本一致时，逐时间、逐股票对齐值与有效状态。

### 4.2 输入对象和时间

- `BaseData / IndicatorDataPoint` 的 `value/time/end_time/symbol` 与 FT 现行合同一致；`TradeBar` 有 `open/high/low/close/volume/period`，其 `value` 对应收盘价。
- 点数据的 `value` 是调用方提供的真实值，不强制解释为 `close`；一个数字不是带 `.rolling()` 的伪序列。
- 一只股票当天缺 Bar 就标记缺失；真实零成交量是有效零，不等同缺 Bar，也不能单凭缺 Bar 认定停牌。
- 行业、市值、财报、新闻、资金流等来自各自真实 Provider；必须有字段类型、可见时间、复权与缺失规则。行业不是 LEAN TradeBar 固有字段。
- 财报用公告/实际可用时间，不能把报告期结束日当成已知时间；截面和动态池也必须使用历史时点实际可见成员。
- `update`、自动注册、预热和手动输入要使用同口径完成时间，分钟、日线、时区、交易日历及结束时间明确记录。

### 4.3 状态与有效性

因子值至少区分：**有效数值、有效零、未就绪、源数据缺失、主动过滤、计算错误**。`return True` 不能复活无效依赖；出错或未就绪不能写成有效 0，更不能复用上一日权重下单。

- 时序窗口状态按**任务 × 因子实例 × 节点 × 标的**隔离；新股和重新进入股票池的状态按实际合同建立。
- 截面节点以同一任务、周期、时间、股票轴、代次封口；缺 Bar / 不属于当期股票池的项要完成缺失结算，不无限等待。
- 因子输出、持仓权重和交易指令是不同的数据；一个因子缺失不等于清仓。
- 空截面、全部无效、同值、NaN、Inf、除零、重复/迟到数据、股票轴变化均需要可观察且一致的处理。

## 5. 每个因子可以选择的实现方式

每个因子作为一个**逻辑因子**管理，可有一个或多个明确版本的实现。选择实现方式不能改变目标公式；只有在同数据、同口径、同有效性对账通过后，才可以标记为等价实现。

| 方式 | 因子作者写什么 | 运行方式 | 适用情况 |
|---|---|---|---|
| A. 推荐：原生 Indicator | `PythonIndicator` + `self.ts / self.cs` | 注册时编译，调用现有 C++ 指标核；是否可用取决于实际支持的算子子集 | 批量、高频、动态池及截面因子 |
| B. 普通 Python Indicator | 同样继承 `PythonIndicator`，在 `update` 写普通 Python；如 `deque` | 真实 Python 回调，按当前实现规则逐标的执行 | 自定义单股逻辑、验证和复杂算法 |
| C. 自定义原生实现 | 用共同 C++ 扩展和绑定实现新算子；用户侧仍用统一 Indicator 前端 | 同一内核内的原生算子 | 高频热路径、已有内置算子不足 |
| D. 表达式 / 外部结果 | 可作为未来可选来源；表达式转换或文件导入都需各自合同 | 未实现前明确标记为拟新增 | 批量迁移已有因子；不能默认与 A/B/C 零差异 |

**重要：**`self.ts/self.cs` 不能执行任意 Python；包含截面但无法安全编译时，不能简单逐股回调伪装成正确截面。B 方式可以是同一指标合同下的真实 Python 公式，但不自动获得原生速度；普通 Python 中出现数值运算或第三方库也不等于已经被编译。

## 6. 实现模板与具体例子

以下代码是项目需要提供和验证的模板。`factortrader` 的类和部分方法来自现有源码；FactorPool 入口是**拟新增设计**，当前仓库不可直接运行。

### 模板 A、B：同一逻辑因子的两种实现

```python
# factors/sma20.py —— 例子：两种写法表达相同的 20 期收盘价均值
from collections import deque
from factortrader import PythonIndicator


class CloseMeanNative(PythonIndicator):
    """A：使用现有 self.ts 前端，目标走 C++ 原生计算。"""
    def __init__(self, period=20):
        super().__init__()
        self.period = period

    def update(self, input):
        mean = self.ts.mean(input.close, self.period)
        self.value = mean
        return self.ts.is_ready(mean)


class CloseMeanPython(PythonIndicator):
    """B：普通 Python 单股回调；不声称它被原生加速。"""
    def __init__(self, period=20):
        super().__init__()
        self.period = period
        self.warm_up_period = period
        self.values = deque(maxlen=period)

    def update(self, input):
        self.values.append(input.close)
        self.value = sum(self.values) / len(self.values)
        return len(self.values) == self.period

    def reset(self):
        self.values.clear()
        super().reset()


class VolumeRankFactor(PythonIndicator):
    """C++ 原生目标：多期 → 截面 → 多期 → 截面。"""
    def __init__(self, volume_period=20, rank_period=5):
        super().__init__()
        self.volume_period = volume_period
        self.rank_period = rank_period

    def update(self, input):
        volume_mean = self.ts.mean(input.volume, self.volume_period)
        first_rank = self.cs.rank(volume_mean, pct=True)
        rank_mean = self.ts.mean(first_rank, self.rank_period)
        self.value = self.cs.rank(rank_mean, pct=True)
        return self.ts.is_ready(rank_mean)
```

**对账样本：**当 `period=3`、有效收盘价依次为 `1、2、3` 时，两种均值实现首次就绪值都应为 `2`。窗口未满时，即使 Python 类的临时 `self.value` 有数值，也不能作为有效因子输出。对缺 Bar、重复数据、预热、重置和零值还必须进一步验证，不能仅凭这一个样本宣称两种实现全等价。

`VolumeRankFactor` 现有源码文档已给出同形的 TS → CS → TS → CS 用法；理想完整数据下其预热需求为 `20 + 5 - 1 = 24` 期，实际缺失时以就绪状态为准。

### 模板 C：FactorTrader 使用相同因子类

```python
# strategy.py —— FactorTrader 侧示意
config = {
    "base": {
        "start_date": "2022-01-01", "end_date": "2026-01-01",
        "frequency": "1d", "accounts": {"stock": 10_000_000},
    },
    "mod": {"data_mmap": {"enabled": True, "priority": 0}},
}

from factortrader import Strategy, Resolution
from factors.sma20 import VolumeRankFactor


class FactorStrategy(Strategy):
    def init(self):
        self.stocks = self.api.universe(type="CS", dynamic=True)
        self.factor = VolumeRankFactor(volume_period=20, rank_period=5)
        self.register_indicator(self.stocks, self.factor, Resolution.DAILY)
        self.warm_up_indicator(self.stocks, self.factor, Resolution.DAILY)

    def before_trading(self): pass
    def open_auction(self, bar_dict): pass
    def handle_bar(self, bar_dict): pass
    def after_trading(self): pass


if __name__ == "__main__":
    FactorStrategy.run(config)
```

策略实际使用因子时，读取当前时点已发布的指标快照；此模板只演示注册，不额外加入交易或固定仓位逻辑。

### 模板 D：FactorPool 使用同一个因子类

```python
# research.py —— FactorPool 侧“拟新增 API 示意”，目前不可直接运行
from factorpool import FactorPool
from factors.sma20 import VolumeRankFactor

pool = FactorPool.from_config("factorpool.toml")
pool.register("volume_rank", VolumeRankFactor, version="1.0.0")
result = pool.calculate(
    "volume_rank", params={"volume_period": 20, "rank_period": 5},
    start="2022-01-01", end="2026-01-01", universe="all_a", frequency="1d",
)
analysis = pool.analyze(
    result, methods=["ic", "rank_ic", "icir", "group"],
    forward_return={"entry": "next_open", "exit": "following_open"},
)
```

`FactorPool.from_config/register/calculate/analyze` 都是**供讨论的目标接口**；最终命名可以调整。示例的因子类源码和 FT 相同，但两边运行环境、数据源、时间轴和结果交付不同。

## 7. 因子结果与复现合同

### 7.1 输出一张有明确状态的因子结果

逻辑结果至少包含：

| 字段 | 含义 |
|---|---|
| `factor_id`、`factor_version` | 逻辑因子与版本 |
| `timestamp`、`trading_date` | 数据完成时间、交易日，必要时包含时区 |
| `symbol` | 股票或其他资产标识 |
| `value` | 因子数值；不可用时允许空值 |
| `valid / ready / reason` | 有效、是否就绪、缺失或过滤原因；具体存储编码由实现确定 |
| `universe_id`、`snapshot_id` | 本次计算所用股票池和数据版本 |
| `engine_version`、`operator_contract_version` | 实际组件、绑定和算子语义版本 |

最终展示格式可以是 DataFrame、文件或数据库批量结果，内部 C++ 不应每个标的都转换一次 Python 对象。

### 7.2 要求两个项目一致的条件

必须固定并记录因子源码 hash、参数、输入字段、数据快照、标的/时间轴、频率、复权、日历时区、股票池、预热、窗口口径、过滤/有效位、算子语义版本、C++ 构建指纹。上述任一不同，先报告差异，不能直接说两个项目计算错误或完全一致。

浮点结果使用明确的绝对/相对容差；`valid/ready`、日期、标的、排序和 NaN 位置必须严格一致。目标是因子值一致，不要求 FactorPool 的 IC 结果等于 FactorTrader 的策略收益。

### 7.3 与 BRAIN 的关系

FactorPool 与 FactorTrader **首先互相对齐**；新增算子再对照 BRAIN 的公开文档。不能将项目现有的 FT 口径、Alpha101 论文口径、BRAIN 普通 Alpha 口径和 SuperAlpha 口径默认混为一套。

- 当前 FT `self.cs.rank(pct=True)` 使用平均并列名次除以有效数，BRAIN 的公开示例使用 0～1 端点范围。例：不并列的三个有效值，FT 可得到 `1/3、2/3、1`，BRAIN 示例式尺度为 `0、0.5、1`。
- 当前 FT 指标时序窗口按真实有效输入推进，缺 Bar 不推进。BRAIN 的日窗口、缺失值和最少有效数需要逐算子核对，不能简单宣称等同。
- 不为了宣称兼容 BRAIN 而静默更改现有 FT 默认值。对确实需要不同语义的算子，设计明确的算子参数或清楚区分的名称；同一因子版本必须固定该选择，两边执行相同选择。
- BRAIN 文档之间个别描述存在矛盾：例如 `group_mean` 的均值类型，以及 `multiply(filter=true)` 的缺失值替换说明。必须向当前官方定义或可复现实测核对，未核验时标为“待确认”，不能猜测。
- 世坤 `ts_ir` 是时间序列均值 / 标准差，不等于因子研究中的 `ICIR`。

---
## 8. BRAIN 算子开发清单与算例

### 8.1 如何使用本清单

- 本节是**目标能力目录，不是已完成列表**。除第 2 节列出的 FT 前端入口外，其余映射均是拟新增，不能据此宣传现已支持。
- `x/y` 表示某股票在当前时点的数值，`d` 为窗口，`g` 为真实分组，`v` 为单股票的向量字段。Python 写法是设计建议，最终名称与参数必须形成统一公共合同。
- 每个算子必须有：名称、签名、输入类型、输出形状、公式、参数默认值、边界、缺失规则、就绪、最少一条正常算例、一条异常/边界算例、来源优先判断（Python → 固定 LEAN → WorldQuant 补充）、BRAIN 对照与 FT 实现状态。表格中的例子是最低要求，不替代完整测试。
- 在 Python → LEAN → WorldQuant 的来源顺序内，可选择 C++ 原生算子、受支持表达式组合或参考算法实现，但所有方式必须逐项对账。纯 Python 参考计算可以作为测试基准，不得被标为 C++ 加速。
- 标注“官方待核验”的项目必须先核对目标版本的官方定义和权限；本文件不把网上资料或推测公式冒充事实。
- BRAIN 普通 Alpha 文档：[Operators](https://platform.worldquantbrain.com/learn/operators)；SuperAlpha 文档：[SuperAlpha Operators](https://platform.worldquantbrain.com/learn/documentation/superalpha/appendix-operators)。公开快照与账号等级有差异，不能仅据此保证覆盖世坤未来新增的所有算子。

### 8.2 基础数学与逻辑

| BRAIN 算子 | 因子中的建议表达 | 最小算例 / 必须验证 |
|---|---|---|
| `add` | `x + y` | `2 + 3 = 5`；`filter` 缺失值规则 |
| `subtract` | `x - y` | `2 - 3 = -1`；多输入方向 |
| `multiply` | `x * y` | `2 × 3 = 6`；`filter=true` 的 NaN 处理有文档冲突，待核验 |
| `divide` | `x / y` | `6 / 3 = 2`；除零、NaN |
| `abs` | `abs(x)` | `abs(-3) = 3` |
| `log` | `log(x)` | `log(1) = 0`；零及负数 |
| `sqrt` | `sqrt(x)` | `sqrt(9) = 3`；负数 |
| `power` | `power(x, y)` / 幂表达式 | `2² = 4`；负底数和小数指数 |
| `signed_power` | `signed_power(x, y)` | `signed_power(-3, 2) = -9` |
| `sign` | `sign(x)` | `sign(-3) = -1`；零、NaN |
| `reverse` | `-x` | `-(-3) = 3` |
| `inverse` | `1 / x` | `inverse(2) = 0.5`；零 |
| `min` | `min(x, y)` | `min(2, 3) = 2`；逐值而非时序最小 |
| `max` | `max(x, y)` | `max(2, 3) = 3`；逐值而非时序最大 |
| `and` | `&` / 合同内逻辑与 | 真与假为假；值及有效位分离 |
| `or` | `\|` / 合同内逻辑或 | 真或假为真 |
| `not` | `~` / 合同内逻辑非 | 对布尔值取反；数值位运算不能混淆 |
| `equal`、`not_equal` | `==`、`!=` | 相同与不同，NaN 比较合同 |
| `greater`、`greater_than_equal` | `>`、`>=` | `2 > 1`；相等边界 |
| `less`、`less_than_equal` | `<`、`<=` | `1 < 2`；相等边界 |
| `if_else` | 条件选择 | `if_else(False, 1, 0) = 0`；与 `where` False 变缺失不同 |
| `is_nan` | `is_nan(x)` | 区分缺失和有效零 |
| `densify` | `self.cs.densify(group)` | 分组 `[0, 2, 99]` 映射为 3 个稠密编号；编号顺序核验 |
| `pasteurize` | 逐值/股票池过滤 | 股票池外和非有限值如何处理须固定 |
| `arc_cos`、`arc_sin`、`arc_tan` | 纯函数 | 输入定义域、非有限数和边界 |
| `exp`、`s_log_1p` | 纯函数 | 零、负数、溢出；`s_log_1p` 公式核验 |
| `sigmoid`、`tanh` | 纯函数 | `sigmoid(0)=0.5`、`tanh(0)=0`；溢出 |
| `ceiling`、`floor`、`round`、`round_down`、`frac` | 纯函数 | 正负半数、舍入模式、小数部分公式 |
| `clamp` | 纯函数 | `clamp(5, 0, 3)=3`；边界含义 |
| `negate` | 一元负号 | `negate(2)=-2`；与 `reverse` 对照 |
| `is_finite`、`is_not_finite`、`is_not_nan` | 纯函数 | NaN、正负 Inf、正常零 |
| `nan_mask`、`nan_out`、`to_nan` | 纯函数 | 值转换与原始有效位；具体参数核验 |
| `replace`、`purify`、`filter` | 纯函数 / 过滤节点 | 保轴、缺失传播，官方签名核验 |
| `left_tail`、`right_tail`、`tail` | 尾部处理节点 | 哪一侧、边界是否包含、替换还是截断，官方定义核验 |
| `one_side`、`truncate`、`unit` | 逐值或截面节点 | 实际作用域和参数由官方定义确认 |

`math`、内置函数和条件表达式只是用户可读的目标写法；编译器需显式识别已支持的调用，不能认为所有外部函数自动原生化。

### 8.3 时序算子：`self.ts`

正常样本使用一只股票的 `x=[1,2,3]`，按从旧到新排序；当前值为 `3`。以下数值只有在窗口完整且输入有效的前提下成立。

| BRAIN 算子 | 建议 FT 写法 | 最小算例 / 边界 |
|---|---|---|
| `ts_mean` | `self.ts.mean(x, 3)` | 均值 `2`；FT 前端已有 |
| `ts_sum` | `self.ts.sum(x, 3)` | 和 `6`；FT 前端已有 |
| `ts_min` | `self.ts.min(x, 3)` | 最小值 `1`；FT 前端已有 |
| `ts_max` | `self.ts.max(x, 3)` | 最大值 `3`；FT 前端已有 |
| `ts_std_dev` | `self.ts.std(x, 3)` | `ddof=0` 时约 `0.81649658`；FT 前端已有，世坤 ddof 需核验 |
| `ts_delay` | `self.ts.delay(x, 1)` | 当前时点前一个有效输入为 `2`；缺失时间轴口径须对照 |
| `ts_delta` | `self.ts.delta(x, 1)` | `3 - 2 = 1` |
| `ts_returns` | `self.ts.returns(x, 1)` | 相对变化示意 `3/2-1=0.5`；`mode` 需核验 |
| `ts_av_diff` | `self.ts.av_diff(x, 3)` | `3 - 2 = 1`；NaN 均值规则 |
| `ts_product` | `self.ts.product(x, 3)` | `1×2×3 = 6` |
| `ts_median` | `self.ts.median(x, 3)` | 中位数 `2`；偶数窗口规则 |
| `ts_min_diff` | `self.ts.min_diff(x, 3)` | `3 - 1 = 2` |
| `ts_max_diff` | `self.ts.max_diff(x, 3)` | `3 - 3 = 0` |
| `ts_arg_min` | `self.ts.arg_min(x, 3)` | 最小值在 2 期前，结果 `2`；并列规则待核验 |
| `ts_arg_max` | `self.ts.arg_max(x, 3)` | 最大值在当前，结果 `0`；并列规则待核验 |
| `ts_rank` | `self.ts.rank(x, 3)` | 当前值是窗口最大；排名尺度、并列核验 |
| `ts_quantile` | `self.ts.quantile(x, 3, driver=...)` | 先时序排名再映射分布；不是直接返回分位点 |
| `ts_percentage` | `self.ts.percentage(x, 3, ...)` | 给定比例的时序处理；具体口径核验 |
| `ts_zscore` | `self.ts.zscore(x, 3)` | `(3-2)/std`；标准差为零时规则 |
| `ts_scale` | `self.ts.scale(x, 3)` | 当前最大，按 min-max 缩放时为 `1`；范围为零 |
| `ts_ir` | `self.ts.ir(x, 3)` | 窗口均值除以窗口标准差；分母为零 |
| `ts_skewness` | `self.ts.skewness(x, 3)` | 对称序列、样本量与偏差修正 |
| `ts_kurtosis` | `self.ts.kurtosis(x, 3)` | 样本量、超额峰度与偏差修正 |
| `ts_moment` | `self.ts.moment(x, 3, k=...)` | 阶数和是否中心矩须核验 |
| `ts_entropy` | `self.ts.entropy(x, 3)` | 分箱、对数底及空窗口必须明确 |
| `ts_corr` | `self.ts.corr(x, y, 3)` | 完全正相关序列应为 `1`；常量/缺失 |
| `ts_covariance` | `self.ts.covariance(x, y, 3)` | 成对缺失、样本数和 ddof |
| `ts_regression` | `self.ts.regression(y, x, 20, ...)` | 截距、斜率、残差、lag 和 rettype |
| `ts_poly_regression` | `self.ts.poly_regression(y, x, 20, ...)` | 阶数、残差和秩不足的定义 |
| `ts_theilsen` | `self.ts.theilsen(y, x, 20)` | 回归方向、重复 x 和最少样本 |
| `ts_partial_corr`、`ts_triple_corr` | `self.ts.partial_corr(...)` 等 | 输入顺序、公式和缺失规则核验 |
| `ts_co_skewness`、`ts_co_kurtosis` | 对应 `self.ts` | 两序列输入顺序、归一化核验 |
| `ts_decay_linear` | `self.ts.decay_linear(x, 3)` | 最近权重高时 `[1,2,3] → (1+4+9)/6=7/3`；dense/NaN |
| `ts_decay_exp_window` | `self.ts.decay_exp_window(x, 20, ...)` | 指数权重、截断、是否归一化 |
| `ts_weighted_decay` | `self.ts.weighted_decay(x, ...)` | 权重位置、参数和初值 |
| `ts_backfill` | `self.ts.backfill(x, 20)` | 向过去找最近有效值；不得找未来 |
| `kth_element` | `self.ts.kth_element(x, 20, k=1)` | 窗口内第 k 个有效值，`ignore` 定义 |
| `ts_count_nans` | `self.ts.count_nans(x, 20)` | 缺失数量；缺 Bar 是否计入窗口须明确 |
| `last_diff_value` | `self.ts.last_diff_value(x, 20)` | 查最近一次与当前值不同的历史值 |
| `days_from_last_change` | `self.ts.days_from_last_change(x)` | 数值未变时计数；缺失与时间轴定义 |
| `ts_step` | `self.ts.step()` | 随时点递增；任务起点与重置 |
| `ts_min_max_cps`、`ts_min_max_diff` | 对应 `self.ts` | 最小/最大值与参数公式，官方核验 |
| `ts_rank_gmean_amean_diff` | 对应 `self.ts` | 几何均值/算术均值、负值和零 |
| `ts_vector_neut`、`ts_vector_proj` | 对应 `self.ts` | 窗口向量投影、中性化与缺失 |
| `hump`、`hump_decay`、`jump_decay` | 对应 `self.ts` | 历史状态、阈值和初始条件 |
| `keep` | `self.ts.keep(...)` | 保持条件和持续期；是否与交易持仓解耦 |
| `trade_when` | `self.ts.trade_when(trigger, value, exit)` | exit 优先，满足时输出 NaN；否则触发更新或保留前值 |
| `ts_target_tvr_decay`、`ts_target_tvr_hump` | 对应 `self.ts` | 换手率所需输入、统计窗口和参数范围；核验官方 |

已有 FT 技术指标 `ema/rsi/roc/pct_change/atr` 要保留现有合同；若它们与 BRAIN 中名称相似的算子参数或窗口规则不同，必须明确区分并验证。

### 8.4 截面与分组算子：`self.cs`

正常样本：同一时点 A、B、C 三只股票的 `x=[3,1,2]`，均有效。组样本：A/B 属于 G1，C 属于 G2。股票轴和组值都要与时点一致。

| BRAIN 算子 | 建议 FT 写法 | 最小算例 / 边界 |
|---|---|---|
| `rank` | `self.cs.rank(x, ...)` | FT 当前 `pct=True` 为 `[1,1/3,2/3]`；WQ 端点尺度与并列另核验 |
| `zscore` | `self.cs.zscore(x)` | `[3,1,2]` 在总体标准差口径下约 `[1.2247,-1.2247,0]`；常量截面 |
| `normalize` | `self.cs.normalize(x, ...)` | 默认去均值 `[1,-1,0]`；是否除标准差及 limit |
| `scale` | `self.cs.scale(x, ...)` | `[3,1,2]` 缩到绝对和 1 为 `[0.5,1/6,1/3]`；多空分别缩放 |
| `scale_down` | `self.cs.scale_down(x, ...)` | 常见 min-max：`[1,0,0.5]`；常量截面核验 |
| `quantile` | `self.cs.quantile(x, driver=...)` | 排名后变换到指定分布，不是取 q 分位点 |
| `winsorize` | `self.cs.winsorize(x, ...)` | 超阈值截断；标准差、中心、缺失规则 |
| `bucket` | `self.cs.bucket(x, ...)` | 连续值映射为分组；区间闭开和缺失分组 |
| `group_cartesian_product` | `self.cs.group_cartesian_product(g1, g2)` | 行业 × 国家生成唯一联合组 |
| `group_rank` | `self.cs.group_rank(x, g)` | A/B 在 G1 内排名，C 单独在 G2；单组单值规则核验 |
| `group_zscore` | `self.cs.group_zscore(x, g)` | 每组各自标准化；单元素组和常量组 |
| `group_neutralize` | `self.cs.group_neutralize(x, g)` | G1 均值 2，A/B 分别为 `1、-1`；G2 为 0 |
| `group_mean` | `self.cs.group_mean(x, weight, g)` | 均值和权重语义有资料冲突；用 `[10,15,20]` 区分算术 15 与调和约 13.8462 |
| `group_sum` | `self.cs.group_sum(x, g)` | G1 的 A/B 均得到 `4` |
| `group_count` | `self.cs.group_count(x, g)` | 统计组内有效 x；NaN 不得自动计有效 |
| `group_std_dev` | `self.cs.group_std_dev(x, g)` | 每组的标准差；ddof、单值组 |
| `group_min`、`group_max` | `self.cs.group_min/max(x, g)` | G1 最小 1、最大 3 |
| `group_median` | `self.cs.group_median(x, g)` | G1 的 `[1,3]` 中位数通常为 2；目标定义核验 |
| `group_scale`、`group_normalize` | 对应 `self.cs` | 分组缩放、归一化及常量组 |
| `group_percentage` | `self.cs.group_percentage(x, g, ...)` | 百分位位置、插值及边界核验 |
| `group_backfill` | `self.cs.group_backfill(x, g, d, ...)` | 组内历史有效值 + 去极值；只看过去 |
| `group_vector_neut`、`group_vector_proj` | 对应 `self.cs` | 各组分别中性化/投影，组缺失、零向量 |
| `group_extra` | `self.cs.group_extra(...)` | 输入/公式/权限未核验，先补正式合同 |
| `regression_neut` | `self.cs.regression_neut(y, x)` | 截面回归残差；是否含截距与缺失样本 |
| `regression_proj` | `self.cs.regression_proj(y, x)` | 截面拟合部分；是否含截距 |
| `vector_neut` | `self.cs.vector_neut(x, y)` | 从 x 中去掉 y 方向；零向量和归一化 |
| `vector_proj` | `self.cs.vector_proj(x, y)` | x 在 y 上投影；零向量 |
| `rank_by_side` | `self.cs.rank_by_side(x, ...)` | 正负两侧如何排序、零如何处理 |
| `rank_gmean_amean_diff` | `self.cs.rank_gmean_amean_diff(...)` | 输入先排名还是先求均值，官方核验 |

FT 现有 `where`、`bottomk`、`scatter`、`count_nonzero` 继续保留：`where(False, x)` 是过滤后缺失而不是有效零；`bottomk` 返回原轴位置；`scatter` 回填原轴；`count_nonzero` 只统计有效、有限、就绪的非零值。

### 8.5 单字段向量与 SuperAlpha 多维归约

`v=[1,2,3]` 是一个标的一个时点的数据向量，**不是过去 3 天，也不是 3 只股票**。向量函数只对真实向量字段生效，若 Provider 没有该类型必须报缺能力。

| BRAIN 算子 | 最小算例 / 验收点 |
|---|---|
| `vec_avg` | `mean([1,2,3])=2`；NaN 和空向量 |
| `vec_sum` | `sum([1,2,3])=6`；NaN 和空向量 |
| `vec_count` | 元素数量；是否计 NaN 以目标版本为准 |
| `vec_min`、`vec_max` | `1`、`3`；NaN 和空向量 |
| `vec_range` | `3-1=2` |
| `vec_stddev` | ddof、NaN、样本不足 |
| `vec_ir` | 均值 / 标准差；零分母 |
| `vec_skewness`、`vec_kurtosis` | 偏度/峰度及偏差修正 |
| `vec_percentage` | 位置、插值及边界 |
| `vec_powersum` | 幂的定义、次数、溢出 |
| `vec_choose` | 索引从 0/1 开始、越界和 NaN |
| `vec_norm` | 范数公式和参数；不能凭名字推断 L1/L2 |
| `reduce_avg`、`reduce_sum` | 多维数组最后一维平均、求和；有效数阈值 |
| `reduce_count` | 按官方定义的阈值计数；不是泛化地只数非空 |
| `reduce_min`、`reduce_max`、`reduce_range` | 最后一维最小、最大、极差；全缺失 |
| `reduce_stddev`、`reduce_ir` | 最后一维标准差、IR；最少有效比例 |
| `reduce_skewness`、`reduce_kurtosis` | 高阶统计，样本不足 |
| `reduce_percentage` | 分位点、插值、全缺失 |
| `reduce_choose` | 元素索引、忽略 NaN、越界 |
| `reduce_powersum`、`reduce_norm` | 幂和 / 绝对值和等公式及参数核验 |
| `self_corr` | 多个因子历史两两相关，轴维度和 lookback 明确 |
| `generate_stats` | 因子历史统计输入、可见时间和输出维度明确 |
| `combo_weight`、`convert`、`count`、`universe_size` | SuperAlpha 对象与上下文；不冒充股票 Bar 字段 |
| `inst_pnl`、`inst_tvr`、`position_safeguard` | 真实结果/账户事实由所属服务提供，数值核只消费符合时间可见性的输入 |

### 8.6 算子实现状态的准确表示

每个算子记录以下状态，禁止只有一个“支持/不支持”笼统结论：

| 状态维度 | 示例 |
|---|---|
| 前端 | 有无 `self.ts` / `self.cs` 或纯函数入口 |
| 编译 | 单股、截面、多次交替是否支持；不支持时错误位置 |
| 原生数值 | C++ 内核是否存在，函数/类及构建归属 |
| 数据 | 行情、行业、财务、向量或 Alpha 统计 Provider 是否真实存在 |
| 世坤对照 | 算子是否在目标版本、参数及详细语义是否已确认 |
| 两端接线 | FT 自动注册、FactorPool 独立研究入口是否使用同一组件版本 |
| 验收 | 正常、边界、缺失、预热、跨项目输出、性能是否运行并通过 |

## 9. 因子分析的计算规则

本节是 FactorPool 的**研究分析**合同。所有默认值、频率、起止时间和价格必须被记录；平台不能在因子作者不知情时改变方向或交易假设。

### 9.1 数据准备和收益时间

每个分析任务显式确定：

1. 因子值的完成时点、可用时点、研究标的与动态股票池。
2. 收益入场和出场时点、交易日历、价格字段、复权/公司行为、费用和不可交易数据的处理。
3. 缺少因子、收益价格无效、退市、停牌等情况时的有效样本规则。
4. `h` 期收益对应的入场、出场和重叠窗口；不允许把“未来收益”输入当期因子。

**时间示例：**因子在 T 日收盘后计算，`entry=next_open, exit=following_open` 表示 T+1 开盘进入、T+2 开盘退出，收益为 `open[T+2] / open[T+1] - 1`；这是研究任务的一种显式选择，不是所有策略的固定撮合规则。若使用收盘到收盘的统计收益，也必须注明它能否在该时点实际交易。

### 9.2 IC、Rank IC 与 ICIR

| 指标 | 合同 |
|---|---|
| IC | 同一个截面内，对成对有效的“当期因子值”和“按约定计算的未来收益”计算 Pearson 相关 |
| Rank IC | 对同一有效样本分别排名，再计算 Pearson；即 Spearman 思路。并列排名规则必须固定 |
| 平均 IC | 逐日有效 IC 的算术平均；无效日期不能填成 0 |
| ICIR | `平均 IC / IC 的时间序列标准差`；说明 ddof、频率、窗口与有效日期数 |
| 年化 ICIR | 如提供，明确年化因子和计算方法；重叠持有期会造成序列相关，不能无条件宣称简单平方根年化准确 |

默认建议：至少 3 对有效样本才计算截面 IC；相关系数分母为 0、样本不足或未来价格无效时返回无效，**不填 0**。这是本项目建议合同，实际参数可配置并随任务记录；不能当作 BRAIN 的默认设置。

**精确算例：**T 日 A/B/C 的因子是 `[1, 2, 3]`；T+1 开盘价为 `[10,20,30]`，T+2 开盘价为 `[11,20,27]`。不计费用的开盘到开盘收益分别为 `[0.10, 0,-0.10]`。当日 `IC = -1`、`Rank IC = -1`。这是研究统计值，不构成真实可成交保证。

### 9.3 分组测试

- 在每个因子时点，仅将满足约定的数据有效股票纳入分组；指定组数、排序方向、并列处理和不足组数行为。
- 每只股票的标签、组数、组内权重、调仓间隔、收益计算区间必须可追溯；一个分组不等于一笔真实交易。
- 默认示例可以展示**组内等权**，每组有效股票的目标权重为 `1 / 组内有效数量`；无法计算组则该组结果无效，不能用上期结果冒充。
- 输出每组样本数、当期收益、累计收益、覆盖率；如提供多空收益，必须明确最高组减最低组、多空资本规模、费用和限制。
- 涨跌停、停牌、T+1、实际委托、手续费和滑点只有接入真实交易语义时才能称交易回测；因子分组研究可先给无费用的理论结果，但必须清楚标记。

上述 A/B/C 例子如果按 3 组、从低到高分别为 G1/G2/G3，则对应理论组收益是 `10%、0%、-10%`；未计费用、滑点或成交限制。

### 9.4 对齐和独立参考的最小测试矩阵

| 编号 | 输入及公式 | 期望 | 必须比较的实现 |
|---|---|---|---|
| F01 | 完整 `[1,2,3]`，3 期均值 | 末期为 `2`、前两期未就绪 | FT Native、Pool Native、Python 参考 |
| F02 | `[1,2,3,4]` 中第 2 期缺 Bar，3 个有效输入均值 | 第 4 期为 `(1+3+4)/3=8/3`，缺 Bar 期间不推进 | FT 当前有效输入口径、Pool 相同口径；另单独测试目标 BRAIN 口径 |
| F03 | 20 期均值 → 截面 rank → 5 期均值 | 理想完整输入第 24 期开始具备窗口条件 | FT Native、Pool Native、独立参考；使用历史股票池 |
| F04 | 同截面 `[3,1,2]`，FT rank pct | `[1,1/3,2/3]` | 两端排序、有效位、symbol 顺序 |
| F05 | 两组 `x=[3,1,2]`，组 `[G1,G1,G2]` | 组中性化 `[1,-1,0]`；接口拟新增 | C++ 核、独立参考、两端接线 |
| F06 | `trade_when` 上期 4、触发假、退出假 | 保留 `4`；退出条件真时输出 NaN | 未来 C++、正确状态参考；不解释为清仓 |
| F07 | 有效零 / NaN / 未就绪并存 | 零仍有效，另两者分别不可用 | 两端 value、valid、ready、reason |
| F08 | T 日因子 `[1,2,3]`，下期开盘收益 `[0.1,0,-0.1]` | IC 和 Rank IC 均为 `-1` | 独立研究参考与 FactorPool 分析结果 |
| F09 | 相同数值、不同代码轴顺序 | 按 symbol 对齐后的结果一致；并列遵守合同 | FT Native、Pool Native |
| F10 | 财报实际公告前后及行业历史变化 | 公告前不可见；历史行业不使用今日分类 | Provider、FT 与 Pool 结果 |
| F11 | 同一因子 A 原生、B 普通 Python 实现 | 在可等价范围内值、时间、有效位一致 | 两端同实现及独立参考；明确记录模式 |
| F12 | 动态股票池新入、移出、再入及空截面 | 不串状态、不发布旧结果；不无限等待 | FT Native、Pool Native |

每个算子都应在对应登记项下增加：**正常样本、空窗口、全缺失、部分缺失、有效零、常量、并列、除零/Inf、期初、跨日、重置与重放**。双输入需额外检查时间对齐；组合算子要验证多次截面封口和累计预热需求。不得只做同名接口测试。

## 10. 因子管理与任务流程

| 步骤 | 必需能力 | 结果 |
|---|---|---|
| 1. 定义 | 选择统一 `PythonIndicator` 因子类及参数；标明实现 A/B/C | 产生可版本化的逻辑定义 |
| 2. 注册 | 名称、分类、说明、作者、源码 hash、依赖及版本 | 可搜索、可重复使用 |
| 3. 准备 | 检查代码、因子类型、所需算子/数据、组件版本、执行模式 | 不支持及时报错，不能静默降级 |
| 4. 计算 | 指定资产池、数据快照、区间、周期、预热和复权 | 按时间/标的输出因子及有效状态 |
| 5. 研究 | 选择 IC、Rank IC、ICIR、分组及明确的未来收益时点 | 可重现的研究结果 |
| 6. 入库 | 批量写入定义、任务、因子值和研究结果 | 可查询、对比和后续复用 |
| 7. 更新 | 新数据到达后只计算受影响且可增量部分 | 在语义/数据版本变化时正确失效或重算 |
| 8. 比较 | 同因子不同参数、实现或版本对比 | 区分语义变更与性能变更 |

一因子可有多个实现，记录 `implementation_id`、源码、语言、模式、能力依赖、版本和对账状态。只有对账通过的实现能被标记为“同一公式的等价实现”；用户可以选择，但系统必须显示实际执行方式。

## 11. PostgreSQL、Redis 与数据边界

**这是目标技术方案，现有 FactorPool 尚未实现。**

| 组件 | 建议职责 | 边界 |
|---|---|---|
| PostgreSQL | 因子信息/版本、任务、计算结果索引、研究结果；适合的数据量下也可持久化因子值 | 一次批量写入，按真实查询做分区与索引设计；不把它当指标执行内核 |
| Redis | 可选的短期热点缓存、任务进度或协调状态 | 不是唯一权威数据源；缓存丢失可恢复；不用 Redis 保存不可重建的唯一正式结果 |
| 输入 Provider | 可适配 FactorTrader 现有数据 Mod，也可对接 PostgreSQL 等其他真实数据源 | 同时明确快照、字段、交易日历、复权、权限和有效位，不把某一种储存强加给所有资产 |
| Indicator 组件 | Python 前端、编译、C++ 状态和计算 | 与 FactorTrader 共享实现和版本；不把原始行情缓存搬进指标层 |

例：5000 只股票 × 每年约 250 个交易日 × 400 个日频因子约为 **5 亿个因子值/年**。存入何种列式文件、数据库分区、压缩和保留周期应依据实际规模和查询测试决定；不能保证“SQL + Redis 一定够快”，也不要求一开始建设不需要的分布式系统。

## 12. 建议的独立项目目录

下表是**建议结构，不表示文件已经存在**。项目目录、模块名可以在实现时结合仓库现状调整。

| 目录 / 文件 | 职责 |
|---|---|
| `FactorPool/README.md` | 项目介绍、安装、最小案例和运行方式 |
| `FactorPool/pyproject.toml` | Python 项目、版本、依赖与构建声明 |
| `FactorPool/src/factorpool/` | 对外 FactorPool API 与命令入口 |
| `src/factorpool/catalog/` | 因子定义、版本、实现方式、发现与元信息 |
| `src/factorpool/indicator_adapter/` | 与共享 FactorTrader Indicator 组件的薄适配，不另写算法 |
| `src/factorpool/datasource/` | 研究数据输入、时间轴和 Provider 适配 |
| `src/factorpool/compute/` | 批量任务、计算调度、结果状态和增量更新 |
| `src/factorpool/analysis/` | IC、Rank IC、ICIR、分组和统计结果 |
| `src/factorpool/storage/` | PostgreSQL 持久化、批量写入、可选 Redis 缓存 |
| `examples/factors/` | `PythonIndicator` 类示例；同一文件可被两个项目导入 |
| `examples/research/` | 因子注册、计算、分析的调用案例 |
| `tests/parity/` | 与 FT 共享数值/有效位/预热/时间对账 |
| `tests/operators/` | 每个算子的正常、边界和缺失样本 |
| `tests/analysis/` | IC、Rank IC、分组等可手工核对样本 |
| `docs/` | 用户指南、算子合同、数据合同及实际支持状态 |

FactorTrader 既有 `sys_indicator` 和 SDK 源码是数值执行的现有位置；FactorPool 中的 `indicator_adapter` 只能做适配，不能复制一份以“保持独立”为由自行维护的数值核。若当前打包结构阻碍复用，先给出实际依赖与组件化方案并验证。

## 13. 必做清单与验收

### P0：先建立共同合同和组件边界

- [ ] 确认并记录 FactorTrader、LEAN 固定上游、Python 版本、构建工具、Provider 与组件指纹。
- [ ] 将第 1.1 节 **Python 自带 → 固定 LEAN → WorldQuant 补充** 写入贡献/设计规则；每个新增算子登记来源、对照与缺口。
- [ ] 审计 FT 指标组件与 Engine/Runtime/Provider 的真实耦合，给出 FactorPool 调用方式；当前不能用的能力标缺口。
- [ ] 同一 `PythonIndicator` 类、真实 input、`self.ts/self.cs`、输出及数据有效性合同；不增加 `update_batch` 或 `self.ops`。
- [ ] 通过 F01、F02、F03、F04、F07、F09、F11、F12 等跨项目对账；同时记录准备、实际运行和未覆盖范围。

### P1：建立独立因子研究工具

- [ ] 因子类注册、参数、源码/组件版本、执行模式、任务与结果查询。
- [ ] 统一 Data Provider 合同，先接入实际可用的行情数据及动态股票池；缺行业/财务等如实提示。
- [ ] 计算因子值、IC、Rank IC、ICIR 和分组；通过第 9 节可手工对账的例子。
- [ ] PostgreSQL 批量入库及查询；Redis 作为可选缓存；不依赖缓存作为唯一真相。
- [ ] 批量任务、结果版本、增量范围和错误报告；明确源码准备时间与计算时间。

### P2：补齐普通 BRAIN Alpha 算子

- [ ] 第 8.2～8.5 节的普通 Alpha 算子逐项登记；能组合的复用 Python / LEAN / 既有 FT 内核。
- [ ] 补足 `.ts`、`.cs`、`.cs.group_*`、向量字段等缺口，并覆盖自动注册、显式预热、动态池和跨项目。
- [ ] 每项标明 BRAIN 来源、目标版本、签名、例子、状态及疑点；有冲突先核验，不以名称相同冒充语义对齐。

### P3：高级与 SuperAlpha 扩展

- [ ] 补齐高阶统计、矩阵归约、多 Alpha 相关、因子统计和组合输入合同。
- [ ] 明确 Selection / Combo 与普通证券因子作用对象；所需结果、账户等由对应服务提供。
- [ ] 用同一研究样本、数值内核和快照对齐两端；超出可见历史、数据缺能力时明确报错。

### 性能与完成标准

1. 受支持算子在准备阶段编译，核心计算走同一 C++ `sys_indicator`；性能结果需要实际测量，不能承诺 Python 必然与 C++ 一样快。
2. 比较 FT 与 Pool 时固定同一数据、公式、窗口、结果明细、组件版本、资源预算；分别记录准备、预热、计算、分析和写入时间。
3. 区分冷启动、热系统文件缓存和热进程；观察耗时、峰值内存、实际读取/缓存命中及 Python 回调数。
4. 性能优化只做通用路径，不对固定因子名、volume、股票池、日期或窗口硬编码；不通过减少工作量、隐藏准备时间或变更公式制造速度。
5. 交付报告分开列出：代码已实现、验证已通过、Git 状态、实际部署、缺口与未测试项目。文档或同名函数不能代替验收。

## 14. 给后续开发 AI 的执行要求

先阅读本文件、两个仓库当前 `AGENTS.md` 与 FT 当前 Indicator 合同；再次核对最新 HEAD 与工作区，不能把本文的 2026-11-06 快照当成永久状态。

按 Python → LEAN → WorldQuant 的顺序逐算子查证；梳理现有实现、来源、差异、数据依赖和测试，再补齐共享内核及各端调用。先保证同一因子代码和同一数值结果可复现，再扩展算子与研究能力。只给已经完成且实际验收的项目打勾；对来源不明、数据缺失、无法嵌入、未覆盖的情况留下明确错误和清单。

**不要另造一个名字相同、行为不同的 `PythonIndicator`；不要复制一套 pandas 算法宣称与 FactorTrader 一致；不要因为 BRAIN 有同名算子就覆盖 Python 或 LEAN 的正确现有行为。**
