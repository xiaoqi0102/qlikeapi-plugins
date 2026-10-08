# Qlike 中转站 API 接口说明

> **版本**：v1.1　**更新日期**：2026-10-08　**适用网关**：New API v1.0.0-rc.41
> **本文档面向调用方**：一个 Base URL、一个密钥，即可调用文本、图片、视频三类能力，请求与返回全部为 **OpenAI 风格**。
> **在线版**：`https://api.qlike.top/api-docs`　**Markdown 下载**：`https://api.qlike.top/api-docs.md?download=1`　（备用域名 `img.qlike.top` 同样可用）

---

## 0. 一句话接入

| 项 | 值 |
|---|---|
| **Base URL** | `https://api.qlike.top` |
| **鉴权头** | `Authorization: Bearer sk-xxxxxxxx`（密钥在后台「令牌」页创建） |
| **计费货币** | 美元（USD / $） |
| **协议** | OpenAI 兼容（官方 SDK、LangChain、Cursor、Cline、Cherry Studio 等可直接填 Base URL + Key） |
| **三条主线** | 文本 `POST /v1/chat/completions`　图片 `POST /v1/images/generations`　视频 `POST /v1/videos` |

---

## 1. 基础信息

| 项 | 说明 |
|---|---|
| API 根地址 | `https://api.qlike.top`（**不要**再加 `/v1`，SDK 里填这个根地址即可） |
| 鉴权方式 | 请求头 `Authorization: Bearer <令牌>`；`GET /v1/models` 等所有接口通用 |
| 请求格式 | `Content-Type: application/json`；图片改图、素材上传另支持 `multipart/form-data` |
| 素材上传 | 图片 / 视频 / 音频先经 `POST /v1/files` 转公网直链（见 §8） |
| 响应格式 | JSON（`{"error":{"message","type","code"}}` 为统一错误体） |
| 流式 | 文本接口支持 `"stream": true`（SSE） |
| 令牌与额度 | 后台「令牌」页自助创建，可设额度上限、过期时间、可用分组 |
| 计费口径 | 文本按 token 计费；图片、视频按**次**计费（单价写在模型名后缀里） |
| 失败退款 | 按次计费的任务，失败（含上游审核不通过）**自动全额退回**，日志页可查 |
| 对账入口 | 后台「日志」页（每条请求的模型、token、费用、状态可查） |

---

## 2. 快速开始（3 步）

**第 1 步 · 创建令牌**
后台 → 「令牌」→ 新建 → 选择分组（见 §3）→ 复制 `sk-` 开头的密钥。

**第 2 步 · 选对分组**
只发文本选 `default`；只做图片选 `image`；只做视频选 `video`。分组决定这个令牌**能看见哪些模型**。

**第 3 步 · 发第一个请求**

```bash
curl https://api.qlike.top/v1/chat/completions \
  -H "Authorization: Bearer sk-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash",
    "messages": [{"role": "user", "content": "你好，介绍一下你自己"}],
    "stream": false
  }'
```

Python（OpenAI 官方 SDK，无需任何改写）：

```python
from openai import OpenAI
client = OpenAI(api_key="sk-xxxxxxxx", base_url="https://api.qlike.top/v1")
r = client.chat.completions.create(
    model="deepseek-v4.1-flash",
    messages=[{"role": "user", "content": "用三句话说明什么是中转站"}],
)
print(r.choices[0].message.content)
```

---

## 3. 令牌与分组

| 分组 | 可见范围 | 适用场景 |
|---|---|---|
| `default` | **全部模型**（文本 + 图片 + 视频） | 通用后端、聚合平台 |
| `image` | 仅图片生成模型 | 作图工具 / 面向 C 端的作图应用 |
| `video` | 仅视频生成模型 | 短视频、漫剧生产工具 |

> 三个分组结算倍率均为 **1.0x**，分组只影响「能看到/能调哪些模型」，不额外加价。

**安全提示**
- 密钥等同账户余额，**不要写进前端代码**或公开仓库；前端请走你自己的服务端转发。
- 一个应用一个令牌，便于按应用统计与单独吊销。
- 建议给每个令牌设置额度上限，泄露时任其耗尽即可，不影响主账户。

---

## 4. 接口总览

