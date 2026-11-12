// Core catalog follows the BRAIN reference inventory; categories do not alter calculations.
export const operatorCategories = [
  {
    "id": "arithmetic",
    "label": "算术算子",
    "english": "Arithmetic",
    "description": "算术类的BRAIN参考算子，按当前项目的实际支持状态列出。"
  },
  {
    "id": "logical",
    "label": "逻辑算子",
    "english": "Logical",
    "description": "逻辑类的BRAIN参考算子，按当前项目的实际支持状态列出。"
  },
  {
    "id": "ts",
    "label": "时序算子",
    "english": "Time Series",
    "description": "时序类的BRAIN参考算子，按当前项目的实际支持状态列出。"
  },
  {
    "id": "cs",
    "label": "截面算子",
    "english": "Cross Sectional",
    "description": "截面类的BRAIN参考算子，按当前项目的实际支持状态列出。"
  },
  {
    "id": "vector",
    "label": "向量算子",
    "english": "Vector",
    "description": "向量类的BRAIN参考算子，按当前项目的实际支持状态列出。"
  },
  {
    "id": "transform",
    "label": "转换算子",
    "english": "Transformational",
    "description": "转换类的BRAIN参考算子，按当前项目的实际支持状态列出。"
  },
  {
    "id": "group",
    "label": "分组算子",
    "english": "Group",
    "description": "分组类的BRAIN参考算子，按当前项目的实际支持状态列出。"
  }
];

const names = {
  "arithmetic": [
    "abs",
    "add",
    "densify",
    "divide",
    "inverse",
    "log",
    "max",
    "min",
    "multiply",
    "power",
    "reverse",
    "sign",
    "signed_power",
    "sqrt",
    "subtract"
  ],
  "logical": [
    "and",
    "if_else",
    "less",
    "less_than_equal",
    "equal",
    "greater",
    "greater_than_equal",
    "not_equal",
    "is_nan",
    "not",
    "or"
  ],
  "ts": [
    "days_from_last_change",
    "hump",
    "kth_element",
    "last_diff_value",
    "ts_arg_max",
    "ts_arg_min",
    "ts_av_diff",
    "ts_backfill",
    "ts_corr",
    "ts_count_nans",
    "ts_covariance",
    "ts_decay_linear",
    "ts_delay",
    "ts_delta",
    "ts_mean",
    "ts_product",
    "ts_quantile",
    "ts_rank",
    "ts_regression",
    "ts_scale",
    "ts_std_dev",
    "ts_step",
    "ts_sum",
    "ts_zscore"
  ],
  "cs": [
    "normalize",
    "quantile",
    "rank",
    "scale",
    "winsorize",
    "zscore"
  ],
  "vector": [
    "vec_avg",
    "vec_sum"
  ],
  "transform": [
    "bucket",
    "trade_when"
  ],
  "group": [
    "group_backfill",
    "group_mean",
    "group_neutralize",
    "group_rank",
    "group_scale",
    "group_zscore"
  ]
};
const categoryByName = new Map(Object.entries(names).flatMap(([category, entries]) => entries.map(name => [name, category])));
export function operatorCategoryOf(operator) { return categoryByName.get(typeof operator === "string" ? operator : operator?.name) || null; }
