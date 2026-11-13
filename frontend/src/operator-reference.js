// Examples describe FactorPool's current local contract, not an official BRAIN oracle.
const input = { name: "x", description: "输入表达式。数学算子接受有限标量或同轴向量；时序算子逐股计算，截面算子使用同一日的证券轴。" };
const second = { name: "y", description: "第二个输入表达式；可为有限常数，也可为与 x 同轴的向量。" };
const period = { name: "period", description: "窗口或滞后期数，可写为字面量或构造参数。旧常规TS入口保持有效观测兼容路径；新增扩展默认使用交易日槽，具体见该项说明。" };
const degrees = { name: "ddof", description: "标准差的自由度修正，默认为 0；有效观测模式要求整数且 0 ≤ ddof < period。" };
const labels = { name: "g", description: "与 x 同日、同证券轴的有限数值分组表达式，可来自实际分类或可用字段的分桶。当前基础行情没有行业分类字段，不可冒充行业数据。" };
const observed = "NaN、Inf 或不可用的输入不会被当成真实零；下例的 null 表示结果不可用。";
const clock = "默认使用有效观测窗口；可显式传 clock=\"trading_days\" 使用交易日槽。缺失会占据日历槽而不压缩时间；统计窗口检查完整窗口，delay/delta 则取指定槽。";
const temporal = "时序示例只展示已得到完整窗口时的当前输出，输入按从旧到新的顺序排列；窗口完成与结果有效应分别检查。";
const grouped = "分组示例中的 g 是数学演示标签，不是内置行业字段。真实研究须提供同轴标签，不能编造 input.industry。";
const entry = (title, category, signature, parameters, expression, values, output, notes = []) => ({
  title, category, signature, parameters,
  example: { expression, input: values, output },
  notes,
});
const arithmetic = (title, signature, expression, values, output, notes = []) =>
  entry(title, "basic", signature, [input, second], expression, values, output, [observed, ...notes]);
const unary = (title, signature, expression, values, output, notes = []) =>
  entry(title, "basic", signature, [input], expression, values, output, [observed, ...notes]);
const window = (title, method, output, notes = [], extra = []) =>
  entry(title, "ts", `self.ts.${method}(x, period${extra.length ? ", ddof=0" : ""})`, [input, period, ...extra],
    `self.ts.${method}(x, 3)`, "最近三个有效观测 x = [1, 2, 3]，当前值为 3", output, [temporal, ...notes]);
const cross = (title, method, values, output, notes = []) =>
  entry(title, "cs", `self.cs.${method}(x)`, [input], `self.cs.${method}(x)`, values, output, [
    "输入和输出均按同一天的原证券顺序列出；只用该截面的有效、有限样本计算。", ...notes,
  ]);
const group = (title, method, output, notes = []) =>
  entry(title, "group", `self.cs.${method}(x, g)`, [input, labels], `self.cs.${method}(x, g)`,
    "同日 x = [1, 3, 10, 14]；g = [0, 0, 1, 1]", output, [grouped,
      "值或标签缺失的证券不参加组内统计，其输出仍不可用；输出广播回各组原证券位置。", ...notes]);

export const operatorReferenceConventions = [
  "x、y、g 是表达式占位名。数组用于说明证券轴或时间序列上的数据；vec_avg/vec_sum 的 tuple/list 则表示同一证券的多个分量表达式，不是整条股票轴。",
  "表达式写在 update(input) 中，把结果赋给 self.value；时序窗口通常配合 self.ts.is_ready(value) 返回就绪状态。",
  "小例子用于说明项目数值合同。没有已接通实现的条目，不提供假定能运行的调用或结果。",
];