| 能力 | 方法 | 路径 | 鉴权 | 备注 |
|---|---|---|---|---|
| 文本对话 | POST | `/v1/chat/completions` | `Bearer` | OpenAI 兼容，支持流式、多模态 |
| 模型清单 | GET | `/v1/models` | `Bearer` | 返回当前令牌可见模型 |
| 文生图 | POST | `/v1/images/generations` | `Bearer` | 按次计费 |
| 图片编辑/参考图 | POST | `/v1/images/edits` | `Bearer` | JSON 或 multipart |
| 创建视频任务 | POST | `/v1/videos` | `Bearer` | 异步，返回任务 ID |
| 查询视频任务 | GET | `/v1/videos/{task_id}` | `Bearer` | 轮询进度 / 取状态 |
| 下载成片 | GET | `/v1/videos/{task_id}/content` | **必须带 `Authorization` 头** | 返回 mp4 字节流 |
| 视频兼容面 | POST | `/v1/video/generations` | `Bearer` | 老客户端兼容路径，等价 `/v1/videos` |
| 素材上传中转 | POST | `/v1/files` | `Bearer` | 图片 / 视频 / 音频 → 公网直链（见 §8） |

---

## 5. 文本对话

`POST /v1/chat/completions`

### 5.1 请求字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | string | ✅ | 模型名，见 §5.2 |
| `messages` | array | ✅ | `[{"role":"system|user|assistant","content":"..."}]` |
| `stream` | bool | ✗ | `true` 返回 SSE 流，默认 `false` |
| `temperature` | number | ✗ | 0–2，默认随模型 |
| `max_tokens` | int | ✗ | 最大输出 token |
| `tools` / `tool_choice` | array | ✗ | 函数调用（`gpt-*` / `gemini-*` / `grok-*` 均支持） |
| `response_format` | object | ✗ | `{"type":"json_object"}` 结构化输出 |

**多模态**：`content` 可传数组，图片支持公网 URL，也支持 `data:image/png;base64,...`：

```json
{"role":"user","content":[
  {"type":"text","text":"这张图里是什么？"},
  {"type":"image_url","image_url":{"url":"https://example.com/a.png"}}
]}
```

### 5.2 模型与价格（USD，每 100 万 token）

| 模型 | 上下文 | 输入 $/1M | 输出 $/1M | 特点 |
|---|---|---|---|---|
| `deepseek-v4.1-flash` | 1M | 0.03 | 0.12 | 最便宜，适合批量文本 |
| `qwen3.8-flash` | 1M | 0.016 | 0.047 | 视觉理解、文档 |
| `glm-5.3-flash` | 1M | 0.015 | 0.05 | 编码 / Agent |
| `gemini-3.6-flash` | 1M | 1.5 | 7.5 | 多模态均衡 |
| `gemini-3.7-flash` | 1M | 0.75 | 3.75 | Agent / 编码 |
| `gemini-3.8-flash` | 1M | 0.75 | 3.75 | 长程软件工程 |
| `grok-4.6` | 500K | 2 | 6 | 长任务 Agent |
| `grok-4.7` | — | 75 | 75 | ⚠ 新上模型，价格待定稿 |
| `gpt-5.6-sol` | 1.1M | 4 | 20 | 复杂专业工作 |
| `gpt-6-astra` | 1.1M | 10 | 50 | 旗舰推理 |

> 以上单价即后台「模型定价」口径；`grok-4.7` 为新增模型，描述与价格尚未最终确认，正式接入前请以后台模型广场显示为准。

### 5.3 响应示例

```json
{
  "id": "chatcmpl-8f2c...",
  "object": "chat.completion",
  "created": 1791439200,
  "model": "gemini-3.8-flash",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "你好！我是一个……"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 12, "completion_tokens": 86, "total_tokens": 98}
}
```

---

## 6. 图片生成

分组 `image`（`default` 令牌同样可用）。全部**按次计费**，先扣后返（失败自动退）。

### 6.1 文生图 `POST /v1/images/generations`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | string | ✅ | 见 §6.4 |
| `prompt` | string | ✅ | 中文 / 英文均可 |
| `size` | string | ✗ | 像素（`1024x1024`）或档位（`1K`/`2K`/`4K`）；也可传比例（`16:9`） |
| `resolution` | string | ✗ | `1k` / `2k` / `4k`（部分模型专用，与 `size` 二选一） |
| `quality` | string | ✗ | `low` / `medium` / `high`（影响阶梯价，见 §6.4） |
| `n` | int | ✗ | 出图张数，默认 1 |
| `response_format` | string | ✗ | `url`（默认）或 `b64_json` |

