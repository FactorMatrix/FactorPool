# HTTP API

所有路径位于同源 `/api`。无登录。服务供可信作者使用；机器路径与并发/超时来自后台配置，不接受任务传路径。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET/POST | `/factors` | 因子列表 / 保存新版本，响应id/version_id/version |
| GET | `/factors/{id}` | latest和完整版本记录 |
| POST | `/tasks` | 创建源码/参数快照；响应id |
| GET | `/tasks` | 最新200个任务 |
| GET | `/tasks/{id}` | 状态、错误、时间及snapshot |
| POST | `/tasks/{id}/cancel` | 排队立即取消；运行中停止进程并确认退出 |
| GET | `/tasks/{id}/logs` | 最近64KiB日志，响应text |
| GET | `/tasks/{id}/result` | 完整成功结果；未成功返回409 |
| GET | `/tasks/{id}/values?offset=0&limit=100` | 证券明细分页，limit≤1000 |
| POST | `/tasks/batch` | factors为因子id列表，附公共data/analysis |
| GET | `/batches/{id}` | 每个子任务状态，避免将部分成功冒充整批成功 |
| GET | `/data-sources` | 实际挂载可读性和频率；不写数据 |
| GET | `/examples` | 同一份PythonIndicator案例源码 |
| GET | `/operators` | 目标算子逐项能力台账 |

保存定义需要 name、source_code、factor_class、factor_params；提供id则在同一因子下产生新版本。
提交任务额外需要 data/analysis。完整请求可用 `scripts/smoke.py` 中的实际样例。

```json
{
  "name":"低成交量",
  "source_code":"完整 PythonIndicator 源码",
  "factor_class":"LowVolumeFactor",
  "factor_params":{"period":20},
  "data":{"provider":"fixture","universe":"fixture","frequency":"1d","adjustment":"raw","factor_start":"2024-01-29","factor_end":"2024-01-29"},
  "analysis":{"entry":"next_open","exit":"following_open","groups":3,"group_weight":"equal","min_ic_pairs":3,"icir_ddof":1,"annualize_icir":false}
}
```

真实数据 provider 为 `ft_daily_mmap`，universe 为证券代码列表或字符串 `all_a_dynamic`。
不支持的频率、复权、收益间隔、年化、字段或算子明确失败，不能静默忽略。
SUCCEEDED前前端不读取正式结果，所有不可用数值为标准JSON null。
