"""渠道插件：Gemini 原生协议（generateContent）。

典型上游：change2pro 香蕉 —— 客户端打标准 OpenAI 图片接口，
本插件把它翻译成 POST /v1beta/models/{model}:generateContent，
参考图走 contents[].parts[].inlineData（裸 base64，禁 data URI 前缀）。
"""
from __future__ import annotations

from .. import protocols
from .base import Channel


class GeminiNative(Channel):
    id = "gemini_native"
    label = "Gemini 原生（generateContent）"
    hint = "标准图片请求 ⇄ /v1beta/models/{model}:generateContent；参考图走 inlineData"
    default_auth = "x-goog-api-key"
    default_base_url = "https://generativelanguage.googleapis.com"
    operations = {"generate": "converted", "edit": "converted"}
    models = {
        "gemini-3.1-flash-image": "gemini-3.1-flash-image",
        "gemini-3-pro-image": "gemini-3-pro-image",
    }

    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        return protocols.build_gemini_native(p, body, edit)

    def parse(self, payload) -> list[dict]:
        return protocols.parse_gemini_native(payload)


CHANNEL = GeminiNative()