```bash
curl https://api.qlike.top/v1/images/generations \
  -H "Authorization: Bearer sk-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3-pro-image",
    "prompt": "一张极简风格的咖啡杯产品图，白色背景，柔和侧光，4K",
    "size": "1024x1024",
    "n": 1
  }'
```

### 6.2 改图 / 参考图 `POST /v1/images/edits`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | string | ✅ | 支持改图的模型见 §6.4 |
| `prompt` | string | ✅ | 编辑指令 |
| `image` | string | ✗ | 单张参考图（URL 或 data URI） |
| `images` | array | ✗ | 多张参考图（URL / data URI 混传均可） |
| `size` / `resolution` / `n` / `response_format` | — | ✗ | 同 §6.1 |

```bash
curl https://api.qlike.top/v1/images/edits \
  -H "Authorization: Bearer sk-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image",
    "prompt": "保持人物一致，把背景换成雨夜霓虹街道",
    "images": [
      "https://example.com/person.png",
      "data:image/png;base64,iVBORw0KGgo..."
    ],
    "size": "2K"
  }'
```

> **参考图怎么传？** URL 与 Base64（data URI）**都支持**，网关会按上游要求自动适配（只认公网 URL 的上游自动上传换直链，只认 Base64 的上游自动下载内联）。参考图数量上限见 §6.4。

### 6.3 尺寸与档位规则

| 传入 | 归一档位 |
|---|---|
| 最长边 ≤ 512 | 0.5K |
| ≤ 1536 | 1K |
| ≤ 2048 | 2K |
| > 2048 | 4K |

- 具体像素会被**吸附到模型支持的最近档位与比例**，不必自己算；
- 只认档位/比例的模型（如 `gpt-image-2-all`）请直接传 `1k`/`2k`/`4k` + 比例；
- 不确定时建议先传 `size: "1K"` 试跑。

### 6.4 模型与单价（USD / 次）

| 模型 | 单价 | 分辨率 | 改图 | 参考图上限 | 备注 |
|---|---|---|---|---|---|
| `gemini-3-pro-image` | $0.122 | 1K/2K/4K | ✅ | — | 质量最优 |
| `gemini-3-pro-image-preview` | $0.19 | 1K/2K/4K | ✅ | ≤14（高保真物体 ≤6、角色一致性 ≤5） | |
| `gemini-3.1-flash-image` | $0.11 | 1K/2K/4K | ✅ | — | 性价比高 |
| `gemini-3.1-flash-image-preview` | $0.16 | 1K/2K/4K | ✅ | ≤14 | |
| `gemini-nano-banana-2.1` | $0.16 | 最高 4K（4096×4096） | ✅ | — | 图片编辑模型 |
| `gpt-image-2` | $0.060235 | 1K/2K/4K | ✅ | — | 固定单价最便宜 |
| `gpt-image-2-all` | $0.03 – $0.14（阶梯） | 1k/2k/4k | ✅ | — | 阶梯按 `quality` × `resolution` 定档 |
| `gpt-image-2.5-flare` | $0.0615 | 1K/2K/4K | ✅ | — | |
| `gpt-image-2.5-sunburst` | $0.0615 | 1K/2K/4K | ✅ | — | |

### 6.5 响应示例

```json
{
  "created": 1791439200,
  "data": [{"url": "https://.../image.png", "revised_prompt": "..."}]
}
```

> - 返回 `data[].url` 或 `data[].b64_json`（取决于模型与 `response_format`）；
> - URL 为临时直链，**请转存到自己的存储**；
> - 依赖 URL 的客户端请优先选支持回链的模型；个别模型固定回 `b64_json` 且不认 `response_format`，接入前先用小样验证一次。

---

## 7. 视频生成

分组 `video`（`default` 令牌同样可用）。**异步三段式**：创建 → 轮询 → 下载。

### 7.1 三步调用

| 动作 | 方法 | 路径 | 说明 |
|---|---|---|---|
| ① 创建任务 | POST | `/v1/videos` | 返回任务 `id`，**请保存** |
| ② 查询任务 | GET | `/v1/videos/{id}` | 返回 `status` + `progress` |
| ③ 下载成片 | GET | `/v1/videos/{id}/content` | 返回 mp4；**必须带 `Authorization` 头** |

