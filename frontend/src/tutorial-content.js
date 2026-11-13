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
