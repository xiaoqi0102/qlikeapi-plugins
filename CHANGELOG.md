## v3.16.0 — 2026-09-19

### 新增：图床候选每行一个独立「启用 / 停用」开关（用户反馈）
- 原来「启用」列是一个裸复选框：只能算「是否参与候选」，改完还得滚到底下点「保存」才生效，
  点一下没有任何反馈 → 看起来像「没有单独的启用/停用」。
- 现在每行是一个**开关（switch）**，点一下**立刻保存**（`POST /api/settings/imagehost`），
  状态药丸「已启用 / 已停用」常显；不用再点保存。上下移、自检按钮不变。
- 顺带修：开关 / 上下移**只重画候选表**（`ihRefreshTable()`），不再整块重渲染 —— 否则会把
  上面还没保存的 ImgBB Key 冲掉。

### 新增：模型目录单价可手动编辑（用户反馈）
- 每行「真实单价」后面加铅笔按钮 → 弹窗填**单价 / 币种（USD/CNY）/ 备注** → 写
  `model_prices(model, provider)` 这一格，`source=manual`。
- **手工价保命**：`balances._keep_manual()` + `store.price_exact()` —— 「同步价格」逐格检查，
  是手工填的就**跳过**（手工 > 上游），结果里用 `skipped` 报出跳过了哪几格（面板 toast 也会说）。
- 弹窗里可「清除手工价」（`DELETE /api/prices/{id}`）→ 回落成上游同步来的价 / 未定价。
- `GET /api/models` 增加 `price_id`（清除手工价要用）。

## v3.15.5 — 2026-09-19

### 新增：设置页「运行信息」加服务器信息（只读探测，零网络）
- 新增 `app/sysinfo.py`：主机名 / 系统 / 宿主内核 / 架构 / CPU 核数与型号 / 负载 /
  运行时长（宿主 + 本服务）/ 时区（含服务器本地时间，便于和本地对表）/ 运行环境
  （是否 Docker + 容器 IP）/ 内存 / 磁盘 / 数据文件大小 / Python 版本。
- **容器里必须两个口径都给**：`os.cpu_count()`、`/proc/meminfo` 读到的是**宿主**值，
  所以额外读 cgroup v2（`/sys/fs/cgroup/memory.max`、`cpu.max`）给出「容器上限」，
  避免出现「明明限了 2 核却显示 64 核」。探测失败一律降级成 `None`，绝不抛错。
- 新增 `GET /api/sysinfo`（`app/admin.py`）；面板设置页渲染「服务器」「资源」区块。

### 修正：设置页信息区块改四列对齐表 + CPU 占用率 + 实时刷新（用户反馈）
- **对齐**：「服务器 / 资源」原本是两块各自独立的键值表，行数与行高都不同 →
  左右两半的行分隔线最多错位 47px，底部也没有收口线。改成**一张四列键值表**
  （左半「服务器」+ 右半「资源」共用同一批行）→ 行线天然对齐；表头两格各跨两列
  当区块小标题；底部由 `.set-sec` 收一条分隔线。「环境 + 运行策略」同样处理，
  并把「密钥加密」从运行策略挪到环境，两侧行数对齐。
- **CPU 占用率**：新增 `sysinfo._cpu_usage()`，读 `/proc/stat` 两次采样差值算百分比；
  首次调用没有基准 → 返回 `None`（前端自动不显示这一行）。容器内是宿主整机口径。
- **实时刷新**：新增 `serverLiveTick()`，**每 5 秒只重画这张表的 tbody**
  （服务器时间 / 运行时长 / CPU 占用 / 负载 / 内存 / 磁盘），不弹顶部进度条、
  不整页重画、不打扰正在填的表单；30 秒的整页自动刷新跳过设置页，避免覆盖输入。
- `ui-kit.css` 新增 `.set-sec` 底线与 `.kv2` 四列表样式（全用 `--line`/`--sp-3`/`--ink-3`
  令牌，`ui_lint` 0 处告警）；`.kv2` 规则用 `table.tb.kv.kv2` 提升优先级，
  否则会被后面的 `table.tb.kv th{width:33%}` 反压 → 值列被挤成窄条、行高 400px+。
- `scripts/ui_style_diff.py`：把 `.set-sec`/`.kv2` 登记为「新增组件」，
  A/B 回归仍为「合计差异: 0」（其余 12 个页面/主题/宽度组合完全一致）。
- 测试：`test_sysinfo.py` 增 `test_cpu_usage_needs_two_samples`；
  `test_models_fetch.py` 断言 `cpu.usage` 合法（None 或 0~100）。

## v3.15.3 — 2026-09-18

### 修正：「同步上游支持的模型」只拉到第一把密钥的分组（用户实测）
- sub2api 系上游的密钥**绑分组**（change2pro 实测：`gemini::` 那把只看到 4 个 gemini 模型、
  `gpt::` 那把只看到 3 个 gpt 模型），旧实现只拿第一把 key 去拉 → 永远丢一半，
  面板底部却提示「共 4 个模型」看着像成功，实际 `gpt-image-2`/`-flare`/`-sunburst` 全没进来。