```bash
# ① 创建
curl https://api.qlike.top/v1/videos \
  -H "Authorization: Bearer sk-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedance-2.0-900-0.70",
    "prompt": "一只橘猫从窗台跳下，慢动作，阳光洒落",
    "seconds": "5",
    "resolution": "720p",
    "aspect_ratio": "16:9"
  }'

# ② 轮询（拿到 id 后每 5–10 秒查一次）
curl https://api.qlike.top/v1/videos/task_xxxxxxxx \
  -H "Authorization: Bearer sk-xxxxxxxx"

# ③ 下载（status=completed 后）
curl -L https://api.qlike.top/v1/videos/task_xxxxxxxx/content \
  -H "Authorization: Bearer sk-xxxxxxxx" -o out.mp4
```

**返回示例**

创建：
```json
{"id":"task_xxxxxxxx","object":"video","model":"seedance-2.0-900-0.70","status":"queued","progress":0}
```

查询：
```json
{"id":"task_xxxxxxxx","object":"video","model":"seedance-2.0-900-0.70",
 "status":"completed","progress":100,"created_at":1790672269,"completed_at":1790677384}
```

`status` 取值：`queued` / `in_progress` / `completed` / `failed`。返回 `failed` 时读 `fail_reason`（常见如「内容未通过审核」），该次费用**自动全额退回**。

> ⚠️ **两个必须知道的硬约束**
> 1. 查询响应里**没有 `url` 字段**，成片地址要自己拼：`{BaseURL}/v1/videos/{id}/content`。
> 2. `/content` **只认请求头 `Authorization`**，不支持 `?token=` 查询参数；不带请求头直接 401。
> 3. 成片是**临时的**（部分模型仅保留 1 小时），`status=completed` 后请立即下载转存；过期返回 `410 {"code":"artifact_gone"}`。

### 7.2 请求字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | string | ✅ | **原样照抄模型名（含价格后缀）**，见 §7.4 |
| `prompt` | string | ✅ | 视频描述；部分模型上限 15000 字符 |
| `seconds` | **string** | ✗ | 时长（`"5"`）。**一律传字符串**，传数字部分上游直接 400 |
| `resolution` | string | ✗ | `480p` / `720p` / `1080p`；**由模型名决定分辨率的模型会忽略此字段** |
| `aspect_ratio` | string | ✗ | 比例，见各模型能力矩阵 |
| `size` | string | ✗ | 比例别名（`16:9`、`1024x1024`），与 `aspect_ratio` 等价 |
| `images` | string[] | ✗ | 参考图 / 首帧，元素为公网 URL 或 data URI |
| `start_frame` / `end_frame` | string | ✗ | 首帧 / 尾帧（仅矩阵中标 ✅ 的模型支持，二者要成对） |
| `videos` | string[] | ✗ | 参考视频（仅部分模型支持） |
| `audios` | string[] | ✗ | 参考音频（仅部分模型支持） |

**字段别名**（网关透传，各上游按需识别；不确定时用上面的规范字段最稳）：

| 语义 | 可用写法 |
|---|---|
| 参考图 | `images` / `image_urls` / `image_refs` / `reference_images` |
| 首帧 | `start_frame` / `first_frame_image` / `images` 的第 1 张 |
| 尾帧 | `end_frame` / `last_frame_image` |
| 参考视频 | `videos` / `video_urls` |
| 参考音频 | `audios` / `audio_urls` |
| 时长 | `seconds` / `duration` |
| 比例 | `aspect_ratio` / `ratio` / `size` |

### 7.3 四种生成形态（每种一个完整请求）

**① 文生视频**（只给文字）

```json
{
  "model": "seedance-2.0-900-0.70",
  "prompt": "雨夜的东京街头，霓虹倒映在积水里，镜头缓慢推进",
  "seconds": "5",
  "resolution": "720p",
  "aspect_ratio": "16:9"
}
```

**② 图生视频**（给 1–N 张参考图，单张即作为首帧）

```json
{
  "model": "minimax-h3-jiasu-1.00",
  "prompt": "让人物自然眨眼并微笑，镜头轻微横移",
  "seconds": "5",
  "aspect_ratio": "16:9",
  "images": ["https://example.com/portrait.png"]
}
```

多图参考（角色/场景/道具一致性，数量上限见矩阵）：

