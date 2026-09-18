# 更新日志

本项目的所有重要变更都记录在这里。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

## [3.10.1] - 2026-09-18

### 修复
- **浅色模式侧栏被改坏**（v3.10.0 引入的回归）：上一版把「恒定色」误当「主题色」，`.side` 渐变收尾 `#111c33`
  被并进 `--surface-2`（浅色值 `#fbfdff`）→ 浅色下侧栏底部渐变成白；选中项高亮渐变替换时多写了一个 `)` →
  整条 `background` 被浏览器丢弃 → 高亮块变成浅白色块。同类问题还有：品牌副标题/分组标题/页脚文字、
  logo 白底、侧栏滚动条、登录页深色底、深色代码块，全部恢复为**常量变量**。
- 深色模式下 `.pill` / `.note` / `thead` 等露白点**保持修复**（不回退）。

### 变更
- 颜色收敛口径重做（见 `docs/DESIGN-SYSTEM.md` §2.2.1 三原则 C1/C2/C3）：**精确值匹配**（不再按近似色合并）、
  **恒定色用常量变量**、**内容区才跟随主题**；`body.dark{...}` 块内规则一并按深色解析。
- 变量总数 100 → 172（新增 72 个「值与原字面量完全相等」的补充刻度 + `--black-rgb/--white-rgb`）。

### 验证
- 浏览器计算样式 A/B（新旧样式同页对比，5 个页面 × 浅/深两套主题）：
  浅色模式颜色差异**全部 Δ≤8/255**（无 Δ>8）；深色模式差异仅为「露白修复 + Δ≤10 档位吸附」；
  侧栏/品牌区颜色**零变化**；深色白块扫描 **0 个**；`make ui-lint` 全绿。

## [Unreleased]

### 计划中
- 智能路由阶段 2：按成功率/P95 健康画像降权 + 临时摘除（until + reason）+ 余额触线降权
- 智能路由阶段 3：按请求内容做能力路由 + 渠道亲和 + 用量面板
- 按成功率智能选路（渠道健康分）
- 价格页支持批量导入/导出（CSV）
- 渠道实例分组与标签筛选

## [3.10.0] - 2026-09-18

### 整体 UI 设计规范落地：全量 token 化 + 可执行守卫（并修掉深色模式露白）

**硬编码清零**（`ui-kit.css` + `login.css`）：颜色 **283 → 0**、字号 **51 → 0**、圆角 **40 → 0**、动效时长 **1 → 0**。
新增约 60 个语义变量，深浅**成对**定义：`--surface-2/-3`（表头/分页条/药丸底）、`--hover/-2/-3`（三档交互底色）、
`--line-strong`、`--ink-3`、语义四件套（`--ok/warn/err` 各配 `-soft/-ink/-line`）、`--nav-*`（侧栏）、
`--note-bg`、`--brand-hi/-text`、`--shadow-rgb`/`--brand-rgb`（阴影与光晕统一写成 `rgb(var(--x) / α)`）。
字号收敛到 10 档刻度（11/12/13/14/15/18/20/22/26/30），圆角 6 档（4/6/9/12/16/999），时长 5 档（.06/.12/.15/.3/.7）。

**修掉两个真 bug**（都由「写死颜色」引起）：

1. **深色模式下 `.pill` / `.pager` / `.note` / 表头仍是白底** —— `.pill` 全站 388 个，
   表现为「鼠标滑过时到处闪白」。根因是同属性的**浅色规则写在深色覆盖之后**（同优先级靠书写顺序决胜），
   深色覆盖被顶掉；改走变量自适应后一次消灭，无需再写 `body.dark` 覆盖。
2. **`tokens.css` 注释提前闭合** —— 往文件头注释里插了 CSS 变量，CSS 注释不能嵌套 → `*/` 提前闭合 →
   浏览器把紧随的 `:root{}` **整块丢弃** → `--ink/--bg/--surface/--line/--brand` 全部未定义 → 主内容区变白。
   已重写 tokens.css 并加静态守卫。