- 新增 `protocols.fetch_upstream_models_multi()`：**逐把密钥各拉一次、取并集**，返回
  `groups`（每个分组标签 → 它那组看到的模型）与 `errors`（哪把没拉到，其余照收）；
  `POST /api/providers/{key}/fetch-models?group=<标签>` 仍可只看单组。
- 面板：模型清单与 toast 显示分组来源（`去重后 7 个模型（gemini 4 个 + gpt 3 个）`），
  一眼看出并集齐不齐。实测 change2pro 4 → 7；aicost(17) / qnaigc(81) 无回归。
- 测试：新增 3 项（并集 / 单组过滤 + 部分失败 / 接口层并集）。

### 新增：渠道弹窗去掉「插件预置」，模型候选只认上游
- 用户口径：「插件内置不需要，只要上游实际获取的模型列表」。删掉 `#mapPills` 预置药丸、
  「快捷添加（本插件的预置模型…）」标签、「同步最新支持模型」按钮与
  `act.mapSyncPreset()/mapAddPreset()/mapFamily()`；候选来源只剩 **上游同步过的（标「已同步」）
  ∪ 当前已选**（已选值必须保留，否则 chips 渲染不出来）。

### 新增：同步上游模型时只保留图片模型（过滤视频/对话）
- aicost 实测 `/v1/models` 回 17 个，其中 12 个是 seedance 视频；qnaigc 更极端 —— 81 个**全是对话模型**，
  一个图片模型都没有（它的图片模型不在这个接口里）。
- `protocols.is_image_model()/split_image_models()`：**先排除视频关键字**（`seedance`/`veo`/`kling`/`i2v`/
  `image-to-video` …，避免名字里带 image 的视频被误收），再要求命中图片家族关键字
  （`image`/`banana`/`flux`/`dall-e`/`seedream`/`z-image`/`sd3` …）。
- `fetch_upstream_models_multi(..., image_only=True)` 默认过滤，被滤掉的进 `dropped`；
  面板显示「已过滤 N 个非图片模型（视频/对话等）」+ 一个「显示全部（含非图片）」按钮
  （走 `POST /api/providers/{key}/fetch-models?all=1`）。
- 实测：aicost 默认 5 个图片模型 ✓ / `?all=1` 17 个 ✓；change2pro 仍 7 个 ✓。

### 精简：同步上游后不再铺「结果预览块」
- 用户口径：「点击完同步上游模型，下面出现的这个是必要的吗？不需要吧？」
  模型已经进了白名单 chips、候选下拉里也标着「已同步」，再列一遍是重复。
- 现在同步完下面**不再**出现提示文字 + 一长串模型 chips（`#upModels` 只在真的过滤掉非图片模型时
  留一行「已过滤 N 个非图片模型（视频/对话等） + 显示全部（含非图片）」）；同步数量改由 toast 汇报。
  连带删掉 `act.upModelsHide()`（收起按钮没了）。
- 实测（真实 DOM）：同步后白名单 5 个图片模型 ✓、下方预览 chips 0 个 ✓、只一行过滤提示 ✓；
  点「显示全部」白名单变 17 个（含 seedance）✓、下方提示清空 ✓；无 JS 报错 ✓。

### 修正：只认 base64 的渠道，客户端给的参考图 URL 会被下载内联（aicost gpt-image-2 编辑面 400）
- 用户实测：走网关调 aicost 的 `gpt-image-2` 改图，客户端**直接给公网 URL**（如 `https://i.ibb.co/...`），
  上游回 400「输入的图片有误，请确认图片格式/链接是否正确」。网关日志证实发出去的 `image` 字段就是那条 URL。
- 零成本探针（aicost `/v1/images/edits`）定性：参考图给 **URL → 400**；给 **data URI / 裸 base64 → 200 正常出图**。
  → aicost 的 image2 面**只吃 base64**，插件原先声明 `"both"` 是错的。
- 插件：`aicost.ref_input_faces` 两面都改成 `base64`，`declared_ref_input()` 恒回 `base64`；`protocol_note` 写清依据。
- 网关：新增**反方向**转换 `imagehost.inline_url_refs()` / `fetch_ref()` / `to_data_uri()` ——
  客户端给公网 URL 时**下载进内存**（不落盘、不转存）换成 `data:<mime>;base64,…`；
  `relay.prepare()` 对 `policy == "base64"` 的渠道走这条路，下载失败**本地 400 带明细**（不把注定失败的 URL 丢给上游）。
  顺带把「按值替换参考图字段」抽成 `_replace_ref_values()`，两个方向共用。
- 实测（零成本 dry-run `POST /up/aicost/v1/images/edits/preview`）：用户那条报文现在发给 aicost 的
  `image` 已是 `data:image/jpeg;base64,…` ✓。
- ⚠️ 探针 B/C 本以为非法 `size` 会先被拒，实际被接受并**真出图成功**（2 张，aicost 余额 1.0715 → 1.051，约 $0.0205）。
  记在案：探针要挑**必然 400** 的字段，别拿 size 当挡箭牌。

