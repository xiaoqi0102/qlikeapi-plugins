# 架构说明

> 目标读者：要改这个项目的人（包括三个月后的你自己）。

## 1. 为什么要存在

客户端（画图工具、ComfyUI 工作流、脚本）只想要**一套标准 OpenAI 图片接口**；
而上游中转站各说各话：

| 上游形态 | 真实接口 | 典型表现 |
|---|---|---|
| Gemini 原生 | `POST /v1beta/models/{model}:generateContent` | 参考图必须放 `contents[].parts[].inlineData`（裸 base64，带 `data:` 前缀会被拒）；尺寸走 `generationConfig.imageConfig` |
| OpenAI 图片（同步） | `POST /v1/images/generations\|edits` | 字段白名单各不相同：有的不认 `response_format`、有的只回 `b64_json`、`quality` 只认 `auto/high` |
| 异步队列 | `POST /queue/{...}` → 轮询 → 取 `images[].url` | 提交后要轮询状态，最终给的是带签名的临时 URL（会过期） |

再加上「一家不稳」（限流、余额、5xx），手写重试很容易重复扣费。
本服务就是把这堆差异收进一个可插拔的路由层。

## 2. 分层

```
客户端 ──► New API（唯一渠道 #30）──► qlikeapi-plugins ──► 上游
```

```
app/
├── main.py        FastAPI 应用装配：静态资源、页面、/healthz、版本号
├── relay.py       ★ 统一入口：鉴权门 → 归一化 → 路由链 → 插件翻译 → 发送 → 归一 → 记账
├── protocols.py   ★ 协议层：三种上游协议的 build/parse + 工具函数（纯函数，可单测）
├── channels/      ★ 渠道插件：一个上游协议 = 一个文件（build/parse/operations）
├── admin.py       控制台 API + 会话（登录、渠道、令牌、路由、价格、日志、站点）
├── store.py       数据层：SQLite（渠道/令牌/路由/价格/日志/健康/站点/任务）+ 加密落库
├── crypto.py      落库密钥加密（标准库 Encrypt-then-MAC）
├── balances.py    站点余额取数器（插件式：newapi / sub2api / deepseek / siliconflow / openai_billing / custom / manual）
└── static/        控制台前端（原生 JS + 本地自托管 Bootstrap/Tabler Icons/Chart.js）
```

**依赖方向**（不许反向）：

```
main ──► relay ──► channels ──► protocols ──► utils
  │        │            │
  └──► admin ──────────►┴──► store ──► crypto
```

- `protocols.py` 与 `utils.py` 是纯函数层：**不碰数据库、不发网络请求**（唯一例外是
  `call_upstream()`，它是所有出站请求的唯一出口，便于统一超时/日志/测试打桩）。
- `channels/*` 只做「翻译」，不知道数据库长什么样，也不知道谁在调用它。
- `store.py` 只做持久化，不含业务判断（熔断阈值判断在 `store` 里，但触发点是 `relay`）。

## 3. 一次请求的完整生命周期

以 `POST /v1/images/generations` 为例：

```
1. 鉴权门        主密钥(QLIKEAPI_UP_TOKEN) / 访问令牌 / 控制台会话，三者任一通过
                 · 令牌额外校验：额度、过期、允许模型、允许渠道、IP 白名单、启停
2. 归一化        body 解析（JSON 或 multipart 附件）、模型名 strip + 大小写不敏感
3. 选链          routes 表里有该模型的显式链 → 用显式链
                 否则按「该模型的所有启用渠道」按 priority 降序成链，同优先级按 weight 加权
4. 逐家尝试      for p in chain:
                   · 渠道熔断中 / 被自动停用 → 跳过
                   · channels.get(p.protocol).build(p, body, edit)  ← 本地翻译，缺提示词直接 400
                   · protocols.call_upstream(url, headers, up_body, timeout)
                   · 状态码判定：
                       200        → 解析 → 归一 → 记账 → 返回
                       400        → 立即返回（参数错，换谁都没用，避免重复扣费）
                       401/403    → 标记该渠道该把 key 失效 → 冷却 → 换下一家
                       429/402/5xx/连接失败 → 记失败、加冷却 → 换下一家
                       超时       → **默认不切换**（上游可能已经出图并计费）
5. 响应归一      {"created":…, "data":[{"url"|"b64_json":…}]} + 响应头
                 X-QLike-Provider: <渠道 key>
                 X-QLike-Failover: <切换了几家>
6. 记账          logs 表（每次尝试一行，含 kind/attempts/key_index/images/cost）
                 tokens 表（按令牌累计请求数/张数/花费）
                 providers.fail_streak / cooldown_until / auto_disabled_at
```

**旧直连面 `/up/<渠道>/...`** 走同一套流程，只是跳过第 3 步（渠道由 URL 指定）。
它存在的意义：排障时绕过 New API 直测某一家；以及给还没接 New API 的客户端用。

