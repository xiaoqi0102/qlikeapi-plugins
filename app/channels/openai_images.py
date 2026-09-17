"""渠道插件：标准 OpenAI 图片协议（同步透传 + 字段纠偏）。

典型上游：七牛 ModelInk 同步面、change2pro image2。
做的事：白名单/纠偏（尺寸吸附、quality 归一、去掉上游不认的字段），然后原样转发；
响应原样返回（客户端拿到 b64_json 或 url 都行）。
"""
from __future__ import annotations

from .. import protocols
from .base import Channel


class OpenAIImages(Channel):
    id = "openai_images"
    label = "OpenAI 图片协议（同步）"
    hint = "上游本身就是 /v1/images/generations | /v1/images/edits，只做字段纠偏后透传"
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
