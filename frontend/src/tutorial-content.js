// User-facing examples and descriptions for the implemented Pool contract.
export const quickStartCode = `from factorpool import PythonIndicator

config = {
    "type": "REGULAR",
    "base": {
        "start_date": "2024-01-04",
        "end_date": "2024-01-08",
        "frequency": "1d",
    },
    "data": {"provider": "fixture", "universe": "fixture", "factor_price": "quote_adjusted"},
    "factor": {"period": 3},
    "analysis": {"groups": 3, "horizons": [1, 5]},
    "settings": {
        "delay": 1,
        "decay": 0,
        "neutralization": "MARKET",
        "truncation": 0,
    },
}

class TutorialFactor(PythonIndicator):
    def __init__(self, period=3):
        super().__init__()
        self.period = period

    def update(self, input):
        mean_volume = self.ts.mean(input.volume, self.period)
        self.value = self.cs.rank_unit(-mean_volume)
        return self.ts.is_ready(mean_volume)

if __name__ == "__main__":
    TutorialFactor.run(config)
`;

export const operatorDescriptions = {
  add: "逐元素相加；可与有限常数组合。",
  subtract: "逐元素相减。",
  multiply: "逐元素相乘。",
  divide: "逐元素相除；分母为零时结果不可用。",
  abs: "逐元素取绝对值。",
  log: "自然对数；输入必须大于零。",
  sqrt: "平方根；输入必须大于或等于零。",
  power: "实数幂；负底数要求整数指数，零的负次幂不可用。",
  signed_power: "保留输入符号：sign(x) × abs(x)**y。零的非负次幂返回零。",
  sign: "将有限输入映射为 -1、0 或 1。",
  reverse: "逐元素取相反数。",
  inverse: "取倒数；零输入不可用。",
  min: "取两个表达式的逐元素较小值。",
  max: "取两个表达式的逐元素较大值。",
  and: "逐元素逻辑与；条件两边加括号，例如 (x > 0) & (y > 0)。",
  or: "逐元素逻辑或；条件两边加括号，例如 (x > 0) | (y > 0)。",
  not: "逐元素逻辑非；使用 ~(x > 0)。缺失条件仍不可用。",
  equal: "逐元素判断相等。",
  not_equal: "逐元素判断不相等。",
  greater: "逐元素判断大于。",
  greater_than_equal: "逐元素判断大于等于。",
  less: "逐元素判断小于。",
  less_than_equal: "逐元素判断小于等于。",
  if_else: "有限非零条件选 yes，零条件选 no；只要求选中的分支有效。缺失条件不可用。",
  is_nan: "已观测 NaN 返回 1，有限值返回 0；Inf、缺 Bar 和尚未预热不伪装成可用的 NaN 观测。",
  ts_mean: "单只股票的窗口算术均值。",
  ts_sum: "单只股票的窗口求和。",
  ts_std_dev: "单只股票的窗口标准差；ddof=0 为总体口径。",
  ts_delay: "取该股票 period 期前的值。",
  ts_delta: "当前值减 period 期前的值。",
  ts_av_diff: "当前值减窗口均值。",
  ts_zscore: "当前值减窗口均值，再除窗口标准差；零标准差不可用。",
  ts_scale: "窗口极差缩放后可加constant；constant新参数入口默认交易日槽，旧普通调用保持兼容。",
  ts_decay_linear: "窗口线性加权；显式dense=False按0计NaN，dense=True仅对有限分量重归一，新参数入口默认交易日槽。",
  rank: "同日截面排名。推荐 rank_unit(x)：端点映射 0..1，并列取平均名次；单样本/全相等为 0.5。旧 rank(pct=True) 是平均名次/N。",
  zscore: "同日截面减均值后除总体标准差；常量或单样本截面不可用。",
  normalize: "截面减均值；useStd/use_std可再标准化，正limit裁剪结果。",
  scale: "按L1缩放；可用scale总系数，或longscale/shortscale分别控制正负两侧。",
  group_rank: "按同轴分组标签计算组内 0..1 排名；并列取平均名次，单样本为 0.5。",
  group_neutralize: "减去各自组内均值。",
  "densify": "按同日有限唯一值升序编码为0..N-1，缺失保留；编码随截面变化，不是永久证券或行业编号",
  "days_from_last_change": "有限值首次出现或变化时为0，连续相同值逐帧递增；默认日历模式的缺失会断开连续段",
  "hump": "阈值=hump*当日有效截面abs_sum；小变化保持，大变化每期最多移动阈值，按股票保存历史输出",
  "kth_element": "从最近向最旧取第k个未被ignore排除的观测，不是窗口第k小；ignore可指定NaN和有限数值",
  "last_diff_value": "窗口内从近到远找最近一个与当前值不同的有限值；当前缺失或无不同值则不可用",
  "ts_arg_max": "返回极大值距当前帧的距离，当前为0，并列选择最近一次",
  "ts_arg_min": "返回极小值距当前帧的距离，当前为0，并列选择最近一次",
  "ts_backfill": "窗口包含当前点，按从近到远取第k个有限值；找不到则不可用",
  "ts_corr": "窗口内成对有限样本的Pearson相关；至少2对且双方方差为正，否则不可用",
  "ts_count_nans": "统计窗口NaN槽数；Inf不计作NaN并使该次结果不可用",
  "ts_covariance": "成对有限样本的总体协方差，分母为有效配对数N；没有配对不可用",
  "ts_product": "要求窗口所有值有限后相乘；NaN、Inf或乘积溢出不可用",
  "ts_quantile": "p=(当前值之前的较小样本数+相等样本数/2)/有效N，再按driver逆分布映射",
  "ts_rank": "当前值的窗口端点排名，平均并列名次映射0..1后加constant；单样本为0.5+constant",
  "ts_regression": "输入顺序为y,x；正lag配对当前y与过去x。rettype0..9；MSE分母N-2，需N>2",
  "ts_step": "按程序收到的帧数输出步长倍数，从第一帧的step值开始，不是自然日期编号",
  "quantile": "截面端点rank r映射p=1/N+r*(1-2/N)，再取driver逆分布并乘sigma",
  "winsorize": "按有效截面总体均值和标准差将数值裁剪到mean±std*sd，保留原证券轴",
  "vec_avg": "逐证券对分量表达式求均值，跳过NaN分量；没有有限分量或含Inf则不可用，不沿股票轴求均值",
  "vec_sum": "逐证券对分量表达式求和，跳过NaN分量；没有有限分量或含Inf则不可用，不沿股票轴求和",
  "bucket": "buckets阈值右闭；range模式默认隐藏区间外桶且右闭，起点保留为首桶。NaNGroup可输出独立缺失桶",
  "trade_when": "exit>0优先清空；否则trigger>0更新为value，其余保持上次输出；首次无触发保持不可用",
  "group_backfill": "当前值有限则保留；缺失时用当前分组对应的历史窗口有限样本作winsorized算术均值补全",
  "group_mean": "按非负有效权重计算组内加权算术均值；有效权重和须为正，不是调和均值",
  "group_scale": "组内按(min,max)极差映射0..1；常量和单样本组不可用",
  "group_zscore": "组内使用总体标准差做标准分；常量和单样本组不可用",
};