**新增规范守卫 `scripts/ui_lint.py`**（`make ui-lint`，已接入 CI 的 lint 任务与 `make check`）：
检查 tokens.css 注释闭合/禁止嵌套、`var(--x)` 必须有定义、tokens.css 不许有规则块外的裸声明、
非 tokens.css 文件不许出现硬编码颜色/字号/圆角/时长、内联 `style` 里的颜色与字号必须走 `var()`。违规退出码 1。

**新增 `tests/test_ui_lint.py`**（4 个用例）：把守卫挂进 pytest，并额外守住「注释里不许出现花括号」
「body.dark 只许覆盖同名变量」两条结构性约束。

**文档**：`docs/DESIGN-SYSTEM.md` §2 重写为完整变量总表（100 个变量，按角色分组）、
新增 §2.2「刻度与收敛规则」与 §2.3「规范守卫」、§9 变更流程加入 `make ui-lint`、
§10 坑清单补 16/17/18（三次真实事故的成因与加固）。`/ui-kit` 展示页同步按组展示全部变量 + 刻度可视化。

## [3.9.0] - 2026-09-18

### 渠道实例的「模型映射」按 sub2api 的逻辑与界面重做

读了 sub2api 的源码（`frontend/src/composables/useModelWhitelist.ts` +
`components/account/ModelWhitelistSelector.vue` + `EditAccountModal.vue`）后按同一套口径实现：

**存储规则一致**：sub2api 用一个对象 `{请求模型: 实际模型}` 存两件事 ——
`from == to` 属于**白名单**，`from != to` 属于**映射**（对应其 `splitModelMappingObject` /
`buildModelMappingObject('combined', …)`）。我们的 `model_map` 结构本来就一样，所以直接沿用这套拆分/合并。

### Added

- **「模型限制（可选）」双 Tab**：模型白名单 / 模型映射，替换原来只有一个 JSON 文本框的写法
  （JSON 编辑移到「高级」折叠区保留，能力不减）。
- **模型白名单**：多选下拉（复用组件库 `UI.picker`，带搜索、全选/清空、已选计数、chips 展示），
  三个零成本按钮 ——「同步最新支持模型」（插件预置）/「同步上游支持的模型」（`GET /v1/models`，
  带前缀的自动建成映射）/「清除所有模型」，以及「自定义模型名称 + 填入」。
- **模型映射**：`请求模型 → 实际模型` 成对输入行（可增删）+ 整宽虚线「+ 添加映射」+
  一排**预置药丸**（按模型家族上色，点一下即添加）。
- **通配符支持**（与 sub2api 同口径）：左侧 `gemini-3*` 合法（`*` 只能一个且在末尾），
  右侧不允许 `*`；多条命中取**最长规则**；路由层也认通配符（`gemini-3*` 能接住 `gemini-3-pro-image`）。
  前后端各校验一道，非法保存直接 400。
- `GET /api/channels` 增加 `model_map`（插件预置映射），供面板渲染药丸与「同步最新支持模型」。
- `protocols.is_valid_wildcard()` / `validate_model_map()` / `wildcard_keys()`；
  `model_list()` 标注 `wildcard`；`default_model()` 优先取非通配符模型。

### Changed

- `match_model()` 增加通配符兜底（精确 → 最长通配符规则），`resolve_chain()` 同步支持
  （原先严格 `model in model_map`，通配符规则接不住请求）。
- 渠道列表的「密钥」列同时显示分组数量，展开行显示「密钥分组（模型 → 用哪一组）」。

### Tests

- 新增 `tests/test_model_map.py`（8 个用例）：通配符校验、最长规则优先、默认模型跳过通配符、
  `model_list` 标注、**通配符真的改写到了上游请求**（断言上游 URL 里的模型名）、
  插件预置映射接口、保存非法通配符返回 400。
- 全量 **298 passed**，`ruff` 干净。

## [3.8.0] - 2026-09-18

### 背景（用户反馈 + 实测确认）

**sub2api 系上游的密钥是「绑分组」的** —— 和 New API 不一样：一个渠道里不能混放不同分组的模型。
实测 change2pro：key#1 的 `GET /v1/models` 只回 4 个 gemini 模型，key#2 只回 `gpt-image-2`；
gemini 与 gpt 不在同一分组。之前把两把 key 不加区分地放在同一渠道，轮询会把 gemini 请求打到 gpt 组的 key 上，
上游回 `404 model_not_found`（日志实证 3 次），白白多一跳才降级。

