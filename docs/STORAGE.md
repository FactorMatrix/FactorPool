# FactorPool 存储与数据库范围

当前持久数据根为 `/home/quant/colorful-data/FactorPool-data`，位于 SATA SSD `/dev/sda1`（ext4）。
`/home/quant/dev/FactorPool-data` 已按用户要求删除，不保留目录或兼容软链接，也不再由默认配置创建。

| 位置 | 职责 |
| --- | --- |
| `FactorPool-data/postgres/` | PostgreSQL 数据目录，仅数据库容器使用 |
| `FactorPool-data/tasks/` | 任务请求/源码快照、运行日志、结果与值文件 |
| `/home/quant/dev/FactorPool/` | 程序、配置模板与代码测试；`.env` 权限0600且不入Git |

`.env` 的 `FACTORPOOL_STATE_DIR` 已切换到新根；Compose 将对应子目录挂载到
`/var/lib/postgresql/data` 和 `/data/tasks`。数据库不暴露宿主端口，网页与 API 仍走原同源入口。
首次配置脚本默认使用新根，不覆盖已有配置或密钥。运行数据不进入 Git，不自动复制备份或建立清理任务。

## PostgreSQL 的范围

使用 PostgreSQL 17 保存因子版本及研究历史。当前只有6张业务表：
`factors`、`factor_versions`、`research_batches`、`research_tasks`、`research_results`、`factor_values`。
这些表保存可复现因子/任务的历史记录；没有产品用户、账号、注册、登录、会话或权限表，也没有对应界面和接口。

已有行情当前仍通过 FactorTrader 的只读 mmap Provider 接入 `/home/quant/quant_data/mmap`，在容器内挂载为 `/data/market:ro`。
该路径由现有项目共用，本次没有搬动、更新或导入行情。行情改由 PostgreSQL 保存/读取的具体范围尚待明确，不能将研究历史库称为已建行情库。
数据库的应用范围限定为行情与研究历史，不扩展账号系统。

## 2026-11-09 迁移验收

1. 确认源路径仅被 FactorPool 后台和数据库容器使用，队列为空；正常停止后台与 PostgreSQL，避免复制运行中的数据库。
2. 将旧根完整复制到 SSD 新根，保留UID/GID、权限、mtime、扩展属性和硬链接关系。源只读挂载，不覆盖已有目标。
3. 校验1,534个文件、83个目录，共69,791,787字节；全部文件SHA-256和所检查元数据一致，树摘要为
   `191f325be6c3ad54c861c08f40edaf492875a3763ae3b9ea394108068702e085`。
4. 切换 `.env` 与实际 Docker 挂载，再启动同一 PostgreSQL 版本和后台；6张表按主键排序后的全量行哈希与计数一致。
5. 确认健康、历史接口、新路径挂载和原行情只读状态；无容器再引用旧根后，重新核验旧根身份与内容并删除旧数据，最后移除空根目录。
6. 旧路径的文件/目录/软链接均不存在，新根已执行文件系统同步。后续浏览器回归产生的合成记录属于新根中的正常新增数据。

切换时保留的数据如下（这是迁移前后对账截面，不是持续更新的运行计数）：

| 表 | 记录数 |
| --- | ---: |
| factors | 12 |
| factor_versions | 19 |
| research_batches | 7 |
| research_tasks | 60 |
| research_results | 38 |
| factor_values | 5,678 |

本次是跨盘迁移后删除已核验旧副本，不是清空数据库。原始目录树/表哈希校验记录保存在忽略的 `build/storage-evidence/`，不上传真实运行数据。
新根已占用对应SSD空间；删除旧根释放原NVMe上的旧副本，但不将逻辑字节数称为实测 `df` 回收量。

## 回退规则

后端二进制可按 [性能记录](PERFORMANCE.md) 回退到旧镜像；存储根继续使用 SSD，不重建旧 `dev/FactorPool-data`。
保留 `.env` 中的 `FACTORPOOL_STATE_DIR` 和现有数据库，禁止 `down -v` 或首次初始化空目录替代历史。
