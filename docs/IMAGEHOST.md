# 参考图形态适配层（图床中转 / 下载内联）

**一句话**：上游对参考图的形态要求不一致 —— 有的只认**公网 URL**（典型：七牛的 fal 异步队列，它自己去拉图），
有的只认 **base64**（典型：Gemini `inlineData`、**aicost 的 gpt-image-2 编辑面**）。这一层在发给上游之前
把参考图换成该渠道要的形态：base64 → 图床直链（上传），或公网 URL → data URI（下载内联）。

```
客户端 → New API → qlikeapi-plugins
                    └─ prepare()：先问渠道「你要哪种参考图形态」→ 必要时上传/下载 → 再交给插件翻译
```

## 1. 转换范围：按渠道能力协商，不搞一刀切

每个渠道插件用 `ref_input` 声明自己**按官方文档**支持的参考图形态（`app/channels/*.py`）：

| 声明 | 含义 | 行为 | 现有渠道 |
|---|---|---|---|
| `url` | 只认公网 URL | base64 → **上传图床**换直链；图床不可用 → 本地 400，绝不硬发 | `qiniu_fal`、`qiniu` 的异步面 |
| `both` | URL / base64 都支持 | **优先 URL**；图床挂了自动回落 base64（不因此失败） | `openai_images`、`change2pro` 的 image2 面 |
| `base64` | 只认 base64 | 客户端给 **URL → 下载内联**成 data URI（内存里，不落盘）；给 base64 → 原样透传 | `gemini_native`、`aicost`（**两面**）、`change2pro` 的 gemini 面、`qiniu` 的同步面 |

补充规则：

- 客户端**本来就给 http(s) 链接** → 对 `url`/`both` 渠道原样透传（不绕冤枉路）；
  对 `base64` 渠道则**下载内联**（这类渠道压根不吃 URL，实测 aicost 传 URL 会 400
  「输入的图片有误，请确认图片格式/链接是否正确」；裸 base64 / data URI → 200 正常出图）。
- 下载失败（404 / 超时 / 不是图片 / 超过 `max_mb`）→ **本地 400** 并带上明细，
  别把注定失败的 URL 丢给上游。
- 合并插件（一个渠道按模型分流到不同「面」）在 `declared_ref_input()` 里按面细化，
  例如 `qiniu`：异步面 `url` / 同步面 `base64`。
- **实例级应急阀门**：渠道实例的 `options.ref_prefer` 填 `inline` 强制内联 base64（不上传）、
  填 `url` 强制转换 —— 上游口径变了不用改代码，面板里就能切。
- 插件文档里没写支持公网 URL 的，**不猜**：保持 base64 现状。

## 2. 图床候选链

按顺序依次尝试，**第一个成功即用**；同一次请求里相同的图只上传一次（sha256 去重）。
默认链 `imgbb → litterbox(72h) → uggu`。

| 图床 | 端点 | 关键字段 | 有效期 | 备注 |
|---|---|---|---|---|
| ImgBB | `api.imgbb.com/1/upload?key=…` | `image`（base64） | 长期 | 需要 API Key，Key 加密存库、界面只显掩码 |
| Litterbox | `litterbox.catbox.moe/resources/internals/api.php` | **`reqtype=fileupload`（必带）**、`time=1h/12h/24h/72h`、`fileToUpload` | 1~72h 可选 | 免费临时图床，最常用 |
| Uguu | `uguu.se/upload.php` | **`files[]`（带方括号）** | ≈3 小时 | 返回域名在 `d./h./n.` 之间**轮换**，不能硬编码 |
| Catbox | `catbox.moe/user/api.php` | `reqtype=fileupload`、`fileToUpload` | 永久 | **默认关闭**：文档实测本机网络被重置 |
| 0x0.st | `0x0.st` | `file` | 数十天 | **默认关闭**：文档实测服务端已关闭上传 |

Catbox / 0x0.st 仍保留在面板里可手动开启（网络环境会变），但**不放进默认链**，
免得每次请求白等一次超时。

## 3. 配置

面板「设置 → 图床」，落库在 `settings` 表 `scope=imagehost`：

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 总开关；关闭时 `url` 型渠道遇 base64 直接本地 400 并说明原因 |
| `chain` | `["imgbb","litterbox","uguu"]` | 候选顺序（面板可上下移、可勾选停用；**手动清空就保持空**，不会偷偷塞回默认链） |
| `imgbb_key` | `""` | 加密落库（`crypto.py`）；接口只回掩码 |
| `litterbox_time` | `72h` | 1h / 12h / 24h / 72h |
| `max_mb` | `20` | 单张参考图上限，超了本地 400（不白等上传） |
| `timeout_s` | `30` | 单次上传超时；上游文档用 120s，网关不能被一次上传拖死 |
| `verify` | `true` | 上传后回读一次，确认直链真能取到图 |

接口：`GET/POST /api/settings/imagehost`、`POST /api/settings/imagehost/test`
（后者=面板「上传 1×1 自检图」按钮：真上传一张 1×1 像素图验证链路，**不调用任何生图接口、不产生费用**）。

## 4. 可观测

- 响应头 `X-QLike-Imagehost: litterbox`（走了哪家图床）+ `X-QLike-Imagehost-N`（张数）。
- 请求日志详情里，`up_body` 里就是替换后的公网直链 —— 可直接复制 curl 复核。
- 日志只记图床名/体积/张数，**不记上传直链**，更不记 Key。

## 5. 边界与红线

- **本服务不落盘、不转存**：图只在第三方图床存在（临时链接，会过期），网关内存里过一手就丢。
- 参考图会出现在**第三方公共服务**上 —— 面板里明确写了「别传私密素材」。
- 图床全挂 + 渠道只认 URL → **本地 400 并列出每家失败原因**：不发上游、不扣费、不静默降级。
- 客户端给了参考图但一个都用不上（例如只认 URL 的渠道 + 转换被关）→ 本地 400，
  **绝不静默丢图**（静默丢图 = 用户拿到一张跟参考图毫无关系的图，比报错难查一百倍）。
- 图床 Key 与所有凭据同规格：加密落库、不进日志、不进仓库、截图里只出现 `••••••••`。

## 6. 加一家图床

`app/imagehost.py` 的 `HOSTS` 里加一条（端点、字段名、有效期、是否要 Key），
在 `upload()` 里加一个分支（约 5 行），补 `tests/test_imagehost.py` 一个用例。
解析器已兼容**纯文本 URL** 与**JSON 递归挖 URL**两种返回形态，一般不用动。
