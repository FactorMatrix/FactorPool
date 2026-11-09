# 矩阵研究与共享指标

浏览器 → Spring 后台 → 一个研究进程 → C++ 指标图 / C++ 矩阵统计；完成后一个 PostgreSQL 事务发布结果。

研究内核只接收 `factor[T,N]`、`open[T+future,N]`、`member[T,N]` 三个矩阵和分组/相关参数。
没有现金、持仓、订单、撮合、费用、交易时钟或策略生命周期。统计一次批量调用，不逐单元 HTTP。

指标计算直接加载固定 FT 的 Graph binding，由 PythonIndicator 编译器一次准备，按完整日截面推进现有 C++ Graph。
这里的逐日输入只为保留同一 TS/CS 窗口与截面封口合同，不运行 EngineRunner，也不构造 FT Engine/Runtime。
矩阵统计与指标公式独立；因子文件可在固定版本 FT SDK 中原样注册。

当前 FT 的绑定/Provider 链接仍以既有 SDK 整库构建，所以构建依赖含 SDK/Core；
这不意味着研究会执行交易代码。没有复制第二套指标/交易执行器，也没有假称已经拆成完全独立的轻量发行包。
共享算子源于 FT 分支 `feat/factorpool-indicator`，子模块锁定实际提交，主工作区原有未提交改动保持。

## 数据与数值合同

- 日线 date 是交易日键，Graph 输入用上海时区同日15:00完成时刻；不支持分钟或跨日会话。
- 缺 Bar 不推进对应 TS 状态；真实0保留。外部/未来财务字段没有 Provider 就报错，不从当前分类伪造历史。
- 窗口按有效输入计数；读 `warm_up_period-1` 个此前交易日，不保证缺数据证券自动补满，是否可用仍以实际 ready 为准。
- 动态全 A 股成员来自同一 Data Mod 的历史 instruments 查询；移出释放窗口，再入重新开始。
- 因子矩阵只有请求日期；收益价多读2个未来交易日，未来帧不投递给指标。
- 价格为 raw、T+1开盘入场、T+2开盘退出，入/出价格不正或不足为无效。未计费用、滑点与成交限制。
- IC 仅用成对有效样本，默认至少3对；常量或不足返回null。Rank IC使用相同样本的平均并列名次。
- ICIR=有效日IC均值/时间标准差，默认样本ddof=1，不年化；不足或0标准差为null。
- 组按因子升序、再按证券代码稳定排序；并列可拆到不同组。索引 k 分到 floor(k*G/N)，不足时允许空组。
- 组成员由因子决定，收益缺失不改变组标签；组收益只对组内收益可用项等权，并保留成员数和实际收益数。
- 一期收益互不重叠；累计一旦出现中途缺期，后续累计保留null，避免隐含补零。高低组仅是研究收益差。
- 覆盖率分母是历史成员矩阵中的实际在池项；动态池外不稀释覆盖率。
- 输出 valid 表示研究可用，ready保留原生快照；reason 区分 ok、missing_bar、source_missing、not_ready、native_unavailable、outside_universe。
  现有 FT 快照不含逐节点错误枚举，因此 native_unavailable 不强行猜为过滤或公式错误，详细诊断仍待共享组件扩展。

## 版本与运行控制

任务保存源码、构造参数、研究参数；结果记录源hash、FT精确提交、参考LEAN、环境来源、合成标识、数据快照指纹和阶段耗时。
真实数据指纹由现有manifest哈希、文件大小和mtime组成；任务结束复核，不是全量行情内容哈希，也不支持运行中原地改快照。
默认输入上限200万 date×symbol 单元、单并发、超时1800秒；Compose总体CPU/内存限制与队列并发是不同限制。
普通 Python 因子需要可信作者；镜像环境不是不可信代码的隔离沙箱。

任务 RUNNING 只代表进程执行；文件清单、汇总和所有 values 导入成功，事务才置 SUCCEEDED。
失败回滚入库，取消确认进程终止后收口，后台重启遗留 RUNNING/CANCELLING 标 INTERRUPTED；QUEUED 可继续。
结果与价格只存在任务进程/有界任务输入里；原始行情缓存仍由 Data Mod 管理。

## 来源与新增结构

FactorPool 原先仅 `参考文档/`，其文件原位保留。新增 `shared/` 固定共享实现、`src/` 输入适配、
`research-core-cpp/` 矩阵统计、`backend/` 任务持久化、`frontend/` 工作台、`docker/` 与 `compose.yaml` 部署。
FT 原目录不搬迁；新增 `sys_indicator/src/batch_program/cross_section.cpp` 承担截面/组数值，
既有 compiler/domain 连接共享入口。SplitPane 从 FT 组件提取，布局存储前缀改为 FactorPool。

[官方 BRAIN 算子页](https://platform.worldquantbrain.com/learn/operators)本次仅可取得 JavaScript 应用壳，
没有获得可执行语义/权限版本或官方oracle。因此台账保留“官方行为待核验”，不以名称清单宣称兼容完成。
