# 第三方组件声明（THIRD-PARTY NOTICES）

本项目自带的前端库均为**本地自托管**（`app/static/vendor/`），不依赖任何 CDN：
离线、内网、国内网络环境都能正常打开控制台。

| 组件 | 版本 | 许可证 | 用途 | 本项目内路径 |
|---|---|---|---|---|
| Bootstrap | 5.3.x | MIT | 布局 / 组件 / 深色模式基座 | `app/static/vendor/bootstrap.min.css`、`bootstrap.bundle.min.js` |
| Bootstrap Icons | 1.11.x | MIT | 图标（保留兼容，已由 Tabler Icons 取代） | `app/static/vendor/bootstrap-icons.css`、`fonts/` |
| Tabler Icons | 3.x | MIT | 控制台现用图标集（6100+） | `app/static/vendor/tabler-icons/` |
| Chart.js | 4.4.x | MIT | 仪表盘图表 | `app/static/vendor/chart.umd.min.js` |

Python 运行时依赖见 `app/requirements.txt`：FastAPI (MIT)、Uvicorn (BSD-3)、HTTPX (BSD-3)、python-multipart (Apache-2.0)。

以上组件的版权归各自作者所有，均以原始许可证分发，本项目未做任何修改（仅原样打包）。
