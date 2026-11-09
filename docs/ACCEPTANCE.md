# 2026-11-09 本地 Docker 部署验收

这是本轮执行证据；当前运行状态仍须实际探测。代码提交、Docker运行、数值验证和官方算子兼容分别记录。

## 运行组合

- Compose项目 `factorpool`，三个独立容器：backend、frontend、db；不改 FactorTrader 的运行服务或混合主工作区。
- FT共享源：`a847a662aa8f7b2ee7dd4beaaa734d79537ba2d3`（`feat/factorpool-indicator` 分支，已推送；未合并FT主分支）。
- 环境：继承 `factortrader/platform:20261105-factortrader-48804c66-r1` 的 Python3.11.16及研究依赖；新指标与研究模块由固定源码重新编译。
- 镜像标签：`factorpool/backend:20261109-r1`、`factorpool/frontend:20261109-r1`；数据库 `pgvector/pgvector:pg17` 使用普通PostgreSQL表，不依赖向量扩展。
- 实际源码清单SHA及父环境镜像ID记录在 `docker/build-info.json`，并由每次任务结果metadata带回。
- 默认数据目录 `/home/quant/dev/FactorPool-data`；已有行情 `/home/quant/quant_data/mmap` 只读挂载。本轮没有下载、更新或重建行情。

## 访问与探测

- Tailnet主要入口：[FactorPool](http://master-2019.tailaeed46.ts.net:8088)，同一节点IPv4 `100.109.63.80:8088`。
- 当前局域网入口：[FactorPool LAN](http://192.168.1.6:8088)，LAN IP可能变化，不当作固定地址。
- `GET /health`：localhost、当前LAN地址与Tailscale userspace `nc` 自节点HTTP请求均返回200 / UP。
- Tailscale仅新增8088 TCP转发到127.0.0.1:8088；80、445、4173原规则保留。自节点探测不等于其他终端外网验收。

## 已运行验证

| 验证 | 实际结果 |
| --- | --- |
| 既有FT指标回归 | 10组CTest通过：原生指标、Graph、Vector、Runtime、案例、LEAN迁移/合同、batch编译/执行及TS/CS交替 |
| 新共享算子 | 7个测试方法，覆盖新增21个TS/CS入口及abs/min/max、并列/零/缺失、常量、零分母、动态轴、重置、多次交替；CTest通过 |
| Pool矩阵研究/对账 | 7个测试方法通过，含FT逐值/状态、缺Bar、有效0、动态轴、24期累计预热、真实Python回调及禁止创建Engine |
| 手算样例 | IC=1、Rank IC=1；三组-10%/0%/10%；单日样本ICIR=null |
| 独立统计参考 | NumPy核对相关/并列；IC序列0.1/0.2/0.3的ddof=1 ICIR=2；无效与零分母不填0 |
| 真实行情小样本 | 3只证券、2024-01-29至02-02，5个有效IC日期，15对样本，覆盖率1 |
| 真实历史动态池 | 2024-01-29的全A动态成员，5104对收益样本；只读现有Data Mod，结果入库 |
| HTTP与PostgreSQL | 版本递增/源码快照、结果分页、批量任务、非法参数、算子准备失败、排队取消与运行进程停止均通过 |
| 异常重启 | 对本项目backend进行KILL后恢复；未完成任务变INTERRUPTED，已成功任务完整JSON与重启前相同 |
| 浏览器E2E | Chromium桌面1512×1050、手机390×844；实际点击运行、保存、因子库、179项目标台账、刷新恢复、无横向溢出、无脚本异常 |

可重放命令见README、`scripts/smoke.py`、`tests/browser.mjs`；本机原始构建/回归日志与浏览器截图在Git忽略的 `build/`。
Dockerfile本身运行矩阵/对账测试后才生成运行镜像。性能阶段耗时只属于这些样本，不构成四年交易回测或普遍性能结论。

## 当前缺口

目标台账179个名称中，46个有共享原生映射；其他项目保留待实现/待核验，不能称全量BRAIN兼容。
BRAIN官方行为尚未有oracle对账；rate=2、dense、官方日历窗口及高级/SuperAlpha仍待核验。
原生分组节点可用，但真实行业/财务/向量Provider、分钟/调整价研究、外部指标依赖与增量重算未接入。
FT快照对不可用原因的精细枚举尚未完整暴露，Pool保留native_unavailable，不猜为过滤或运算错误。
研究没有账户、委托、撮合或交易事件循环；分组结果是按所选价格和权重得到的无费用研究统计。
