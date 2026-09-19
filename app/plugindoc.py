"""app/plugindoc.py —— 面板「给 AI 的插件编写说明」全文（唯一真源）。

同一份文本同时是：
  · 面板「渠道插件 → 给 AI 的说明」里可一键复制的全文
  · 仓库里的 docs/PLUGIN-AUTHORING.md（由 `python -m app.plugindoc --write` 生成）

改这里就够，测试 test_plugindoc.py 会断言两份一致，防止漂移。
"""
from __future__ import annotations

import pathlib

AUTHORING_DOC = r'''# 渠道插件编写说明（把这份说明 + 中转站接口文档一起发给 AI）

你现在要为 **qlikeapi-plugins**（一个图片协议转换网关）写一个**渠道插件**。
网关对外只讲一种语言：**OpenAI 图片接口**（`POST /v1/images/generations` 生成、
`POST /v1/images/edits` 编辑）。插件的工作就是把这种标准请求**翻译**成某个上游中转站
（或厂商）自己的报文格式，再把上游的返回**翻译回** OpenAI 形状。

## 一、交付物

**一个 `.py` 文件**，文件名小写字母开头、只含小写字母/数字/下划线（例如 `my_relay.py`）。
文件里必须有：

1. 一个继承 `Channel` 的类；
2. 模块级一行 `CHANNEL = 你的类()`（**这行不能漏，也不能是注释**）。

插件会被放在网关的插件目录里，模块名固定为 `app.channels.<文件名>`，
所以文件里可以用相对导入：`from .. import protocols`、`from .base import Channel`。

## 二、骨架（照抄这个结构）

```python
"""我的中转站渠道插件。"""
from __future__ import annotations

from .. import protocols
from .base import Channel, ChannelError


class MyRelay(Channel):
    # ---- 元信息（全部必填，面板和日志都靠它）----
    id = "my_relay"                        # 唯一标识：小写字母开头，小写字母/数字/下划线
    label = "我的中转站（示例）"             # 面板上显示的中文名
    vendor = "中转站官方协议名"              # 协议归属方：谁家的协议就写谁的名字
    docs = "https://example.com/docs"      # 官方文档地址（http/https）
    protocol_note = "与 OpenAI 官方 Images API 的差异：..."   # 关键约束，面板会展示
    hint = "一句话说明这个渠道怎么工作"       # 面板上的一句话
    auth_modes = ("bearer",)               # 支持哪些鉴权：bearer / x-goog-api-key / fal_key / none
    default_auth = "bearer"                # 默认用哪种（必须在上面的元组里）
    default_base_url = "https://api.example.com"   # 上游根地址（不带 /v1）
    operations = {"generate": "native", "edit": "native"}  # 支持哪些操作 + 翻译方式
    ref_input = "both"                     # 参考图形态：url / both / base64（见第五节）
    models = {                             # 预置模型名（客户端名 → 上游真实名，可被实例覆盖）
        "my-image-1": "my-image-1",
    }

    def build(self, p, body, edit):
        """标准 OpenAI 图片请求 → 上游报文。返回 (url, upstream_body, meta)。"""
        prompt = protocols.prompt_of(body)          # 空串 / 纯空白都算「没有提示词」
        if not prompt:
            raise ChannelError("prompt is required")

        up_model = protocols.upstream_model(p, body.get("model") or "")
        path = "edits" if edit else "generations"
        url = f"{p['base_url'].rstrip('/')}/v1/images/{path}"

        up_body = {"model": up_model, "prompt": prompt,
                   "size": body.get("size") or "1024x1024"}
        refs = protocols.collect_refs(body)         # 各种客户端写法 → 一个列表
        if refs:
            up_body["image"] = refs
        return url, up_body, {"up_model": up_model, "refs": len(refs)}

    def parse(self, payload):
        """上游返回 → OpenAI 形状的 data 列表（多数情况不用自己写，见第七节）。"""
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]


CHANNEL = MyRelay()
```

## 三、元信息字段

| 字段 | 必填 | 取值 | 说明 |
|---|---|---|---|
| `id` | ✅ | 小写字母开头，`[a-z0-9_]`，2~41 字符 | 插件唯一标识，也是渠道实例的 `protocol` 值 |
| `label` | ✅ | 任意中文 | 面板显示名 |
| `vendor` | ✅ | 任意 | **协议归属方**：谁家的协议写谁的名字（别写成"XX 中转"） |
| `docs` | ✅ | http(s) 地址 | 官方文档。写口径时以它为准，不许猜 |
| `protocol_note` | ✅ | 任意 | 该协议的关键约束 / 与形似协议的区别 |
| `hint` | ✅ | 任意 | 一句话说明 |
| `auth_modes` | ✅ | `bearer` / `x-goog-api-key` / `fal_key` / `none` 的元组 | 该渠道支持哪些鉴权 |
| `default_auth` | ✅ | 同上，必须在 `auth_modes` 里 | 默认鉴权 |
| `default_base_url` | ✅ | http(s) 地址 | 新建渠道实例时的默认上游地址 |
| `operations` | ✅ | `{"generate": 模式, "edit": 模式}` | 支持的操作；不支持的**不要声明**，网关会本地 400 |
| `models` | 建议 | `{客户端名: 上游真名}` | 预置模型；上游真实名就是键本身时写 `{名: 名}` |
| `ref_input` | ✅ | `url` / `both` / `base64` | 参考图形态，**按该家官方文档声明，别猜** |
| `ref_input_faces` | 可选 | `{"面": 形态}` | 一个插件里分多个"面"时（按模型分流）分别声明 |
| `site_type` | 可选 | `newapi` / `sub2api` / `manual` … | 该协议对应的「站点余额」类型。加渠道时用它**自动建站点条目**（令牌/id 由用户手填）；不写按 `manual`（手工记账）记 |

操作模式（`operations` 的值）：

- `native` —— 上游协议就是 OpenAI 图片接口，报文原样透传
- `converted` —— 本服务负责翻译成上游协议（大多数情况）
- `queue` —— 异步队列：提交任务 + 轮询取结果（七牛 fal 风格）
- 上游只是**偶尔**回异步任务时不要用 `queue`（那会把同步请求也塞进队列流程），改用 4.1 的可选钩子 `poll()`

## 四、`build()` 契约

```python
def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]
```

- `p` —— 渠道实例（字典）：`p["base_url"]`、`p["api_key"]`（多把 key 用换行分隔的字符串）、
  `p["auth_mode"]`、`p.get("options")`（面板里可编辑的微调项）、`p["model_map"]`
- `body` —— 客户端发来的标准 OpenAI 请求体：`prompt`、`model`、`size`、`quality`、`n`、
  参考图字段（`image` / `images` / `image_urls` / `reference_images` / `mask` …）
- `edit` —— `True` 表示这是 `/v1/images/edits`（改图），`False` 是 `/v1/images/generations`（生图）
- 返回 `(url, upstream_body, meta)`：
  - `url` 发往哪里（不含 query 也行）
  - `upstream_body` 上游要的 JSON
  - `meta` 进请求日志的附加信息（**别放密钥**），常用键：`up_model`、`refs`、`face`、`removed`

**要报错就抛 `ChannelError("原因")`** —— 它会变成给客户端的 HTTP 400，**不会**打到上游、不会扣费。

### 4.1 可选：上游回的是异步任务（`poll()`）

少数站点会**先回任务号、稍后再出图**（如 `{"task_id": "...", "status": "pending"}`）。
这种不用改 `operations`，只给插件加一个可选钩子：

```python
def poll(self, first, meta, headers, timeout=None) -> tuple[str, object]:
    """first = 上游第一次返回的报文；meta 就是 build() 返回的那个 meta（轮询地址可以塞在里面）。"""
```

- `("OK", 最终报文)` —— 网关用 `parse()` 解析它，客户端照样拿到同步结果
- `("SKIP", None)` —— 这不是异步任务，交回网关常规流程（**不是任务时必须 SKIP**，否则会把正常结果吞掉）
- 其它状态 —— 拿不到图，网关按上游错误处理（日志里能看到原始返回）

没实现 `poll()` 的插件行为一点不变：网关只在「上游没直接给图」时才多问一次。
轮询用 `protocols.HTTP.get(url, headers=h)`，间隔与上限用 `protocols.POLL_INTERVAL` / `protocols.POLL_MAX`，
参考实现见 `app/channels/aicost.py`。

## 五、参考图口径 `ref_input`（重要）

客户端可能用 base64 内联参考图，也可能给公网 URL。网关会**先问插件要哪种形态**再决定是否中转：

| 值 | 什么时候用 | 网关行为 |
|---|---|---|
| `base64` | 上游文档说参考图必须是 base64（如 Gemini 的 `inlineData`、aicost 的 gpt-image-2 编辑面） | 客户端给 base64 → 原样透传；给**公网 URL → 下载内联**成 data URI（内存里，不落盘） |
| `both` | 上游文档里公网 URL 和 base64 都写了 | 优先转成公网直链（图床挂了自动回落 base64，不失败） |
| `url` | 上游文档说**只吃公网 URL**（如 fal 异步队列，它自己去拉图） | 必须转成公网直链；图床全挂 → 本地 400，**不硬发** |

客户端本来就给 http(s) 链接时：`url` / `both` 档位**原样透传**（不绕冤枉路）；
`base64` 档位会**下载内联**（这类上游压根不吃 URL，实测 aicost 传 URL 会 400「输入的图片有误」）。
下载失败（404 / 超时 / 不是图片 / 超过 `max_mb`）→ **本地 400** 并带上明细，别把注定失败的 URL 丢给上游。

**别把 `url` 写成 `both`**：如果上游其实不吃 URL，客户端会拿到一张跟参考图无关的图。
拿不准就写 `both`（有回落），并在 `protocol_note` 里写清依据。

## 六、上游要的字段不一样时

- **改字段名/位置**：在 `build()` 里自己拼 `up_body`
- **删字段**：渠道实例的 `options.drop_fields` 支持点号路径（如 `generationConfig.responseModalities`），
  路由层会统一删；插件里也可以 `protocols.remove_path(up_body, "quality")`
- **尺寸换算**：`protocols.snap_size(size, model, mode)` / `protocols.gpt_safe_size(size, model)`
  （宽高吸附到上游允许的档位）；`protocols.size_mode(p)` 读实例的「尺寸处理方式」
- **Gemini 档位**：`protocols.gemini_policy(p)`

## 七、现成实现（优先复用，别重复造）

| 你要做的 | 直接调 |
|---|---|
| 上游就是 OpenAI 图片接口 | `return protocols.build_openai_images(p, body, edit)` |
| 上游是 Gemini 原生 `generateContent` | `return protocols.build_gemini_native(p, body, edit)` + `parse_gemini_native(payload)` |
| 上游是 fal 风格异步队列 | `return protocols.build_fal_queue(p, body, edit)`（配套 `poll_fal` / `fal_endpoints`） |
| 从任意形状响应挖图片 URL | `protocols.extract_urls(payload) -> (urls, 错误信息)` |
| 上游偶尔回异步任务 | 实现可选钩子 `poll()`（见 4.1，参考 `app/channels/aicost.py`） |
| 打上游（带超时与统一日志） | `protocols.call_upstream(url, headers, body)` → `(状态码, json, 文本)` |
| 拼鉴权头 | `protocols.auth_headers(auth_mode, secret)` |
| 取上游模型名（走实例映射） | `protocols.upstream_model(p, body["model"])` |
| 拉上游模型列表（零成本，只 GET） | `protocols.fetch_upstream_models(p)` |

## 八、铁律（违反会被判 bug）

1. **缺提示词必须抛 `ChannelError`，绝不回落默认提示词** —— 回落一次就是一次真出图、真扣费。
   判空统一用 `protocols.prompt_of(body)`（已 strip，空串/纯空白都算没有）。
2. **不支持的操作不要声明**在 `operations` 里 —— 网关会本地 400，绝不打到上游才发现。
3. **插件里不要自己发网络请求、不要读写文件、不要落盘图片** —— 统一走 `protocols.call_upstream`。
4. **`meta` 里不要放密钥**；日志会脱敏请求头，但插件自己塞进去的东西不管。
5. **`build()` 只做翻译，不做业务判断**（重试、降级、熔断、密钥轮换都是网关的事）。
6. 上传插件 = 往网关里装可执行代码。**只装你能看懂的插件。**

## 九、会被自动校验拦下的写法（面板「校验」会逐条报出来）

- 文件名不合规、源码 > 256 KB、语法错误
- 没有继承 `Channel` 的类，或没有模块级 `CHANNEL = 类()`
- **禁止导入**（报错）：`os`、`sys`、`subprocess`、`socket`、`ctypes`、`importlib`、
  `multiprocessing`、`pickle`、`marshal`、`shutil`、`pty`、`tty`、`pathlib`、`glob`、`tempfile`、
  `sqlite3`、`asyncio`、`threading`、`concurrent`、`requests`、`httpx`、`aiohttp`、`urllib`
- **禁止调用**（报错）：`eval`、`exec`、`compile`、`__import__`、`open`、`input`、`breakpoint`、
  `globals`、`locals`、`vars`
- **禁止访问**（报错，反射逃逸）：`__globals__`、`__builtins__`、`__subclasses__`、`__code__`、
  `__bases__`、`__mro__`、`__reduce__`、`__getattribute__`
- **警告**（能装，但通常没必要）：`time`、`random`、`logging`；`models` 为空；没实现 `parse()`
- 元信息缺字段、`id` 与已有插件冲突、`build()` 没实现、`ref_input` 取值非法

校验还会在**子进程里真装一次**（超时 25s，且会剥掉本站密钥环境变量）——
所以 import 阶段就报错、或 import 时卡住的插件装不上。

## 十、装上去之后

1. 面板「渠道插件 → + 添加插件」→ 贴代码 → 点「校验」→ 通过后勾选安全确认 → 「保存并安装」
2. 安装即热重载，**不用重启容器**
3. 「渠道实例 → + 新建实例」→ 插件选你刚装的 → 填上游地址、密钥、模型映射
4. 点该实例的「探活」（按模型逐条，零成本，不会真出图）确认链路通
5. 客户端把 `model` 填成你在实例里配的模型名即可

## 十一、排查

- 请求日志里能看到插件写进 `meta` 的字段，以及真实发往上游的地址、报文、状态码
- 插件装载失败会在面板插件表上标红，并显示 Python 报错原文
- 「请求日志 → 详情」里有可复制的完整 curl，方便对照上游文档

---

## 附：发给我（AI）的输入模板

```
我要给 qlikeapi-plugins 写一个渠道插件，请按下面的说明产出**一个 .py 文件**（不要拆多个文件）：

<把上面的《渠道插件编写说明》整段粘进来>

以下是该上游的接口文档：
<把中转站/厂商的接口文档整段粘进来：接口地址、鉴权方式、请求字段、返回示例、报错示例>

补充信息（有就写）：
- 该上游是 OpenAI 兼容还是自有协议：
- 参考图是收 base64 还是公网 URL，还是都收（以文档原文为准）：
- 支持的模型名：
- 只支持生成 / 还是也支持改图：
```

拿到 AI 的代码后，直接贴进面板的「添加插件」，点「校验」——
有不合规的地方会连行号一起报出来，把报错回给 AI 让它改，通常一两轮就能过。
'''


def write_doc(root: str | pathlib.Path = ".") -> pathlib.Path:
    """把说明写到仓库的 docs/PLUGIN-AUTHORING.md（保持与代码内文本一致）。"""
    p = pathlib.Path(root) / "docs" / "PLUGIN-AUTHORING.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(AUTHORING_DOC, encoding="utf-8")
    return p


if __name__ == "__main__":  # pragma: no cover
    import sys
    print(write_doc(sys.argv[1] if len(sys.argv) > 1 else "."))