### Added

- **同一渠道内的「多分组密钥」**（不拆渠道）：
  - 密钥池写法升级为 **`分组标签::密钥`**（一行一把，保持书写顺序）；没写标签的行＝通吃；
  - `options.key_groups`：手写「模型 → 分组」规则（支持 `*` `?` 通配），优先级最高；
  - `options.key_models`：「探测各密钥分组」的自动结果；
  - 路由按请求模型挑**对应分组**的密钥，轮换与失败冷却都在分组内进行；
    规则没命中且没有通吃 key 时**退化成全部 key**（绝不因配置不全而断路）。
- **`POST /api/providers/{key}/discover-groups`**：逐把 `GET /v1/models` 读出「这把 key 属于哪个分组、
  能用哪些模型」——**零成本、只读、绝不出图**，结果写入 `options.key_models`。
- **`POST /api/providers/{key}/fetch-models?group=<分组>`**：可按指定分组的密钥去拉模型列表。
- 面板渠道弹窗新增「**探测各密钥分组**」按钮与结果展示；密钥池、分组汇总（标签/把数/模型）
  在渠道列表与展开行里都能看到（**只回掩码与分组标签，绝不回密钥内容**）。
- `/api/providers` 每把 key 增加 `label` 字段，并新增 `key_groups` 汇总；`/v1/route-preview` 也带出 `key_groups`。

### Changed

- `store.provider_keys()` 会剥掉分组标签；新增 `store.key_entries()` 返回 `{idx, label, key}`，
  密钥冷却按 `idx`（渠道内稳定序号）记账，加/删其它 key 不影响既有冷却。

### Tests

- 新增 `tests/test_key_groups.py`（10 个用例）：标签解析、显式规则/自动探测解析、分组内挑 key 与逐级退让、
  真调用按分组选 key（断言发出去的 `Authorization`）、探测接口落库与错误上报、密钥内容不外泄。
- 全量 **290 passed**，`ruff` 干净。

## [3.7.0] - 2026-09-18

### 口径复核（两条反馈都对照官方原文核过）

- **GPT 系尺寸上限**：OpenAI《Image generation》"Size constraints" 原文 —— 最长边 ≤ **3840px**、
  两边都必须是 **16 的倍数**、长短边比 ≤ **3:1**、总像素 **655,360 ~ 8,294,400**；
  "Popular sizes" 里 **4K = `3840x2160`**、2K = `2048x1152`/`2048x2048`。
  所以 `4096x4096`（16.8MP）**超出上限一倍**，被缩到 `2880x2880` 是正确行为 ——
  2880² 正好等于 8,294,400，**像素总量与官方 4K 相同**，不是降档。
  （`4096x4096` 是 **Gemini** 的 4K 方图尺寸，Google 按方图边长定义档位，两套口径不同。）
- **Gemini 档位像素**：Google 官方表里 16:9 只有 `688x384`(0.5K) / `1376x768`(1K) /
  `2752x1536`(2K) / `5504x3072`(4K)，**没有 `3840x2160`** —— `image_size` 只接受
  `512px`/`1K`/`2K`/`4K` 四个值，输出像素由「档位 + 比例」决定。
  想要 ≥3840 的长边只能选 4K（`5504x3072`，token 更贵）。

### Added

- **尺寸处理方式**（渠道实例 `options.size_mode`，面板「尺寸处理」下拉）：
  `snap`（默认，按官方约束最小改动吸附）/ `passthrough`（**一个像素都不改**，原样发给上游）。
  给「上游实际接受更大尺寸」的渠道用，例如你确认过某渠道能吃 `4096x4096` 就切成 `passthrough`。
- **官方口径在面板上摊开**：尺寸换算器新增
  ① 官方常用尺寸 chips（点一下填入：`1024x1024` / `2048x2048` / `2048x1152` / `3840x2160` …）；
  ② 换算时列出 Gemini 该比例下的**整张官方档位表 + 各档 token**，并标出本次命中的档位；
  ③ GPT 系额外给出「离你这次请求最近的官方尺寸」。