export const operatorReference = {
  add: arithmetic("加法", "x + y", "x + 2", "x = [1, 3]；y = 2", "[3, 5]"),
  subtract: arithmetic("减法", "x - y", "x - 2", "x = [1, 3]；y = 2", "[-1, 1]"),
  multiply: arithmetic("乘法", "x * y", "x * 2", "x = [1, 3]；y = 2", "[2, 6]"),
  divide: arithmetic("除法", "x / y", "x / y", "x = [6, 0, 2]；y = [3, 0, 0]", "[2, null, null]", ["分母为零、非有限商或溢出时不可用；0/0 不会变成零。"]),
  abs: unary("绝对值", "abs(x)", "abs(x)", "x = [-2, 0, 3]", "[2, 0, 3]"),
  log: unary("自然对数", "self.cs.log(x)", "self.cs.log(x)", "x = [1, e, 0]，其中 e 为自然常数", "[0, 1, null]", ["定义域是有限 x > 0；也支持已正确导入的 math.log(x)。"]),
  sqrt: unary("平方根", "self.cs.sqrt(x)", "self.cs.sqrt(x)", "x = [0, 4, -1]", "[0, 2, null]", ["定义域是有限 x ≥ 0；负数平方根不可用。也支持 math.sqrt(x)。"]),
  power: entry("实数幂", "basic", "self.cs.power(x, y) / x**y / pow(x, y)", [input, { name: "y", description: "有限实数指数；负底数只接受整数指数。" }],
    "self.cs.power(x, y)", "x = [-2, -2, 0]；y = [2, 0.5, 0]", "[4, null, 1]",
    ["0**0 为 1；零的负次幂、负底数的非整数次幂、溢出或非有限结果不可用。", "也支持 math.pow(x, y)；不是复数幂。"]),
  signed_power: entry("保留符号的幂", "basic", "self.cs.signed_power(x, y)", [input, second],
    "self.cs.signed_power(x, 2)", "x = [-2, 0, 2]；y = 2", "[-4, 0, 4]",
    ["按 sign(x) × abs(x)**y 计算；零的非负次幂为 0，因此 signed_power(0, 0) 与普通 0**0 不同。", "零的负次幂或非有限结果不可用。"]),
  sign: unary("符号", "self.cs.sign(x)", "self.cs.sign(x)", "x = [-5, 0, 2]", "[-1, 0, 1]"),
  reverse: unary("取相反数", "-x", "-x", "x = [-2, 0, 3]", "[2, 0, -3]", ["与 negate 是同一取负操作的台账别名。"]),
  inverse: unary("倒数", "1 / x", "1 / x", "x = [2, -4, 0]", "[0.5, -0.25, null]", ["零输入不可用。"]),
  min: arithmetic("逐元素较小值", "min(x, y, ...)", "min(x, 2)", "x = [1, 3]；y = 2", "[1, 2]", ["至少两个参数；这是同一位置不同表达式的比较，不是时序窗口最小值。"]),
  max: arithmetic("逐元素较大值", "max(x, y, ...)", "max(x, 2)", "x = [1, 3]；y = 2", "[2, 3]", ["至少两个参数；时序最大值请使用 self.ts.max。"]),
  and: arithmetic("逻辑与", "(condition_a) & (condition_b)", "(x > 0) & (y > 0)", "x = [-1, 1, 1]；y = [1, -1, 2]", "[0, 0, 1]", ["向量条件使用 & 并给两侧条件加括号，不使用 Python 的 and。缺失条件严格传播，不按短路逻辑补值。"]),
  or: arithmetic("逻辑或", "(condition_a) | (condition_b)", "(x > 0) | (y > 0)", "x = [-1, 1, -1]；y = [1, -1, -1]", "[1, 1, 0]", ["向量条件使用 | 并给两侧条件加括号，不使用 Python 的 or；缺失条件保持不可用。"]),
  not: unary("逻辑非", "~(condition)", "~(x > 0)", "x = [-1, 0, 2]", "[1, 1, 0]", ["对向量条件使用 ~，不是 Python 的 not。"]),
  equal: arithmetic("相等比较", "x == y", "x == 2", "x = [1, 2, 3]；y = 2", "[0, 1, 0]"),
  not_equal: arithmetic("不等比较", "x != y", "x != 2", "x = [1, 2, 3]；y = 2", "[1, 0, 1]"),
  greater: arithmetic("大于比较", "x > y", "x > 2", "x = [1, 2, 3]；y = 2", "[0, 0, 1]"),
  greater_than_equal: arithmetic("大于等于比较", "x >= y", "x >= 2", "x = [1, 2, 3]；y = 2", "[0, 1, 1]"),
  less: arithmetic("小于比较", "x < y", "x < 2", "x = [1, 2, 3]；y = 2", "[1, 0, 0]"),
  less_than_equal: arithmetic("小于等于比较", "x <= y", "x <= 2", "x = [1, 2, 3]；y = 2", "[1, 1, 0]"),
  if_else: entry("条件选择", "basic", "self.cs.if_else(condition, yes, no)", [
    { name: "condition", description: "有限非零条件选 yes，有限零条件选 no；可以是同轴条件向量。" },
    { name: "yes", description: "条件成立时使用的表达式。" },
    { name: "no", description: "条件不成立时使用的表达式。" },
  ], "self.cs.if_else(x > 0, self.cs.sqrt(x), 0)", "x = [4, 0, -1]", "[2, 0, 0]", [
    "仅要求选中的分支有效并准备好；未选分支的负数平方根不污染结果。条件本身缺失时仍不可用。",
  ]),
  is_nan: unary("检测 NaN", "self.cs.is_nan(x)", "self.cs.is_nan(x)", "已观测的 x = [NaN, 1, Inf]", "[1, 0, null]", [
    "已观测 NaN 返回 1，有限值返回 0；Inf 仍不可用。缺 Bar 或上游尚未 ready 不会被伪装成可检测的 NaN 观测。",
    "也支持 math.isnan(x)；检测缺失不等于把缺失自动填零。",
  ]),
  ts_mean: window("窗口均值", "mean", "当前输出 2", [clock]),
  ts_sum: window("窗口求和", "sum", "当前输出 6", [clock]),
  ts_std_dev: window("窗口标准差", "std", "当前输出 √(2/3) ≈ 0.816497", [
    "ddof=0 时除以窗口样本数；ddof=1 使用样本标准差。常量完整窗口的标准差为 0。",
    "支持 clock=\"trading_days\"；日历模式 ddof 只接受 0 或 1，分母不足时结果不可用。",
  ], [degrees]),
  ts_delay: entry("滞后值", "ts", "self.ts.delay(x, period)", [input, period], "self.ts.delay(x, 2)",
    "有效观测 x = [10, 11, 12, 13]，当前值为 13", "当前输出 11", [temporal, clock, "取 period 个观测以前的值，因此通常需要当前点及此前 period 个观测。"]),
  ts_delta: entry("滞后差值", "ts", "self.ts.delta(x, period)", [input, period], "self.ts.delta(x, 2)",
    "有效观测 x = [10, 11, 12, 13]，当前值为 13", "当前输出 13 - 11 = 2", [temporal, clock]),
  ts_av_diff: window("偏离窗口均值", "av_diff", "当前输出 3 - 2 = 1", ["等于当前值减窗口均值；显式 clock 可选择完整交易日槽或有效观测时钟。"]),
  ts_zscore: window("时序标准分", "zscore", "当前输出 (3 - 2) / √(2/3) ≈ 1.224745", [
    "以当前值减窗口均值，再除窗口标准差；零标准差时不可用。显式 clock 可选择完整交易日槽或有效观测时钟。",
  ], [degrees]),
  ts_scale: window("窗口极差缩放", "scale", "当前输出 (3 - 1) / (3 - 1) = 1", [
    "以窗口最小值和最大值映射当前值；最大值等于最小值时不可用。显式 clock 可选择完整交易日槽或有效观测时钟。",
  ]),
  ts_decay_linear: window("线性加权窗口", "decay_linear", "当前输出 (1×1 + 2×2 + 3×3) / (1+2+3) = 7/3", [
    "完整 period 窗口中，最旧到最新的权重为 1..period，除以权重和。", clock,
    "这是因子表达式窗口；settings.decay 是生成 Alpha 目标前的设置级变换，位置不同。",
  ]),

  rank: entry("截面排名", "cs", "self.cs.rank_unit(x)\nself.cs.rank(x, ascending=True, pct=False)", [input,
    { name: "ascending", description: "仅 rank 接受。默认为 True，数值越小名次越靠前；False 反向。" },
    { name: "pct", description: "仅 rank 接受。默认为 False，返回从 1 起的平均名次；True 再除以有效样本数。" },
  ], "self.cs.rank_unit(x)", "同日 x = [10, 20, 20, 40]", "rank_unit: [0, 0.5, 0.5, 1]", [
    "rank_unit 使用 (平均名次 - 1)/(N - 1)，单样本或全相等截面为 0.5。",
    "同一输入的 rank 为 [1, 2.5, 2.5, 4]；rank(pct=True) 为 [0.25, 0.625, 0.625, 1]。两套写法不会静默互换。",
  ]),
  zscore: cross("截面标准分", "zscore", "同日 x = [1, 2, 3]", "[-1.224745, 0, 1.224745]（约）", [
    "使用总体标准差：先减当日有效截面均值，再除以标准差；这个入口没有 ddof 参数。",
    "常量或单样本截面的标准差为零，输出不可用，而不是零。",
  ]),
  normalize: cross("截面减均值", "normalize", "同日 x = [1, 2, 3]", "[-1, 0, 1]", [
    "当前入口只减均值，不默认除以标准差，也不接受 useStd 等其他平台参数。",
  ]),
  scale: cross("截面 L1 缩放", "scale", "同日 x = [-1, 1, 2]", "[-0.25, 0.25, 0.5]", [
    "每个值除以有效截面的绝对值之和。绝对值和为零时不可用。这里只做表达式缩放，不生成交易账户仓位。",
  ]),
  group_rank: group("组内排名", "group_rank", "[0, 1, 0, 1]", ["各组独立做 0..1 端点排名，并列取平均名次；单样本组为 0.5。"]),
  group_neutralize: group("组内减均值", "group_neutralize", "[-1, 1, -2, 2]", ["每个值减去所属组内的有效均值，不自动缩放。"]),
  densify: entry("连续整数编码", "basic", "self.cs.densify(x)", [input], "self.cs.densify(x)",
    "同日 x = [99, 2, 0, 2, NaN, 1]", "[3, 2, 0, 2, null, 1]", [
      "对当日有限唯一值升序编号为0..N-1；相同值得到相同编号，缺失保持不可用。编码随当日截面变化，不是永久证券或行业编号。",
    ]),
  days_from_last_change: entry("距上次变化的期数", "ts", "self.ts.days_from_last_change(x)", [input],
    "self.ts.days_from_last_change(x)", "同一股票逐帧 x = [3, 3, 4, 4, 4]", "逐帧输出 [0, 1, 0, 1, 2]", [
      "第一次有限值或数值改变时计0，连续相同值每帧加1。默认按交易日槽推进；缺失会中断连续段并使当前输出不可用。",
      "clock=\"valid_observations\" 可跳过缺失帧，移出再入证券从新状态开始。",
    ]),
  hump: entry("限制逐期变化", "ts", "self.ts.hump(x, hump=0.01)", [input,
    { name: "hump", description: "非负阈值系数，默认0.01；绝对阈值为该系数乘当日有效截面abs_sum。" }],
    "self.ts.hump(x, hump=0.1)", "前期输出 [1, 2]；当日输入 [1.01, 4]，阈值=0.1×5.01=0.501", "当日输出 [1, 2.501]", [
      "变化不超过阈值时保留旧值；超过时沿变化方向最多移动一个阈值。首次有限输入原样作为状态，按股票独立保存。",
      "阈值取同日整个有效输入截面的绝对值之和，不能简化成每只股票值的百分比。缺失输入当前不可用，不伪造交易行为。",
    ]),
  kth_element: entry("从近到远第k个观测", "ts", "self.ts.kth_element(x, period, k=1, ignore=\"NaN\")", [input, period,
    { name: "k", description: "从最新向最旧数第k个未忽略观测，正整数，默认1。" },
    { name: "ignore", description: "忽略项的字符串，可包含NaN及有限数值，例如\"NaN 0\"或\"NaN,0\"。" }],
    "self.ts.kth_element(x, 4, k=2, ignore=\"NaN\")", "最近四帧 x = [3, NaN, 5, NaN]", "当前输出 3", [
      "这是时间顺序中的第k个保留观测，不是数值排序后的第k小。默认交易日槽窗口包含当前点，完整窗口后从近到远查找。",
      "找不到足够观测或窗口含Inf时不可用；可显式clock=\"valid_observations\"选择有效观测时钟。",
    ]),
  last_diff_value: entry("最近的不同值", "ts", "self.ts.last_diff_value(x, period)", [input, period],
    "self.ts.last_diff_value(x, 4)", "最近四帧 x = [1, 2, 2, 2]", "当前输出 1", [
      "从当前往过去查找最近一个与当前值不同的有限值。默认交易日槽窗口包含当前点；当前值缺失、没有不同值或窗口含Inf时不可用。",
    ]),
  ts_arg_max: entry("窗口最大值位置", "ts", "self.ts.arg_max(x, period)", [input, period],
    "self.ts.arg_max(x, 4)", "最近四帧 x = [3, 5, 5, 2]", "当前输出 1", [
      "默认交易日槽窗口，返回距当前帧的距离：当前为0、上一帧为1。并列最大值选择最近的一次，有限样本中的NaN不参加极值比较。",
      "没有有限样本或窗口含Inf时不可用；结果是时间位置，不是最大值本身。",
    ]),
  ts_arg_min: entry("窗口最小值位置", "ts", "self.ts.arg_min(x, period)", [input, period],
    "self.ts.arg_min(x, 4)", "最近四帧 x = [1, 3, 1, 2]", "当前输出 1", [
      "默认交易日槽窗口，返回距当前帧的距离，当前为0。并列最小值选择最近的一次；NaN不参加极值比较，Inf使该窗口结果不可用。",
    ]),
  ts_backfill: entry("向历史寻找有效值", "ts", "self.ts.backfill(x, lookback, k=1)", [input,
    { name: "lookback", description: "包含当前帧的正整数查找范围，是period的别名。" },
    { name: "k", description: "从最新往过去找到的第k个有限值，默认1。" }],
    "self.ts.backfill(x, lookback=4, k=1)", "最近四帧 x = [3, NaN, 5, NaN]", "当前输出 5", [
      "默认交易日槽，窗口准备完整后包含当前点查找。当前值有限且k=1时原样返回；找不到足够有效值或窗口含Inf时不可用。",
    ]),
  ts_corr: entry("窗口相关系数", "ts", "self.ts.corr(x, y, period)", [input, second, period],
    "self.ts.corr(x, y, 3)", "最近三帧 x = [1, 2, 3]；y = [2, 4, 6]", "当前输出 1", [
      "默认交易日槽；只保留x、y都有限的成对样本计算Pearson相关。至少需要2对样本且双方方差均为正。",
      "NaN按配对剔除，任意一列出现Inf会使本次结果不可用；两列数据必须同日、同证券轴。",
    ]),
  ts_count_nans: entry("窗口缺失值个数", "ts", "self.ts.count_nans(x, period)", [input, period],
    "self.ts.count_nans(x, 3)", "最近三帧 x = [1, NaN, NaN]", "当前输出 2", [
      "默认交易日槽，完整窗口内统计NaN槽数量。Inf不是NaN，会使该窗口结果不可用；当前无Bar不会发布伪造观测。",
    ]),
  ts_covariance: entry("窗口总体协方差", "ts", "self.ts.covariance(x, y, period)", [input, second, period],
    "self.ts.covariance(x, y, 3)", "最近三帧 x = [1, 2, 3]；y = [2, 4, 6]", "当前输出 4/3 ≈ 1.333333", [
      "默认交易日槽；使用成对有限样本，分母为实际有效配对数N，不是N-1。至少1对样本；单对样本协方差为有效零。",
      "没有有效配对或任一输入列出现Inf时不可用。",
    ]),
  ts_product: entry("窗口乘积", "ts", "self.ts.product(x, period)", [input, period],
    "self.ts.product(x, 3)", "最近三帧 x = [2, 3, 4]", "当前输出 24", [
      "默认交易日槽，要求窗口全部值有限后相乘。NaN、Inf或乘积溢出会使结果不可用；真实零则产生有效零乘积。",
    ]),
  ts_quantile: entry("时序排名分布映射", "ts", "self.ts.quantile(x, period, driver=\"gaussian\")", [input, period,
    { name: "driver", description: "逆分布类型：gaussian（默认标准正态）、uniform（返回概率）、cauchy。" }],
    "self.ts.quantile(x, 3, driver=\"uniform\")", "最近三帧 x = [1, 2, 3]，当前值为3", "当前输出 (2+0.5)/3 = 5/6", [
      "默认交易日槽。对有限样本计较小数L、相等数E，概率p=(L+E/2)/N，再作driver逆分布；此处用的是中间名次概率。",
      "同一输入用gaussian时约0.967422。当前值缺失、没有有限样本或窗口含Inf时不可用。",
    ]),
  ts_rank: entry("当前值的窗口排名", "ts", "self.ts.rank(x, period, constant=0)", [input, period,
    { name: "constant", description: "排名完成后加上的有限常数，默认为0；加常数后可能超出0..1。" }],
    "self.ts.rank(x, 3, constant=0.1)", "最近三帧 x = [1, 2, 3]，当前值为3", "当前输出 1.1", [
      "默认交易日槽，有限窗口样本的平均并列名次映射到0..1，再加constant。单个有限样本得到0.5+constant。",
      "当前值缺失或窗口含Inf时不可用；它与同日截面rank处理的轴不同。",
    ]),
  ts_regression: entry("窗口线性回归", "ts", "self.ts.regression(y, x, period, lag=0, rettype=0)", [
    { name: "y", description: "被解释变量表达式，必须放第一个数据参数。" },
    { name: "x", description: "解释变量表达式，与y同证券轴；拟合包含截距。" }, period,
    { name: "lag", description: "非负滞后帧数；正lag将当前y与更早的x配对，窗口保存period+lag帧。" },
    { name: "rettype", description: "0残差；1截距；2斜率；3当前拟合值；4残差平方和SSE；5总平方和SST；6 R²；7 MSE；8斜率标准误；9截距标准误。默认0。" }],
    "self.ts.regression(y, x, 4, lag=0, rettype=2)", "y = [3, 5, 7, 9]；x = [1, 2, 3, 4]", "当前斜率输出 2（截距为1）", [
      "默认交易日槽，只保留滞后对齐后成对有限样本。解释变量须有正方差，至少2对样本；任何输入Inf使本次不可用。",
      "MSE=SSE/(N-2)，标准误也使用该分母，因此rettype=7、8、9要求有效N>2。R²在y零方差时不可用。",
      "rettype=0、3还要求对应当前y/x或拟合输入有效。这些为本地明确定义的结果，不宣称与官方oracle逐位一致。",
    ]),
  ts_step: entry("计算帧计数", "ts", "self.ts.step(period=1)", [
    { name: "period", description: "每帧增加的正整数步长，默认1；不是回看窗口。" }],
    "self.ts.step(2)", "程序依次收到第1、2、3个输入帧", "逐帧输出 [2, 4, 6]", [
      "计数从第一帧开始，随整个输入帧时钟推进。它不是证券上市天数，也不是自然日期编号；无当前Bar不发布该证券的值。",
    ]),
  quantile: entry("截面排名分布映射", "cs", "self.cs.quantile(x, driver=\"gaussian\", sigma=1)", [input,
    { name: "driver", description: "gaussian、uniform或cauchy，默认gaussian。" },
    { name: "sigma", description: "逆分布结果的非负缩放系数，默认1。" }],
    "self.cs.quantile(x, driver=\"uniform\")", "同日 x = [1, 2, 3]", "[1/3, 1/2, 2/3]", [
      "有效样本数为N，先取端点排名r，再按p=1/N+r×(1-2/N)构造概率并作逆分布，最后乘sigma。",
      "单样本或N=2的gaussian结果均为0；这与时序quantile的中间名次概率规则不同。缺失仍不可用。",
    ]),
  winsorize: entry("截面缩尾", "cs", "self.cs.winsorize(x, std=4)", [input,
    { name: "std", description: "非负总体标准差倍数，默认4；值为0时所有有限样本变为截面均值。" }],
    "self.cs.winsorize(x, std=1)", "同日 x = [1, 2, 3]，均值2，总体标准差√(2/3)", "[1.183503, 2, 2.816497]（约）", [
      "将有限值裁剪到均值±std×总体标准差，不删除证券、不进行排序替换。缺失保持不可用。",
    ]),
  vec_avg: entry("证券内分量均值", "vector", "self.cs.vec_avg((x, y, ...))", [
    { name: "components", description: "一个非空tuple/list，内部是同一证券的多个分量表达式；也支持逐个位置传入。" }],
    "self.cs.vec_avg((input.close, input.high))", "证券A的(close,high)=(1,3)；证券B=(10,14)", "按证券输出 [2, 12]", [
      "分量是在每只证券内部汇聚，绝不能把其他股票的横截面当成该证券的向量。NaN分量跳过；全部缺失或含Inf时不可用。",
      "可由已有行情列构造分量，但没有因此自动接入财务向量或其他外部数据。",
    ]),
  vec_sum: entry("证券内分量求和", "vector", "self.cs.vec_sum([x, y, ...])", [
    { name: "components", description: "一个非空tuple/list，内部是同一证券的分量表达式；不是证券清单。" }],
    "self.cs.vec_sum([input.close, input.high])", "证券A的(close,high)=(1,3)；证券B=(10,14)", "按证券输出 [4, 24]", [
      "逐证券对有限分量相加，NaN分量跳过；全部缺失或含Inf时不可用。不会沿股票轴归约，也没有新增数据源。",
    ]),
  bucket: entry("数值分桶", "cs", "self.cs.bucket(x, range=\"0,1,0.1\")\nself.cs.bucket(x, buckets=\"0.2,0.5,0.7\")", [input,
    { name: "range", description: "等距边界字符串start,end,step；与buckets二选一。默认隐藏范围外两端桶，起点归入首桶。" },
    { name: "buckets", description: "严格递增的显式边界字符串；默认保留外侧桶。桶的上界为右闭。" },
    { name: "skipBegin / skipEnd / skipBoth", description: "隐藏首个、最后或两侧外桶；同时接受snake_case别名。" },
    { name: "NaNGroup", description: "True为NaN分配独立桶编号，默认False；同时接受nan_group。" }],
    "self.cs.bucket(x, range=\"0,1,0.1\")", "同日 x = [0.05, 0.45, 0.9]", "[0, 4, 8]", [
      "range模式默认隐藏外桶并使用右闭区间，例如0.9归入(0.8,0.9]的编号8；这些是项目对齐参考算例的明确边界。",
      "显式buckets=\".2,.5,.7\"时，输入[.1,.3,.6,.8]得到[0,1,2,3]。NaNGroup编号位于保留的有限桶之后。",
    ]),
  trade_when: entry("条件更新与保持", "cs", "self.cs.trade_when(trigger, value, exit)", [
    { name: "trigger", description: "有限值>0时更新为value，否则保留上一输出。" },
    { name: "value", description: "触发更新时写入的表达式；未选中时不阻塞已有状态。" },
    { name: "exit", description: "有限值>0时优先清空为不可用；其优先级高于trigger。" }],
    "self.cs.trade_when(trigger, value, exit)", "单股逐帧(trigger,value,exit) = [(1,4,-1),(0,5,-1),(1,6,1),(0,7,-1)]", "逐帧输出 [4, 4, null, null]", [
      "按股票保存因子输出；首次没有触发时不可用。证券退出再入从空状态开始，不能等同于实际开平仓或成交。",
      "退出条件无效时当前结果不可用；非退出路径还要求触发条件有效，仅更新时才要求value准备好。",
    ]),
  group_backfill: entry("组内历史补值", "group", "self.cs.group_backfill(x, g, period, std=4)", [input, labels, period,
    { name: "std", description: "对候选历史样本进行缩尾的非负总体标准差倍数，默认4。" }],
    "self.cs.group_backfill(x, g, 2, std=4)", "g两日均为[0,0,1]；前日x=[2,10,100]，当日x=[NaN,20,NaN]", "当日输出 [32/3, 20, 100]", [
      "当前x有限则直接保留；仅缺失项使用其当前分组对应的历史窗口样本。候选跨证券与时间取值，但保留每个历史样本当时的分组。",
      "候选按均值±std×总体标准差缩尾后取算术均值；无有效候选或当前分组缺失时不可用。行业分类没有自动接入。",
    ]),
  group_mean: entry("组内加权算术均值", "group", "self.cs.group_mean(x, weight, g)", [input,
    { name: "weight", description: "与x同轴的非负权重表达式；只有有限值和有限非负权重参加加权，总权重须大于0。" }, labels],
    "self.cs.group_mean(x, weight, g)", "同日x=[10,20,30]；weight=[1,3,2]；g=[0,0,1]", "[17.5, 17.5, 30]", [
      "计算sum(x×weight)/sum(weight)，是加权算术均值，不是调和均值。有效组均值可广播到同组的缺失值位置。",
      "负权重不参与统计；有效权重和为0或组标签缺失时不可用。g可由真实列或分桶表达式构造，不表示已有行业Provider。",
    ]),
  group_scale: entry("组内极差缩放", "group", "self.cs.group_scale(x, g)", [input, labels],
    "self.cs.group_scale(x, g)", "同日x=[10,20,30,99]；g=[0,0,0,1]", "[0, 0.5, 1, null]", [
      "各组独立按(x-min)/(max-min)缩放。常量或单样本组的分母为0，输出不可用。", grouped,
    ]),
  group_zscore: entry("组内标准分", "group", "self.cs.group_zscore(x, g)", [input, labels],
    "self.cs.group_zscore(x, g)", "同日x=[10,20,30,99]；g=[0,0,0,1]", "[-1.224745, 0, 1.224745, null]（约）", [
      "每组减均值后除以总体标准差；常量或单样本组不可用，不用0代替。", grouped,
    ]),
};