### 改进：参考图下载改为「流式 + 边读边卡上限」；日志不再存 base64
- 用户问「下载完转成 base64 会删除吗？占内存吗？」→ 顺手把两个真问题修了：
- **内存**：`fetch_ref()` 原来 `c.get(url)` 先整体缓冲**再**判大小 → 超大文件会先吃满内存。
  改成 `c.stream()` + 64KB 分块，**超 `max_mb` 立刻中断**；返回的字节只在本次请求内存在，
  请求结束即回收（不缓存、不落盘），`httpx` 客户端也 `close()` 掉。
- **日志**：`store.log_row` 原来会把 base64 截 8000 字符写进 SQLite。新增 `utils.compact_b64()`，
  长 base64 / data URI 一律落成 `<base64:511167 bytes>` 占位（保留长度信息），日志可读且不占地方。
- 实测（零成本端到端：容器内假上游 + 临时 aicost 渠道，测完删干净）：
  上游收到 `image: ["data:image/jpeg;base64,…"]` ✓；日志 `upstream_request` 仅 174 字节，
  含 `<base64:511167 bytes>` ✓。
- 现状提醒：`QLIKEAPI_MAX_CONCURRENCY` 未设 = 并发不限；单张上限由图床配置 `max_mb`（默认 20MB）决定，
  峰值 ≈ 单张体积 ×2~3 × 并发数。要收紧就调这两个值，不用改代码。

### 新增：渠道可选 `options.retry_on_4xx` —— 上游 4xx 也换下一个渠道
- 用户问「aicost 都失败好几次了怎么不自动轮换到 change2pro？」→ 查清两条：
  ① 他调的是 `/up/aicost/...`（**单渠道直连面**，设计上就钉死一个渠道、不轮换）；
  ② 那 5 次失败是上游 **400**，而 `RETRYABLE` 名单是 `402/408/409/425/429/5xx/529`，**400 默认不换**
  （"请求本身有问题直接返回，避免无谓重试"）；日志佐证 `attempts=1`。
- 但这次的 400 本质是**渠道口径差异**（aicost 编辑面不吃 URL 参考图、change2pro 吃）→ 值得可配。
  新增 `relay._retryable(p, status)`：默认行为不变，渠道实例开 `options.retry_on_4xx: true` 后 4xx 也换下一个渠道。
  面板 options 提示已列出该键；已给 `aicost` 打开（`{"retry_on_4xx": true}`），要关就删掉该键。
- 实测（零成本端到端：两个假上游 400/200 + 两个临时渠道，测完删干净）：
  统一入口改图 → `HTTP 200`、`X-QLike-Provider: zz-4xx-b`、`X-QLike-Failover: 1`、`X-QLike-Attempt: 2` ✓。
- 提醒：`/up/<渠道>/...` 直连面永远单渠道；要自动兜底请用统一入口 `/v1/images/edits`。

### 改进：日志里「参考图转换」两个方向都记，且不再是一串英文占位
- 用户口径：「保留，URL 转 base64 还是 base64 转 URL 都显示」。
- `logs` 表新增 `imagehost` 列（自动迁移，老行留空）：存转换明细
  `[{"mode":"inline","from":"<原URL>","bytes":383357,"mime":"image/jpeg"}]` /
  `[{"host":"imgbb","bytes":204800}]`。面板「日志详情」据此多显示一行
  **参考图转换：URL → 内联 base64（约 374KB）** 或 **约 200KB → 图床直链（imgbb）**。
- `relay._imagehost_info()` 两个方向都报（之前只报上传方向、内联方向被过滤掉了）；
  响应头 `X-QLike-Imagehost` 相应变成 `imgbb` / `inline` / `imgbb,inline`。
- 日志里的长 base64 占位符从 `<base64:511167 bytes>` 改成中文
  `<参考图 base64 数据，约 374KB>`（一眼看懂，仍不存图）。
- 实测（零成本端到端，假上游 + 临时渠道，测完删干净）：`X-QLike-Imagehost: inline`；
  日志 `upstream_request` 里 image 字段为 `<参考图 base64 数据，约 374KB>`；`imagehost` 列记下 from/bytes/mime ✓。

### 修复：日志详情的「①② 完整请求」标题行对齐
- 现象（用户截图指出）：② 的标题比 ① 长，`flex-wrap:wrap` 把右侧按钮组挤到第二行并变成左对齐
  （实测 ① `acts.left=783`、② `acts.left=258`，② 行高 60 vs ① 33）。
- 修法：新增 `.curl-head`（`flex-wrap:nowrap` + 标题 `flex:1 1 auto;min-width:0` + `.acts{flex:0 0 auto}`），
  标题过长时在自身内部换行，按钮恒在右上；两个区块标题同步缩短（说明文字移到下面那行提示）。
- 实测（真实 DOM，同一弹窗）：①② 均 `rowH=33`、`actsLeft=783`、`sameLine=True` —— 完全对齐。
- `make ui-diff` 期望「合计差异: 0」✓（顺带修好 Makefile 的 ui-diff 目标：`.venv` 里没 playwright，改用系统 `python3` 并自动注入管理凭据环境变量）。

