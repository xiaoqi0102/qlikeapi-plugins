# 配置说明

配置分三层，优先级从低到高：

1. **环境变量**（`.env`）—— 进程级，改了要重启容器
2. **控制台**（渠道实例 / 令牌 / 路由 / 站点 / 价格）—— 存在 SQLite 里，改完即时生效
3. **渠道实例的 `options`**（JSON）—— 单个渠道的插件级微调

---

## 1. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `QLIKEAPI_UP_TOKEN` | 空 | **内部主密钥**。New API 渠道里填的「密钥」就是它；也是 `/v1/*` 的凭据之一。留空＝不设防（仅限本机调试） |
| `QLIKEAPI_SECRET` | 空 | 会话 Cookie 签名 + 落库加密的派生源。**换掉它 → 已存的渠道密钥解不开，需要重填** |
| `QLIKEAPI_ENC_KEY` | 空 | 单独的加密主密钥（不填则用 `QLIKEAPI_SECRET` 派生）。想轮换会话密钥又不想重填渠道密钥时用它 |
| `QLIKEAPI_ADMIN_USER` | `admin` | 控制台账号。**只在首次启动建库时生效**，之后改这里无效 |
| `QLIKEAPI_ADMIN_PASS` | 空 | 控制台密码。留空＝首次启动随机生成并打印到容器日志（推荐用 `openssl rand -hex 24` 显式设置） |
| `QLIKEAPI_PLUGIN_DIR` | `/data/plugins` | **面板上传的渠道插件目录**。挂在卷上（重建容器不丢）；改这里就换了「添加插件」的落盘位置 |
| `QLIKEAPI_DB` | `/data/qlikeapi.db` | SQLite 文件路径（容器内），配合 `./data:/data` 卷持久化 |
| `QLIKEAPI_SECURE_COOKIE` | `1` | Cookie 加 `Secure`。HTTPS 反代下保持 1；纯 http 内网调试设 0 |
| `QLIKEAPI_SESSION_DAYS` | `30` | 控制台登录态有效天数 |
| `QLIKEAPI_TIMEOUT` | `900` | 单次上游请求最长等待（秒）。图片生成可能几十秒，别调太小 |
| `QLIKEAPI_POLL_INTERVAL` | `3` | 异步面（队列类渠道）轮询间隔（秒） |
| `QLIKEAPI_POLL_MAX` | `600` | 异步面最长轮询时间（秒） |
| `QLIKEAPI_PROBE_TIMEOUT` | `12` | **探活硬超时**（秒）。必须保留：挂起的上游不能把探活卡死 |
| `QLIKEAPI_AUTO_DISABLE_AFTER` | `5` | 连续失败多少次自动停用该渠道 |
| `QLIKEAPI_AUTO_RECOVER_SEC` | `600` | 熔断后最短冷却时间（秒）；到点后仍需**探活通过**才恢复 |
| `QLIKEAPI_MAX_CONCURRENCY` | `0` | **并发闸门全局上限**（同时在跑的转发请求数）。`0`＝不限（默认，行为与老版本一致）。渠道级上限优先于它 | 
| `QLIKEAPI_QUEUE_WAIT` | `30` | 拿不到并发槽位时最多排队多少秒；等不到就拒绝（路由层会先换下一家，全部占满才 503 + `Retry-After`） |
| `QLIKEAPI_MAX_WAITING` | `32` | 等待队列上限，超过直接拒（防止请求无限堆积把内存吃光） |
| `QLIKEAPI_MAX_ROUTE_ATTEMPTS` | `6` | 一条请求最多打几次上游（含同档重试与降级换家） |
| `QLIKEAPI_IMPORT_ENV` | 空 | 只读挂载的宿主机 `.env` 路径，控制台「从 .env 导入站点」会读它 |

密钥生成：

```bash
openssl rand -hex 24      # 每个密钥单独生成一次，别复用
```

---

## 2. 渠道实例（控制台 → 渠道实例）

