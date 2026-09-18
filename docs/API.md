# HTTP 接口手册

基地址：`http://<host>:18673`

三组接口，鉴权方式不同：

| 组 | 前缀 | 鉴权 | 给谁用 |
|---|---|---|---|
| 统一入口 | `/v1/*` | 主密钥 或 访问令牌 | 客户端（经 New API 或直连） |
| 直连面 | `/up/<渠道key>/v1/*` | 主密钥 或 访问令牌 | 排障、绕过 New API 直测某一家 |
| 控制台 API | `/api/*` | 登录会话 Cookie（除登录外全部要登录） | 控制台前端 |

主密钥 = `.env` 里的 `QLIKEAPI_UP_TOKEN`。凭据放哪都行：

```
Authorization: Bearer <主密钥或访问令牌>
x-api-key: <主密钥或访问令牌>
x-qlikeapi-token: <主密钥或访问令牌>
```

控制台会话的优先级**高于**请求里带的令牌（页面内的预览/探活会以控制台身份记账）。

---

## 1. 统一入口

### 响应头（统一入口）

| 头 | 含义 |
|---|---|
| `X-QLike-Provider` / `X-QLike-Failover` | 实际出图的渠道 / 是否换过家 |
| `X-QLike-Chain` / `X-QLike-Attempt` / `X-QLike-Degrade` / `X-QLike-Queue-Ms` | 候选链 / 尝试次数 / 降了几档 / 排队耗时 |
| `X-QLike-Size` | **尺寸被换算过时才有**：`原尺寸->实际尺寸`（Gemini 面附档位，如 `1920x1080->2752x1536 (16:9@2K)`）；规则见 [`SIZE-MAPPING.md`](SIZE-MAPPING.md) |

### `POST /v1/images/generations`

标准 OpenAI 图片生成接口。请求体（JSON）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `model` | ✅ | 客户端模型名（大小写不敏感；须在某个渠道实例的模型映射里） |
| `prompt` | ✅ | 提示词。**缺 / 空 / 纯空白 → 本地 400**（绝不回落默认提示词） |
| `size` | | 如 `1024x1024`、`1536x864`、`16:9`；会按上游能力吸附 |
| `quality` | | `auto / standard / hd / high / low / medium`；按上游归一 |
| `n` | | 张数（部分上游上限 4） |
| `image` / `images` / `reference_images` | | 参考图（URL / data URI / 裸 base64），部分渠道支持 |
| 其它 | | 原样带给上游；上游不认的字段可用渠道实例的 `options.remove_params` 删掉 |

```bash
curl -s http://127.0.0.1:18673/v1/images/generations \
  -H "Authorization: Bearer $QLIKEAPI_UP_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-image-2","prompt":"一只戴墨镜的柯基","size":"1024x1024"}'
```

响应（归一化的标准形状）：

```json
{
  "created": 1789654003,
  "data": [{ "url": "https://up.example.com/img/abc.png" }],
  "model": "gpt-image-2"
}
```

- 上游返回 base64 时 → `data[].b64_json`；
- 响应头：`X-QLike-Provider: <渠道key>`、`X-QLike-Failover: <切换了几家>`。

### `POST /v1/images/edits`

同上，但接受 `multipart/form-data`（`image` 文件 + `prompt` 等字段），也接受 JSON
（`image` 传 URL / data URI / base64）。

```bash
curl -s http://127.0.0.1:18673/v1/images/edits \
  -H "Authorization: Bearer $QLIKEAPI_UP_TOKEN" \
  -F model=gpt-image-2 -F prompt='改成水彩风格' -F image=@cat.png
```

### `GET /v1/models`

列出所有「至少有一个可用渠道」的模型：

```json
{"object":"list","data":[{"id":"gpt-image-2","object":"model","owned_by":"qlikeapi-plugins","providers":["change2pro","qnaigc-fal"]}]}
```

### `GET /v1/route-preview?model=<模型>`

**干跑**：只看这个模型会走哪条链，不发任何上游请求（零成本排障）。