### 修复：客户端那一行（路由汇总）看不到「② 本网关 → 上游」
- 现象：客户端真实走的是 `/v1/images`，落的是 `kind=router` 汇总行，该行只记了 ① 客户端报文，
  ② 显示 `--data 'null'` + 「老日志没记上游地址」。
- 修法：`invoke_provider()` 的成功返回里带上 `up_url/up_method/up_body/up_headers`（请求头已过
  `_redact_headers()`，无凭据），路由汇总行把它们一起落库。
- 实测：路由汇总行 `POST http://…/v1/images/edits` + 上游报文（参考图仍是 `<参考图 base64 数据，约 374KB>`）✓。
- 同时把探针留下的 3 条测试日志行（#285/#288/#290）清掉。

### 修复：日志详情「参考图转换」这行在 base64 直传时完全不出现
- 现象（用户反馈）：日常请求都是客户端直接传 base64，网关原样转发、没有任何转换，
  于是 `imagehost` 为空 → 这一行整行不渲染，看着像「功能没做」。
- 修法：新增 `act.refNote()` —— 有转换记录就报转换（两个方向）；没有转换但请求里带着
  base64 参考图（识别报文里的 `<参考图 base64 数据，约 NKB>` 占位）就报
  「参考图：base64 直传（约 NKB，网关未转换）」。**旧日志同样生效**（数据本来就在报文里）。
- 实测（真实 DOM，弹窗里读 `.hint`）：#301/#302 →「参考图转换：URL → 内联 base64（约 374KB）」；
  #291（真实客户端请求）→「参考图：base64 直传（约 1810KB，网关未转换）」。
- 顺手清掉探针遗留：3 条测试日志行 + 临时渠道 `zz-log-inline`（渠道实例现在只剩 aicost/change2pro/qnaigc）。

### 参考图说明：两个方向的文案对称化 + 图床回退不再显示错
- 用户问「内联 base64（约 374KB）→ URL 这种情况呢？」——该方向一直支持，但文案只写「约 374KB → 图床直链（imgbb）」，
  没说清**从什么转过来**。现在两个方向都写成「A → B」：
  · 渠道只认 base64 ← 客户端给 URL：`URL → 内联 base64（约 NKB）`
  · 渠道只认公网 URL ← 客户端给 base64：`内联 base64（约 NKB）→ 图床直链（host）`
- `imagehost.py` 图床方向的 note 补 `mode: "imgbb"`（前端不再靠「没有 mode」猜方向）；`_imagehost_info()` 文案同步。
- 修 bug：图床中转失败的回退 note（`{"fallback":true,"warnings":[…]}`）原先被渲染成「约 1KB → 图床直链（）」，
  现在显示「图床中转失败，已按原样转发（上游可能拒收）」。
- 测试：`test_router_gate.py` 文案断言更新；`test_relay_imagehost.py` 补「图床方向的 note 必须落到 logs.imagehost」
  （mode=imgbb / host / bytes 三项断言）。
- 零成本实测（浏览器里直接调真实 JS `act.imagehostNote()`，6 种输入）：内联 / 图床 / 双向 / 回退 / 老格式 / 无转换 —— 全部符合预期。

## v3.15.2 — 2026-09-18

### 修正：CI 变红（文档防漂移校验失败）
- `docs/PLUGIN-AUTHORING.md` 是 `app/plugindoc.py` 生成的（面板里「复制说明」也是同一份），
  v3.15.1 手改了文档却没同步生成器，导致 `tests/test_plugindoc_matches_repo_file` 失败、CI 从 v3.15.1 起一直是红的。
- 现在把 `site_type` 那一行补进生成器并重新生成文档 —— 两边重新一致，CI 恢复全绿。

### 新增：Docker 镜像发布流水线（GHCR）
- `.github/workflows/docker-publish.yml`：打 `v*` 标签时自动构建并推送
  `ghcr.io/xiaoqi0102/qlikeapi-plugins`（标签 `3.15.2` / `3.15` / `v3.15.2` / `latest` / `sha-xxxx`），
  用 Actions 自带的 `GITHUB_TOKEN`（`permissions.packages: write`），不需要用户 PAT。
- 推送后自动把镜像拉回来跑一遍冒烟（`/healthz` 必须通），避免推上去一个起不来的镜像。
- 也支持手动补推：Actions 页面 `Run workflow` 传 `version=v3.15.2`。

### 新增：首次归档记录
- `docs/ARCHIVE.md` —— 记录本次归档的版本、产物位置、恢复方式与验收证据（归档点 = v3.15.2）。

## v3.15.1 — 2026-09-18

### 新增：异步任务页展示所有渠道的异步请求（不再只有 fal 队列）
- 以前只有「提交任务 + 轮询取结果」的队列型渠道（七牛 fal）会进「异步任务」页；
  上游先回任务号、由本服务轮询的渠道（如 aicost.me）虽然也是异步，却一条都不记。
- `jobs` 表新增 `mode` 列（`queue` = 队列轮询 / `poll` = 任务号轮询），`relay.py::_job_id()` 负责
  从上游响应里取任务号（插件声明的 `task_id` 优先，再兜底扫常见字段名）。
