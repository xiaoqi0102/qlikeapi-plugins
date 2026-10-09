# PROJECT_INDEX — qlikeapi-plugins

> 给接手本项目的 AI 看的入口。**按需检索**，不要顺序通读全部文档（规则见 `AGENTS.md`）。

## 一句话定位

**图片协议转换网关**：挂在 New API 后面当唯一上游渠道（New API 里是优先级最高的渠道），
把标准 OpenAI 图片接口（`/v1/images/generations|edits`）翻译成各家上游自己的协议
（Gemini 原生面 / OpenAI 图片面 / 异步队列面），并负责多渠道路由、故障切换、熔断、记账。

```
客户端 → New API（渠道 #30 最高优先级）→ qlikeapi-plugins(:18673) → 各生图上游
```

## 当前摘要

| 项 | 值 |
|---|---|
| 当前版本 | **3.21.4**（2026-10-09，见 `CHANGELOG.md` 顶部） |
| 仓库 / 分支 | `github.com/xiaoqi0102/qlikeapi-plugins` / `main`，MIT |
| 运行形态 | 单容器 `qlikeapi-plugins`（healthy），端口 `127.0.0.1:18673`，单 SQLite 落库 |
| 部署目录 | `/opt/qlikeapi-plugins`（`docker compose up -d --build` 构建） |
| 数据 | `data/qlikeapi.db`（渠道密钥密文、日志、令牌、站点），运行插件 `data/plugins/*.py` |
| 测试 | `make test`（零网络，绝不打上游）；`make check` = lint + ui-lint + test |

## 任务 → 先读哪里（路由表）

| 我要做的事 | 先看 |
|---|---|
| 搞懂整体分层/一次请求怎么走 | `docs/ARCHITECTURE.md`（第 2、3 节） |
| 加/改一个上游渠道 | `docs/CHANNEL-PLUGINS.md`；让 AI 写插件用 `docs/PLUGIN-AUTHORING.md`；插件模板 `app/channels/_template.py`；契约 `app/channels/base.py` |
| 改统一入口 / 路由 / 故障切换 / 记账 | `app/relay.py`（流程 7 步都在这） |
| 改某协议的报文翻译 | `app/protocols.py`（纯函数）+ 对应 `app/channels/<厂商>.py` |
| 改控制台 API / 登录 / 渠道路由编辑 | `app/admin.py`（1600 行，按功能块搜索定位） |
| 改数据表 / 迁移 / 加密落库 | `app/store.py`（含 MIGRATIONS）、`app/crypto.py` |
| 改前端页面 / 样式 | `app/static/`；改样式前先读 `docs/DESIGN-SYSTEM.md`，改完跑 `make ui-lint` |
| 改素材中转（`POST /v1/files`） | `app/media.py`、`docs/IMAGEHOST.md` |
| 改站点余额取数 | `app/balances.py` |
| 尺寸换算（客户端像素 → 上游实际尺寸） | 代码在 `app/utils.py`：`snap_size()`（GPT 系就近吸附；`protocols.py:24` 以 `snap_size = utils.snap_size` 复用，出图路径 `protocols.py:601/647` 调用）；Gemini 原生面另走 `utils.gemini_plan()`。说明文档 `docs/SIZE-MAPPING.md` |
| 参考图转公网直链 / 内联 base64 | `app/imagehost.py`、`docs/IMAGEHOST.md` |
| 查 HTTP 接口对外长什么样 | `docs/API.md`；在线页 `/api-docs`（源 `docs/api-docs.md`） |
| 环境变量 / 调参 | `docs/CONFIGURATION.md`、`.env.example` |
| 部署 / 反代 / 备份 / 升级回滚 | `docs/DEPLOYMENT.md` |
| 智能路由/并发控制的设计意图 | `docs/SMART-ROUTING-PLAN.md`（规划稿，注意区分「已做/规划中」） |
| 历史上换过什么 | `CHANGELOG.md`（按版本倒序）；`docs/ARCHIVE.md`（首次归档） |
| 下个版本的计划 | `docs/ROADMAP.md` |

## 三条铁律（改任何代码都不能破，详见 `AGENTS.md`）

1. **缺 prompt 直接 400** —— 绝不回落默认提示词（否则真出图、真扣费）。
2. **图片不落盘、不转存** —— b64 或上游 URL 原样回给客户端。
3. **探活一律零成本** —— 探活前递归抹掉一切提示词字段，用 4xx 判定「链路可达」。

## 两条容易想当然的（已核实）

- **卖价规则不在本仓库**：「生图卖价 = 各渠道最高实付 USD + 0.05」是**下游 New API 侧**的定价约定；
  本仓库只从各渠道自己的平台读实付单价（`app/balances.py` → `store.list_prices()`）。
- `data/plugins/*.py`（如 `change2pro_cgw.py`、`tudou.py`）是**面板热装的运行插件**，`data/` 整目录在 `.gitignore` 内
  （见 `.gitignore` 第 2 行），因此不在版本管理里；仓库内的内置插件只有 `app/channels/*.py`（面板中只读）。

_状态与文档职责若变化，只改本文件对应行；不要在本文件复制专题正文。_