```json
{
  "model": "gpt-image-2",
  "rule": "auto(按优先级)",
  "chain": [{"provider":"change2pro","label":"…","priority":10,"plugin":"change2pro","url":"https://api.change2pro.com","keys":2}],
  "note": "这是路由顺序；实际会从第一个开始，遇到上游不可用（429/5xx/超时/连不上）自动换下一个"
}
```

---

## 2. 直连面（排障用）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/up/<key>/v1/models` | 这个渠道实例支持的模型 |
| POST | `/up/<key>/v1/images/generations` | 直接打这个渠道（不参与路由与切换） |
| POST | `/up/<key>/v1/images/edits` | 同上（改写） |
| POST | `/up/<key>/v1/images/preview` | **干跑**：返回「将要发给上游的 URL + 报文」，不发送 |
| POST | `/up/<key>/v1/images/selftest?model=<模型>` | **零成本探活**：抹掉提示词后发一次，4xx = 链路可达 |

`preview` 示例：

```bash
curl -s -X POST "http://127.0.0.1:18673/up/change2pro/v1/images/preview" \
  -H "Authorization: Bearer $QLIKEAPI_UP_TOKEN" -H 'Content-Type: application/json' \
  -d '{"model":"gemini-3-pro-image","prompt":"x","size":"1024x1024"}'
```

---

## 3. 控制台 API

除 `POST /api/login`、`GET /healthz` 外，全部需要登录会话（Cookie）。
写操作都要求已登录；`GET` 类只读接口同样要登录。

### 会话

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/login` | `{"username","password"}` → 设 Cookie；失败 401 |
| POST | `/api/logout` | 退出 |
| GET | `/api/me` | 当前登录状态 |
| POST | `/api/password` | `{"old","new"}`（新密码 ≥6 位）；旧密码错 → 400；改完会话全部失效需重新登录 |

### 渠道实例

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/channels` | 已装载的渠道插件（含 `operations`、`models` 预置模型清单、`model_map` 预置映射） |
| POST | `/api/channels/reload` | 热重载插件目录（加插件后不用重启） |
| GET | `/api/plugins` | 插件文件列表（含来源 / 状态 / 参考图口径 / 被哪些实例在用 / 装载报错）+ 插件目录 |
| POST | `/api/plugins/validate` | **只校验不落盘**：`{file, source}` → 报告（错误带行号 / 提醒 / 通过项 / 解析出的 info） |
| POST | `/api/plugins/save` | 校验并安装：`{file, source, overwrite?}`；已存在且没给 `overwrite` 时返回 `need_overwrite` |
| GET | `/api/plugins` `?file=` | 读某个插件的源码（内置的可读不可写） |
| DELETE | `/api/plugins` `?file=` | 删除插件（还有渠道实例在用时拒绝） |
| POST | `/api/plugins/toggle` | 启用 / 停用：`{file, enabled}`（停用 = 文件改名 `.py.disabled`，不装载） |
| GET | `/api/plugins/template` | 插件模板 `_template.py` 全文 |
| GET | `/api/plugins/authoring-doc` | 给 AI 的插件编写说明全文（面板一键复制） |
| GET | `/api/providers` | 渠道实例列表（密钥只回 `{"index","masked","label"}`；另带 `key_groups` 分组汇总：标签/把数/已知模型） |
| POST | `/api/providers` | 新建/更新实例（密钥加密入库；`model_map` 会校验通配符格式与右侧禁用 `*`，非法 → 400） |
| POST | `/api/providers/{key}/patch` | 轻量改：`enabled` / `priority` / `weight`（即时生效） |
| DELETE | `/api/providers/{key}` | 删除实例 |
| POST | `/api/providers/{key}/test?model=<模型>` | 零成本探活，返回逐模型结果 |
| POST | `/api/providers/{key}/fetch-models?group=<分组>` | 拉取上游模型列表（零成本 `GET /v1/models`）；带 `group` 则用该分组的密钥去拉 |
| POST | `/api/providers/{key}/discover-groups` | **探测各密钥分组**（零成本：逐把 `GET /v1/models`），把「分组 → 可用模型」写进 `options.key_models`，返回 `{ok, groups, errors}` |

