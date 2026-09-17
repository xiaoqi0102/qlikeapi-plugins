# 智能路由与负载/并发控制 —— 调研与整合规划

> 调研对象：**New API**（`Calcium-Ion/new-api`）与 **Sub2API**（`Wei-Shaw/sub2api`）
> 目的：把两者的「智能路由（按请求内容与预设规则分发到合适渠道）」与「负载与并发控制（结合优先级、实时负载、健康状态分配请求，降低单点依赖）」中有价值的部分，整合进 `qlikeapi-plugins` 的图片协议转换网关。
> 状态：**规划（待确认后开工）**。所有验证手段均为零成本（干跑 / 缺 prompt 本地 400 / 只读探活），不做真实出图。

---

## 一、现状盘点（qlikeapi-plugins 已经有的）

| 能力 | 现状 | 代码位置 |
|---|---|---|
| 模型 → 渠道链 | 每个模型配置一条有序渠道链（`routes`） | `app/store.py` / `app/relay.py` |
| 优先级 | 渠道 `priority`（**数字大者优先**，与 New API 一致） | `providers.priority` |
| 权重 | 渠道 `weight`，同优先级档内加权随机 | `app/relay.py` |
| 熔断 | 连续失败计数 `fail_streak` → 自动禁用；**探活通过才恢复** | `app/relay.py` / `app/admin.py` |
| 多密钥 | `api_key` 换行分隔 + 轮换 + 单密钥失败冷却 | `app/relay.py` |
| 余额降权 | 站点余额低于阈值 → 关联渠道自动停用 | `app/balances.py` |
| 参数改写 | `param_override`（含删除字段、尺寸/质量归一） | `app/store.py` |
| 可观测 | `X-QLike-Provider` / `X-QLike-Failover` 响应头、请求日志（客户端请求 + 发上游请求双份） | `app/relay.py` |
| 干跑 | `GET /v1/route-preview?model=xxx` 不发出请求即可预览路由链 | `app/main.py` |

**结论**：骨架已具备「模型→渠道链 + 优先级 + 权重 + 熔断 + 密钥轮换」。缺的是**失败降级语义、并发闸门、健康画像、请求内容感知**四块。

---

## 二、New API 调研结论

### 2.1 智能路由（模型 → 渠道）

内存缓存 + 定时同步，避免每请求查库：

- `model/channel_cache.go`：`group2model2channels`（仅启用渠道）、`channelsIDM`（含禁用）、`SyncChannelCache(frequency)` 定期从 DB 重建。
- 选择算法 `GetRandomSatisfiedChannel(retry)`：
  1. 取该 `group + model` 下**所有启用渠道**，按 `priority` 降序取出**去重后的优先级档列表** `uniquePriorities`；
  2. **`targetPriority = sortedUniquePriorities[retry]`** —— 重试次数直接决定用哪一档；
  3. 在该档内**加权随机**：DB 版 `weightSum += ability.Weight + 10`（**基础权重 10**，保证 `weight=0` 的渠道也有机会）。
- DB 版 `model/ability.go`：`getPriority()` 用 `SELECT DISTINCT(priority) ... ORDER BY priority DESC` 取档，`retry` 超出档数时退到**最低档**（`priorities[len-1]`）；`getChannelQuery()` 用 `MAX(priority)` 子查询。
- 渠道约束：`filterAbilitiesByConstraints(abilities, model, filters)` + `dto.ChannelFilter` —— **按请求内容过滤候选渠道**（如身份/密钥约束）。
- 自动禁用/恢复：渠道 `Status != ChannelStatusEnabled` 直接跳过；测试失败即 `DisableChannel`，由渠道测试任务（`controller/channel-test.go`，含 `shouldUseStreamForAutomaticChannelTest`）定期复测并恢复。

### 2.2 负载与并发控制

| 机制 | 实现要点 |
|---|---|
| 全局限流 | `middleware/rate-limit.go`：命名空间 `rateLimit:v2`，**Redis 固定窗口 + Lua 原子 INCR/EXPIRE**；按 **IP** 与 **用户** 两种维度；无 Redis 时降级为进程内内存限流；拒绝时 **429 + `Retry-After`** |
| 模型级限流 | `middleware/model-rate-limit.go`：`MRRL`（总请求）/`MRRLS`（成功请求）**双计数**，叠加 `common/limiter` 的 **GCRA 令牌桶**（`WithCapacity/WithRate/WithRequested`，默认 Capacity=10、Rate=1）→ 平滑突发 |
| 插件协议限流 | `controller/plugin_protocol_limiter.go` |
| 渠道亲和 | `controller/channel_affinity_cache.go`（同一上下文尽量复用渠道，减少切换抖动） |

**可借鉴**：优先级档 + `retry` 降级、加权随机的基础权重、固定窗口 + 令牌桶双层限流、429 带 `Retry-After`。

---

## 三、Sub2API 调研结论

Sub2API 是「订阅 → API」中转，核心是**多账号池调度**，其「账号」等价于我们的「渠道/密钥」。

### 3.1 智能路由（账号调度器）