- `GET /api/size-plan` 新增 `mode` 参数，返回体新增 `tiers` / `tokens` / `tokens_all` /
  `official` / `nearest_official` / `source` 字段。
- `utils.gemini_tokens()`：各模型各档位的官方 token 消耗（3.1 Flash `1120/1680/2520`、
  3.1 Pro `1120/1120/2000`、2.5 Flash `1290`）。
- `utils.OFFICIAL_GPT_LABEL`：官方常用尺寸的中文标签，前端 chips 用（有测试锁死两边一致）。
- 路由预览（`/api/routes`）每条渠道带出 `size_mode` 与 `gemini_size_policy`。

### Changed

- `docs/SIZE-MAPPING.md` 补齐：OpenAI 官方「常用尺寸」表、2K/4K 命名差异说明、
  Gemini 各档 token 表、`size_mode` 用法，并标注复核日期与三份官方文档。
- 换算器的 GPT 规则说明改为同时引用 OpenAI《Image generation》与 Azure《GPT image models》，
  并显式提示「官方 4K = 3840x2160，方形最大 2880x2880」。

### Tests

- 新增 10 个用例：`passthrough` 不改尺寸且不出 `X-QLike-Size`、默认 `snap` 仍吸附到 2880x2880、
  官方常用尺寸全部合法且有标签、Gemini token 表、官方档位表（含「表里没有 3840」）、
  Pro 无 0.5K、`/api/size-plan` 新字段、前端 chips 与 Python 表一致性。
- 全量 **280 passed**，`ruff` 干净。

## [3.6.0] - 2026-09-18

尺寸换算全面重做（**不再为凑比例把图放大一档**）+ 模型目录列对齐 + 渠道专属价级联清理 + 尺寸换算器。

### Fixed
- **尺寸吸附不再多扣费**：旧版 `gpt_safe_size()` 只要尺寸不合法就整张图按比例跳到最近的标准档，
  例：客户端传 `1920x1080`（高度 1080 不是 16 的倍数）→ 被抬成 `2048x1152`（像素 +13.8%）。
  上游按 1K/2K/4K 分档计费时，这就是多扣费。新版改为**最小改动**：保留合法的那一边（1920），
  只把不合法的那一边就近修成 16 的倍数 → `1920x1072`（像素只会变小，绝不放大）。
  取整后若比例/面积又被顶出界（如 `4000x500` 压到 3:1 后取整成 `1504x496`，比例 3.03），
  会再按「优先缩长边」微调到真合规为止。
- **模型目录页三张表列宽不再错位**：每张渠道表原先各自算列宽，导致「真实单价 / 价格来源」
  两列上下对不齐。改为共用 `colgroup` 固定列宽（`table-layout:fixed`）+ 单价列右对齐
  （`tabular-nums`）+ 价格来源单行省略（鼠标悬停看全文）。
- **渠道专属价不再留孤儿**：`DELETE /api/providers/{key}` 原先只删渠道和健康记录，
  渠道专属价（`model_prices.provider`）会留在价格页变成重复行；现在删除渠道时级联清理，
  并新增 `POST /api/prices/prune` + 价格页「清理孤儿价」按钮做兜底。

### Added
- **尺寸换算器**（模型目录页，零成本不出图）+ `GET /api/size-plan?model=&size=&policy=`：
  输入模型名和尺寸，直接看到本服务最终发给上游的尺寸/档位/比例与实际输出像素。
- **Gemini 档位策略**（渠道实例 `options.gemini_size_policy`，面板下拉可选）：
  `class`（默认，按尺寸档位分类）/ `floor`（向下取档，最省）/ `nearest`（取最接近档）/
  `ceil`（向上取档，不降级）。
- **`X-QLike-Size` 响应头**：尺寸被换算时回传 `原尺寸->实际尺寸`（ASCII，便于排查对账），
  中文说明同时写进请求日志详情。
- `docs/SIZE-MAPPING.md`：把两套尺寸规则连同**官方出处**写清楚 ——
  GPT-Image-2/2.5 系「自由尺寸」四条硬限制、GPT-Image-1 系只认三种尺寸、
  Gemini 只能给「档位 + 宽高比」以及各档位在各比例下的**真实输出像素表**。

