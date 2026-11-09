# Simulation 工作台

FactorPool 按用户要求参考 BRAIN Simulation 的公开界面：黑色顶部导航、黑色代码编辑器、覆盖式设置面板、浅色结果区与竖排结果导航。
因子仍写为 `factortrader.PythonIndicator`，直接提交原始 Python 源码；没有增加 Fast Expression 转译或第二套算子。

## 交互与职责

| 入口 | 实际行为 |
| --- | --- |
| Simulate | 工作台；新建/复制模拟、切换独立草稿与各自最近的研究结果 |
| Alphas | 现有因子库、保存版本、选定因子的批量研究 |
| Learn | PythonIndicator 使用说明、样例与现有数据源状态 |
| Data | 179 项现有算子兼容台账；各项实现和官方兼容状态仍独立披露 |
| History | 查询原有持久化任务、状态与结果 |
| Settings | 编辑股票池、已有数据源、日期、类名、构造参数、分析周期、分组和预测力衰减阶数；Apply 生效，Cancel 丢弃本次设置编辑 |
| Code / Results | 显示或隐藏相应窗格，至少保留一个；分隔条支持拖动与方向键调整 |
| 结果竖排导航 | Chart、Summary、IC Analysis、Groups、Factor Values、Logs、Source |
| Export Results | 用户点击时在本地下载当前任务与报告 JSON，包含提交时的源码快照，不向其他服务发送 |
| Source → 载入为新模拟 | 从不可变任务快照复制源码、参数和数据配置；保留原模拟和原结果 |

Settings 的 PythonIndicator、Equity、Region、Delay=1、Raw/Daily 是当前合同的固定展示项。
预测力衰减阶数控制现有 IC 衰减分析，不是 BRAIN 的 Alpha 平滑 Decay 参数。
不提供尚未接入的 Truncation、Neutralization、Train/Test 或外部 Submit Alpha 操作。

新建与复制只改变当前页面内存，不自动保存、提交或停止任务。各草稿保存源码、配置、因子版本关联和最近任务ID；
复制后的草稿不继承原因子的保存ID。切换草稿不会取消后台任务，返回时重新查询该任务。
页面刷新后从最近任务快照恢复；未保存、未运行的编辑需要先 Save 保存版本。
浏览器仍只持久化布局和最近任务ID，不新增源码本地存储。

六项概要按 Sharpe、Turnover、Fitness、Returns、Drawdown、Margin 展示。1/5/20等周期联动概要、曲线与诊断；
一期 IC/Rank IC/ICIR/覆盖率单独标明 1D。数值直接读取报告，不在前端重算收益或更改后端指标定义。
具体统计口径继续以 [REPORT-METRICS](REPORT-METRICS.md) 为正本。

## 文件拆分

| 旧路径 → 新路径 | 责任与原因 |
| --- | --- |
| `frontend/src/App.vue` 内嵌设置 → `components/WorkspaceSettings.vue` | 隔离临时设置、输入校验和 Apply/Cancel |
| `frontend/src/App.vue` 内嵌结果 → `components/SimulationResults.vue` | 统一结果导航、图表、因子值、日志、快照与本地导出 |
| `frontend/src/App.vue` 内嵌统计 → `components/BrainSummary.vue` | 六项概要格式化，保留 null 为 -- |
| 新增 `components/UiIcon.vue` | 本地 SVG 图标；不依赖外部图片、字体请求或 BRAIN 私有资源 |
| 保留 `App.vue` | API 调用、任务轮询、草稿切换与顶层页面编排 |
| 保留 `FactorDiagnostics/ReportChart/ResearchChart` | 继续使用现有报告字段，改为浅色图表；不迁移数值计算职责 |
| 新增 `scripts/frontend_build_info.py`、`docker/frontend-build-info.json` | 独立追踪前端源码，允许只更新 Nginx 镜像 |

## 构建、验证与部署

前端可独立发布，`FACTORPOOL_FRONTEND_RELEASE` 未设时仍沿用原 `FACTORPOOL_RELEASE`。
`docker/build-info.json` 继续表示后端构建，本次不重生成该文件、不重启后台或数据库。
前端独立摘要位于 `/ui-build.json`，以 `frontend_source_sha256` 核对实际镜像与本地源码。

