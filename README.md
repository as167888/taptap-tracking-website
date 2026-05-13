# TapTap 数据分析大盘

TapTap 平台数据跟踪与游戏分析工具集，纯静态 HTML 仪表盘网站。

## 页面结构

| 页面 | 说明 |
|------|------|
| `index.html` | 导航主页，汇总各子页面入口 |
| `taptap_download_dashboard.html` | TapTap 每周 Top10 榜单归档（全时段完整版） |
| `taptap_download_trends.html` | TapTap 每周日均新增下载与评论跟踪 |
| `taptap_forum_dashboard.html` | TapTap 论坛 Top10 榜单数据归档 |
| `taptap_forum_trends.html` | TapTap 论坛数据跟踪可视化 |
| `pc_game_ranking.html` | TapTap 平台 PC 端游戏下载量明细表 |
| `taptap_maker_result.html` | TapTap Maker 游戏数据抓取明细表 |
| `revenue_predict.html` | 游戏畅销榜流水自动测算工具 |
| `sentiment_dashboard_llm.html` | 02400.HK 雪球社区情绪仪表盘（DeepSeek LLM） |

## 数据覆盖

- **TapTap 下载排行**：每日/每周 Top10 榜单，含下载量、评分、评论数
- **TapTap 论坛**：热门帖子、互动数据、趋势变化
- **PC 游戏**：TapTap PC 端游戏的下载与关注数据
- **TapTap Maker**：Maker 计划游戏的表现跟踪
- **流水测算**：基于排名反推游戏畅销榜收入预估
- **社区情绪**：港股 02400（B站）雪球社区 LLM 情绪分析

## 技术栈

纯 HTML + 内联 CSS / JavaScript，无需构建工具，零依赖可直接部署。

- 图表：Apache ECharts
- 访客统计：不蒜子
- 情绪分析：DeepSeek LLM API

## 部署

项目部署在 [GitHub Pages](https://as167888.github.io/taptap-tracking-website/)，push 到 `master` 分支即自动更新。
