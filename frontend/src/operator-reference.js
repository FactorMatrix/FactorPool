// Examples describe FactorPool's current local contract, not an official BRAIN oracle.
const input = { name: "x", description: "输入表达式。数学算子接受有限标量或同轴向量；时序算子逐股计算，截面算子使用同一日的证券轴。" };
const second = { name: "y", description: "第二个输入表达式；可为有限常数，也可为与 x 同轴的向量。" };
const period = { name: "period", description: "正整数窗口或滞后期数，可写为字面量或构造参数。默认按有效观测推进，不等同于自然日。" };
const degrees = { name: "ddof", description: "标准差的自由度修正，默认为 0；有效观测模式要求整数且 0 ≤ ddof < period。" };
const labels = { name: "g", description: "与 x 同日、同证券轴的有限数值分组标签。每只证券必须有真实可用标签；当前基础行情没有行业分类字段。" };
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
  "x、y、g 是表达式占位名。下面的数组是输入/输出说明，不表示可以直接在原生因子中传入任意 Python 列表。",
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
  ts_av_diff: window("偏离窗口均值", "av_diff", "当前输出 3 - 2 = 1", ["等于当前值减窗口均值；此派生算子不支持 trading_days 选项。"]),
  ts_zscore: window("时序标准分", "zscore", "当前输出 (3 - 2) / √(2/3) ≈ 1.224745", [
    "以当前值减窗口均值，再除窗口标准差；零标准差时不可用。此派生算子不支持 trading_days 选项。",
  ], [degrees]),
  ts_scale: window("窗口极差缩放", "scale", "当前输出 (3 - 1) / (3 - 1) = 1", [
    "以窗口最小值和最大值映射当前值；最大值等于最小值时不可用。此派生算子不支持 trading_days 选项。",
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

};