- 页面标题改为「异步任务」，新增「方式」和「耗时」两列；提交记 `RUNNING` → 拿到图 `DONE` + 结果链接 →
  失败/超时 `FAILED` + 原因；不是异步任务的响应会立刻删掉占位行，不留脏数据。

### 新增：渠道实例 ↔ 站点余额联动
- 保存渠道时自动保证有对应「站点余额」条目：同 `base_url` 只建一个（已存在就复用，**绝不覆盖已填的令牌/uid**）。
- 站点类型按插件声明的 `site_type` 走（`change2pro`→`sub2api`、`aicost`→`newapi`，未声明按 `manual` 手工记账），
  免得自动建出来就天天报「取数失败」。
- **令牌 / id 一律留空，由用户手动填**（这是用户明确要求的口径）；保存后提示到「站点余额」页补齐，
  `site_id` 自动回填到渠道上，余额页立刻能看到这一条。
- `channels/base.py` 新增可选声明 `site_type`；`POST /providers` 里 `site_id=0` 视为「未指定」。

### 修正：分组倍率口径精确化（优先读令牌的 `groups` 数组）
- `GET /api/token/` 的 `group` 字段只反映默认分组；**令牌对象里的 `groups` 数组才是完整的选择顺序**
  （首选 → 备用）。现在优先读它，读不到才退回「该模型可售分组里最便宜的」并标注估算。
- 复验（与用户日志分毫不差）：`gpt-image-2` = $0.445 × `gpt-image-2-主` 0.023 = **$0.010235/张**。

### 实测
- **aicost 图片面首次真实出图**：`/up/aicost/v1/images/generations` 打 `gpt-image-2` / `1024x1024`
  → HTTP 200、23.1 秒、1 张图（回 `b64_json`）。余额 $1.0817 → $1.0715，**实付 $0.0102**，
  与「裸价 × 分组倍率」的推算一致。
- 据此把 aicost 提到图片链路首选（`priority 11 > change2pro 10 > qnaigc 5`）——
  它的 `gpt-image-2` 单价只有 change2pro 的 1/6，而且此前已在实际请求里兜底成功出过图。

### 测试
- 新增 `tests/test_site_sync.py`（6 例）：自动建站点 / 同 base_url 复用不覆盖 / 插件声明决定类型 /
  `site_id` 回填 / 未指定不报错 / 余额页能查到。

## v3.15.0 — 2026-09-19

### 新增：价格同步改成「按渠道独立、从各自平台直读」
- **每个渠道一个自己的「同步价格」按钮**（模型目录页，每个渠道块的标题栏里），点谁只同步谁，
  别的渠道一行价都不动：`POST /api/providers/{key}/prices/sync`。
- **直读渠道自己配置的平台**（不再只看别处的转售价）：
  - `newapi` 系站点（如 aicost.me）→ `GET {base}/api/pricing`（`Bearer` + `New-Api-User: <uid>`），
    取按次报价 `quota_type=1` 的 `model_price`，单位 USD，来源标记 `platform`（界面显示「平台直读」）。
  - `sub2api` 系站点（如 change2pro）→ `GET {base}/v1/usage` 的每模型累计消耗 ÷ 请求数，
    来源标记 `upstream`（界面显示「上游实测」）。
- **落库口径修正**：价格按**客户端模型名**写（`模型目录` 表就是按这个名字查价的）。
  此前 sub2api 的用量反推价按「上游真实名」落库，导致表格里显示「未定价」——现在会正确显示。
- 没绑定站点 / 站点类型不支持时给出可读错误（红色提示），不再静默返回 0 条。
- 全部站点一起同步的按钮改叫「全部渠道同步」，并会把失败站点名与原因带进提示。

### 修正：价格必须计入「分组倍率」（同一个模型在不同分组不是一个价）
- aicost 面板原话：**一个令牌可以绑多个分组，第一个是默认请求分组，后面的按选择顺序作为失败备用分组**；
  每个分组有自己的倍率折扣；模型只在部分分组可售（`enable_groups`）。
- 所以口径定为：**实付单价 = 模型裸价 × 实际服务分组的倍率**，
  实际服务分组 = `令牌分组顺序 ∩ 该模型可售分组` 的第一个（与 New API 的路由语义一致）。
- 令牌分组从 `GET /api/token/` 读（列表里 key 是打码的 → 用尾 4 位匹配；`group` 支持逗号分隔多分组）。
  读不到时退化为「该模型可售分组里最便宜的那个」，并在备注里标明是估算；连分组信息都没有时倍率按 1，不编造。
- 价格来源备注会把账写全，例如：
  `aicost.me /api/pricing：gpt-image-2 $0.445 × 令牌分组 gpt-image-2-主 倍率 0.023 = $0.010235/次`。
- 渠道选项里可填 `price_ratio` 手工锁死倍率，或 `price_groups` 手工指定令牌分组顺序
  （数组 / 逗号串，如 `["即梦便宜900分组","gpt-image-2-主","nano-banana渠道2"]`）——
  令牌列表读不到时也能按真实分组算倍率。

### 实测（零成本，只读接口）
- aicost.me `/api/pricing`：64 个模型带按次报价；`group_ratio` 共 30 个分组；
  令牌 `sd` 绑了 6 个分组（首个 `即梦便宜900分组`，含 `gpt-image-2-主` 0.023 / `nano-banana渠道2` 0.6）。