```json
{
  "model": "seedance-2.5-301010-2.00",
  "prompt": "男子穿着图中的外套，站在图中的街景中转身，@图片1 @图片2 保持主体一致",
  "seconds": "5",
  "resolution": "720p",
  "images": ["https://example.com/a.png", "https://example.com/b.png"]
}
```

**③ 首尾帧生成视频**（给第一帧 + 最后一帧，中间由模型补全）

```json
{
  "model": "doubao-seedance-2-5-260628-0.50",
  "prompt": "从白天平滑过渡到黄昏，镜头固定",
  "seconds": "5",
  "resolution": "720p",
  "aspect_ratio": "16:9",
  "start_frame": "https://example.com/first.png",
  "end_frame": "https://example.com/last.png"
}
```

**④ 全能参考（多模态参考：图 + 视频 + 音频）**

```json
{
  "model": "minimax-h3-jiasu-1.00",
  "prompt": "参考视频的运镜节奏，参考音频的情绪，让图中的角色开口演唱",
  "seconds": "10",
  "aspect_ratio": "16:9",
  "images": ["https://example.com/role.png"],
  "videos": ["https://example.com/ref-motion.mp4"],
  "audios": ["https://example.com/ref-voice.mp3"]
}
```

> 参考视频可带时长：`{"url": "https://.../ref.mp4", "duration_seconds": 5}`（不传按 5 秒计）。

### 7.4 模型清单与单价（USD / 次）

| 模型名（**原样传**） | 单价 | 一句话 |
|---|---|---|
| `sd2.0-mini-903-480p-0.83` | $0.83 | 480p 便宜档，5–15 秒 |
| `sd2.0-mini-903-720p-0.85` | $0.85 | 720p，5–12 秒 |
| `seedance-2.0-900-0.70` | $0.70 | 480p/720p，4–15 秒 |
| `seedance-2.5-900-0.80` | $0.80 | 480p/720p，4–30 秒，9 图 |
| `doubao-seedance-2-0-260128-0.50` | $0.50 | 480p/720p/1080p，4–15 秒，支持首尾帧 |
| `doubao-seedance-2-5-260628-0.50` | $0.50 | 480p/720p/1080p，4–30 秒，支持首尾帧 |
| `MiniMax-H3-0.50` / `MiniMax-H3-gaisc-0.50` | $0.50 | 720p/1080p，4–15 秒，支持首尾帧 |
| `minimax-h3-jiasu-1.00` | $1.00 | **2K 固定**，4–15 秒，9 图 3 视频 3 音频 |
| `dola-sd-2.0-933-1.30` | $1.30 | 过真人，9 图 3 音频 |
| `seedance-2.5-101010-1.80` | $1.80 | 720p，4–30 秒，10 图 10 音频（不参考视频） |
| `seedance2.0-900-fast-1.80` | $1.80 | **固定 15 秒 / 720P / fast**，9 图 |
| `seedance-2.5-301010-2.00` | $2.00 | 480p/720p，4–30 秒，30 图 10 音频 |
| `sd-2.0-933-720-fast-原生真人-2.60` | $2.60 | 720p，4–15 秒，9 图 3 视频 3 音频 |

> 模型名末尾的 `-0.70`、`-0.85` 等是**按次单价标记，不是可省略的装饰**，必须逐字符原样传，否则返回「模型不存在」。

### 7.5 能力矩阵

