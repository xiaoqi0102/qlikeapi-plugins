# 渠道插件开发指南

> 一个渠道插件 = **一个上游协议的实现**。
> 加一个新上游，只动 `app/channels/` 下的一个文件，核心代码一行都不用改。

## 1. 三分钟上手

```bash
cp app/channels/_template.py app/channels/my_relay.py
# 改 id / label / hint / default_base_url / operations / models，实现 build()
make test && make lint
# 控制台 → 渠道实例 → 右上「重载插件」→ 新建实例时就能选到 my_relay
```

规则：

- 文件名**以 `_` 开头 = 不注册**（模板文件本身就是这样）；
- 文件里必须有一个模块级 `CHANNEL = MyRelay()` 对象；
- 注册表自动发现（`channels/__init__.py`），单个插件写错不会影响其它插件，
  错误会收集到 `channels.ERRORS` 并在控制台「设置」页显示。

## 2. 插件契约

`app/channels/base.py`：

```python
class ChannelError(ValueError):
    """请求翻译阶段的用户侧错误 → HTTP 400"""

class Channel:
    # —— 元信息（子类必须覆盖）——
    id: str = ""                 # 插件唯一标识，等于 providers.protocol 的值
    label: str = ""              # 中文名（控制台显示）
    hint: str = ""               # 一句话说明（控制台提示）
    auth_modes: tuple = ("bearer", "x-goog-api-key", "fal_key")
    default_auth: str = "bearer" # 建实例时的默认鉴权方式
    default_base_url: str = ""   # 建实例时的默认上游地址
    operations: dict = {"generate": "native", "edit": "native"}
    models: dict = {}            # 预置：客户端模型名 → 上游真实模型名

    # —— 行为（子类实现）——
    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]: ...
    def parse(self, payload) -> list[dict]: ...
```

### 2.1 `operations`：声明支持的操作

| mode | 含义 | 谁会用到 |
|---|---|---|
| `native` | 上游就是这个协议，只做字段纠偏后透传 | `openai_images` |
| `converted` | 由本服务把标准请求翻译成上游协议 | `gemini_native`、`change2pro` |
| `queue` | 异步：提交 → 轮询 → 取结果 | `fal_queue` |

```python
operations = {"generate": "native"}          # 只支持生成，edit 会被本地 400
operations = {"generate": "queue", "edit": "queue"}
```

**不支持的操作在进上游之前就被挡掉**（400 `operation_not_supported`），不会白花一次钱。

### 2.2 `build(p, body, edit) -> (url, upstream_body, meta)`

| 入参 | 说明 |
|---|---|
| `p` | 渠道实例字典（`store.get_provider()` 解密后的那份）：`key / protocol / base_url / auth_mode / api_key / model_map / options`；`p["keys"]` 是多把 key 的列表 |
| `body` | 客户端请求体（已归一化：multipart 附件已转成 data URI；`model` 已 strip） |
| `edit` | `True` 表示来自 `/v1/images/edits`（带参考图的改写） |

| 返回 | 说明 |
|---|---|
| `url` | 上游地址（绝对 URL） |
| `upstream_body` | 上游要的 JSON 体 |
| `meta` | 进日志的附加信息（`up_model` / `face` / `refs` / `removed` …），**不许放密钥** |

**铁律（会被测试和 CI 守住）**：

1. **缺提示词 → 抛 `ChannelError`**。绝不允许自己填默认提示词。用
   `protocols.prompt_of(body)` 判空（它已经 `strip()`，所以 `"   "` 也算缺）。
2. **不要在 `build()` 里发网络请求**。出站统一走 `protocols.call_upstream()`
   （带超时、统一日志、测试可打桩）。
3. 图片**不落盘**：只处理 URL / base64 的搬运与翻译。

### 2.3 `parse(payload) -> list[dict]`

把上游响应翻译成 OpenAI 形状的 `data` 数组：`[{"url": "..."}]` 或 `[{"b64_json": "..."}]`。

默认行为（不重写就是它）：

```python
urls, error = protocols.extract_urls(payload)
return [{"url": u} for u in urls]
```

`extract_urls()` 能处理 URL 形态、`b64_json` 形态，以及常见的各种嵌套；
一般不用自己写 `parse()`。

