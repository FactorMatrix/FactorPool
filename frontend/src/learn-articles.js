import { quickStartCode } from "./tutorial-content.js";

// Product documentation. Examples describe the current FactorPool contract.
export const learnGroups = [
  { id: "getting-started", title: "入门", items: [
    { id: "welcome", title: "文档首页", description: "了解研究流程与阅读路线" },
    { id: "quick-start", title: "创建第一个因子", description: "复制一份完整脚本并运行" },
    { id: "indicator", title: "编写 Indicator", description: "输入、时序、截面与就绪状态" },
  ] },
  { id: "data-computation", title: "数据与计算", items: [
    { id: "data-fields", title: "行情字段与股票池", description: "选择数据范围和因子输入" },
    { id: "price-adjustment", title: "因子前复权", description: "用基础报价换算当前信号日的价量输入" },
    { id: "windows", title: "窗口、缺失与时钟", description: "区分有效观测和交易日窗口" },
    { id: "backtest", title: "收益与回测口径", description: "从收盘信号到比例组合" },
  ] },
  { id: "reference", title: "参考手册", items: [
    { id: "config", title: "配置脚本", description: "组织日期、数据、因子和报告参数" },
    { id: "operators", title: "算子参考", description: "查找调用方式、用途和支持状态" },
    { id: "simulation-settings", title: "模拟设置指南", description: "理解权重变换的顺序和影响" },
    { id: "settings", title: "参数参考", description: "查阅当前参数、默认值和选项" },
  ] },
  { id: "results", title: "结果与排错", items: [
    { id: "metrics", title: "解读研究结果", description: "阅读 IC、组合表现和权重追踪" },
    { id: "faq", title: "常见问题", description: "定位配置错误、缺失值和不可用结果" },
  ] },
];

