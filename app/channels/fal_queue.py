"""渠道插件：fal 异步队列协议。

典型上游：七牛 fal 面（gemini 图、gpt-image-2.5 变体等）。
流程：POST /queue/... 提交 → 轮询 /requests/{id}/status → GET /requests/{id} 取 images[].url
对外仍然回标准 OpenAI 形状（data[].url），客户端无需改；Kodo 签名 URL 有 7 天有效期，不转存。
"""
from __future__ import annotations

from .. import protocols
from .base import Channel


class FalQueue(Channel):
    id = "fal_queue"
    label = "fal 异步队列"
    hint = "提交 /queue/... → 轮询 → 取结果 URL；参考图必须是公网 URL"
    default_auth = "fal_key"
    default_base_url = "https://api.qnaigc.com"
    operations = {"generate": "queue", "edit": "queue"}
    models = {
        "gemini-3.1-flash-image-preview": "fal-ai/gemini-3.1-flash-image-preview",
        "gemini-3-pro-image-preview": "fal-ai/gemini-3-pro-image-preview",
        "gpt-image-2": "openai/gpt-image-2",
    }

    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        return protocols.build_fal_queue(p, body, edit)

    def parse(self, payload) -> list[dict]:
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]


CHANNEL = FalQueue()