| 模型 | 时长 | 分辨率 | 比例 | 文生 | 首帧 | 尾帧 | 参考图 | 参考视频 | 参考音频 |
|---|---|---|---|---|---|---|---|---|---|
| `doubao-seedance-2-5-260628-0.50` | 4–30s | 480/720/1080p | 16:9 / 9:16 / 4:3 / 3:4 / 1:1 | ✅ | ✅ | ✅ | ≤9 | — | — |
| `doubao-seedance-2-0-260128-0.50` | 4–15s | 480/720/1080p | 同上 | ✅ | ✅ | ✅ | ≤9 | — | — |
| `MiniMax-H3-0.50` / `-gaisc-0.50` | 4–15s | 720/1080p | 16:9 / 9:16 / 1:1 / 4:3 / 3:4 | ✅ | ✅ | ✅ | ≤9 | — | — |
| `minimax-h3-jiasu-1.00` | 4–15s（默认 15） | **2K 固定** | 9:16 / 1:1 / 3:4 / 4:3 / 16:9 | ✅ | ✅ | ✅ | ≤9 | ≤3 | ≤3 |
| `seedance-2.0-900-0.70` | 4–15s | 480p / 720p | 9:16 / 1:1 / 3:4 / 4:3 / 16:9 | ✅ | ✅（单图参考） | — | ≤9 | — | — |
| `seedance-2.5-900-0.80` | 4–30s | 480p / 720p | 同上 | ✅ | ✅（单图参考） | — | ≤9 | — | — |
| `seedance-2.5-101010-1.80` | 4–30s | 720p | 同上 | ✅ | ✅（单图参考） | — | ≤10 | — | ≤10 |
| `seedance-2.5-301010-2.00` | 4–30s | 480p / 720p | 同上 | ✅ | ✅（单图参考） | — | ≤30 | — | ≤10 |
| `sd-2.0-933-720-fast-原生真人-2.60` | 4–15s | 720p | 同上 | ✅ | ✅（单图参考） | — | ≤9 | ≤3 | ≤3 |
| `dola-sd-2.0-933-1.30` | — | — | — | ✅ | ✅（单图参考） | — | ≤9 | — | ≤3 |
| `seedance2.0-900-fast-1.80` | 固定 15s | 固定 720p | — | ✅ | — | — | ≤9 | — | — |
| `sd2.0-mini-903-480p-0.83` | 5–15s | 固定 480p | 1:1 / 3:4 / 4:3 / 9:16 / 16:9 / 21:9 / 自适应 | ✅ | ✅ | ✅（成对） | ≤9 | ≤3 | ≤3 |
| `sd2.0-mini-903-720p-0.85` | 5–12s | 固定 720p | 同上 | ✅ | ✅ | ✅（成对） | ≤9 | ≤3 | ≤3 |

> 「—」表示该模型不提供此项能力。分辨率为「固定」的模型会**忽略**请求里的 `resolution`。

### 7.6 校验与互斥规则

| 规则 | 说明 |
|---|---|
| 时长越界 | 部分上游**静默收敛**（不报错，按默认时长出片），部分直接 400；以矩阵区间为准 |
| 首尾帧 ⟂ 参考图 | 同时传会报错或丢弃参考图，二选一 |
| 首尾帧必须成对 | 只给一张会自动降级为「参考图」处理（不报错） |
| 未提供视频/音频参考的模型 | 传了会被忽略或报错，见矩阵 |
| `seconds` 传数字 | 部分上游 400，**统一传字符串** |
| 总秒数避免小数点 | 传 `"5"` 而不是 `"5.0"` |
| 素材数量超限 | 网关侧直接报中文错（如「参考图最多 9 个，本次提交 12 个」），不消耗上游额度 |

### 7.7 老客户端兼容面

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/video/generations` | 等价 `/v1/videos` |
| GET | `/v1/video/generations/{task_id}` | 等价 `/v1/videos/{id}` |

---

## 8. 素材上传中转（图片 / 视频 / 音频 → 公网直链）

### 8.1 为什么需要它

本站的**视频上游多为「只认公网 http(s) 素材」**：素材地址交给上游后，由上游服务器自己去抓取，
Base64 / 本地文件一律不收。而 New API 的任务插件沙箱里，插件自己**不能发 HTTP 请求**
（只能声明一个请求交给宿主代发），所以「提交任务时自动把 Base64 转成直链」在插件层做不到。

为此本站提供**素材上传中转**：先调 `POST /v1/files` 把素材（图片 / 视频 / 音频）转成公网直链，
再把直链填进 `imageUrls` / `videoUrls` / `audioUrls` / `firstFrameUrl` / `lastFrameUrl` 提交任务。

### 8.2 接口

`POST /v1/files`

| 项 | 说明 |
|---|---|
| 鉴权 | `Authorization: Bearer <你的令牌>`；也支持 `?token=<令牌>`（客户端加不了请求头时用） |
| 入参（三选一） | ① `multipart/form-data` 直接传文件（字段名随意，如 `file`）<br>② `application/json`：`{"data_url":"data:image/png;base64,..."}`、`{"base64":"..."}`、`{"url":"https://..."}`<br>③ 裸字节：`Content-Type: image/png` 直接 POST 文件内容 |
| 可选参数 | `?target=sudashui` / `jiasu` / `imgbb` / `uguu` 指定落到哪个站点；`?format=text` 只回纯文本直链 |
| 返回 | `{"ok":true,"url":"https://…","host":"…","host_label":"…","mime":"video/mp4","bytes":123456,"expires":"…","attempts":[…​]}` |

已经是公网 http(s) 直链的素材**原样返回**（不转存、不消耗额度）；素材不落本站磁盘。

### 8.3 三种调用方式

**① 本地文件（multipart，最常用）**

```bash
curl https://api.qlike.top/v1/files \
  -H "Authorization: Bearer sk-xxx" \
  -F "file=@./reference.mp4"