| 字段 | 说明 |
|---|---|
| 显示名 / 实例名（key） | 显示名给人看；`key` 是唯一标识，出现在 `/up/<key>/...` 与响应头 `X-QLike-Provider` |
| 渠道插件（protocol） | 选一个已装载的插件（`channels/` 目录里的） |
| 上游地址 | 上游 API 的根地址，如 `https://api.change2pro.com`（插件会自己拼路径） |
| 鉴权方式 | `bearer`（`Authorization: Bearer`）/ `x-goog-api-key`（Gemini 系）/ `fal_key`（`Authorization: Key`） |
| 密钥 | 上游给你的 API key。**多把 key 用换行分隔** → 自动轮换 + 失败冷却。**入库即加密** |
| 模型映射 | `客户端模型名 → 上游真实模型名`，一行一条；大小写不敏感。留空＝同名透传 |
| 优先级 priority | **数字大者优先**（与 New API 一致） |
| 权重 weight | 同优先级内按权重加权随机分流（weight 3 : 1 ≈ 3 倍流量） |
| 启用 | 关掉＝不参与路由（人工停用的渠道**不会**被自动恢复） |
| 关联站点 | 绑一个站点后：余额低于预警线 → 自动停用该渠道（余额熔断） |
| 操作标签 | 该插件支持的操作（generate / edit 及各自模式），只读展示 |
| 密钥池 | 每行一把 key；`分组标签::密钥` 可按上游分组区分（**同一渠道内多组并存，不用拆渠道**）。轮换与冷却按「分组内」进行 |

### 模型限制：模型白名单 / 模型映射（界面与逻辑参照 sub2api）

渠道弹窗里的「**模型限制（可选）**」分成两个 Tab，**存储仍然是同一个 `model_map` 对象**，
拆分规则与 sub2api 完全一致：

| 你填的位置 | 存进 `model_map` 的样子 | 含义 |
|---|---|---|
| 模型白名单里的 `gpt-image-2` | `{"gpt-image-2": "gpt-image-2"}` | 精确放行：客户端请求什么、上游就收到什么 |
| 模型映射里的 `gpt-image-2 → openai/gpt-image-2` | `{"gpt-image-2": "openai/gpt-image-2"}` | 改写：客户端说 `gpt-image-2`，上游收到 `openai/gpt-image-2` |

也就是 sub2api 的 `splitModelMappingObject`（`from == to` 归白名单、`from != to` 归映射）
与 `buildModelMappingObject('combined', …)`（保存时两段合并成一个对象）。

**三个按钮**（都在「模型白名单」Tab 里，全部零成本）：

| 按钮 | 做什么 |
|---|---|
| 同步最新支持模型 | 把**该渠道插件自带的预置模型**全部加进白名单（不需要联网） |
| 同步上游支持的模型 | `GET /v1/models` 拉上游清单（**只读、不出图**）；带 `fal-ai/`、`openai/` 前缀的自动建成「裸名 → 带前缀真名」的映射，其余进白名单 |
| 清除所有模型 | 清空白名单与映射（点保存后生效） |

「自定义模型名称」输入框 + 「填入」可以把清单里没有的模型名直接加进白名单。
「模型映射」Tab 里除了「+ 添加映射」成对输入，还有一排**预置药丸**（按模型家族上色），点一下即添加。

**通配符**（与 sub2api 同口径，后端也会校验，非法直接 400）：

- **左边（请求模型）**支持通配符：`*` 只能有一个，且**必须在末尾** —— `gemini-3*` 合法，
  `gemini-*-image`、`a*b`、`*abc` 一律非法；
- **右边（实际模型）不允许**出现 `*`；
- 多条规则命中时**最长（最具体）的优先**：`gemini-3-pro*` 会盖过 `gemini-3*`；
- 白名单里**不写通配符**（与 sub2api 的注释同理：通配符写进白名单会把真实模型名映射成 `xxx*`，
  上游必然失败）；想用通配符请切到「模型映射」Tab；
- 路由也认通配符：`gemini-3*` 能让渠道接住 `gemini-3-pro-image` 这类请求。

> 「高级：直接编辑 JSON」折叠区保留了原始 `model_map` 编辑能力（与可视化编辑等价），
> 里面还有「整理去重」和「用 JSON 重置上面的编辑」。

### 同一个渠道里的「多分组密钥」（sub2api 系上游必看）

**背景**：New API 一个渠道可以放多个分组的模型；但 **sub2api 系上游不一样 —— 密钥是绑分组的**，
`GET /v1/models` 只返回这把 key 所属分组里的模型（实测 change2pro：一把 key 只有 4 个 gemini 模型，
另一把只有 `gpt-image-2`）。gemini 与 gpt 通常不在同一分组，所以一个上游会有多把不同分组的 key。