## 3. 可复用的工具箱

### `protocols.py`

| 函数 | 用途 |
|---|---|
| `prompt_of(body)` | 取提示词并 strip（判空必须用它） |
| `match_model(p, m)` / `resolve_model(p, m)` / `upstream_model(p, m)` | 模型名归一：大小写不敏感、走实例 `model_map`、取上游真实名 |
| `model_list(p)` / `default_model(p)` | 该实例可用的模型集合 / 默认模型 |
| `unify_model(p, body, payload, client_model)` | 统一响应里的模型名 = 客户端请求的名字 |
| `auth_headers(auth_mode, secret)` | 生成鉴权头（`bearer` / `x-goog-api-key` / `fal_key`） |
| `call_upstream(url, headers, body, timeout=None)` | **唯一出站口**，返回 `(status, payload, text)` |
| `extract_urls(payload)` | 从任意响应里挖 `(urls, error)` |
| `set_path` / `remove_path` / `apply_removals` | 点号路径读写字段（支持 `*` 与数字下标，用于 `options.remove_params`） |
| `build_gemini_native` / `build_openai_images` / `build_fal_queue` | 三种协议的现成实现，插件里可直接复用 |
| `parse_gemini_native` / `parse_openai_images` / `poll_fal` | 对应的解析与轮询 |
| `fal_endpoints(p, model, edit)` | 取 fal 的提交/查询端点（可被 `options` 覆盖） |

### `utils.py`

| 函数 | 用途 |
|---|---|
| `parse_size(s)` / `nearest_ratio(w,h)` / `resolution_of(w,h)` | 尺寸解析与比例吸附（`1024x1024`、`1024×1024`、`16:9`…） |
| `gpt_safe_size(s)` | OpenAI 系上游的尺寸约束（16 整除、面积区间、比例 ≤3）安全吸附 |
| `normalize_quality(q)` | `standard→auto`、`hd→high` 之类归一 |
| `collect_refs(body)` | 收集参考图：兼容 `image / images / image_urls / reference_images / mask` 等写法 |
| `to_raw_b64(ref)` / `fetch_as_b64(ref, http)` | data URI / 裸 base64 / URL → `(mime, base64)`（URL 才会真的去取） |
| `mask(secret, keep=6)` | 密钥打码 |

## 4. 四个内置插件（参考实现）

| 插件 | 上游协议 | operations | 关键点 |
|---|---|---|---|
| `gemini_native` | `POST /v1beta/models/{model}:generateContent` | `converted` | 参考图放 `contents[].parts[].inlineData.data`，**裸 base64，禁 `data:` 前缀**；尺寸走 `generationConfig.imageConfig.{imageSize,aspectRatio}` |
| `openai_images` | `POST /v1/images/generations\|edits` | `native` | 只做字段纠偏后透传：尺寸吸附、`quality` 归一、按 `options.remove_params` 删上游不认的字段（如 `response_format`） |
| `fal_queue` | `POST /queue/{...}` → 轮询 `/requests/{id}/status` | `queue` | 参考图**必须是公网 URL**（异步面拿不到本地文件）；结果 `images[].url` 是带签名的临时链接，会过期，所以不转存 |
| `change2pro` | **合并插件**：同一站点两套协议 | `converted` | `face_of(model)` 按模型名分流：`gemini-*` 走 `generateContent`，其它走 `/images/generations`（注意**没有 `/v1`**）；一个实例、一把 key 覆盖两套协议 |

> `change2pro` 也是「合并插件」的示范：当上游站点把多套协议挂在同一个域名同一把 key 下时，
> 用一个插件内部按模型名分流，比在 New API 里配两个渠道更好维护。
> **注意**：合并插件正是探活事故的源头，写这类插件时必须确认 `blank_for_probe()` 的递归
> 抹除覆盖到你新增的每一个提示词字段（并在 `tests/test_probe.py` 里补一条用例）。

## 5. 完整示例：给一个「中转站」写插件

假设某站：`POST https://api.example.com/v1/draw`，鉴权 `Bearer`，
提示词字段叫 `text`，尺寸字段叫 `wh`（如 `"1024*1024"`），返回 `{"result": {"link": "..."}}`。

