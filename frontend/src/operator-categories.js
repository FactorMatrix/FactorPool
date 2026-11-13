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

const categoryIds = new Set(operatorCategories.map(category => category.id));
export function operatorCategoryOf(operator) {
  return categoryIds.has(operator?.category) ? operator.category : null;
}