密钥池写法：`api_key` 按行分隔，一行一把；行内支持 **`分组标签::密钥`**（sub2api 系上游的 key 绑分组，
gemini 与 gpt 常常不同组）—— 同一个渠道里放多组密钥，路由按模型自动挑对应分组（`options.key_groups` 手写规则，
`options.key_models` 为自动探测结果），详见 [CONFIGURATION.md](CONFIGURATION.md)。

`GET /api/providers` 单行结构（节选）：

```json
[{"key":"change2pro","label":"Change2Pro（香蕉 + image2 合一）","protocol":"change2pro",
  "enabled":true,"plugin_label":"…","plugin_ok":true,"base_url":"https://api.change2pro.com",
  "priority":10,"weight":1,"fail_streak":0,"disabled_reason":"","auto_disabled":false,
  "cooldown_until":null,"site_id":null,
  "models":[{"id":"gpt-image-2","upstream":"openai/gpt-image-2","aliased":true}],
  "operations":[{"operation":"generate","mode":"converted"}],
  "keys":[{"index":0,"masked":"sk-a3e…91cb","label":"gemini"},{"index":1,"masked":"sk-645…7e0f","label":"gpt"}],
  "key_groups":[{"label":"gemini","keys":1,"models":["gemini-3.1-flash-image","gemini-3-pro-image"],"labeled":true},
                {"label":"gpt","keys":1,"models":["gpt-image-2"],"labeled":true}]}]
```

### 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/routes` | 每个模型的链路（`mode`: `auto` 自动 / `explicit` 显式） |
| POST | `/api/routes` | `{"model","chain":["key1","key2"],"note"}`；链里出现空值/非字符串 → 400 |
| DELETE | `/api/routes/{model}` | 删除显式链，回到自动 |
| GET | `/api/routes/preview?model=` | 干跑某个模型的链路 |

### 令牌

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tokens` | 令牌列表（`token_masked`）+ 主密钥掩码 + 统一入口地址 |
| POST | `/api/tokens` | 新建；**明文 token 只在这一次响应里出现** |
| POST | `/api/tokens/{id}/patch` | 改额度/过期/允许模型/白名单/启停 |
| POST | `/api/tokens/{id}/reset` | 重置用量统计 |
| DELETE | `/api/tokens/{id}` | 删除 |

### 日志 / 统计 / 用量

> 面板「请求日志 → 详情」会把上游请求拼成**可直接复制的 curl**（客户端 → 本网关、本网关 → 上游两条），
> 提示词按多行展示（可切回严格 JSON 再复制去实测）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/logs?limit=50&offset=0` | 请求日志（支持按令牌、渠道、类型筛选） |
| GET | `/api/logs/{id}` | 单条详情：客户端报文 + **发给上游的完整请求**（`upstream_url` / `upstream_method` / `upstream_headers`，密钥写成 `YOUR_API_KEY` 占位符）+ 响应片段 |
| GET | `/api/stats?days=7` | 概览 KPI：今日 / 24h / 总计 + 逐小时曲线 |
| GET | `/api/usage?days=7` | 用量统计：汇总 + 按天序列（请求数/张数/花费/耗时） |
| GET | `/api/usage.csv?days=7` | 导出 CSV |
| GET | `/api/jobs?limit=50&provider=` | 异步队列任务 |