**做法（推荐）**：**不要拆渠道**，在同一个渠道里按分组写 key：

```
gemini::sk-aaaa...
gpt::sk-bbbb...
```

然后点渠道弹窗里的「**探测各密钥分组**」（零成本：逐把 `GET /v1/models`，绝不出图），
它会把「分组 → 可用模型」写进 `options.key_models`；之后路由按请求的模型自动挑对应分组的 key。

- 没写标签的行＝**通吃**（任何模型都能用），可用于只有一把 key 的普通上游；
- 规则没命中且没有通吃 key 时，**退化成全部 key**（绝不因配置不全而断路）；
- 想手写规则覆盖自动结果：`options.key_groups`，如 `{"gemini-*": "gemini", "gpt-image-2": "gpt"}`；
- 轮换与失败冷却都在**分组内**进行，不会把 gemini 的请求轮询到 gpt 那把 key 上。

> 反例（曾经的坑）：把两把不同分组的 key 不加标签地放在同一渠道 → 轮询会把 gemini 请求打到 gpt 组的 key，
> 上游回 `404 model_not_found`，白白多一跳才降级到下一家。

### 插件选项 `options`（JSON）

| 键 | 作用 |
|---|---|
| `remove_params` | 要删掉的字段，**支持点号路径与 `*`**：`["response_format","generationConfig.thinkingConfig","data.*.revised_prompt"]` |
| `drop_fields` | 兼容旧配置：只删顶层字段（新配置请用 `remove_params`） |
| `force_fields` | 强制覆盖字段（对象）：`{"quality":"auto"}` |
| `drop_quality` | 直接删掉 `quality`（个别上游不认这个字段） |
| `generations_path` / `edits_path` | 覆盖同步面路径（如 change2pro 的 image2 面**没有 `/v1`** 前缀） |
| `image_size_override` | 强制 Gemini 面的尺寸档：`"1K"` / `"2K"` / `"4K"` |
| `gemini_size_policy` | **Gemini 档位策略**（`class` 默认 / `floor` 最省 / `nearest` 取最接近 / `ceil` 不降级）。Gemini 给不了任意像素，只能给「档位 + 宽高比」，这条决定怎么把客户端像素换成档位；详见 [`SIZE-MAPPING.md`](SIZE-MAPPING.md) |
| `key_groups` | **模型 → 密钥分组**的手写规则（对象，支持 `*` `?` 通配），如 `{"gemini-*": "gemini", "gpt-image-2": "gpt"}`。优先级**高于**自动探测结果；sub2api 系上游的 key 绑分组（gemini 与 gpt 常常不同组），用它在同一渠道内按模型挑对应分组的 key |
| `key_models` | 「探测各密钥分组」的**自动结果**（`{"gemini": [...模型], "gpt": [...]}`），一般不用手写 —— 面板点一下按钮即可写入 |
| `size_mode` | **尺寸处理方式**（`snap` 默认 / `passthrough`）。`snap`＝按官方约束最小改动吸附（只修不合法的那一边，绝不放大一档）；`passthrough`＝一个像素都不改，原样发给上游（上游实际接受更大尺寸时用）。控制台「渠道实例 → 编辑 → 尺寸处理」直接选 |
| `max_concurrency` | **该渠道的并发上限**（同时最多几个请求在跑）。不填则用全局 `QLIKEAPI_MAX_CONCURRENCY`；两者都是 0＝不限。控制台「渠道实例 → 编辑 → 并发上限」直接填 |
| `retry` | **同档重试次数**（0-2，默认 0）：失败先在同一优先级档里重试几次再降档。图片生成重试有重复扣费风险，默认直接降档 |
| `models_path` | 拉取上游模型列表的路径（默认 `/v1/models`，其次试 `/models`）—— 控制台渠道弹窗「拉取上游模型」用它 |
| `unify_model` | 设 `false` → 响应里保留上游真实模型名（排查用，默认统一成客户端请求的名字） |

**异步面（fal）的端点不在 `options` 里**，而是写在「模型映射」的每条记录上：