```python
"""channels/example_draw.py —— 某中转站的私有图片协议。"""
from __future__ import annotations

from .. import protocols, utils
from .base import Channel, ChannelError


class ExampleDraw(Channel):
    id = "example_draw"
    label = "Example 画图"
    hint = "私有协议：POST /v1/draw，提示词字段叫 text，返回 result.link"
    auth_modes = ("bearer",)
    default_auth = "bearer"
    default_base_url = "https://api.example.com"
    operations = {"generate": "converted"}          # 该站不支持图生图
    models = {
        "example-image-1": "example-image-1",
        "example-image-2": "example-image-2-pro",   # 客户端名 → 上游真名
    }

    def build(self, p, body, edit):
        if edit:
            raise ChannelError("operation_not_supported")   # 双保险（operations 已挡一次）

        prompt = protocols.prompt_of(body)                  # ← 必须用它判空
        if not prompt:
            raise ChannelError("prompt is required")

        w, h = utils.parse_size(body.get("size")) or (1024, 1024)
        if edit:  # 不会走到，这里演示参考图收集
            pass

        up_body = {
            "text": prompt,                                  # 字段名不一样 → 翻译
            "wh": f"{w}*{h}",
            "n": int(body.get("n") or 1),
        }
        opts = p.get("options") or {}
        for k, v in (opts.get("extra") or {}).items():       # 允许实例级附加字段
            up_body[k] = v

        url = f"{p['base_url'].rstrip('/')}/v1/draw"
        meta = {"up_model": protocols.upstream_model(p, body.get("model") or ""), "face": "draw"}
        return url, up_body, meta

    def parse(self, payload):                                # 形状特殊 → 自己解析
        link = ((payload or {}).get("result") or {}).get("link")
        return [{"url": link}] if link else []


CHANNEL = ExampleDraw()
```

配套测试（`tests/test_channels.py` 里照抄一段改改即可）：

```python
def test_example_draw_refuses_missing_prompt():
    p = {"key": "x", "protocol": "example_draw", "base_url": "https://api.example.com",
         "auth_mode": "bearer", "model_map": {}, "options": {}}
    for bad in ({"model": "example-image-1"}, {"model": "example-image-1", "prompt": "   "}):
        with pytest.raises(ValueError):
            channels.get("example_draw").build(p, bad, False)


def test_example_draw_translates_fields():
    p = {...}
    url, up, meta = channels.get("example_draw").build(
        p, {"model": "example-image-2", "prompt": "一只猫", "size": "1024x1024"}, False)
    assert url.endswith("/v1/draw")
    assert up["text"] == "一只猫" and up["wh"] == "1024*1024"
    assert meta["up_model"] == "example-image-2-pro"
```

## 6. 常见坑

| 坑 | 症状 | 处理 |
|---|---|---|
| 忘记判空提示词 | 探活/空请求真的出图（**花钱**） | 一律 `protocols.prompt_of(body)` + 抛 `ChannelError` |
| Gemini 参考图带 `data:` 前缀 | 上游 400 `invalid inline data` | 用 `utils.to_raw_b64()` 去前缀 |
| fal 异步面传本地文件/base64 | 上游报错或挂住 | 只能传公网 URL；本地面base64 场景请走同步面插件 |
| 忘了改 `default_base_url` | 建实例后打错域名 | 抄模板时逐个字段过一遍 |
| `parse()` 返回空 | 客户端拿到空图片数组 | 用 `/up/<key>/v1/images/preview` 看上游原始响应，再对 `extract_urls` 补规则 |
| 在 `build()` 里直接 `httpx.post` | 没有超时/没有统一日志/测试打桩失效 | 只允许 `protocols.call_upstream()` |
| 新增提示词字段但没进 `_PROBE_BLANK` 类似逻辑 | 探活又开始真出图 | 确认 `relay.blank_for_probe()` 的递归覆盖，并补 `tests/test_probe.py` 用例 |
| 插件写错导致整个渠道不可用 | 控制台看不到该插件 | 看「设置」页的插件错误列表（`channels.ERRORS`），修好后点「重载插件」 |