- `backend/internal/repository/scheduler_cache.go`（33KB）：账号调度快照放 **Redis 有序集合**，`score = 数据库返回的排序序号`（**排序语义由 DB 决定**，Redis 只做加速）；配合**快照版本号 + outbox**（`scheduler_outbox_repo.go`）保证 DB 与缓存一致；`RetireBucket/ReopenBucket` + 组级生命周期租约（`TryAcquireGroupLifecycleLease`）防止并发重建。
- **`UpdateLastUsed` / `schedulerLastUsedKey`**：记录每个账号「上次使用时间」→ 支持轮询/最少最近使用，天然分散负载。
- `backend/internal/repository/account_repo_schedulable_projection*.go`：**可调度账号投影** —— 预先算出「当前可调度」的账号集合，请求期只读。
- `backend/internal/repository/composite_model_route_repo.go`：**复合模型路由**（一个对外模型名映射到多平台账号池）。
- `backend/internal/service/account.go`：账号画像字段 `Priority int`、`Concurrency int`、**`LoadFactor *int`（调度负载因子；nil 时用 Concurrency）**、`GetBaseRPM()/GetRPMStrategy()/GetRPMStickyBuffer()/CheckRPMSchedulability(currentRPM)`（**RPM 维度可调度性 + 粘性缓冲**）。

### 3.2 健康状态与临时摘除

- `account_scheduling_threshold_eval.go`：**额度阈值暂停** —— 按平台配置阈值百分比（`account_scheduling_threshold`，`ThresholdPercent`），用量触线即暂停调度该账号（`AccountSchedulingThresholdDecision`）。
- `account_scheduling_threshold_reason.go`：临时取消调度（**TempUnsched**）带 **JSON reason（source / errorMessage / triggeredAt / untilUnix）**，**到期自动恢复**；`IsAccountSchedulingThresholdReason()` 用于区分摘除原因。
- `temp_unsched_cache_*`：摘除状态的缓存与健康检查。

### 3.3 并发控制（最直接可抄的一块）

- `backend/internal/handler/image_concurrency_limiter.go`（**图片专用**）：

```go
TryAcquire(enabled bool, limit int) (release func(), ok bool)
Acquire(ctx, enabled bool, limit int, wait bool, timeout time.Duration, maxWaiting int) (release func(), ok bool)
```

  —— **并发槽位 + 可选等待队列**（`maxWaiting` 上限、`timeout` 超时、`wait` 开关），返回 `release()` 释放；`maxWaiting < 0` 归零。
- `backend/internal/repository/concurrency_cache.go`：并发计数的 Redis 缓存（跨实例一致）。
- `session_limit_cache.go`：会话级限制（粘性窗口）。
- `concurrency_error_response.go`：并发超限时的统一错误响应。

---

## 四、差距分析（我们 vs 目标）

| 维度 | New API | Sub2API | qlikeapi-plugins 现状 | 差距 |
|---|---|---|---|---|
| 失败降级 | `priorities[retry]` 逐档降级 | 账号池重试 | 换下一家，但**不区分档位**（同档随机换） | ⚠ 需补 |
| 权重 | 权重 + 基础 10 | — | 纯权重随机 | 可选 |
| 并发闸门 | 全局/模型限流（429+Retry-After） | **图片并发槽 + 排队**（wait/timeout/maxWaiting） | **无**（只有超时） | ⚠ 缺口最大 |
| 健康画像 | 连续失败禁用 + 定期复测 | 成功率/阈值/TempUnsched（带 until+reason） | 连续失败熔断 + 探活恢复 | ⚠ 需补指标化 |
| 额度降权 | 渠道余额 | 用量阈值暂停（按平台百分比） | 余额阈值停用 | 基本够用 |
| 请求内容感知 | `ChannelFilter` 约束过滤 | 复合模型路由 + 能力判定（`account_grok_media_eligibility`） | 仅按模型名 | ⚠ 需补 |
| 渠道亲和 | `channel_affinity_cache` | `lastUsed` + 粘性缓冲 | 无 | 可选 |
| 可观测 | 日志 + 用量 | 面板（阈值原因、并发峰值） | 请求日志 + 响应头 | 可增强 |

---

## 五、整合规划（分三阶段，每阶段独立可上线）

> 每阶段遵守既有铁律：**部署 → 零成本验证 → 截图**；不引入真实出图；密钥不落库；SQLite 不变；侧边栏结构不变。

### 阶段 1（P0）：降级语义 + 并发闸门 + 决策可见

1. **重试降级链**（照 New API 语义）
   - 候选渠道按 `priority` 去重成档，`attempt N` 用第 N 档；档内加权随机（权重 = `weight`，基础权重 10）。
   - 触发降级的条件沿用现有规则：429 / 402 / 5xx / 连接失败；**400 立即返回**；401/403 标记密钥失效并降级；**超时默认不降级**（防重复扣费）。