## 4. 路由与故障切换

### 选链规则

1. **显式链优先**：`routes` 表里给某个模型写死了渠道顺序 → 完全按它来（控制台可编辑，支持备注）。
2. 否则**自动成链**：取该模型所有「启用且不在冷却/未自动停用」的渠道，
   按 `priority` 降序；同一 `priority` 内按 `weight` 加权随机（weight 越大被选中的概率越高）。
3. 显式链里的渠道不可用（被停用/熔断）→ 自动跳过，**不会**回落到自动链
   （这是有意的：写死顺序通常有业务理由，比如成本/合规）。

> New API 侧的优先级语义是「**数字大者优先**」，所以本服务对应的渠道要给它最高优先级，
> 才能成为首选通道。

### 状态码 → 动作（`relay.RETRYABLE`）

| 上游结果 | 动作 | 理由 |
|---|---|---|
| 200 | 归一返回 | — |
| 400 参数错 | **立即返回，不切换** | 换一家还是同样的参数错，只会多花一次钱 |
| 401 / 403 | 标记该 key 失效 + 冷却 → 换下一家 | 密钥问题，换一家可能就通了 |
| 402 余额不足 | 冷却 → 换下一家（并建议配余额熔断） | 这家没钱了 |
| 429 限流 | 冷却 → 换下一家 | — |
| 5xx / 连接失败 | 记失败 → 换下一家 | — |
| **超时** | **默认不切换** | 上游可能已经生成并计费，切换＝重复扣费；宁可给客户端报超时 |

### 熔断状态机

```
   正常 ──失败──► fail_streak+1 ──达到阈值(QLIKEAPI_AUTO_DISABLE_AFTER)──► 自动停用
    │                                                                        │
    │                                                        冷却到期(QLIKEAPI_AUTO_RECOVER_SEC)
    │                                                                        ▼
    │                                                              探活候选（零成本）
    └──────────── 探活通过 ──────────────── 恢复（fail_streak 归零） ◄────────┘
                     │
                  探活失败 → 继续停用，冷却延长
```

- **人工停用的渠道永不被自动恢复**（只有人工点「启用」才会回来）。
- 恢复必须**探活通过**，不是「冷却到期就无条件放回」——避免一放回就又失败。
- 探活是**零成本**的（见下节）。

### 并发闸门（v3.5.0）

```
请求 → 候选链（按优先级分档）→ 每档按权重随机 → 逐个候选：
        闸门 acquire(渠道, 上限) ──拒绝──► 换下一个候选（降低单点依赖）
              │ 拿到槽位
              ▼
        打上游 ──成功──► 回图（finally 释放槽位）
              │ 失败（429/402/5xx/连接失败）
              ▼
        下一个候选；同档可重试 options.retry 次；全部失败 → 最后一家的错误原样返回
```

- 上限取值：渠道 `options.max_concurrency` ＞ 全局 `QLIKEAPI_MAX_CONCURRENCY`；`0` = 不限（默认关闭，行为与老版本一致）。
- 排队：最多 `QLIKEAPI_QUEUE_WAIT` 秒，等待队列超过 `QLIKEAPI_MAX_WAITING` 直接拒；**所有候选都占满**才返回 `503 + Retry-After`。
- 计数是进程内单例（单实例 SQLite 部署足够）；将来要多实例时把 `_Gate` 换成共享计数即可，接口不变。
- 决策全程可见：`X-QLike-Chain` / `X-QLike-Attempt` / `X-QLike-Degrade` / `X-QLike-Queue-Ms`。



## 5. 零成本探活（本项目最重要的一条工程约束）

探活/自检**绝不能**产生真实出图与计费。实现方式：

1. `relay.blank_for_probe()` **递归**遍历请求体，把一切提示词类字段（`prompt / text /
   content / contents / parts / input / inputs / prompt_text`）置空，参考图类字段
   （`image / images / mask / init_image / reference_images`）也置空。
   —— 之所以要「递归 + 覆盖所有插件」，是因为曾经吃过亏：老实现只对 Gemini 插件抹字段，
   而「合并插件」（一个实例内部按模型名分流到两套协议）会漏判，结果探活真的出图了。
2. 探活报文**缺提示词**，所以上游一定会回 4xx（`prompt is required` 之类）；
   **4xx = 链路可达**（能连通、能鉴权、能路由到这个模型），5xx = 上游异常。
3. 硬超时 `QLIKEAPI_PROBE_TIMEOUT`（默认 12s）：挂起的上游不会把探活卡死。
4. 探活结果写进 `logs` 表（`kind='probe'`），可审计「谁在什么时候探了哪个模型」。
5. 守门测试：`tests/test_probe.py` 断言「探活报文里不存在任何提示词内容」，
   任何人改坏了这条底线，CI 立刻红。

