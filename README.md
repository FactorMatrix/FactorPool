# FactorPool

因子研究工作台：Vue 3 + CodeMirror、一个 Spring Boot 后台、PostgreSQL、Python 因子入口与 C++ 矩阵统计。
因子沿用 `factortrader.PythonIndicator` 和 `self.ts/self.cs`。研究运行不创建 Engine/Runtime，也没有交易账户、订单或撮合。

## Docker 启动

本机默认端口 **8088**。数据库不暴露宿主端口，网页 `/api` 走同源代理，无登录页。

```bash
cd /home/quant/dev/FactorPool
git submodule update --init shared/factortrader
python3 scripts/configure.py
python3 scripts/build_info.py
# 沿用已核验的 FactorTrader Python 3.11.16 研究环境镜像。
docker image inspect factortrader/platform:20261105-factortrader-48804c66-r1 >/dev/null
docker compose build
docker compose up -d --wait
curl --fail http://127.0.0.1:8088/health
```

浏览器打开 `http://127.0.0.1:8088`。默认载入 LowVolumeFactor；点击“运行研究”，手算样例 IC=1、Rank IC=1、
三组收益 -10%/0%/10%，单日 ICIR 为 `--`。样例明确标为合成数据。
真实行情选择“FactorTrader 日线 · 只读真实行情”，输入证券代码或历史动态全 A 股、日期后运行。
后台按因子预热量读此前交易日，并多读两天开盘价用于收益，未来数据不进入 Indicator。

```bash
# 测试矩阵统计、共享值/状态、Python 回调及禁止创建 Engine。
docker compose exec backend python3 -m unittest discover -s /opt/factorpool/tests -p test_research.py -v
# 真实 HTTP/数据库、版本、批量、失败、排队及运行取消。
python3 scripts/smoke.py --base http://127.0.0.1:8088
# 停机保留 PostgreSQL 和任务目录。
docker compose down
```

`.env` 由脚本首次生成且不覆盖，权限0600，不提交密码。默认状态目录 `/home/quant/dev/FactorPool-data`；
市场目录 `/home/quant/quant_data/mmap` 以只读挂载。可首次通过 `--state-dir/--market-root/--release` 指定，
已有配置直接编辑 `.env`，随后 `docker compose up -d`。不对数据做下载、更新、重建或定时刷新。
环境基线镜像须已存在；可通过 `FT_ENV_IMAGE` 指定兼容镜像。构建从固定共享源码重编指标，绝不使用旧应用里的指标二进制。

## 实际支持范围

- 研究内核：日期 × 证券的因子、开盘价和历史成员矩阵；C++ 计算 T+1→T+2 开盘收益、Pearson IC、平均并列 Rank IC、
  未年化 ICIR（ddof=0/1）、等权分组和非重叠一期累计。没有完整交易回测。
- 指标端：共享 FT 编译器、原生 Graph、TS/CS 多次交替、历史预热、动态轴、缺 Bar/有效零；普通单股 Python 回调明确显示 python。
- 本次共享新增：8个 TS 组合入口、13个截面/分组入口和 abs/min/max 原生表达。原有 rank/pct 默认语义保持。
- 因子/版本、任务不可变快照、历史、日志、排队、停止、超时、中断恢复、结果事务入库、分页与多因子批次。
- [算子台账](docs/operators.json)登记179个目标名称；46个目标已接线到共享能力，其余保留明确缺口。
  “已接通”仅指当前 FT/Pool 合同，**不代表 BRAIN 全量官方兼容**。

当前只支持日线、raw、开盘一期收益和等权组；调整价、其他收益间隔、分钟研究、财务/行业/向量 Provider、
外部指标依赖和增量复算尚未接入。不支持的参数/字段明确拒绝。rank_unit 的单值=0.5、并列平均；
BRAIN rate=2/dense/日历窗口等需要官方执行器对账，不能用原 FT pct 冒充。
已保存结果不受后续草稿编辑影响；关闭网页可从测试记录查看。浏览器只保存布局与最近任务ID。

## 目录与源码入口

| 路径 | 职责 |
| --- | --- |
| `shared/factortrader` | 固定 Git 子模块；公共因子类、编译器与唯一 C++ 指标实现 |
| `src/factorpool/adapter.py` | 加载共享 Graph、对齐轴及适配输出，不实现 TS/CS 算法 |
| `src/factorpool/runner.py` | 单任务研究进程与不可变结果文件 |
| `research-core-cpp/src/analysis.cpp` | 矩阵收益、IC/Rank IC/ICIR、分组 |
| `research-core-cpp/src/provider.cpp` | 既有 FT 日线 Data Mod 的只读适配 |
| `backend/` | Spring API、PostgreSQL、版本与队列/进程控制 |
| `frontend/` | Vue 编辑器、配置、结果、历史与兼容台账 |
| `tests/` / `scripts/smoke.py` | 数值/跨项目对账与 HTTP 闭环验证 |
| `参考文档/` | 用户提供的目标规范，未实现目标不等于当前能力 |

更多边界与口径见 [架构](docs/ARCHITECTURE.md)、[API](docs/API.md)、[部署验收](docs/ACCEPTANCE.md)。
