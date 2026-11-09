# 矩阵研究核心性能

目标是快速计算因子矩阵、未来收益、IC/Rank IC、分组与研究组合，仍使用唯一的共享 PythonIndicator/TS/CS 实现。
本轮优化 FactorPool 的矩阵统计与报告编排，不引入账户/订单/撮合，不修改 FactorTrader 子模块。

## 开源/公开项目参考

2026-11-09 查阅下列官方文档与源码，只借鉴计算组织方式，没有复制或新增这些项目作为运行依赖。

| 项目与一手来源 | 可借鉴方式 | 本轮落点 |
| --- | --- | --- |
| [Polars 优化器](https://docs.pola.rs/user-guide/lazy/optimizations/) / [许可证](https://github.com/pola-rs/polars/blob/main/LICENSE) | 公共子计划复用、只物化需要的列和结果；MIT | 一个任务只准备一次矩阵；共享因子截面排序；诊断只生成需要的 IC 序列 |
| [Qlib rolling.pyx](https://github.com/microsoft/qlib/blob/main/qlib/data/_libs/rolling.pyx) / [许可证](https://github.com/microsoft/qlib/blob/main/LICENSE) | 类型明确的原生数值循环，Python组织计算；MIT | 统计仍在 C++，Python继续拼装报告和调用现有SciPy尾概率 |
| [vectorbt](https://vectorbt.dev/) / [组合实现说明](https://vectorbt.dev/api/portfolio/base/) / [许可证](https://vectorbt.dev/terms/license/) | 数组批量执行、减少跨层开销与不必要的大数组 | 保持简单矩阵研究架构，复用任务输入；不引入其交易模拟器或复制代码 |

vectorbt 当前许可证是 Apache 2.0 加 Commons Clause，不能按无附加限制的 Apache 2.0 项目对待；这里将其列为公开架构参考。
本轮不增加 Numba JIT、Polars、Qlib 或新的 Python 环境，继续使用已固定的 FactorTrader 环境与编译器。

## 原始瓶颈与实现

504×512、3个持有期、10阶衰减的原实现剖析中，`ic_decay` 约占0.926秒矩阵计算的0.639秒；
4次 `analyze` 占0.198秒。该剖析仅用于定位，不作为最终A/B加速数字。

1. 原主报告及每个持有期的分析、组合、衰减都会从 Python 序列重新转换三块矩阵。新增任务局部 `ResearchInput`，复制/校验一次，所有报告分支复用。
2. 原来每个滞后周期重新排序因子值。现在每个日期按“因子值、原证券顺序”稳定排序一次，供多个周期和组合复用。
3. 每次 IC 仍按当期实际未来价格过滤配对样本，再从共享排序中线性筛选、重新计算并列平均名次。不会把全截面的排名用于缺价格后的样本。
4. 一期主分析直接提供一期 IC；其他周期调用仅返回 IC/Rank IC 的 `ic_series`，不再生成随后丢弃的全部因子收益/分组大数组。最终公开报告和一期明细保持完整。
5. 仅计算平均名次的排序使用 `std::sort`：相同值的内部顺序不影响平均名次。分组依赖的稳定排序保持，累加顺序与浮点公式保持。

输入副本和排序均由单个任务进程持有，无跨任务行情缓存或全局可变状态。修改调用方列表不会改变已准备的研究输入。
没有 fast-math、截断结果、降低精度、匹配策略名称/证券/日期/窗口、改变收益或缺失值定义。

## 结构与边界

| 旧路径 → 新路径 | 职责与原因 |
| --- | --- |
| `analysis.cpp/decay.cpp/portfolios.cpp` 各自转换矩阵/排序 → 新增 `research_input.hpp/.cpp` | 统一输入所有权、轴校验、稳定截面排序和配对样本排名；三类统计只保留各自算法 |
| `reporting/diagnostics.py` 多次完整 analyze → 复用 prepared 输入、一期结果与原生 ic_series | 避免重复转换、排序及无用中间结果；公开报告字段保持 |
| `runner.py` 分开传三矩阵 → 准备一次、传给主分析和诊断 | 保留所有权到任务结束，输入准备计入分析耗时 |
| 新增 `compose.benchmark.yaml`、`scripts/benchmark_core.py`、`run_core_benchmarks.py` | 离线、固定资源、串行A/B与完整结果哈希验收 |
| 新增 `scripts/research_oracle.py`、`tests/test_prepared_research.py` | 固定旧镜像的完整输出对照、独立SciPy排名、缺失/并列与输入生命周期验证 |

旧 `analyze`、`portfolio_report`、`ic_decay` 的列表调用入口继续可用；`prepare_research`、`ic_series` 是新增内部入口。
报告仍为版本2。旧任务的源码、值与结果不重算、不改写。

## 复现与测量口径

```bash
# 已有旧镜像作为oracle，新镜像按常规Compose构建。
python3 scripts/build_info.py
FACTORPOOL_RELEASE=20261109-r4 docker compose build backend
python3 scripts/run_core_benchmarks.py --candidate factorpool/backend:20261109-r4 --repeat 3

# 单个镜像的完整结果对照；另一次改为候选镜像和输出名后比较JSON。
FACTORPOOL_BENCH_IMAGE=factorpool/backend:20261109-r2 \
  docker compose -f compose.benchmark.yaml run --rm --entrypoint python3 benchmark \
  /bench/scripts/research_oracle.py baseline-oracle
```

基准是没有网络、行情和服务状态目录挂载的独立 Compose 容器，输入是可重现的合成矩阵。
两端固定1个逻辑CPU、CPU亲和0、4GiB内存、相同Python/NumPy/FT提交。每个用例先旧版再新版，串行执行，每端3次。
每次重新准备任务输入；计时包括原生输入准备、一期完整结果及全部1/5/20期报告、10阶衰减、5组多空/组合。
输入矩阵生成、Python/科学库导入、完整JSON规范化哈希在计时区间之外；两端使用相同边界。
内存是整个进程的峰值RSS，包含输入生成和哈希，不等同于仅内核额外内存。
这是内存矩阵阶段比较，不是读取行情、Indicator推进、JSONL导出、HTTP排队或数据库入库的完整任务耗时。
没有读行情文件，不涉及冷/热行情页缓存；同进程重复与首次调用样本均保留。共享主机、未锁定CPU频率，不把本机结果推广为所有硬件的固定延迟。

原始耗时、资源、二进制和完整结果哈希见随本页提交的性能证据；剖析和详细日志位于忽略的 `build/performance/`。

## 2026-11-09 实测结果

单元格是日期×证券，表中为3次中位数；每组完整输出哈希均与旧版相同。

| 矩阵 | 数据 | 旧版秒 | 新版秒 | 倍率 | 旧/新峰值RSS MiB |
| --- | --- | ---: | ---: | ---: | ---: |
| 128×64 | 稠密 | 0.0359 | 0.0239 | 1.50× | 118.7 / 116.8 |
| 128×64 | 缺失/并列/零/动态成员 | 0.0320 | 0.0206 | 1.55× | 117.2 / 116.5 |
| 504×512 | 稠密 | 1.1812 | 0.6216 | 1.90× | 203.1 / 174.2 |
| 504×512 | 缺失/并列/零/动态成员 | 0.9325 | 0.5239 | 1.78× | 197.0 / 169.7 |
| 1000×2000 | 稠密 | 10.3128 | 5.3622 | 1.92× | 863.8 / 570.9 |
| 1000×2000 | 缺失/并列/零/动态成员 | 7.6309 | 4.3983 | 1.73× | 825.7 / 552.2 |

[完整性能证据](performance/20261109-matrix-core.json)保留全部采样、二进制摘要、构建版本和20组oracle哈希。

## 数值与流程验证

- 17项完整Python/C++测试通过：包含原14项，增加实际配对样本的独立SciPy Rank IC、任务输入快照/隔离、完整报告复用与轴校验。
- 固定旧镜像与最终新镜像20组完整结果哈希一致，覆盖稠密/缺失、并列/有效零、不同分组数、ddof、1/2/5/20/252期、空矩阵/空列及极端价格。
- 单独验证全部基准用例的主结果与完整诊断哈希，不只比较均值或抽样字段。

## 后续按剖析继续处理的部分

共享 Indicator 输入适配、真实行情读取、values 导出和数据库导入属于其他阶段，应分阶段测量。
并行/SIMD、零拷贝buffer输入、批量跨因子复用、增量结果缓存尚未接入；不把参考项目支持的功能当成当前实现。

## 本次部署与流程验收

- 已部署后台 `factorpool/backend:20261109-r4`，前端继续为 `factorpool/frontend:20261109-r3`。线上原生二进制SHA-256与性能测试候选完全相同：
  `b19c2530d587eaaba51cf59ef3990f5fe2f81aa413d4586db9811edd47a16bd3`。
- 当前后台构建摘要为 `93dc101f1112abb76cd3fc0caaa1e0a52173aa9136a45e421db5d03dc0203f55`，FT仍固定 `a847a662`。
- HTTP/数据库回归已通过：版本、不可变快照、分页、批量、失败、排队与运行取消；已有结果完整哈希未变。
- 后续存储切换后再次通过桌面/手机浏览器样例与完整报告检查：1/5/20期、分组、多空、衰减和缺失值均可用，未出现脚本异常或手机横向溢出。
- localhost与当前LAN健康/前端摘要检查通过，Tailscale本机转发路径健康200/UP。未将本机自检称为独立外部客户端验收。
- 前端未重构建，`ui-build.json` 的 `backend_build` 是r3前端包构建时的后台引用，实际后台版本以任务结果 `metadata.build` 为准；报告API仍为版本2。

回退后台时先等待队列为空，保留当前 `.env` 数据根与数据库，前端继续沿用r3：

```bash
FACTORPOOL_RELEASE=20261109-r2 docker compose up -d --no-build --no-deps --wait backend
docker compose exec -T frontend nginx -s reload
```

回退后更新本地后台release配置；不删除历史、不回退存储目录、不重建数据库。