## 6. 数据模型（SQLite，单文件）

| 表 | 作用 | 关键字段 |
|---|---|---|
| `providers` | 渠道实例 | `key`、`protocol`（= 插件 id）、`base_url`、`auth_mode`、`api_key`（**加密**，多把 key 用换行分隔）、`model_map`(JSON)、`options`(JSON)、`enabled`、`priority`、`weight`、`site_id`、`fail_streak`、`cooldown_until`、`auto_disabled_at` |
| `tokens` | 访问令牌 | `name`、`token`、`quota`、`expires_at`、`allowed_models`、`allowed_providers`、`allowed_ips`、`qps`、`enabled`、`used_requests`、`used_images`、`used_cost` |
| `routes` | 显式路由链 | `model`、`chain`(JSON 数组)、`note` |
| `model_prices` | 单价表 | `model`、`provider`、`price`、`currency`、`source`、`updated_at` |
| `logs` | 请求日志 | `ts`、`kind`(relay/probe)、`provider`、`model`、`status`、`ms`、`attempts`、`key_index`、`images`、`cost`、`cost_currency`、`token`、`public_path`、`detail`(JSON) |
| `sites` | 上游站点与余额 | `name`、`type`(取数器)、`base_url`、`token`（**加密**）、`balance`、`unit`、`used`、`plan`、`warn_line`、`checked_at` |
| `health` | 渠道健康快照 | `provider`、`model`、`ok`、`status`、`ms`、`ts` |
| `jobs` | 异步任务（队列类渠道） | `provider`、`upstream_id`、`status`、`result` |
| `users` / `sessions` | 控制台账号与会话 | `username`、`pwd_hash`（scrypt+盐）、`salt`；会话是签名 Cookie |

设计取舍：

- **坚持 SQLite**：部署只要一个容器 + 一个文件，不需要 Redis/Postgres；
  单机自用场景下并发完全够（WAL + 短事务）。
- **JSON 字段**：`model_map` / `options` / `chain` 用 JSON 存，改结构不需要迁移；
  代价是不能用 SQL 直接查内部字段（用不到）。
- **迁移**：`store.MIGRATIONS` 是「表 + 列 + DDL」的声明式清单，启动时 `ALTER TABLE` 补齐，
  老库直接起新版本不会丢数据。

## 7. 加密与凭据

- `crypto.py`：`HMAC-SHA256` 派生密钥流 + Encrypt-then-MAC 完整性标签（纯标准库，不引入
  cryptography 依赖）。密文带版本前缀，`decrypt` 遇到非法/被篡改内容返回空串而不是抛异常。
- 主密钥来源：`QLIKEAPI_ENC_KEY`，未设置则回退 `QLIKEAPI_SECRET` 派生。
  **换掉 `QLIKEAPI_SECRET` 会导致已存密钥解不开**，需要重填渠道密钥。
- 历史明文密钥在 `init_db()` 时自动加密（一次性迁移）。
- 对外一律掩码：`store.mask()`，接口/日志/控制台都拿不到明文密钥。
- 控制台口令：`scrypt` + 随机盐；会话是 HttpOnly 签名 Cookie（HTTPS 下加 `Secure`）。

## 8. 前端

- 原生 JS + Bootstrap 5.3 + Tabler Icons + Chart.js，**全部本地自托管**
  （`app/static/vendor/`），不依赖 CDN —— 内网/断网也能用。
- 一个 `index.html` + `app.js`（按视图切换）+ `style.css`；深浅色用 `body.dark` 命名空间。
- 侧边栏结构固定不动（有意为之），页面内容区自由演进。
- 所有按钮都必须接到真实动作：没有「点了没反应」的空壳按钮（回归清单见 `CONTRIBUTING.md`）。

## 9. 明确不做的事（以及为什么）

| 不做 | 原因 |
|---|---|
| 用户体系 / 注册 / 充值 / 兑换码 / 邀请返利 | 这是自用网关，不是 SaaS；用户体系会带来一整套越权面 |
| 拼车共享 / 多账号池 | 账号池涉及上游 ToS 风险，且本项目已有「多 key 轮换 + 冷却」够用 |
| 语义缓存 / token 级计费 | 图片按张计费，缓存图片＝转存（与「不落盘」冲突） |
| 用 New API 的插件机制实现 | 实测受限：路由重叠会被整份拒绝、宿主协议表无图片项、全局钩子被拒、请求体 1 MiB 上限 |
| 引入 Redis / Postgres | 单机自用，SQLite 足够；多一个组件就多一份运维负担 |
| 复制 gpt-load 全套重设计 | 只需要它的「密钥池细节」，不需要它的分组/计费模型 |

详见 [`ROADMAP.md`](ROADMAP.md)。