### Changed
- `gpt-image-1` / `1.5` / `1-mini` 走独立的「三选一」吸附（`1024x1024` / `1536x1024` / `1024x1536`），
  不再套用自由尺寸规则。
- Gemini 档位按**模型能力**收敛：`gemini-3.1-flash-lite-*` 只给 1K；`gemini-3-pro-*` 无 0.5K 且
  不支持 `1:4`/`4:1`/`1:8`/`8:1`；`gemini-2.5-flash-*` 有自己的一套像素表。
- 超 `2560x1440` 的自由尺寸会在换算说明里标注「上游实验档」。

## [3.5.0] - 2026-09-18

智能路由**阶段 1**（并发闸门 + 优先级分档降级 + 路由决策可观测）+ 日志完整请求回放 + 上游模型列表一键拉取 + 登录页配色回归项目规范。

### Added
- **并发闸门**（`app/relay.py` 的 `_Gate`）：渠道/全局并发上限 + 排队等待 + 队列上限 + 超时拒绝，
  语义借鉴 sub2api 的图片并发限流器。默认不限并发（`QLIKEAPI_MAX_CONCURRENCY=0`），
  渠道级用「渠道实例 → 编辑 → 并发上限」配置（`options.max_concurrency`）；
  排队超时不会硬等 —— 路由层先换下一家（降低单点依赖），全部占满才返回 503 + `Retry-After`。
- **优先级分档降级**：候选链按优先级分档 → 档内按权重分流 → 档失败降级到下一档；
  渠道可选「同档重试」（`options.retry`，上限 2，默认 0 = 直接降档，避免重复扣费）。
- **路由决策响应头**：`X-QLike-Chain`（完整候选链）/ `X-QLike-Attempt`（实际尝试次数）/
  `X-QLike-Degrade`（降了几档，0 = 首档成功）/ `X-QLike-Queue-Ms`（排队耗时）。
- **日志可回放完整请求**：请求日志新增 `upstream_url` / `upstream_method` / `upstream_headers`
  三列（密钥一律写成 `YOUR_API_KEY` 占位符，**绝不落明文**）；面板「请求日志 → 详情」新增
  **可直接复制的 curl**（客户端 → 本网关 / 本网关 → 上游两条），提示词支持**多行展示**（可切严格 JSON）。
- **上游模型列表一键拉取**：`POST /api/providers/{key}/fetch-models` —— 只发一个 `GET /v1/models`
  （**零成本，绝不出图**），路径可用 `options.models_path` 覆盖；面板渠道弹窗内勾选后一键写进模型映射。
- `GET /api/sysinfo` 增加 `gate`（并发占用/排队/拒绝数）与 `router`（最大尝试次数、可重试状态码）字段，
  控制台「系统信息」页新增「并发闸门」「路由决策」两行。

### Changed
- **登录页配色回归项目规范**：不再照搬参照站点的青绿主色，改为与项目统一的 `--brand`（`#2f6bff`）体系；
  `login.css` 改为**只引用 `tokens.css` 变量**，并跟随系统偏好自动进入深色模式。
- `tokens.css` 新增 `--surface` / `--field` / `--brand-ink` 三个变量（卡片面 / 字段底色 / 主色上的前景色）。
- [`docs/DESIGN-SYSTEM.md`](docs/DESIGN-SYSTEM.md) 新增 **§2.1 配色规范**：主色 / 语义色 / 中性色 /
  深色对应表 + 使用规则（禁止写死色值、语义色不做装饰、状态必须带文字等）。
- 模型清单去重：同一平台里重复命名（含首尾空格）的模型只保留一条（`protocols.model_list`）。
- 渠道弹窗新增「并发上限」「同档重试」两个字段（写进 `options`，表单值优先于 JSON 里的同名字段）。
- 测试 208 → **237** 个用例（新增 `tests/test_router_gate.py`、`tests/test_models_fetch.py`；
  `conftest.py` 增加 `reset_relay_state`：每个用例清 relay 的进程级内存态，避免 key 冷却跨用例污染）。

### Fixed
- 渠道「同档重试」在 JSON 里填了非法值时不再抛异常（退回 0）。