```json
{
  "gemini-3.1-flash-image-preview": {
    "upstream": "fal-ai/gemini-3.1-flash-image",
    "submit": "/queue/fal-ai/gemini-3.1-flash-image",
    "submit_edit": "/queue/fal-ai/gemini-3.1-flash-image/edit",
    "poll_base": "/queue/fal-ai/gemini-3.1-flash-image"
  }
}
```

（只写 `"客户端名": "上游名"` 也可以，端点会按 `/queue/<上游名>` 推导。）

`options` 的修改在控制台「编辑实例」里做；改完立即生效，不用重启。

---

## 3. 访问令牌（控制台 → 访问令牌）

一把令牌 = 一个调用方（如「ComfyUI 本机」「某同事的脚本」）。

| 字段 | 说明 |
|---|---|
| 名字 | 给谁用的，出问题好定位 |
| 额度 quota | 金额上限（配合币种）；0 = 不限 |
| 过期时间 | 到期自动失效；留空 = 永不过期 |
| 允许模型 / 允许渠道 | 白名单，留空 = 不限 |
| IP 白名单 | 逗号分隔，支持前缀匹配；留空 = 不限 |
| QPS | 每秒请求上限；0 = 不限 |
| 启停 | 随时禁用 |

- 明文令牌**只在创建那一次响应里出现**，之后只能看掩码（`sk-ql-xxxx…abcd`）；
- 用量按令牌统计：请求数 / 张数 / 花费（控制台「用量统计」可按令牌过滤）。

---

## 4. 路由规则（控制台 → 渠道实例页顶部的链路概览）

| 模式 | 行为 |
|---|---|
| `auto` | 按优先级自动成链；同优先级按权重随机 |
| `explicit` | 完全按你写的顺序（支持备注）。链上渠道不可用时**跳过，不回落到 auto** |

规则：

- `POST /api/routes` 里链不能有空的/非字符串的渠道 key，否则 400；
- 保存空数组 = 删除显式规则，回到自动；
- 干跑看效果：`GET /v1/route-preview?model=xxx` 或控制台「链路预览」。

---

## 5. 价格（控制台 → 模型与价格）

- 一条价格 = `(model, provider)` 的组合，带 `currency` 与 `source`（来源）。
- **不做汇率换算**：`USD` 和 `CNY` 各算各的，用量统计里按币种分开汇总。
- 查找优先级：**渠道专属价 ＞ 全局模型价（provider=`*`）＞ 全局兜底价（`*`/`*`）**。
- `POST /api/prices/sync` 可以从 New API 同步价格口径（`ModelPrice`），
  同步来的条目 `source` 会标注来源，便于和上游实际账单对账。
- 价格只用于「估算花费」，不影响出图；对账时以**上游账单**为准。

---

## 6. 站点与余额（控制台 → 站点余额）

| 取数器 | 适用 | 取数方式 |
|---|---|---|
| `newapi` | 自建 New API | `GET {base}/api/user/self`（Bearer + `New-Api-User: <uid>`），`quota/500000` = USD |
| `sub2api` | sub2api 系中转 | `GET {base}/v1/usage`（Bearer）→ `remaining` / `balance` |
| `deepseek` | DeepSeek 官方 | `GET https://api.deepseek.com/user/balance` |
| `siliconflow` | 硅基流动 | `GET /v1/user/info` → `data.balance`（注：官方已下线该接口时取不到） |
| `openai_billing` | OpenAI 官方 | `GET /dashboard/billing/subscription` → `hard_limit_usd` |
| `custom` | 其它 | 自定义 URL + JSON 路径提取 |
| `manual` | 都没有 | 手工记账（自己填余额） |

- 站点可设**余额预警线**：低于阈值 → 关联渠道自动停用（余额熔断）；
- 站点令牌同样加密入库；
- 「从 .env 导入站点」可以批量把宿主机 `.env` 里的凭据导进来（需只读挂载该文件）。

---

## 7. 熔断参数怎么调

| 场景 | 建议 |
|---|---|
| 上游偶发抖动 | `QLIKEAPI_AUTO_DISABLE_AFTER` 调大（如 10），避免频繁熔断 |
| 上游经常限流 | 调小（如 3），让它快速切走；同时给同模型多配一家做备胎 |
| 想人工确认再恢复 | 保持默认：冷却到期后仍要探活通过 |
| 只想临时摘掉一家 | 直接在控制台点停用（人工停用不会被自动恢复） |
