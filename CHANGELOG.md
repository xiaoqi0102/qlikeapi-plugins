# 更新日志

本项目的所有重要变更都记录在这里。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 计划中
- 按成功率智能选路（渠道健康分）
- 价格页支持批量导入/导出（CSV）
- 渠道实例分组与标签筛选

## [3.3.0] - 2026-09-17

控制台二次升级 + 探活安全修复 + 测试体系落地。

### Added
- **测试体系**：`tests/` 199 个 pytest 用例（零网络、零成本），覆盖加密、协议翻译、
  渠道插件、数据层、HTTP 鉴权门、探活安全；新增 `pyproject.toml`（ruff / pytest / 覆盖率）。
- **CI**：GitHub Actions 跑 lint + 测试 + 镜像构建；issue/PR 模板、dependabot。
- **工程规范文件**：`README`、`CONTRIBUTING`、`SECURITY`、`CODE_OF_CONDUCT`、
  `CHANGELOG`、`LICENSE`、`Makefile`、`.editorconfig`、`.env.example`、
  `docker-compose.example.yml`、`THIRD-PARTY-NOTICES.md`、`docs/` 六篇文档、
  `scripts/` 零成本验收脚本。
- 控制台：请求日志分页（20/50/100/200 每页）、骨架屏、空状态、批量操作条
  （批量启用/停用/探活/删除）、表头全选（只选当前筛选结果）、深色模式、
  Tabler Icons 全站替换（本地自托管）、图表升级（面积渐变 / 平滑曲线 / 深色 tooltip）。
- 控制台：所有按钮都接到真实动作（复制 base_url、复制统一入口、复制令牌明文、
  路由链编辑、恢复自动路由、插件重载、探活、余额刷新……没有空壳按钮）。
- 探活新增「逐模型进度」：每行显示 ✓通过 / ✗失败 + 耗时毫秒 + 上游状态码，
  顶部进度条 + 通过/失败汇总。

### Fixed
- 直连已停用渠道时提示「unknown provider」（像是被删了），现在明确回 503「渠道 'x' 已停用」。
- **探活曾可能真出图**：旧实现在「合并插件」（一个实例内部按模型名分流到两套协议）
  上只抹掉了部分字段，导致探活像真实生成请求一样发出去，触发上游计费。
  现改为**递归抹掉一切提示词类字段**（prompt/text/content/contents/parts/input/
  image/images/mask/…）并加**硬超时**（`QLIKEAPI_PROBE_TIMEOUT`，默认 12s），
  探活只以 4xx 判定链路可达。`tests/test_probe.py` 为这条底线加了守门用例。
- **空白提示词未被拦截**：`"   "` 这种纯空白提示词会带着上游跑。现在 `prompt_of()`
  统一 `strip()` 后判空，缺提示词一律本地 400。
- **价格优先级错乱**：`model_prices` 查询没有 `ORDER BY`，兜底行（`*`/`*`）会因行序
  随机顶掉「模型专属价」。现在渠道专属 > 全局模型价 > 全局兜底，逐级回退。
- 渠道路由保存：链中出现空值/非字符串之前会 500，现在明确返回 400。
- 修改密码空提交之前返回 400 且提示含糊，现在前端即校验。

### Changed
- 版本号统一为 `3.3.0`（`app/main.py` 与 `/healthz` 一致）。
- 渠道页「新建渠道实例」按钮去重（原来页头和工具栏各有一个）。
- 库内 `zip()` 显式声明 `strict=`；清理未使用导入（ruff 全绿）。

## [3.2.0] - 2026-09-17

控制台第一轮视觉/交互升级（按 New API 与 Tabler 的设计语言收敛）。

### Added
- 渠道实例页：紧凑表格 + 内联可编辑优先级（`input.pinp`）+ 权重列 + 行展开明细 +
  操作区图标成排 + 启停开关 + 搜索/筛选 tab（全部/启用/停用/自动熔断/异常）。
- 渠道实例页：勾选列 + 批量操作条、探活进度弹窗、路由链编辑弹窗。
- 站点表单新增「余额预警线」；设置页新增「余额熔断」行。
- 前端库本地自托管（Bootstrap 5.3 / Bootstrap Icons / Tabler Icons / Chart.js 4.4），
  离线与内网环境可用。

### Removed
- 独立「路由规则」页：路由并入渠道实例页（优先级列 + 可折叠链路概览）。

## [3.1.0] - 2026-09-17

### Added
- **访问令牌体系**：一把令牌 = 一个调用方，支持额度（金额）、过期时间、允许模型、
  允许渠道、IP 白名单、QPS、启停；主密钥继续兼容。
- **渠道权重分流**：同优先级按权重随机（实测 3:1 权重 → 604:196 调用分布）。
- **渠道自动熔断/恢复**：连续失败 `N` 次自动停用；冷却到期后**探活通过才放行**
  （人工停用的渠道不会被自动恢复）。
- **密钥加密落库**：渠道密钥与站点令牌入库即加密（标准库 Encrypt-then-MAC，
  主密钥派生自 `QLIKEAPI_SECRET` / `QLIKEAPI_ENC_KEY`），历史明文自动迁移；
  设置页显示加密健康度（明文 / 已加密 / 解不开）。
- **余额熔断**：站点余额低于预警线 → 自动停用其关联渠道。
- 控制台令牌页、日志按令牌过滤、用量按令牌维度、`/api/sysinfo` 加密状态。

### Changed
- 模型名解析大小写不敏感；响应统一回客户端请求的模型名。
- 嵌套字段删除：`remove_params` 支持点号路径（`a.b.c`）与列表通配（`options.*.trace`）。

## [3.0.0] - 2026-09-17

从「单文件转换脚本」重写为「模块化 + 渠道插件化 + 软件层路由」的服务。

### Added
- 分层结构：`main` / `relay` / `protocols` / `admin` / `store` / `crypto` /
  `balances` + `channels/` 插件目录。
- 渠道插件契约（`Channel.build()` / `parse()` / `operations`）与 `_template.py` 样板。
- 三个渠道插件：`gemini_native`（Gemini 原生面）、`openai_images`（OpenAI 图片面）、
  `fal_queue`（异步队列面）；`change2pro` 为「合并插件」示例（一个实例两套协议）。
- 统一入口 `/v1/images/generations|edits` + 旧直连面 `/up/<渠道>/...` 双轨并存。
- 软件层路由：按模型自动成链、显式路由链、故障切换、响应头
  `X-QLike-Provider` / `X-QLike-Failover`。
- 控制台：概览 / 渠道实例 / 模型与价格 / 请求日志 / 用量统计 / 站点余额 / 访问令牌 / 设置。
- 干跑接口 `GET /v1/route-preview` 与 `POST /up/<渠道>/v1/images/preview`（只看报文不发请求）。

### Notes
- 前端库与图标全部本地自托管，不依赖任何 CDN。
- 数据层坚持单文件 SQLite：部署只需要一个容器 + 一个文件。

[Unreleased]: https://github.com/xiaoqi0102/qlikeapi-plugins/compare/v3.3.0...HEAD
[3.3.0]: https://github.com/xiaoqi0102/qlikeapi-plugins/compare/v3.2.0...v3.3.0
[3.2.0]: https://github.com/xiaoqi0102/qlikeapi-plugins/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/xiaoqi0102/qlikeapi-plugins/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/xiaoqi0102/qlikeapi-plugins/releases/tag/v3.0.0