export const learnArticles = {
  welcome: {
    id: "welcome", title: "FactorPool 文档", subtitle: "从一份因子脚本开始，完成可复现的矩阵研究。", readingTime: "2 分钟",
    sections: [
      { id: "workflow", title: "一次研究，分三步", paragraphs: [
        "先在 Simulate 编写 config 和 PythonIndicator，再运行因子，最后检查逐股值、IC、分组与组合表现。源码、配置和结果会一起保存在任务快照里。",
      ], table: { columns: ["步骤", "做什么", "从哪里开始"], rows: [
        ["1. 定义因子", "选择输入字段，用时序和截面算子构造信号", "创建第一个因子 → 编写 Indicator"],
        ["2. 确定研究范围", "设置日期、股票池、持有期和 Alpha 权重变换", "配置脚本 → 模拟设置指南"],
        ["3. 检查结果", "核对因子值，再阅读统计和组合收益", "解读研究结果 → 常见问题"],
      ] } },
      { id: "reading-path", title: "找到你需要的内容", paragraphs: [
        "第一次使用时，按左侧“入门”章节完成合成样例。准备使用真实数据时，再阅读“数据与计算”，了解股票池、前复权输入和缺失处理。",
        "写因子时可直接打开“算子参考”查调用方式；调整参数时使用“参数参考”核对默认值与支持状态。搜索也可以按算子名、参数名或问题查找。",
      ] },
      { id: "research-scope", title: "当前研究范围", paragraphs: [
        "FactorPool 使用已发布的日线行情矩阵，按股票权重比例计算收益。当前市场范围为中国股票；合成 fixture 数据用于熟悉流程和核对计算。",
        "教程说明的是本项目当前行为。算子和设置中的“已接通”表示本项目调用链可用；具体选项还要满足相应的数据条件。",
      ] },
    ],
  },
  "quick-start": {
    id: "quick-start", title: "创建第一个因子", subtitle: "用三只虚拟股票运行低成交量排名，熟悉完整研究流程。", readingTime: "4 分钟",
    sections: [
      { id: "first-run", title: "运行这份脚本", paragraphs: [
        "复制下面的完整示例，回到 Simulate，粘贴到代码框并点击 Simulate。示例只使用合成数据，不需要准备真实行情。",
        "这个因子先计算每只股票最近 3 个有效观测的平均成交量，再对负均量做同日截面排名。因此，成交量越低，因子排名越高。",
      ], code: quickStartCode, codeLabel: "入门完整脚本" },
      { id: "inspect-first-result", title: "先看因子值，再看收益", paragraphs: [
        "任务完成后，打开 Factor Values，确认输出日期、股票、因子值和 ready 状态符合你的表达式。窗口尚未完成时不会发布可用因子值。",
        "接着在 IC Analysis 和 Groups 查看因子与未来收益的关系，在 Chart 与 Summary 查看 Alpha 组合的表现。合成数据只用于演示流程，不能作为真实因子有效性的证据。",
      ] },
      { id: "change-one-parameter", title: "只改一个参数做比较", paragraphs: [
        "把 config.factor.period 从 3 改成 1，再运行一次。构造参数会传入 TutorialFactor 的 __init__，因此这次比较只改变成交量窗口。",
        "需要保留版本时点击 Save。每次运行都会保存提交时的源码和有效配置，之后继续编辑不会改写原任务。",
      ], note: { title: "草稿与任务", text: "模拟草稿保存在当前页面内存里；刷新后可恢复最近任务。尚未运行或保存的编辑，请先保存版本。" } },
    ],
  },
  indicator: {
    id: "indicator", title: "编写 Indicator", subtitle: "用 update 定义因子，让时序与截面计算组成同一个表达式。", readingTime: "5 分钟",
    sections: [
      { id: "indicator-structure", title: "类、输入和输出", paragraphs: [
        "因子类继承 PythonIndicator。构造函数接收 config.factor 中的参数；update(input) 接收当前已经完成的日线输入。",
        "把本次计算结果赋给 self.value。使用窗口时，再通过 self.ts.is_ready(value) 返回表达式是否已经完成预热。",
      ], code: `class MeanClose(PythonIndicator):
    def __init__(self, period=20):
        super().__init__()
        self.period = period

    def update(self, input):
        value = self.ts.mean(input.close, self.period)
        self.value = value
        return self.ts.is_ready(value)`, codeLabel: "因子类示例" },
      { id: "ts-and-cs", title: "时序 TS 与截面 CS", paragraphs: [
        "self.ts 对每只股票自己的历史序列计算，例如均线、延迟和标准差。self.cs 对同一交易日的完整股票截面计算，例如排名和标准化。",
        "可以先做时序，再做截面。下面的表达式会对每只股票的 20 期均量做同日排名；截面步骤等待完整的同日数据，不按股票到达顺序提前消费。",
      ], code: `mean_volume = self.ts.mean(input.volume, 20)
self.value = self.cs.rank_unit(-mean_volume)
return self.ts.is_ready(mean_volume)`, codeLabel: "update 内的组合表达式" },
      { id: "ready-and-valid", title: "ready 和 valid 表示什么", table: { columns: ["状态", "含义", "常见原因"], rows: [
        ["ready=False", "历史窗口还未完成", "均线样本不足、股票重新进入计算轴"],
        ["ready=True，valid=False", "窗口时间已到，但本次结果不可用", "所需值缺失、除数为零、标准差为零"],
        ["ready=True，valid=True", "当前因子值可用于研究", "真实零值也属于有效值"],
      ] }, paragraphs: ["不要用 0 代替未准备好或缺失的结果。先检查 Factor Values 的 reason，再决定表达式是否需要调整。"] },
      { id: "rank-conventions", title: "明确使用哪一种排名", paragraphs: [
        "推荐 self.cs.rank_unit(x)：正常截面映射到 0 到 1，端点为 0 和 1，并列值使用平均名次；单样本或全相等截面取 0.5。",
        "旧 self.cs.rank(x, pct=True) 保留“平均名次 / 样本数”的口径。两者端点不同，替换接口会改变因子值。",
      ] },
    ],
  },
  "data-fields": {
    id: "data-fields", title: "行情字段与股票池", subtitle: "先明确因子在什么数据和证券范围上计算。", readingTime: "4 分钟",
    sections: [
      { id: "providers", title: "选择数据源", table: { columns: ["provider", "universe", "适用场景"], rows: [
        ["fixture", "fixture", "合成样例、熟悉界面与计算核对"],
        ["market_matrix", "证券代码列表", "在明确的一组股票上研究"],
        ["market_matrix", "all_a_dynamic", "使用历史动态全 A 股票池"],
      ] }, paragraphs: ["真实行情任务读取已经发布的行情快照。省略 snapshot_id 时，提交任务会固定当时选用的快照；任务本身不会导入或刷新行情。"] },
      { id: "quote-fields", title: "可用的基础字段", table: { columns: ["字段", "含义", "使用提示"], rows: [
        ["input.open / high / low / close", "日线开、高、低、收盘价", "在代码中分别写 input.open、input.high 等"],
        ["input.prev_close", "当前日行情中的前收盘价", "收益率与报价价格桥使用这个字段"],
        ["input.volume", "前复权成交量", "按价格倍率的倒数换算；真实零量和缺失仍区分"],
        ["input.total_turnover", "成交额", "保持基础行情原值"],
        ["input.is_st / is_suspended", "ST 与停牌状态", "按所选快照中的实际字段使用"],
      ] } },
      { id: "market-config", title: "配置一个明确股票池", code: `"data": {
    "provider": "market_matrix",
    "universe": ["000001.XSHE", "000002.XSHE", "600000.XSHG"],
    "factor_price": "quote_adjusted"
}`, codeLabel: "config 中的 data 部分", paragraphs: [
        "证券列表必须非空且不重复。日期和股票数会影响有效配对数量；单日 IC 默认至少需要 3 组有效的因子值与未来收益配对。",
      ] },
      { id: "classification-data", title: "分组和池外计算", paragraphs: [
        "分组算子需要与证券轴对齐的真实分组标签。当前基础行情没有历史行业分类，不能把任意数字当作行业数据。财务向量和风险暴露同样需要对应的数据接入。",
        "pasteurization=ON 在研究池上计算截面；OFF 可用快照中当时存续的更大证券轴计算因子，再把结果投影回研究池。组合和研究结果仍限于所选股票池；fixture 没有额外池外数据。",
      ] },
    ],
  },
  "price-adjustment": {
    id: "price-adjustment", title: "因子前复权", subtitle: "用收盘价和前收盘价计算倍率，在信号日统一历史价量基准。", readingTime: "5 分钟",
    sections: [
      { id: "select-price-basis", title: "统一使用前复权输入", paragraphs: [
        "因子只使用前复权输入。factor_price 省略时默认为 quote_adjusted，有效配置会明确记录它；不支持切换成原始价或后复权。",
      ], table: { columns: ["factor_price", "因子看到的历史价格", "用途"], rows: [
        ["quote_adjusted（默认）", "以当前信号日为基准前复权的历史价格", "在同一基准计算窗口与截面信号"],
      ] }, code: `"data": {
    "provider": "market_matrix",
    "universe": ["000001.XSHE", "000002.XSHE", "600000.XSHG"],
    "factor_price": "quote_adjusted"
}`, codeLabel: "前复权配置" },
      { id: "quote-bridge", title: "倍率如何计算", paragraphs: [
        "相邻有效交易日之间，用当前前收盘价除以上一日收盘价，得到价格桥 q。累计这些倍率得到 Q；历史价格再乘以“当前 Q / 历史 Q”。这个过程只使用信号日及之前已经完成的基础行情。",
      ], code: `q[t] = prev_close[t] / close[t-1]
Q[t] = Q[t-1] * q[t]
价格倍率 = Q[t] / Q[s]
历史价格在当前基准下 = raw_price[s] * 价格倍率
历史成交量在当前基准下 = raw_volume[s] / 价格倍率`, codeLabel: "计算公式", table: { columns: ["项目", "示例数值"], rows: [
        ["上一日收盘价", "102"],
        ["当前前收盘价 / 当前收盘价", "51 / 51"],
        ["报价桥", "51 / 102 = 0.5"],
        ["历史收盘价换到当前基准", "102 × 0.5 = 51"],
        ["历史成交量换到当前基准", "100 / 0.5 = 200"],
        ["当前基准下的两期均价", "(51 + 51) / 2 = 51"],
      ] } },
      { id: "unchanged-returns", title: "收益矩阵 R 与调整矩阵 Q", paragraphs: [
        "基础矩阵分为收益 R 和调整倍率 Q。收盘信号需要计算因子时，用 Q 将历史开高低收和前收换到当前基准，成交量按价格倍率的倒数调整；成交额、状态字段及因子公式和算子接口不变。",
        "组合在非换仓日只用 R 计算收益和净值，不重新计算因子或前复权。R 独立遵循快照收益规则，正常成交使用 close / prev_close - 1。",
        "不需要另外读取分红明细、公司行为或外部复权因子表，也不会改写原行情、历史任务或已发布因子结果。",
      ] },
      { id: "price-boundaries", title: "未知倍率与支持范围", paragraphs: [
        "缺 Bar、无量或关键报价不可信时，程序不会猜倍率为 1，也不会跨缺口拼接价格。受影响股票建立新的可比价格段，相应价格窗口重新预热。",
        "换仓日计算窗口时仍保留窗口内所需的日观测，时序算子不会把两个换仓日误当成相邻交易日。不支持的表达式或状态会明确报错，不通过改成原始价绕过。",
      ] },
    ],
  },
  windows: {
    id: "windows", title: "窗口、缺失与时钟", subtitle: "同样的 period，先确定它数的是有效观测还是交易日。", readingTime: "5 分钟",
    sections: [
      { id: "window-clocks", title: "两种窗口时钟", table: { columns: ["时钟", "如何推进", "写法"], rows: [
        ["valid_observations（默认）", "按有效观测推进；缺失不占有效观测次数", "self.ts.mean(x, 5)"],
        ["trading_days", "按传入的交易日轴占槽；缺失日仍保留槽位", "self.ts.mean(x, 5, clock=\"trading_days\")"],
      ] }, paragraphs: ["交易日窗口不把周末当成交易日。动态退出后重新进入计算轴的股票会重新积累状态。"] },
      { id: "delay-example", title: "用 delay 看出差别", paragraphs: [
        "假设某只股票连续四个交易日的输入为 10、缺失、30、40。在第三日，delay(x, 1) 的默认时钟返回上一个有效观测 10；交易日时钟要取第二日的槽位，所以结果不可用。第四日，两者都可以取到第三日的 30。",
        "若当前交易日没有 Bar，不发布该股票的有效因子值。日历槽位存在，与当前日存在真实观测，是两件不同的事。",
      ], code: `# 默认：上一个有效观测
value = self.ts.delay(input.close, 1)

# 显式：上一个交易日槽位
value = self.ts.delay(input.close, 1, clock="trading_days")`, codeLabel: "在 update 中选择时钟" },
      { id: "calendar-support", title: "交易日窗口支持哪些方法", paragraphs: [
        "当前显式交易日时钟支持 mean、sum、min、max、std、delay、delta 和 decay_linear。窗口遵循严格的完整窗口规则；所需槽位中有 NaN 时，相应结果保持不可用。",
        "窗口未就绪、已观测 NaN、Inf 和缺 Bar 分别处理。is_nan 不会把预热不足或不存在的 Bar 包装成一个有效的 NaN 观测。",
      ] },
      { id: "zero-and-missing", title: "真实零值不是缺失", paragraphs: [
        "输入为零时仍可能是有效观测，例如真实零成交量或表达式算出的零。不要用“值等于零”判断窗口是否完成。",
        "若表达式需要条件选择，可使用 self.cs.if_else(condition, yes, no)。缺失条件保持不可用；只要求实际选中的分支有效。",
      ] },
    ],
  },
  backtest: {
    id: "backtest", title: "收益与回测口径", subtitle: "把收盘信号、未来收益和比例组合放在同一条时间轴上。", readingTime: "4 分钟",
    sections: [
      { id: "daily-return", title: "日收益从哪里来", paragraphs: [
        "研究直接读取持久化的日收益矩阵 R。正常有效行情下，R[t] = close[t] / prev_close[t] - 1；无需再单独处理分红拆股。",
        "缺失或异常行情按快照记录的录入规则处理。任务固定收益快照，不在回测过程中修复或补齐源数据；因此，比较结果时也要核对 snapshot_id。",
      ] },
      { id: "signal-timeline", title: "信号日与收益日", table: { columns: ["日期位置", "已知的信息", "参与什么计算"], rows: [
        ["信号日 t 收盘", "截至 t 的完整行情与历史窗口", "计算因子值和目标权重"],
        ["下一交易日 t+1", "R[t+1]", "开始计算该信号的未来收益"],
        ["t+1 至 t+h", "连续 h 个交易日的收益", "合成 h 期前瞻收益和持有段表现"],
      ] }, paragraphs: ["当前设置只支持 delay=1。输入不再额外整体向后移一天；未来收益不会进入 Indicator 的因子计算。"] },
      { id: "holding-period", title: "持有期与比例权重", paragraphs: [
        "每个 horizon 从首个因子日开始，按互不重叠的持有段再平衡。段内先复合各只股票的收益，再按期初权重合成组合；持有权重随价格自然漂移。",
        "组合按权重比例计算，日线逐日估值；当前不计佣金、滑点和税费。某个非零持仓的收益缺失时，不会事后删掉它再重新分配权重。",
      ] },
      { id: "pnl-and-nav", title: "区分 PnL 与净值", paragraphs: [
        "Net value 展示逐日复利净值；PnL 展示固定 book 的累积收益贡献。两种视角使用各自定义的序列，不应只看曲线名称就互换解释。",
        "book_size 仅把比例 PnL 缩放为名义金额，pnl_unit 只是单位标签；它们不会改变因子排名、IC、按比例收益或产生整手约束。",
      ] },
    ],
  },
  config: {
    id: "config", title: "配置脚本", subtitle: "所有研究参数跟随源码保存，明确每一项配置影响哪里。", readingTime: "5 分钟",
    sections: [
      { id: "config-sections", title: "五个配置部分", table: { columns: ["部分", "控制什么", "例子"], rows: [
        ["base", "因子研究日期和频率", "start_date、end_date、frequency"],
        ["data", "数据源、股票池和前复权因子输入", "provider、universe、factor_price=quote_adjusted"],
        ["factor", "因子类构造参数", "period 会传入 __init__(period=...)"],
        ["analysis", "分组、持有期与报告诊断", "groups、horizons、decay_steps"],
        ["settings", "Alpha 权重变换与展示", "decay、neutralization、truncation"],
      ] } },
      { id: "config-example", title: "一个真实行情配置", code: `config = {
    "type": "REGULAR",
    "base": {
        "start_date": "2024-01-29",
        "end_date": "2024-03-29",
        "frequency": "1d",
    },
    "data": {
        "provider": "market_matrix",
        "universe": ["000001.XSHE", "000002.XSHE", "600000.XSHG"],
        "factor_price": "quote_adjusted",
    },
    "factor": {"period": 20},
    "analysis": {"groups": 3, "horizons": [1, 5, 20]},
    "settings": {
        "decay": 0,
        "neutralization": "MARKET",
        "truncation": 0,
        "visualization": True,
    },
}`, codeLabel: "搭配接收 period 的因子类使用" },
      { id: "report-parameters", title: "常用报告参数", table: { columns: ["参数", "含义", "当前范围 / 默认"], rows: [
        ["analysis.groups", "按原始因子排序后的分组数", "1..100；默认 3"],
        ["analysis.horizons", "研究的持有期列表", "1..6 个不重复的整数；每项 1..252；默认 [1,5,20]"],
        ["analysis.decay_steps", "预测力衰减诊断的阶数", "1..10；默认 10"],
        ["analysis.annual_days", "组合指标的年化交易日数", "1..366；默认 252"],
        ["analysis.trace_dates", "需要逐股权重 / PnL 追踪的信号日", "最多 5 个日期；默认空"],
      ] } },
      { id: "source-config", title: "源码配置与有效配置", paragraphs: [
        "config 必须是源码中的字面量对象；使用 Python 的 True、False 和 None。不要把配置藏进文件读取、函数调用或顶层动态表达式。",
        "Settings 会校验并展示解析配置，后台在提交时再次校验。原始 source_config 保留你的写法，effective_config 记录补齐默认值和固定行情快照后的实际配置。",
        "type 当前使用 REGULAR。其他模式或未支持的选项会在校验时明确报错，不会被当作一个已经生效的开关。",
      ] },
    ],
  },
  operators: {
    id: "operators", title: "算子参考", subtitle: "按调用方式和用途查找当前可用的因子算子。", readingTime: "参考",
    sections: [
      { id: "operator-contract", title: "如何阅读算子参考", paragraphs: [
        "数学与逻辑算子逐元素计算；TS 沿单只股票的历史计算；CS 使用同日截面；分组算子另需与证券轴对齐的分组标签。",
        "已接通表示本项目已有调用链。待接通的项可以用于查看规划和缺口，不能直接视为已实现。调用签名、数据条件与当前项目口径分别列出。",
      ] },
    ],
  },
  "simulation-settings": {
    id: "simulation-settings", title: "模拟设置指南", subtitle: "了解因子如何变成 Alpha 权重，以及每个设置会影响什么。", readingTime: "6 分钟",
    sections: [
      { id: "alpha-pipeline", title: "Alpha 权重的生成顺序", paragraphs: [
        "原始因子值先经过可选历史衰减，再进行当日截面处理，之后按绝对值和归一，最后限制单只股票的权重。",
      ], code: `原始因子值
  → decay 历史线性衰减
  → neutralization（NONE / MARKET）
  → L1 归一：w[i] = x[i] / sum(abs(x))
  → truncation 单股权重截断`, codeLabel: "Alpha 目标权重流水线", note: { title: "IC 与 Alpha 是不同视角", text: "decay、neutralization 和 truncation 只变换 Alpha 组合目标。原始 Indicator 的 IC 与因子分组仍使用原始因子值。" } },
      { id: "decay-settings", title: "Decay：历史因子衰减", paragraphs: [
        "settings.decay=0 关闭衰减，1 是恒等窗口，2..512 使用线性加权窗口，近期有效因子观测权重更高。窗口有自己的预热和重新入池状态。",
        "analysis.decay_steps 则是预测力衰减诊断的阶数。前者改变 Alpha 目标，后者增加报告里的诊断范围。",
      ] },
      { id: "neutralization-settings", title: "Neutralization：截面处理", table: { columns: ["选项", "处理", "后续步骤"], rows: [
        ["NONE", "保留当前有效因子值", "按 L1 归一"],
        ["MARKET（默认）", "减去当日有效截面的算术均值", "按 L1 归一"],
      ] }, paragraphs: [
        "MARKET 后若截面全为零，就没有可归一的权重。行业、国家和风险模型选项需要合格的历史分类或风险暴露，当前会明确拒绝。",
      ] },
      { id: "truncation-settings", title: "Truncation：单股权重上限", paragraphs: [
        "truncation=0 关闭上限；设为正数时，限制每只股票目标权重的绝对值。例如 0.1 表示 |w[i]| 不超过 0.1。",
        "截断后不会再归一到满额 gross。被截掉的部分保留为未分配 gross，结果会展示实际敞口和容量不足；它不是对因子值做去极值。",
      ] },
      { id: "evaluation-settings", title: "测试区间与可视化", paragraphs: [
        "testPeriod 从研究结束日向前划出测试期，支持 P30D、P2W、P1Y6M 等日历长度；默认 P0Y0M 不划分。Train/Test 只划分研究日期范围内的组合表现，不重新训练或筛选因子。",
        "All 保留完整持有收益，包括研究结束后用于完成持有段的尾部，因此可能和 Train/Test 合计范围不同。",
        "visualization=False 关闭图表，数值和结果导出仍保留。完整参数和每个选项的支持状态可在“参数参考”中查阅。",
      ] },
    ],
  },
  settings: {
    id: "settings", title: "参数参考", subtitle: "核对真实配置路径、默认值、可选值和当前支持状态。", readingTime: "参考",
    sections: [
      { id: "parameter-reference", title: "如何阅读参数参考", paragraphs: [
        "参数参考随当前服务能力更新。先看项目配置路径，再核对默认值和逐个选项的状态；同一个参数里可能同时包含已支持和缺数据的选项。",
        "项目默认值与 BRAIN 官方默认值分别展示。尚未核实的外部标签不会被拼成一个可用的 config 路径；这些条目用于说明当前缺口。",
      ] },
    ],
  },
  metrics: {
    id: "metrics", title: "解读研究结果", subtitle: "先检查因子值，再分别阅读预测力、组合表现和逐股贡献。", readingTime: "6 分钟",
    sections: [
      { id: "result-map", title: "各个页面看什么", table: { columns: ["入口", "适合回答的问题"], rows: [
        ["Factor Values", "哪只股票的值或 ready 状态与预期不同？"],
        ["IC Analysis", "原始因子和未来收益是否存在稳定关系？"],
        ["Groups", "排序后的分组收益是否有区分度？"],
        ["Chart / Summary", "Alpha 组合的收益、风险和换手表现如何？"],
        ["Weights / PnL", "目标权重、持有敞口和逐股收益贡献如何对应？"],
        ["Source / Logs", "这次实际提交了什么，计算在哪个阶段失败？"],
      ] } },
      { id: "ic-metrics", title: "IC、Rank IC 与 ICIR", table: { columns: ["指标", "当前含义", "阅读提示"], rows: [
        ["IC", "同日因子值与对应前瞻收益的 Pearson 相关", "只使用有效配对；常量截面无法计算相关"],
        ["Rank IC", "按平均名次处理并列后计算相关", "用于观察排序关系"],
        ["ICIR", "日 IC 均值 / 日 IC 标准差", "默认 ddof=1，当前不年化；单日或零标准差时不可用"],
        ["Coverage", "历史成员轴上的有效因子覆盖情况", "与实际能配对计算 IC 的样本数一起看"],
      ] } },
      { id: "portfolio-metrics", title: "组合表现与六项概要", paragraphs: [
        "Summary 的 BRAIN 风格概要使用本项目定义的固定 book PnL 口径；与按逐日复利净值计算的 CAGR、净值回撤分开阅读。",
      ], table: { columns: ["指标", "项目口径"], rows: [
        ["Sharpe", "固定 book 日 PnL 增量的均值 / 样本标准差，再按交易日数年化"],
        ["Returns", "固定 book 平均日 PnL 增量年化后，除以 0.5 的多头侧基准"],
        ["Turnover", "目标权重与前期价格漂移后权重之差的 L1；包含首次建仓，按日平均"],
        ["Drawdown", "累计算术 PnL 从高点到低点的最大回撤 / 0.5"],
        ["Fitness", "Sharpe × sqrt(abs(Returns) / max(Turnover, 0.125))"],
        ["Margin", "总归一 PnL / 总归一成交额；界面以 bp 显示"],
      ] }, note: { title: "项目口径", text: "这些是可复现的本地研究定义，尚未取得 BRAIN 官方数值对照。条件不足时显示空值，不填成零。" } },
      { id: "trace-and-replay", title: "定位一次异常结果", paragraphs: [
        "需要逐股拆解时，在 analysis.trace_dates 里加入信号日，再运行任务。最多指定 5 个日期，明细只记录符合所选 horizon 再平衡条件的信号。",
        "按原始因子 → 衰减值 → 截面处理 → 目标权重 → 收益前漂移敞口 → 逐股 PnL 的顺序核对。Source 和导出结果保留本次源码及配置，可用它们比较两次任务的差异。",
      ] },
    ],
  },
  faq: {
    id: "faq", title: "常见问题", subtitle: "从配置、输入、就绪状态和统计条件逐层定位问题。", readingTime: "5 分钟",
    sections: [
      { id: "config-fails", title: "配置校验失败，先查哪里？", bullets: [
        "确认 config 是字面量字典，日期为 YYYY-MM-DD，Python 布尔值使用 True / False。",
        "确认末尾使用受 __main__ 保护的 因子类.run(config)，没有在顶层启动其他运行入口。",
        "核对 factor 的键与 __init__ 参数名是否一致；不认识的配置项不会被忽略。",
        "先运行 fixture 样例，再逐项替换数据、股票池和表达式，缩小出错范围。",
      ] },
      { id: "values-missing", title: "任务成功，为什么因子值为空？", paragraphs: [
        "先看 Factor Values 的 ready 和 reason。not_ready 通常表示预热未完成；missing_bar 表示当天没有该股票的 Bar；source_missing 表示表达式所需字段缺失。",
        "ready 已完成仍可能没有有效值，例如除以零、对非正数取 log，或对常量截面做 zscore。使用交易日时钟或遇到前复权价格段重置时，也要检查对应的缺失与预热规则。",
      ] },
      { id: "statistics-missing", title: "为什么 IC、ICIR 或 Sharpe 显示 --？", paragraphs: [
        "统计量需要满足自己的样本和分母条件：有效配对不足或截面常量时 IC 不可用；单日或 IC 标准差为零时 ICIR 不可用；PnL 标准差为零时 Sharpe 不可用。",
        "先增加足够的研究日期和有效股票，再核对原始值及收益缺失情况。显示空值不等于该指标为零。",
      ] },
      { id: "unsupported-fields", title: "为什么台账有这个选项，却不能运行？", paragraphs: [
        "台账同时列出已接通能力和待实现、缺数据或待核实项。请按具体条目的状态使用；例如行业中性化需要历史分类，向量算子需要实际向量数据，delay=0 需要当前日线模型尚未支持的执行价格。",
        "若提示前复权不支持该表达式，先检查错误中指出的窗口、状态与输入依赖，再用已支持的表达式复现。不要通过填零、切换原始价或改写行情绕过不可用状态。",
      ] },
      { id: "compare-results", title: "两次结果不同，怎么比较？", bullets: [
        "对照 Source 中的原始源码、构造参数与 effective_config。",
        "核对数据快照、研究日期、股票池、factor_price 与窗口时钟。",
        "区分原始因子 IC、Alpha 权重设置、horizon 和 Train/Test/All 的统计范围。",
        "用相同条件复现后，再逐次只改一项参数。",
      ] },
    ],
  },
};
