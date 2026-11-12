// User-facing examples and descriptions for the implemented Pool contract.
export const quickStartCode = `from factorpool import PythonIndicator

config = {
    "type": "REGULAR",
    "base": {
        "start_date": "2024-01-04",
        "end_date": "2024-01-08",
        "frequency": "1d",
    },
    "data": {"provider": "fixture", "universe": "fixture"},
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
  ts_scale: "(当前值 - 窗口最小值) / (窗口最大值 - 窗口最小值)。",
  ts_decay_linear: "窗口线性加权，越近的输入权重越高。调用 self.ts.decay_linear(x, period)。",
  rank: "同日截面排名。推荐 rank_unit(x)：端点映射 0..1，并列取平均名次；单样本/全相等为 0.5。旧 rank(pct=True) 是平均名次/N。",
  zscore: "同日截面减均值后除总体标准差；常量或单样本截面不可用。",
  normalize: "当前有效截面逐元素减去同日算术均值。",
  scale: "将截面按绝对值和缩放。",
  group_rank: "按同轴分组标签计算组内 0..1 排名；并列取平均名次，单样本为 0.5。",
  group_neutralize: "减去各自组内均值。",
};

export const settingHints = {
  type: "当前使用 REGULAR 单因子研究；SUPER 需要尚未接入的选择与组合合同。",
  base: "研究起止日期包含边界，只支持日线；预热由窗口自动准备。",
  data: "选择已发布行情矩阵或合成数据、股票池与价格基准；提交固定快照，不导入或更新行情。",
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
