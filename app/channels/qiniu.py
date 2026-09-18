"""渠道插件：七牛 ModelInk（同步面 + fal 异步面合一）。

同一个站点、同一把 key，按「模型名」自动分流到两套七牛自家协议：
  · gemini 系（gemini-*）      → fal 风格异步队列（/queue/{model} → /requests/{id}）
  · gpt-image 系（gpt-image-*）→ 官方形状同步面（/v1/images/generations|edits，固定 b64_json）

这样 New API 里只挂**一个七牛渠道**即可，模型名照旧区分（跟 change2pro 一个思路：
能合成一个插件、一个渠道的，就不拆成两个）。

七牛口径（实测，别拿 OpenAI / fal.ai 的文档套）：
  · 同步面：路径 /v1/images/generations | /v1/images/edits，但**固定返回 b64_json**，
    且不认 response_format —— 本插件自动剔掉该字段，响应里加 note 说明。
  · 异步面：Authorization: Key <token>，参考图必须公网 URL，结果是 Kodo 签名 URL（约 7 天）。
"""
from __future__ import annotations

from .. import protocols
from .base import Channel


class Qiniu(Channel):
    id = "qiniu"
    label = "七牛 ModelInk（同步 + fal 异步合一）"
    site_type = "manual"      # 加渠道时自动建这种「站点余额」条目
    vendor = "七牛云 / ModelInk（api.qnaigc.com）"
    docs = "https://www.qiniu.com"
    hint = "同一站点按模型自动分流：gemini 系走 fal 异步队列，gpt-image 系走同步面（固定 b64_json）"
    protocol_note = "七牛自家口径：同步面固定 b64_json 且不认 response_format；异步面是七牛封装的队列（非 fal.ai 官方），参考图需公网 URL。"
    default_auth = "bearer"
    default_base_url = "https://api.qnaigc.com"
    operations = {"generate": "converted", "edit": "converted"}
    models = {
        "gemini-3.1-flash-image-preview": "fal-ai/gemini-3.1-flash-image-preview",
        "gemini-3-pro-image-preview": "fal-ai/gemini-3-pro-image-preview",
        "gpt-image-2": "openai/gpt-image-2",
        "gpt-image-2.5-flare": "openai/gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst": "openai/gpt-image-2.5-sunburst",
    }

    ref_input_faces = {"异步面": "url", "同步面": "base64"}
    def declared_ref_input(self, p: dict, body: dict, edit: bool) -> str:
        """按「面」声明：异步面只认公网 URL；同步面文档没写公网 URL，就不猜、保持 base64。"""
        up_model = protocols.upstream_model(p, body.get("model") or "")
        return "url" if self.face_of(up_model) == "fal" else "base64"

    @staticmethod
    def face_of(upstream_model: str) -> str:
        """按上游模型名判断走哪一面：gemini 系 → 异步队列；其余 → 同步面。"""
        return "fal" if "gemini" in (upstream_model or "").lower() else "sync"

    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        up_model = protocols.upstream_model(p, body.get("model") or "")
        face = self.face_of(up_model)
        if face == "fal":
            url, up, meta = protocols.build_fal_queue(p, body, edit)
            meta["mode"] = "queue"
            meta["auth_mode"] = "fal_key"            # 异步面：Authorization: Key <token>
        else:
            url, up, meta = protocols.build_openai_images(p, body, edit)
            up.pop("response_format", None)          # 七牛同步面不认这个字段
            meta["auth_mode"] = "bearer"
        meta["face"] = face
        return url, up, meta

    def parse(self, payload) -> list[dict]:
        data = protocols.parse_gemini_native(payload)
        if data:
            return data
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]


CHANNEL = Qiniu()
