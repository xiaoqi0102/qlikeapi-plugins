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
| `QLIKEAPI_DB` | `/data/qlikeapi.db` | SQLite 文件路径（容器内），配合 `./data:/data` 卷持久化 |
| `QLIKEAPI_SECURE_COOKIE` | `1` | Cookie 加 `Secure`。HTTPS 反代下保持 1；纯 http 内网调试设 0 |
| `QLIKEAPI_SESSION_DAYS` | `30` | 控制台登录态有效天数 |
| `QLIKEAPI_TIMEOUT` | `900` | 单次上游请求最长等待（秒）。图片生成可能几十秒，别调太小 |
| `QLIKEAPI_POLL_INTERVAL` | `3` | 异步面（队列类渠道）轮询间隔（秒） |
| `QLIKEAPI_POLL_MAX` | `600` | 异步面最长轮询时间（秒） |
| `QLIKEAPI_PROBE_TIMEOUT` | `12` | **探活硬超时**（秒）。必须保留：挂起的上游不能把探活卡死 |
| `QLIKEAPI_AUTO_DISABLE_AFTER` | `5` | 连续失败多少次自动停用该渠道 |
| `QLIKEAPI_AUTO_RECOVER_SEC` | `600` | 熔断后最短冷却时间（秒）；到点后仍需**探活通过**才恢复 |
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

### 插件选项 `options`（JSON）

| 键 | 作用 |
|---|---|
| `remove_params` | 要删掉的字段，**支持点号路径与 `*`**：`["response_format","generationConfig.thinkingConfig","data.*.revised_prompt"]` |
| `drop_fields` | 兼容旧配置：只删顶层字段（新配置请用 `remove_params`） |
| `force_fields` | 强制覆盖字段（对象）：`{"quality":"auto"}` |
| `drop_quality` | 直接删掉 `quality`（个别上游不认这个字段） |
| `generations_path` / `edits_path` | 覆盖同步面路径（如 change2pro 的 image2 面**没有 `/v1`** 前缀） |
| `image_size_override` | 强制 Gemini 面的尺寸档：`"1K"` / `"2K"` / `"4K"` |
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