- 按上面口径推算（与用户日志一致）：`gpt-image-2 = $0.445 × 0.023 = $0.010235/张`；
  `gemini-3-pro-image = $0.12 × 0.6 = $0.072/张`；`gemini-3.1-flash-image = $0.1 × 0.6 = $0.06/张`；
  `gpt-image-2.5-flare / sunburst = $0.5 × 0.023 = $0.0115/张`。
- 对比 change2pro 实测 $0.06/张 —— aicost 的 image2 面便宜得多，但它的图片面**尚未真实出图验证过**。

### 测试
- 新增 `tests/test_prices_sync.py`（5 例）：客户端名落库 / 只动被点的渠道 / 未绑站点报错 /
  类型不支持报错 / sub2api 用量反推仍然可用。

## v3.14.2 — 2026-09-19

### 修复
- **change2pro 改图面字段口径**：站点（sub2api）改图面只认 `images:[{image_url: ...}]`，不认 OpenAI 的
  `image:[...]` —— 之前一律 400 `images[].image_url is required`。现在由插件层自动翻译，**客户端零改动**。
  实测：改成 `images[]` 后校验通过（进而走到账号池 503，与字段口径无关）。
- **熔断重复计数**：全部 key 已在冷却里的「短路」请求（`attempts=0`，根本没打上游）不再计入渠道级
  `fail_streak`。此前一串请求（或一次探针）就能把整条渠道顶到阈值自动停用 10 分钟 —— 实测踩到过。

### 测试
- 新增 `tests/test_breaker.py`：① 短路不计数；② 真失败够阈值仍会熔断。
- 新增 `tests/test_channels.py::test_change2pro_edit_translates_refs_to_images_array`。

## v3.14.1 — 数字字段规范化（修「客户端把 n 发成字符串导致上游 400」）

**现象**：出图失败，上游回
`invalid request body: json: cannot unmarshal string into Go struct field RelayImageEditForm.n of type int`。

**根因**：客户端（可视化工作流工具常见）把 `n` 序列化成字符串 `"1"`，而翻译层对客户端字段是**原样透传**（只做字段删除，不做类型规范化）；
七牛 / sub2api / new-api 这类 Go 上游用强类型结构体接参，字符串直接 400。

**修复**：翻译层新增数字字段规范化（`utils.coerce_numeric_fields` / `utils.to_int`），
只对白名单里的数字字段（`n` / `seed` / `steps` / `width` / `height` / `output_compression` / `temperature` / `top_p` …）
做「看起来是数字就转成真数字」，**非数字串（`"auto"` / `"1024x1152"` / `""`）原样保留**，尺寸与枚举字段不受影响。
- `build_openai_images`（generations + edits，覆盖 qnaigc / change2pro / aicost / 任意自定义插件）
- `build_fal_queue`（顺带修掉 `int(body["n"])` 遇到 `"auto"` 抛 500 的老坑）

**测试**：新增 3 条用例（字符串数字被规范化 / 非数字串不动 / fal 面字符串与非法值都安全）。

**顺带**：新增 `POST /up/{provider}/v1/images/edits/preview` —— 改图面（带参考图）的 dry-run，只回「将要发给上游的请求」不发出去（这类问题就是靠它零成本看到真实报文）。

## [3.14.0] - 2026-09-18

### 新增
- **aicost.me 生图渠道插件**（`app/channels/aicost.py`，口径来自站点方《aicost.me 图片插件模型接口文档》）：
  一个插件覆盖该站两套图片协议，按模型名自动分流 —— `gemini-*` 走
  `POST /v1beta/models/{model}:generateContent`（Base 去掉 `/v1` 再拼 `v1beta`），
  `gpt-image-*` 走 `POST /v1/images/generations|edits`。
  站点方文档写死的「必填」字段按文档补默认值（image2 面 `n=1 / quality=auto / output_format=jpeg /
  moderation=auto`；gemini 面 `responseModalities=["TEXT","IMAGE"]` + `imageConfig` 默认 `16:9 / 2K`），
  **客户端显式给了就听客户端的**。解析覆盖文档 §5 列出的全部返回位置（`b64_json` / `image_base64` /
  `images[]` / `output` / `result` / `choices[].message.content` 里的链接 / gemini `inlineData`）。
  模型预置映射（`gemini-3-pro-image` → `gemini-3-pro-image-preview` 等）在实例漏配时兜底，插件开箱可用。
- **插件可选钩子 `poll()`：上游回异步任务时也能同步交付**。有的站点不回图而回
  `{"task_id": "...", "status": "pending"}`（aicost.me 文档 §2.3 / §3.3）。插件实现 `poll()` 后，
  relay 只在「上游没直接给图」时调用它轮询到出图，客户端拿到的仍是 OpenAI 形状的同步结果；
  **没实现 `poll()` 的插件行为一点不变**。`("SKIP", None)` = 不是异步任务，交回常规流程。

### 文档
- 《渠道插件编写说明》新增 **4.1 节（`poll()` 契约）**、「现成实现」表新增一行；`_template.py` 与
  `docs/CHANNEL-PLUGINS.md` 同步（含 aicost 参考实现）。

