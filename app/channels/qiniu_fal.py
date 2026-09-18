"""渠道插件：七牛 ModelInk 的 fal 风格异步队列（qnaigc 定制）。

⚠ 命名口径：**这不是 fal.ai 官方协议**。fal 的队列只是「提交 + 轮询」这个思路，
   各家中转站都是按自家接口改过的 —— 路径、字段名、返回结构、签名 URL 有效期全都不一样。
   所以本插件按来源命名为 `qiniu_fal`（跟 `change2pro` 一样，谁家的插件用谁家的名字），
   文档口径以七牛 ModelInk 为准，不要拿 fal.ai 的文档套。

七牛这条链路的实际形态（实测）：
  · 提交 POST /queue/{model}                 → 返回 request_id
  · 轮询 GET  /requests/{id}/status          → IN_QUEUE / IN_PROGRESS / COMPLETED
  · 取件 GET  /requests/{id}                 → images[].url（七牛 Kodo 签名 URL，约 7 天）
  · 鉴权 Authorization: Key <token>
  · 参考图必须是公网 URL（不支持本地 base64 上传）
本服务不落盘、不转存，直接把 URL 回给客户端。
"""
from __future__ import annotations

from .. import protocols
from .base import Channel


class QiniuFal(Channel):
    id = "qiniu_fal"
    label = "七牛 fal 异步队列（qnaigc 定制）"
    vendor = "七牛云 / ModelInk（api.qnaigc.com）"
    docs = "https://www.qiniu.com"
    hint = "七牛自家的 fal 风格队列：/queue/{model} 提交 → /requests/{id}/status 轮询 → /requests/{id} 取图"
    protocol_note = "非 fal.ai 官方协议（各家中转各写各的）；参考图必须是公网 URL；结果是 Kodo 签名 URL（约 7 天有效），本服务不转存。"
    default_auth = "fal_key"
    default_base_url = "https://api.qnaigc.com"
    operations = {"generate": "queue", "edit": "queue"}
    # 文档口径：异步队列只拉公网 URL，不接受 base64/本地文件 → base64 必须先转图床直链
    site_type = "manual"      # 加渠道时自动建这种「站点余额」条目
    ref_input = "url"
    models = {
        "gemini-3.1-flash-image-preview": "fal-ai/gemini-3.1-flash-image-preview",
        "gemini-3-pro-image-preview": "fal-ai/gemini-3-pro-image-preview",
        "gpt-image-2": "openai/gpt-image-2",
        "gpt-image-2.5-flare": "openai/gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst": "openai/gpt-image-2.5-sunburst",
    }

    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        url, up, meta = protocols.build_fal_queue(p, body, edit)
        meta["mode"] = "queue"
        return url, up, meta

    def parse(self, payload) -> list[dict]:
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]


CHANNEL = QiniuFal()