export const settingHints = {
  type: "当前使用 REGULAR 单因子研究；SUPER 需要尚未接入的选择与组合合同。",
  base: "研究起止日期包含边界，只支持日线；预热由窗口自动准备。",
  data: "选择已发布行情矩阵或合成数据与股票池；因子统一使用当前信号日基准的前复权输入，提交固定快照，不导入或更新行情。",
  factor: "传给因子类构造函数。例如 period=20 对应 __init__(period=20)。",
  analysis: "控制 IC、分组、多期收益和报告。与 settings 的 Alpha 权重变换分开。",
  decay: "Alpha 目标生成前的线性衰减：0 关闭、1 恒等、2..512 越近期权重越高。它与 analysis.decay_steps 的预测力诊断不同。",
  neutralization: "NONE 不减均值；MARKET 对当日有效截面减算术均值，随后 L1 归一。行业和风险模型选项缺数据。",
  truncation: "0 关闭；正数限制单只股票的绝对目标权重，未分配的 gross 保留。它不是因子值去极值。",
  delay: "当前只支持 1：信号日收盘形成因子，收益从下一交易日起计算。",
  testPeriod: "从研究结束日向前划出测试期，例如 P30D、P2W、P1Y6M；P0Y0M 不划分。",
  visualization: "True 展示图表；False 关闭图表，数值与结果导出仍保留。",
  universe: "跟随 data.universe；可以是明确证券列表或 all_a_dynamic。TOPxxx 需要另有合格股票池。",
  pasteurization: "ON 只在研究池上计算因子截面；OFF 使用快照中当时存续的更大证券轴计算因子，最终研究与收益仍限于所选研究池。合成 fixture 没有额外池外数据。",
  nanHandling: "当前只支持 OFF；缺失保持不可用，不把最后一步填零当成算子级 NaN 处理。",
  unitHandling: "当前只支持 OFF；VERIFY 所需的算子量纲验证尚未接入。",
  maxTrade: "当前只支持 OFF；ON 所需成交容量规则和数据尚未接入。",
  maxPosition: "当前只支持 OFF；ON 所需仓位约束规则和数据尚未接入。",
  simulationMode: "当前只支持 FULL；QUICK 没有已支持的执行合同。",
  instrumentType: "当前数据与研究范围是股票 EQUITY。",
  region: "当前数据与研究范围是中国市场 CHN。",
  language: "使用 Pool 的 PythonIndicator 类与 config 脚本。",
};