## [3.13.2] - 2026-09-18

### 修复
- **渠道插件页「操作」按钮竖排**（用户反馈）：该列被前面几列的 chips 挤到 ~53px，Bootstrap 的 `flex-wrap`
  于是让每个按钮各占一行。现在操作列按内容收缩 + 不换行、容器固定 `flex-direction:row`，
  编辑/停用/删除多个按钮也始终横排（与渠道实例页的操作列一致）。
- **左下角状态栏把插件名一个个列出来**（用户反馈「插件会越装越多」）：改成只显示数量（`插件 5`），
  全名收进悬停提示，插件再多也撑不爆底栏。
- **change2pro 站点余额查询失败**：该站点记录 base_url 填的是站点主页 `change2pro.com`、key 为空，
  而接口是 `https://api.change2pro.com/v1/usage`（`Authorization: Bearer`）。已修正该站点记录；
  并按飞书《站点余额查询代码接入》补齐取数口径 —— `remaining = data.remaining ?? quota.remaining ?? data.balance`、
  `used = quota.used ?? usage.total.cost`，同时带上「请求数 / 累计 tokens / 额度上限」明细，
  sub2api 系站点（sixoner / kaola / cch 等）现在也能显示「已用」。

## [3.13.1] - 2026-09-18

### 修复
- **日志详情顺序反了**（用户反馈）：原来先显示「本网关 → 上游」，再显示「客户端 → 本网关」。
  改成按请求实际流向自上而下排列，并加上 ①② 编号：
  `① 客户端 → 本网关`（入口形态 curl）→ `② 本网关 → 上游`（翻译后 curl）→ ① 客户端原始报文 → ② 上游原始报文 → 响应片段。

### 变更
- **设置页整页重排**（用户反馈「太拥挤」）：
  - 运行信息：顶部 4 张指标卡（版本 / 已装载插件 / 数据量 / 密钥加密），下面拆成「环境」「运行策略」**左右两栏键值表**，
    不再是一条长长的竖排表格；新增右上角「刷新」按钮。
  - 图床：五家候选服务改成**对齐的对照表**（启用 / 服务 / 有效期 / 上传地址 / 操作），
    说明文字降级为服务名下方的小字，参数区独立成「参数」小节。
  - 修改密码 + 界面规范与组件库并排放在最下面（原来和长表格挤在同一行，被拉得很难看）。

## [3.13.0] - 2026-09-18

### 新增
- **渠道插件页可增 / 删 / 改**：面板里直接「添加插件 → 贴代码 → 校验 → 安装」「编辑」「停用 / 启用」「删除」，
  不用进服务器放文件。上传的插件落在 `QLIKEAPI_PLUGIN_DIR`（默认 `/data/plugins`，挂载卷 → 重建容器不丢），
  保存即热重载，**不用重启容器**。内置插件只读（要改就另存为新文件名）。
- **安装前自动校验**（`app/pluginstore.py`）：语法 → 结构（`Channel` 子类 + `CHANNEL` 实例）→
  危险写法（进程 / 网络 / 文件 / 反射逃逸，带行号）→ **真装一次**（子进程执行、剥掉密钥类环境变量、带超时）
  → 元信息完整性（`id/label/vendor/docs/protocol_note/hint`、`operations`、`ref_input`、`default_auth`）。
  报告分「错误 / 提醒 / 通过项」三级，有错误拒绝写入；安装前必须勾选「插件是可执行代码」确认。
  ⚠ 静态校验**不是沙箱**，只是挡住手滑和明显危险写法。
- **给 AI 的插件编写说明**：面板「渠道插件 → 给 AI 的说明」一键打开 / 复制 / 下载全文
  （`docs/PLUGIN-AUTHORING.md`），连同上游接口文档发给 AI 即可产出插件；文档里的示例代码由测试
  保证**真的**能通过校验（`test_doc_example_passes_validation`）。
- 插件表格新增列：来源（内置 / 上传）、状态（已装载 / 停用 / 装载失败）、支持操作、参考图口径、
  预置模型、被哪些渠道实例在用；装载失败的插件连报错一起显示。
- 新增 API：`GET /api/plugins`、`POST /api/plugins/validate`、`POST /api/plugins/save`、
  `GET|DELETE /api/plugins`、`POST /api/plugins/toggle`、`GET /api/plugins/source`、
  `GET /api/plugins/template`、`GET /api/plugins/authoring-doc`。

### 变更
- 插件装载统一走 `channels.load_path()`（内置与上传走同一条路，模块名统一 `app.channels.<文件名>`）。
- 「查看模板」改为从 `/api/plugins/template` 取，删掉前端内嵌副本（不再两份漂移）。
- 编辑器用本地自托管的 CodeMirror 5（`app/static/vendor/codemirror/`），无 CDN 依赖；深浅色自动换主题。

### 修复
- **`protocols.collect_refs` 之前根本不存在**：模板教插件写 `protocols.collect_refs(body)`，
  实际会 `AttributeError` —— 已在 `protocols` 里再导出 `collect_refs` / `to_raw_b64` / `snap_size` / `gpt_safe_size`。