// Optional core-call arguments extend the compatible old examples above.
for (const name of ["add", "subtract", "multiply"]) {
  operatorReference[name].signature = `self.cs.${name}(x, y, ..., filter=False)`;
  operatorReference[name].parameters.push({ name: "filter", description: "可选布尔值。True时NaN在add/subtract中替为0、multiply中替为1；真实零仍保留，Inf不被替换。" });
  operatorReference[name].notes.push("self.cs入口支持多个位置表达式，subtract从第一个值起依次相减；filter只对NaN生效。Python符号写法仍可用。");
}
operatorReference.normalize.signature = "self.cs.normalize(x, useStd=False, limit=0)";
operatorReference.normalize.parameters.push(
  { name: "useStd / use_std", description: "默认False只减均值；True再除以总体标准差，零标准差时不可用。" },
  { name: "limit", description: "非负截断阈值，默认0不裁剪；正数把变换后结果裁剪到[-limit,limit]。" },
);
operatorReference.normalize.notes = ["默认只做截面减均值；显式useStd=True或use_std=True可以标准化，再按正limit裁剪。缺失保持不可用。"];
operatorReference.scale.signature = "self.cs.scale(x, scale=1)\nself.cs.scale(x, longscale=1, shortscale=1)";
operatorReference.scale.parameters.push(
  { name: "scale", description: "非负总缩放倍数，默认1。" },
  { name: "longscale / shortscale", description: "任一被指定时，正值与负值分别按各自绝对值和归一，再乘对应系数和scale；支持long_scale/short_scale别名。" },
);
operatorReference.scale.notes = ["不指定分侧参数时按全截面L1归一。指定longscale/shortscale时分别控制正、负两侧，另一侧未指定的系数默认为1。", "带参数的新入口对全零截面保留有效零；无参数旧入口仍保留既有零范数不可用行为。这里只缩放表达式，不创建交易账户。"];
operatorReference.rank.signature = "self.cs.rank_unit(x, rate=2)\nself.cs.rank(x, ascending=True, pct=False)";
operatorReference.rank.parameters.push({ name: "rate", description: "rank_unit的新参数入口可传非负整数；已核验0和2均使用当前精确平均并列名次，不实现近似随机分档。rank(x, rate=...)也选择这个端点合同。" });
operatorReference.rank.notes.push("显式rate参数选择0..1端点排名合同；无rate的旧rank(ascending,pct)保持原名次/百分比合同，不把二者混为同一默认值。");
operatorReference.reverse.notes = [observed, "逐元素取负，与 -x 相同。"];
operatorReference.ts_scale.signature = "self.ts.scale(x, period, constant=0)";
operatorReference.ts_scale.parameters.push({ name: "constant", description: "在极差缩放后加的有限常数，默认0；显式constant选择支持该参数的新时序入口。" });
operatorReference.ts_scale.notes.push("显式constant参数的新入口默认交易日槽，可指定clock；无constant的旧调用保持有效观测兼容路径。");
operatorReference.ts_decay_linear.signature = "self.ts.decay_linear(x, period, dense=False)";
operatorReference.ts_decay_linear.parameters.push({ name: "dense", description: "显式dense选择新日历槽入口：False把NaN分量按0纳入总权重；True跳过NaN并仅除以有限分量的权重和。" });
operatorReference.ts_decay_linear.notes.push("显式dense参数与旧无参数调用的缺失合同不同；新入口默认交易日槽，可指定clock。NaN处理不会把Inf或未ready输入视作有效零。");