```bash
python3 scripts/frontend_build_info.py
python3 scripts/frontend_build_info.py --check
FACTORPOOL_FRONTEND_RELEASE=20261109-r3 docker compose build frontend
FACTORPOOL_FRONTEND_RELEASE=20261109-r3 docker compose up -d --no-build --no-deps --wait frontend
curl --fail http://127.0.0.1:8088/ui-build.json
curl --fail http://127.0.0.1:8088/health
```

将前端版本写入本机忽略的 `.env`，保留后台的 `FACTORPOOL_RELEASE=20261109-r2`。
回退仅切换前端版本：

```bash
FACTORPOOL_FRONTEND_RELEASE=20261109-r2 docker compose up -d --no-build --no-deps --wait frontend
```

浏览器检查使用已有 Playwright 安装，通过 `FACTORPOOL_PLAYWRIGHT_MODULE`、`PLAYWRIGHT_BROWSERS_PATH` 指定位置。
`FACTORPOOL_URL` 可指向 Compose 临时前端，API仍连接当前后台；合成任务会留下可见测试记录。

```bash
node tests/browser.mjs
node tests/browser-diagnostics.mjs
node tests/browser-workbench.mjs
```

前两个检查覆盖手算样例、保存版本/因子库/算子页、刷新恢复、多周期报告、分组/热力图/衰减与缺失值。
工作台检查覆盖设置校验和 Apply/Cancel、原始 Python 源码与服务端快照一致、复制/切换不提交、
各草稿源码/配置/结果独立、周期联动、窗格显隐与键盘调整、本地导出，以及任务快照复制。
桌面1512×1050和手机390×844检查脚本错误和横向溢出；截图放忽略的 `build/workbench-*.png`。
真实行情展示读取此前已完成的报告，本轮浏览器只新增合成任务，没有发起行情下载或更新。
构建仍有既有大包提示及依赖树审计告警，本轮仅将已锁定的 CodeMirror 高亮模块声明为直接依赖，没有升级依赖树。

## 视觉参考范围

参考的是公开发布的 BRAIN 截图，未访问认证后的 BRAIN 页面，不声明与最新私有版本逐像素一致：
[公开界面文章](https://www.10100.com/article/51273384)、
[Simulation 截图](https://cdn.10100.com/content/20251217/9cec7046-7457-4cea-9f78-6137f00e61d3.png)、
[Settings 截图](https://cdn.10100.com/content/20251217/c5897516-c4fc-44eb-9636-6e42d677b352.png)。
参考图片没有进入应用或 Git；保留 FactorPool 标识，本地实现布局、配色与图标。
界面风格参考和 BRAIN 官方数值兼容是两个独立结论。

## 2026-11-09 实际验收

- 前端已部署 `factorpool/frontend:20261109-r3`；后台仍为 `factorpool/backend:20261109-r2`，数据库沿用原容器。
- 部署前后后台容器 `1c9e5dbd3885`、数据库容器 `5b28d93f0144` 的完整ID、镜像与启动时间一致；三个服务健康。
- 前端源码摘要 `d0ab948b0c6c4b64f4f900d73bc82440c2ae11ac18ab58e16a900de6b36ab63c`，与线上 `/ui-build.json` 一致。
- 最终镜像经 Compose 临时前端运行以上三个浏览器脚本全部通过；样例 IC/Rank IC=1，ICIR=--，覆盖率100%，没有脚本异常或手机横向溢出。
- 上线后额外只读浏览器检查通过：原有真实行情报告可读、设置 Apply 按钮在手机面板内可见、页面没有向其他站点发出请求。
- [Tailscale 主入口](http://master-2019.tailaeed46.ts.net:8088) 经本机 `tailscale nc` 转发自检：health与前端摘要均200；未将此称为独立外部客户端验收。
- [当前局域网入口](http://192.168.1.6:8088) 经本机直连检查：health UP、摘要匹配、同源API返回179项算子。局域网IP为动态值。
- 行情挂载仍为只读，FT子模块、矩阵研究代码、后台和数据库实现均未修改。临时前端验证完成后移除，不改动正式服务数据。
