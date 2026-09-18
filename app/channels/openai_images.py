"""渠道插件：OpenAI 官方图片协议（/v1/images/*）。

口径严格按 OpenAI 官方 Images API 文档写，不掺任何中转站的私货：
  · 文生图 POST /v1/images/generations
  · 改图   POST /v1/images/edits（multipart/form-data，image[] + prompt）
  · 鉴权   Authorization: Bearer <key>
  · 参数   model / prompt / n / size / quality / background / output_format / response_format …
  · 响应   {created, data:[{url | b64_json, revised_prompt}]}

本插件只做两件事：① 按官方约束纠偏（尺寸吸附、quality 归一、剔掉上游不认的字段）；
② 原样转发、原样返回。协议语义一个字都不改。

⚠ 七牛的「同步面」长得像这个协议，但它有自家特性（固定返回 b64_json、不认 response_format），
   那属于七牛的口径，写在 qiniu 插件里，不要混进这里。
"""
from __future__ import annotations

from .. import protocols
from .base import Channel


class OpenAIImages(Channel):
    id = "openai_images"
    label = "OpenAI 官方图片协议"
    vendor = "OpenAI"
    docs = "https://platform.openai.com/docs/api-reference/images"
    hint = "严格按 OpenAI 官方口径：/v1/images/generations（文生图）· /v1/images/edits（改图），Bearer 鉴权"
    protocol_note = "官方约束：size 需满足「边长 ≤3840、两边都是 16 的倍数、长宽比 ≤3:1、总像素 655,360~8,294,400」；本插件按最小改动吸附，绝不放大。"
    default_auth = "bearer"
    default_base_url = ""
    operations = {"generate": "native", "edit": "native"}
    models = {
        "gpt-image-2": "gpt-image-2",
        "gpt-image-2.5-flare": "openai/gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst": "openai/gpt-image-2.5-sunburst",
    }

    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        return protocols.build_openai_images(p, body, edit)

    def parse(self, payload) -> list[dict]:
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]


CHANNEL = OpenAIImages()