2. **并发控制**（照 Sub2API `imageConcurrencyLimiter`）
   - 新增「渠道级」与「模型级」并发上限：`concurrency_limit`、`queue_max_waiting`、`queue_timeout_ms`。
   - 语义：`limit` 满 → 若 `wait=true` 且有 `maxWaiting` 余量则排队，超时返回 **429 + `Retry-After`**（与 New API 一致）；否则立即 429。
   - 计数放进程内（单实例 SQLite 部署足够）；预留 `concurrency_cache` 接口以便将来多实例。
3. **决策可见**
   - 响应头增加 `X-QLike-Chain`（本次候选链）、`X-QLike-Attempt`（第几次尝试）、`X-QLike-Degrade`（是否降档）、`X-QLike-Size-Adjusted`（尺寸是否被吸附，见 §六）。
   - `/v1/route-preview` 扩展为返回**分档候选链**，面板渠道页展示「优先级档」可视化。

### 阶段 2（P1）：健康画像 + 临时摘除

1. **滑窗指标**：每渠道维护近 N 次请求的成功率、P50/P95 延迟、429/5xx 占比（滚动窗口，落 SQLite 小表 + 内存缓存）。
2. **状态机合并**：`健康 → 降权（成功率低于阈值）→ 临时摘除（TempUnsched：until + reason JSON，照 Sub2API 格式）→ 探活通过恢复`。
   - 保留现有「连续失败熔断」为快速路径；新增「成功率/延迟」为慢速路径。
3. **额度联动**：站点余额/用量触线 → 关联渠道**降权或摘除**（现在只有停用），恢复同样要求探活通过。

### 阶段 3（P2）：请求内容感知 + 亲和 + 用量面板

1. **能力路由**：为渠道声明能力（支持的尺寸/比例、是否支持参考图、是否支持异步任务、最大 n），按请求内容（`size`/`aspect_ratio`/是否有参考图/是否要异步）先过滤候选，再走优先级档 —— 等价于 New API `ChannelFilter`。
2. **渠道亲和**：同一令牌 + 同一模型在冷却期内优先复用上次成功渠道（减少上游冷启动），失败即打破亲和。
3. **用量面板**：按渠道的成功率/P95/并发峰值/降级次数出图（Chart.js 已有），并可导出。

### 明确不做（沿用既有决策）

用户体系 / 注册 / 充值 / 兑换码 / 邀请返利 / 拼车共享 / 多账号池 / 语义缓存 / token 级计费 / 签到 —— 这些与图片协议转换网关的定位无关。

---

## 六、附：转发时「尺寸会变」的原因（已用真实日志定位）

**结论：尺寸不是上游改的，是本程序在做「安全吸附」（snapping）。**

`app/utils.py::gpt_safe_size()`：

```python
def gpt_safe_size(size):
    """OpenAI 系上游对尺寸有整除/面积/比例限制，做一次安全吸附。"""
    wh = parse_size(size)
    if not wh:
        return "auto"
    w, h = wh
    if (w % 16 == 0 and h % 16 == 0 and max(w, h) <= 3840 and max(w, h) / min(w, h) <= 3.0
            and 655_360 <= w * h <= 8_294_400):
        return f"{w}x{h}"
    t = w / h
    best = min(GPT_SIZES, key=lambda s: abs(math.log(t) - math.log(s[0] / s[1])))
    return f"{best[0]}x{best[1]}"
```

- 合法尺寸需同时满足：**宽高都能被 16 整除**、最长边 ≤ 3840、长短边比 ≤ 3、面积在 655,360 ~ 8,294,400 之间。
- `1920x1080`：`1920 % 16 == 0` ✓，但 **`1080 % 16 == 8` ✗** → 进入吸附分支。
- `GPT_SIZES` 里 `16:9` 的合法档只有 `2048x1152`（1.7778）与 `3840x2160`；按 `|log(t) - log(s)|` 取最近 → **`2048x1152`**。

**真实日志（本机 `logs` 表）**：

| 日志 id | 渠道 | 模型 | 客户端请求 | 发往上游 | 结果 |
|---|---|---|---|---|---|
| 121 | qnaigc-fal | gpt-image-2 | `size: 1920x1080` | `image_size: 2048x1152` | HTTP 200 |
| 115 | change2pro | gpt-image-2 | `size: 1920x1080` | `size: 2048x1152` | HTTP 404（上游无此端点） |

两条链路（同步面 / fal 异步面）都走同一个吸附函数，行为一致。

**想要「原样不改」的办法**：

1. 客户端直接传合法档：`2048x1152`（16:9）、`1536x1024`（3:2）、`1024x1024`（1:1）、`3840x2160`（4K 16:9）—— 满足整除规则就不会被改；
2. 或走 Gemini 系（`change2pro` 的香蕉 / `qnaigc-fal` 的 gemini）用 `aspect_ratio` + `resolution`，尺寸表达的是比例 + 档位，不存在整除问题；
3. 若希望**保比例优先**（现状）之外再加**保像素优先**模式（宁可比例微变也不放大），可作为渠道级开关，需确认后实现。

> 面板「请求日志 → 详情」里同时保留「客户端请求」与「发往上游（翻译后）」两份 JSON，任何参数改写都能逐字段对照。
