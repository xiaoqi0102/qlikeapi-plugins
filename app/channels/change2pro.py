"""渠道插件：Change2Pro 合并渠道（香蕉 Gemini 面 + image2 OpenAI 面）。

同一个站点、同一把 key，按「模型名」自动分流到两套上游协议：
  · gemini-* 系        → POST /v1beta/models/{model}:generateContent（参考图走 inlineData）
  · 其它（gpt-image-2）→ POST /images/generations | /images/edits（标准 OpenAI 图片面，注意无 /v1）

这样 New API 里只需要挂一个 Change2Pro 渠道，模型名照旧区分即可。
鉴权：两套协议都吃 Bearer（实测 x-goog-api-key 与 Bearer 均可用），所以渠道实例统一用 bearer。
"""
from __future__ import annotations

from .. import protocols
from .base import Channel


class Change2Pro(Channel):
    id = "change2pro"
    label = "Change2Pro（香蕉 + image2 合一）"
    vendor = "Change2Pro（api.change2pro.com）"
    docs = "https://api.change2pro.com"
    hint = "同一站点按模型自动分流：gemini 系走 generateContent，gpt-image 系走 /images/generations"
    protocol_note = "站点自有口径：两套协议都吃 Bearer；gemini 面参考图走 inlineData，image2 面注意路径无 /v1。"
    default_auth = "bearer"
    default_base_url = "https://api.change2pro.com"
    operations = {"generate": "converted", "edit": "converted"}
    models = {
        "gemini-3.1-flash-image": "gemini-3.1-flash-image",
        "gemini-3-pro-image": "gemini-3-pro-image",
        "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image",
        "gemini-3-pro-image-preview": "gemini-3-pro-image",
        "gpt-image-2": "gpt-image-2",
    }

    def declared_ref_input(self, p: dict, body: dict, edit: bool) -> str:
        """gemini 面走 inlineData（只吃 base64）；image2 面是 OpenAI 形状（两者都行）。"""
        up_model = protocols.upstream_model(p, body.get("model") or "")
        return "base64" if self.face_of(up_model) == "gemini_native" else "both"

    @staticmethod
    def face_of(upstream_model: str) -> str:
        """按上游模型名判断走哪个协议面。"""
        return "gemini_native" if "gemini" in (upstream_model or "").lower() else "openai_images"

    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        up_model = protocols.upstream_model(p, body.get("model") or "")
        if self.face_of(up_model) == "gemini_native":
            url, up, meta = protocols.build_gemini_native(p, body, edit)
        else:
            url, up, meta = protocols.build_openai_images(p, body, edit)
        meta["face"] = self.face_of(up_model)
        return url, up, meta

    def parse(self, payload) -> list[dict]:
        data = protocols.parse_gemini_native(payload)
        if data:
            return data
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]


CHANNEL = Change2Pro()