```

**② Base64 / data URI（JSON）**

```bash
curl https://api.qlike.top/v1/files \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{"data_url":"data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."}'
```

**③ 已经是直链（原样返回，零成本）**

```bash
curl "https://api.qlike.top/v1/files?target=sudashui" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/a.jpg"}'
```

**返回示例**

```json
{
  "ok": true,
  "url": "https://files.sudashuiapi.com/proxy/uploads/20261008/xxx.mp4",
  "link": "https://files.sudashuiapi.com/proxy/uploads/20261008/xxx.mp4",
  "host": "sudashui_files",
  "host_label": "速搭水文件站",
  "mime": "video/mp4",
  "kind": "video",
  "bytes": 1048576,
  "filename": "reference.mp4",
  "expires": "上游自托管（长期）",
  "attempts": [
    {"host": "sudashui_files", "label": "速搭水文件站", "ok": true, "status": 200, "ms": 431, "url": "https://…"}
  ]
}
```

### 8.4 落地站点与上限

| 站点 id | 名称 | 接受类型 | 单文件上限 | 直链有效期 |
|---|---|---|---|---|
| `sudashui_files` | 速搭水文件站 | 图片 / 视频 / 音频 | 图片 30MB、视频 50MB、音频 15MB | 约 2 小时（签名直链，返回体 `expires` 里有站方给的到期时间） |
| `jiasu_media` | 佳速素材 CDN | 图片 / 视频 / 音频 | 32MB | 长期（按内容 hash 存 CDN） |
| `imgbb` | ImgBB | 仅图片 | 20MB | 长期 |
| `uguu` | Uguu | 图片 / 视频 / 音频 | 100MB | 约 3 小时 |

- 不传 `?target` 时按**链路顺序**依次尝试（默认：速搭水 → 佳速 → ImgBB → Uguu），第一个成功即用；
  返回体里的 `attempts` 会列出每一站的尝试结果、耗时与失败原因。
- 视频 / 音频会自动跳过只收图片的 ImgBB；超过某站上限的素材会跳过并记明原因。
- **建议**：给某个上游提交任务时用 `?target=` 指定该上游自己的文件站 —— 直链就在它自家 CDN 上，最稳。
- **时效**：速搭水文件站返回的是签名直链（约 2 小时），**拿到直链请尽快提交任务**（上游是提交后立刻抓取，正常够用）；
  需要长期可读的链接（例如先传素材、隔很久再生成）请用 `?target=jiasu`（长期 CDN）或图片走 `?target=imgbb`（长期）。

> 客户端里通常不用手写这些请求：作图 / 视频工具（如 盐值AI）在「参考素材中转 / 自定义上传接口」填
> `https://api.qlike.top/v1/files` 即可，本地文件与 Base64 会被自动转成直链。

### 8.5 常见错误

| HTTP | message | 说明 |
|---|---|---|
| 400 | `素材既不是 http(s) 链接，也不是 base64 字符串` | 传的内容既不是链接也不是 Base64 |
| 400 | `裸 base64 嗅探不出文件类型` | 用 Base64 时请改传 data URI，或在 JSON 里补 `mime` 字段 |
| 401 | `Invalid token` | 令牌没带 / 带错 |
| 413 | `素材 xx MB 超过上限 100MB` | 超过本站单次上限（本站上限 100MB） |
| 503 | `素材上传失败：…` | 所有候选站点都失败，`attempts` 里有每站原因 |

---

## 9. 参考素材规则