### 价格 / 站点 / 健康 / 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/models` | 模型 × 渠道矩阵（含单价） |
| GET | `/api/meta/options` | 下拉选项：`models[]`（统一模型名 + 可用渠道数 + 上游真名）、`providers[]`（渠道 key/标签/启停）；供令牌弹窗的多选选择器使用 |
| GET / POST | `/api/prices` | 单价表（`model`,`provider`,`price`,`currency`,`source`） |
| POST | `/api/prices/sync` | 从 New API 同步价格口径 |
| POST | `/api/prices/prune` | 清理**孤儿价**（`provider` 已不存在的遗留行），返回 `{removed, items}`；删渠道时已自动级联清理，这里是兜底 |
| DELETE | `/api/prices/{id}` | 删一条价格 |
| GET | `/api/size-plan?model=&size=&policy=&mode=` | **尺寸换算**（纯计算、零成本、不出图）：返回 `{size, final, changed, family, mode, ratio, tier, policy, note, rules?, allowed?, official?, nearest_official?, tiers?, tokens?, tokens_all?, source?}`。GPT 系按最小改动吸附（`official` = 官方常用尺寸，`nearest_official` = 离请求最近的那个）；Gemini 系返回档位/比例、**实际输出像素**（`tiers` = 该比例下整张官方档位表）与官方 token 消耗。`policy` = `class`（默认）/`floor`/`nearest`/`ceil`；`mode` = `snap`（默认）/`passthrough`（原样透传不改尺寸） |
| GET | `/api/sites` / `/api/sites/types` | 站点余额与取数器类型 |
| POST | `/api/sites` | 新建/更新站点（含 `warn_line` 余额预警线） |
| POST | `/api/sites/{id}/check` | 查这一个站点的余额 |
| POST | `/api/sites/check` | 查全部 |
| POST | `/api/sites/import-env` | 从只读挂载的宿主机 `.env` 导入站点凭据 |
| POST | `/api/health/run` | 跑一轮渠道健康检查 |
| GET | `/api/sysinfo` | 版本、库路径、计数、加密健康度、熔断参数 |

---

## 4. 其它

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | `{"ok":true,"app":"qlikeapi-plugins","version":"3.10.0","plugins":[…],"plugin_errors":{},"providers":[…]}` |
| GET | `/` | 控制台页面（未登录跳 `/login`） |
| GET | `/static/*` | 前端静态资源 |

---

## 5. 错误码

统一返回 OpenAI 风格的错误体：

```json
{"error": {"message": "prompt is required", "type": "invalid_request_error"}}
```

| 状态 | 实际 `message`（示例） | 含义 / 处理 |
|---|---|---|
| 400 | `prompt is required` | 缺/空/纯空白提示词 —— 本地拦下，**没有打上游**（不花钱） |
| 400 | `渠道 'x'（OpenAI 图片协议）不支持图片编辑` | 该插件没声明这个操作（`operations`） |
| 400 | `model is required（统一入口必须带模型名…）` | 统一入口没带模型名 |
| 400 | `chain 里出现了空的渠道 key（请重新选渠道再保存）` | 路由链校验失败（空值/非字符串/渠道不存在） |
| 401 | `unauthorized（密钥无效：既不是内部主密钥，也不是本服务签发的访问令牌）` | 凭据缺失或不对 |
| 401 | `该访问令牌已被停用：<名字>` / `该访问令牌已于 … 过期` | 令牌被停用 / 过期 |
| 402 | `该访问令牌额度已用尽（10.0 CNY）` | 令牌额度用尽 |
| 403 | `该访问令牌不允许来自 IP 1.2.3.4` | IP 白名单拦截 |
| 403 | `该访问令牌不允许调用模型 'x'（允许：…）` / `不允许走渠道 'x'` | 令牌的模型/渠道白名单 |
| 404 | `unknown provider 'x'` | `/up/<key>` 里的渠道不存在 |
| 503 | `渠道 'x' 已停用（在控制台「渠道实例」里启用后再试）` | 直连一个已停用的渠道（停用的渠道不参与路由） |
| 404 | `没有渠道实例支持模型 'x'（可在控制台「路由规则」里指定）` | 该模型没有任何可用渠道 |
| 429 | `该访问令牌超过限速（3 次/秒）` | 令牌 QPS 限制 |
| 503 | `渠道 'x' 未配置 API key` | 实例没填密钥 |
| 503 | `该模型的所有上游渠道都失败了（依次尝试：a(429), b(500)）` | 链上所有渠道都失败，括号里是每家的结果 |
| 503 | `upstream 400: …` | 单渠道直连时上游返回的错误原样透出 |

> 排障顺序：`/v1/route-preview`（看链路）→ `/up/<key>/v1/images/preview`（看报文）→
> `/up/<key>/v1/images/selftest`（看链路是否可达）→ 控制台「请求日志」看每次尝试的细节。
