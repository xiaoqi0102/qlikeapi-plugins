"""渠道插件：Google 官方 Gemini 图片协议（generateContent）。

口径严格按 Google 官方 Gemini API「Image generation」文档写：
  · 端点 POST /v1beta/models/{model}:generateContent
  · 鉴权 x-goog-api-key: <key>
  · 请求 contents[].parts[].text（提示词）+ parts[].inlineData（参考图，裸 base64，禁 data: 前缀）
  · 尺寸 generationConfig.imageConfig.aspectRatio（比例）+ imageSize（档位 1K/2K/4K，Lite 只到 1K）
  · 响应 candidates[].content.parts[].inlineData.data（base64）

关键差异：Gemini 不接受任意像素尺寸，只认「档位 + 固定比例」；所以客户端给 1920x1080 时，
本插件只能换算成「最接近的档位 + 比例」，最终像素由上游档位表决定（见 /api/size-plan）。
"""
from __future__ import annotations

from .. import protocols
from .base import Channel


class GeminiNative(Channel):
    id = "gemini_native"
    label = "Gemini 官方图片协议"
    vendor = "Google（Gemini API）"
    docs = "https://ai.google.dev/gemini-api/docs/image-generation"
    hint = "严格按 Google 官方口径：POST /v1beta/models/{model}:generateContent，x-goog-api-key 鉴权，参考图走 inlineData"
    protocol_note = "官方约束：尺寸只能给 aspectRatio + imageSize（512px/1K/2K/4K 档位）；Pro 不支持 1:4 / 4:1 / 1:8 / 8:1。"
    default_auth = "x-goog-api-key"
    default_base_url = "https://generativelanguage.googleapis.com"
    operations = {"generate": "converted", "edit": "converted"}
    # 官方只吃 parts[].inlineData（裸 base64），不吃 URL → 不转换
    ref_input = "base64"
    models = {
        "gemini-3.1-flash-image": "gemini-3.1-flash-image",
        "gemini-3-pro-image": "gemini-3-pro-image",
    }

    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        return protocols.build_gemini_native(p, body, edit)

    def parse(self, payload) -> list[dict]:
        return protocols.parse_gemini_native(payload)


CHANNEL = GeminiNative()
