"""渠道插件模板 —— 复制这个文件改名即可新增一个渠道类型。

步骤：
  1. 复制成 my_relay.py（文件名随意，别以 _ 开头）
  2. 改 id / label / vendor / docs / hint / default_base_url / operations / models
  3. 实现 build()：把标准 OpenAI 图片请求翻译成上游要的格式，返回 (url, body, meta)
  4. 补两个测试：缺提示词必须报错 + 报文形状（见 tests/test_channels.py）
  5. Web 控制台点「重载插件」，然后新建一个该渠道的实例（填 base_url、key、模型映射）

约定与铁律：
  · operations 声明支持的操作：{"generate": "native"} 表示只支持生成，
    不支持的操作会被本地 400，绝不会打到上游才发现
  · 缺提示词必须抛 ChannelError（HTTP 400），**绝不回落默认提示词**（回落一次就是一次真出图、真扣费）
  · 空白提示词也算「没有提示词」：统一用 protocols.prompt_of(body) 判空（它已 strip）
  · 图片不落盘：b64 或上游 URL 原样交给客户端
  · build() 里不要自己发网络请求：统一走 protocols.call_upstream（它有超时与统一日志）
  · meta 里放有用的信息（face / refs / removed / up_model），会进请求日志，便于排障
  · 上游如果「偶尔回异步任务」（{task_id, status}）而不是直接给图，实现可选的 poll() 钩子即可，
    不要为此声明 operations 的 queue 模式（那会把同步请求也塞进队列流程）—— 参考 app/channels/aicost.py
"""
from __future__ import annotations

from .. import protocols
from .base import Channel, ChannelError


class MyRelay(Channel):
    id = "my_relay"                        # 唯一标识（= 渠道实例的 protocol 值）
    label = "我的中转（示例）"
    vendor = "谁家的协议就写谁的名字"
    docs = "https://上游官方文档"
    protocol_note = "该协议的关键约束 / 与形似协议的区别（面板会展示）"
    hint = "一句话说明这个渠道怎么工作"
    auth_modes = ("bearer", "x-goog-api-key")   # 这个渠道支持哪些鉴权方式
    default_auth = "bearer"                     # bearer | x-goog-api-key | fal_key
    default_base_url = "https://api.example.com"
    operations = {"generate": "native", "edit": "native"}
    ref_input = "base64"          # url / both / base64 —— 按该家官方文档声明参考图形态
    #            ↑ 操作名         ↑ native 原样透传 | converted 本服务翻译 | queue 异步提交+轮询
    models = {"example-image-1": "example-image-1"}   # 预置模型名 → 上游真实名

    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        """把标准 OpenAI 图片请求翻译成上游报文。

        返回 (url, upstream_body, meta)：
          url            发往哪个地址（不带 query 也行）
          upstream_body  上游要的 JSON 体
          meta           进日志的附加信息（别放密钥）
        """
        prompt = protocols.prompt_of(body)          # 空、纯空白都算没有
        if not prompt:
            raise ChannelError("prompt is required")

        up_model = protocols.upstream_model(p, body.get("model") or "")
        path = "edits" if edit else "generations"
        url = f"{p['base_url'].rstrip('/')}/v1/images/{path}"

        up_body = {
            "model": up_model,
            "prompt": prompt,
            "size": body.get("size") or "1024x1024",
        }
        # 参考图：collect_refs 会把各种客户端写法（image / images / image_urls /
        # reference_images …）收成一个列表，data URI 与裸 base64、URL 都能处理
        refs = protocols.collect_refs(body)
        if refs:
            up_body["image"] = refs

        return url, up_body, {"up_model": up_model, "refs": len(refs)}

    def parse(self, payload) -> list[dict]:
        """上游返回 → OpenAI 形状的 data 列表。

        大多数情况不用自己写：默认实现（protocols.extract_urls）已经能处理
        URL 形态、b64_json 形态、以及常见的嵌套结构。
        """
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]

    # ---- 可选：上游回的是异步任务时 ----
    # 实现了这个方法，网关就只会在「上游没直接给图」时多问你一次；
    # 不是异步任务时务必返回 ("SKIP", None)，把流程交回网关。
    #
    # def poll(self, first, meta, headers, timeout=None):
    #     tid = (first or {}).get("task_id")
    #     if not tid or (first or {}).get("status") not in ("pending", "queued", "processing"):
    #         return "SKIP", None
    #     url = f"{meta['poll_base']}/{tid}"
    #     h = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    #     while True:
    #         time.sleep(protocols.POLL_INTERVAL)
    #         payload = protocols.HTTP.get(url, headers=h).json()
    #         if self.parse(payload):
    #             return "OK", payload
    #         if str(payload.get("status", "")).lower() in ("failed", "error", "cancelled"):
    #             return "FAILED", payload


# 文件名以下划线开头 = 不注册。去掉下面这行、并把文件名改成不以 _ 开头即可启用。
# CHANNEL = MyRelay()