- **编辑已装插件必然失败**：`importlib.reload()` 对「按路径装载的外部模块」会去包目录找文件 →
  `ModuleNotFoundError`。改为每次重新构造 spec 执行（`channels.load_path`），编辑/覆盖安装后即时生效。
- **删除停用中的插件报 404**：现在会连 `.py.disabled` 一起处理。

## [3.12.0] - 2026-09-18

### 新增
- **参考图公网直链层（图床中转）**：有的上游只认**公网 URL**（七牛 fal 异步队列自己去拉图），
  客户端却给 base64 —— 现在网关会在调插件之前自动换成公网直链再发上游。
  新增 `app/imagehost.py` 适配层 + 面板「设置 → 图床」+ `docs/IMAGEHOST.md`。
- **转换范围按渠道能力协商**：插件用 `ref_input` 按**各家官方文档**声明参考图形态 ——
  `url`（只认 URL：必须转，转不了本地 400）/ `both`（都支持：优先 URL，图床挂了回落 base64）/
  `base64`（只认 base64：原样透传，一个字节都不上传）。合并插件按「面」细化
  （`qiniu`：异步面 url / 同步面 base64）。客户端本来就给 http 链接 → 任何渠道都不上传。
- **渠道实例级应急阀门** `options.ref_prefer`：`inline` 强制内联 base64、`url` 强制转换，
  上游口径变了不用改代码。
- **图床自检按钮**：面板一键真上传一张 1×1 像素图验证链路（不调用任何生图接口、不产生费用），
  回读确认直链可取到图。
- 图床候选链可勾选/上下移排序，ImgBB Key 加密落库、界面只显掩码；默认链
  `imgbb → litterbox(72h) → uggu`，Catbox / 0x0.st 保留可手动开启（默认关闭，免白等超时）。

### 变更
- 响应头新增 `X-QLike-Imagehost`（走了哪家图床）/ `X-QLike-Imagehost-N`（张数），日志只记图床名与体积。

### 修复
- **`qiniu_fal` 不再静默丢参考图**：客户端给了参考图但不是公网 URL 时，原先只在 `edits` 上报错，
  `generations` 会**悄悄把参考图丢掉**（用户拿到一张跟参考图无关的图）。现在一律本地 400 说清原因。
- 图床候选链被手动清空后，不再偷偷回落到默认三家。

## [3.11.1] - 2026-09-18

### 修复
- **面板「探活」后渠道状态不再停在「未探测」**：探活按钮是按**模型逐条**探的，而后端只在
  「不带 model」时才写健康记录，导致点多少次状态都不变。现在新增 `model_health` 表逐模型落地，
  并按「**已探模型全部通过才算健康**」实时重算渠道汇总；渠道行显示「已探 x/y 模型」，
  展开行逐条列出各模型健康（模型/状态码/耗时，悬停看原文）。改过模型配置后，
  已删除模型的历史记录会自动忽略，不再拖累汇总。

## [3.11.0] - 2026-09-18

### 新增
- **渠道插件补「协议归属」元信息**：每个插件声明 `vendor`（谁家的协议）/ `docs`（官方文档）/ `protocol_note`（关键约束）。
  面板「新建渠道实例」选中插件后会直接显示这些，避免把「某家中转商自定的类 fal 队列」当成标准协议。
- **合并插件 `qiniu`（七牛 ModelInk）**：同一个站点按模型自动分流 —— `gemini-*` 走异步队列、`gpt-image-*` 走同步面，
  一个插件 + 一个渠道即可覆盖七牛两家面（对齐 `change2pro` 的合一思路）。
- **`build()` 可声明本次走法**：插件可返回 `meta.mode`（`queue`/`converted`）与 `meta.auth_mode`（`bearer`/`fal_key`），
  路由层据此决定走队列轮询还是同步转发，不再靠硬编码插件 id 判断。

### 变更
- **插件按各家官方协议分别命名与撰写口径**：`openai_images`（OpenAI 官方 Images API）、`gemini_native`（Google 官方
  Gemini API）、`change2pro`（该站自定）、`qiniu` / `qiniu_fal`（七牛 ModelInk）。
- **`fal_queue` 更名为 `qiniu_fal`**，标签「七牛 fal 异步队列（qnaigc 定制）」，`protocol_note` 明确写出
  **「不是 fal.ai 官方协议」**；旧 id `fal_queue` 保留**兼容别名**（历史数据无需迁移）。
- **「尺寸换算」面板重做**：标题栏（零成本 / 不出图 状态 + 同步价格按钮）+ 2×2 字段网格（每项附一行说明）+
  模型名下拉候选（直接取自模型目录）+ 结果区改为独立提示块；移除原先挤在一行的表单布局。

### 修复
- 路由层队列判定与探活判定由「硬编码插件 id」改为**插件声明驱动**，避免改名后链路失效。
- 渠道插件下拉在新建时默认展示第一个插件的协议口径（原先显示占位文案）。

### 测试
- 新增 5 条用例：插件必须带 `vendor`/`docs`、合并插件按模型分流、旧 id 兼容别名、口径文案非空等。

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
