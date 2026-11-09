# FactorPool 协作约定

先读 README、docs/ARCHITECTURE.md 和参考文档，再检查本仓及共享 FactorTrader 子模块的 HEAD/status。

- FactorPool 是因子研究工具。研究核直接处理日期 × 证券矩阵，不构建账户、订单、撮合、交易 Runtime 或第二个 EngineRunner。
- PythonIndicator、TS/CS 数值和编译器只有 shared/factortrader 指向的一份实现；本仓只做输入/轴适配和研究统计。改共享能力，在 FactorTrader 的隔离 worktree 提交并固定子模块版本，保留主工作区其他改动。
- 算子先确定合同，再依次检查 Python、固定 LEAN、已核验 BRAIN。默认值、并列、缺失、零、预热和日历窗口不同不能宣称同名等价。每次接线同步 docs/operators.json 和独立参考/两端对账。
- FactorPool 持久数据放 `/home/quant/colorful-data/FactorPool-data`，不在 dev 下创建 FactorPool-data；PostgreSQL 只服务行情/研究历史，不开发产品账号、注册和登录模块。
- 行情只读。下载、补数、更新、重建与调度须用户当次限定授权；部署权限不包含行情更新。
- 未知行业/财务/向量数据明确拒绝；不可用结果为 null，不填0。普通 Python 回调明确标记 python；不支持的 TS/CS 不降级重跑。
- 任务源码/参数/版本快照不可变，必要结果在同一事务完成后才发布成功；失败、取消、中断保留可见记录。
- 启动、构建、检查和部署统一使用 Compose；Python 环境来源、共享提交和实际二进制指纹须记录。临时产物放 build/，服务数据放配置指定的独立路径，不入 Git。
- 验证后仅提交本任务路径，按已登记 GitHub remote 正常 push 并核验；不覆盖并行工作。源码、知识库、部署和浏览器验收分别报告。