## [3.4.1] - 2026-09-17

### Added
- 站点图标 `app/static/favicon.svg`，登录页 / 控制台 / 组件展示页统一引用。
- 登录页样式表 `app/static/css/login.css`（`lg-*` 命名空间，不污染控制台 tokens 与 ui-kit 组件）。
- [`docs/SMART-ROUTING-PLAN.md`](docs/SMART-ROUTING-PLAN.md)：New API / Sub2API 的智能路由与负载并发控制调研 + 三阶段整合规划；附「转发时尺寸变化」的根因定位（含真实日志证据）。

### Changed
- **登录页改版**：视觉参照 Sub2API 登录页 —— 浅色青绿渐变 + 方格纹理、居中品牌区、白色圆角卡片、图标输入框、密码可见性切换、提交态与错误提示。
- **站点标题统一为 `QlikeAPI`**：登录页 `登录 · QlikeAPI`、控制台 `QlikeAPI 控制台 · 图片协议转换网关`、组件页 `UI 组件库 · QlikeAPI`；侧边栏品牌标识改用同源图标。

## [3.4.0] - 2026-09-17

控制台可用性三改 + 可复用组件库 + 设计规范。

### Added
- **组件库**：`app/static/js/ui-kit.js`（`window.UI.*`：`toast` / `confirm` / `modal` / `picker` /
  `busy` / `bar` / `copy`，无依赖、零构建）+ `app/static/css/tokens.css`（设计变量）+
  `app/static/css/ui-kit.css`（组件样式）。历史样式**按原顺序**迁入，不改变任何视觉表现。
- **组件展示页 `GET /ui-kit`**：每个组件都能真点（多选选择器、确认框、吐司、进度条、弹窗、表格）。
- **设计规范** [`docs/DESIGN-SYSTEM.md`](docs/DESIGN-SYSTEM.md)：设计变量、组件清单、交互与文案约定、
  变更流程、坑清单（含本次踩到的 CSS 顺序翻转与截图脱敏）。
- **接口** `GET /api/meta/options`：返回模型与渠道选项（含每个模型的可用渠道数），供控制台下拉多选使用。
- **脚本** `scripts/ui_style_diff.py`（`make ui-diff`）：同一页面状态下 A/B 切换新旧样式表，
  逐元素比对计算样式，验证 CSS 重构零回归。

### Changed
- 访问令牌弹窗：「允许的模型 / 允许的渠道」由逗号分隔文本框改为**下拉多选选择器**
  （搜索、全选、清空、已选计数）。
- 站点余额页：每行操作按钮改为**横向一排**（`.acts`）。
- 前端目录整理：`static/app.js` → `static/js/app.js`；`static/style.css` 拆为
  `static/css/tokens.css` + `static/css/ui-kit.css`；登录页重写。
- 原生 `confirm()` 统一替换为组件库的 `UI.confirm`（可键盘关闭、有焦点管理）。
- 测试 199 → **208** 个用例（新增 `/api/meta/options` 用例、`tests/test_static.py` 静态检查：
  `onclick` 反斜杠、内联脚本 `node --check`、页面引用完整性）。

### Security
- `python-multipart` 0.0.20 → **0.0.32**（清掉 Dependabot 13 条告警，含 4 条 high；
  该库负责登录表单与 `/v1/images/edits` 的 multipart 解析，升级后已回归验证：208 用例 +
  浏览器登录 + multipart 无 prompt 本地 400）。

### Fixed
- 组件展示页内联脚本语法错误：HTML 拼在 JS 单引号字符串里时 `\'` 会**原样进入属性**，浏览器按 JS 解析
  直接报 `SyntaxError`，整块脚本失效（所有演示按钮点了没反应，只有控制台能看到）。改用 HTML 实体
  `&#39;`，并新增 `tests/test_static.py`（`node --check` 内联脚本 + `onclick` 反斜杠检查）兜底。
- CSS 拆分时合并重复规则导致 `.side-toggle` 在桌面端露出：改为**完全保序拆分**，
  并用**提升特异性**（而非依赖书写顺序）加固折叠按钮显隐；新增 A/B 计算样式比对兜底。

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