1. **优先公网 HTTPS 直链**：图床、对象存储（OSS / R2 / S3）均可。这是**全渠道通吃**的传法。
2. **Base64 / 本地文件**：图片侧（§6.2）网关自动适配；**视频侧不要直接传**——多数视频上游只认公网 URL，会直接拒。
   请先调 **§8 素材上传中转** `POST /v1/files` 换成公网直链，再填进 `imageUrls` / `videoUrls` / `audioUrls` 等字段。
3. **不接受**：内网地址、`file://`、需鉴权的私有链接、超大文件（建议单图 ≤ 10MB）。
4. 数量上限：参考图 / 视频 / 音频上限见 §7.5 矩阵，超限网关侧直接拦截。
5. 提示词里引用素材请用自然语言指代（如「@图片1」「图里的人物」），**不要依赖序号映射的隐式规则**。

---

## 10. 模型总表（当前在线 35 个）

| 类别 | 分组 | 数量 | 计费 | 端点 |
|---|---|---|---|---|
| 文本对话 | `default` | 10 | 按 token | `/v1/chat/completions` |
| 图片生成 | `default` / `image` | 9 | 按次 | `/v1/images/generations`、`/v1/images/edits` |
| 视频生成 | `default` / `video` | 14 | 按次 | `/v1/videos` |

> 另有 2 个仅 `default` 分组可见的按次模型：`grok-imagine-image-2.0`（$0.20 / 次）、`grok-imagine-video`（$0.50 / 次），调用方式以模型广场说明为准。
> 模型清单会随上游调整，**接入前请以 `GET /v1/models` 或后台模型广场为准**。

---

## 11. 错误码

统一错误体：

```json
{"error": {"message": "参考图最多 9 个，本次提交 12 个，请删减后重试", "type": "invalid_request_error", "code": null}}
```

| HTTP | 常见 `message` | 处理建议 |
|---|---|---|
| 400 | 参数错误 / 中文校验提示 | 按提示修正参数；素材数量超限不改会一直失败 |
| 401 | `Invalid token` / 令牌已过期 / 额度不足 | 检查 `Authorization` 头；`/content` 下载别忘了带请求头 |
| 403 | 模型未授权 | 令牌分组与模型不匹配，换分组或换令牌 |
| 404 | 模型不存在 | 模型名拼错（含价格后缀），或该模型已下线 |
| 410 | `artifact_gone` | 成片已过期，需重新生成，及时下载 |
| 429 | 上游限流 / 并发超限 | 退避重试（建议 5s 起指数退避） |
| 500 / 502 | 上游异常 | 可重试；任务类失败会自动全额退款 |
| 200（任务 `failed`） | `fail_reason` 如「内容未通过审核」 | 调整提示词/素材后重试，本次**不扣费**（已全额退回） |

---

## 12. 计费与退款

- **文本**：按输入 + 输出 token 计费，单价见 §5.2，日志页可见每次的 token 与费用。
- **图片 / 视频**：按**次**计费，创建任务即预扣，**失败自动全额退回**（含上游审核不通过）。
- **对账**：后台「日志」页按时间/模型/令牌筛选；任务类可在「任务」页看到进度与失败原因。
- **并发**：默认按分组限流，需要更高并发请单独沟通。

---

## 13. 接入检查清单

- [ ] Base URL 填 `https://api.qlike.top`，Key 用 `sk-` 令牌
- [ ] 令牌分组与业务匹配（文本 / 图片 / 视频）
- [ ] 模型名**逐字符原样**传（含价格后缀）
- [ ] `seconds` 传字符串；总秒数不带小数点
- [ ] 视频：创建 → 轮询（5–10s 间隔）→ `status=completed` 后**立刻**下载
- [ ] 下载成片走 `/v1/videos/{id}/content` 并**带 `Authorization` 头**
- [ ] 参考素材用公网 HTTPS 直链（本地文件 / Base64 先过 `POST /v1/files` 转直链，见 §8）
- [ ] 首尾帧与参考图不混传
- [ ] 处理 410（成片过期）与 503（审核不过）两个常见异常
- [ ] 上线前用 1 次最低价模型做端到端小样验证

---

## 14. 变更记录

| 日期 | 版本 | 内容 |
|---|---|---|
| 2026-10-08 | v1.0 | 首版：文本 / 图片 / 视频三线接口、35 个在线模型、能力矩阵与素材规则 |
| 2026-10-08 | v1.1 | 新增 §8 素材上传中转（`POST /v1/files`，图片/视频/音频 → 公网直链）；新增在线文档与 Markdown 下载 |
