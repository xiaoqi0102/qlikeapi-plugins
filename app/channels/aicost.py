"""渠道插件：aicost.me（gpt-image-2 面 + Gemini 图片面 合一）。

站点：https://www.aicost.me（new-api 系站点，Bearer 鉴权）
口径来源：站点方《aicost.me 图片插件模型接口文档》（本仓库只落实现，不改口径）

同一个站点、同一把 key，按「模型名」自动分流到两套上游协议：
  · gemini 系        → POST /v1beta/models/{model}:generateContent（Base 去掉 /v1 再拼 v1beta）
  · gpt-image-2 系   → POST /v1/images/generations | /v1/images/edits（标准 OpenAI 图片面）

站点方文档里写死的「必填」字段（我们不发明，照抄默认值，客户端给了就听客户端的）：
  · image2 面：n=1、quality=auto、output_format=jpeg、moderation=auto
  · gemini 面：generationConfig.responseModalities=["TEXT","IMAGE"]、imageConfig.{imageSize,aspectRatio}
               默认 aspect_ratio=16:9、image_size=2K（文档默认值）

异步任务：站点可能不回图而回 {task_id, status}，轮询 GET /v1/images/generations/{task_id}。
  本插件实现了可选钩子 poll()（见 channels/base.py），relay 在「上游没直接给图」时会调它，
  这样客户端拿到的仍然是 OpenAI 形状的同步结果。只认图的插件不需要实现这个钩子。
"""
from __future__ import annotations

import re
import time

from .. import protocols
from .base import Channel

# 文档 §5 列出的「图片可能出现在哪些字段」
B64_KEYS = ("b64_json", "image_base64", "base64")
URL_KEYS = ("url", "image_url")
NEST_KEYS = ("data", "images", "image", "output", "result")
TEXT_KEYS = ("content", "text", "message", "delta")     # 文档 §5：从文本里抠图片 URL
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
_IMG_RE = re.compile(r"\.(png|jpe?g|webp|gif|avif)(\?|$)", re.I)

# 文档 §5 的异步状态表
PENDING_STATES = {"queued", "pending", "processing", "running", "in_progress", ""}
FAIL_STATES = {"failed", "error", "cancelled", "canceled", "rejected", "content_filter"}

_B64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.:\-]")


def _safe_id(tid) -> str:
    """任务号拼进 URL 前自净化（插件校验器禁 import urllib）。"""
    return _SAFE_ID_RE.sub("", str(tid))


class Aicost(Channel):
    id = "aicost"
    label = "aicost.me（image2 + Gemini 合一）"
    site_type = "newapi"      # 加渠道时自动建这种「站点余额」条目
    vendor = "aicost.me"
    docs = "https://www.aicost.me"
    hint = "同一站点按模型自动分流：gemini 系走 generateContent，gpt-image 系走 /v1/images/*"
    protocol_note = ("new-api 系站点自有口径：两套协议都吃 Bearer。**两面参考图都只吃 base64**"
                     "（gemini 面走 inlineData、image2 面走 image 字段；公网 URL 一律拒 —— "
                     "实测 400「输入的图片有误」，网关会自动把 URL 下载内联成 data URI）。"
                     "image2 面的 n/quality/output_format/moderation "
                     "是站点方「必填」字段，本插件缺省时按文档补默认值（客户端显式给了就听客户端的）。")
    default_auth = "bearer"
    default_base_url = "https://www.aicost.me"
    operations = {"generate": "converted", "edit": "converted"}
    models = {
        # 客户端模型名 → 该站真实模型名（站点方文档 §1.1；2.5 两档为站点实探可用）
        "gpt-image-2": "gpt-image-2",
        "gpt-image-2.5-flare": "gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst": "gpt-image-2.5-sunburst",
        "gemini-3-pro-image": "gemini-3-pro-image-preview",
        "gemini-3.1-flash-image": "gemini-3.1-flash-image-preview",
        "gemini-3-pro-image-preview": "gemini-3-pro-image-preview",
        "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image-preview",
    }

    # 参考图形态按「面」声明：**两面都只吃 base64**（裸 base64 或 data URI）。
    # 实测 2026-09-19：gpt-image-2 编辑面传公网 URL → 400「输入的图片有误，请确认图片格式/链接是否正确」；
    # 传裸 base64 / data URI → 200 正常出图。（网关会给只认 base64 的渠道把 URL 下载内联成 data URI。）
    ref_input_faces = {"gemini 面": "base64", "image2 面": "base64"}

    def declared_ref_input(self, p: dict, body: dict, edit: bool) -> str:
        return "base64"

    def up_model(self, p: dict, client_model: str) -> str:
        """上游真实模型名。

        实例的 model_map 配了就一律听实例的（含显式同名映射）；没配才用插件预置映射兜底 ——
        这个站的 gemini 模型名带 -preview 后缀，实例里漏配就会 404，兜底能让插件开箱可用。
        """
        mm = p.get("model_map") or {}
        key = protocols.match_model(p, client_model)
        if key in mm:
            return protocols.upstream_model(p, client_model)
        preset = self.models.get(key) or self.models.get((client_model or "").strip())
        return preset if isinstance(preset, str) and preset else (client_model or "")

    @staticmethod
    def face_of(upstream_model: str) -> str:
        """按上游模型名判断走哪个协议面。"""
        return "gemini_native" if "gemini" in (upstream_model or "").lower() else "openai_images"

    # ---- 翻译 ----
    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        client_model = body.get("model") or ""
        up_model = self.up_model(p, client_model)
        face = self.face_of(up_model)
        base = str(p.get("base_url") or "").rstrip("/")
        # 把解析好的上游名回灌给协议层：否则 build_gemini_native 会用未映射的名字拼 URL
        pp = {**p, "model_map": {**(p.get("model_map") or {}), client_model: up_model}}
        if face == "gemini_native":
            url, up, meta = protocols.build_gemini_native(pp, body, edit)
            cfg = up.setdefault("generationConfig", {})
            # 站点方文档把这两项写成「必填」：缺了上游直接拒
            cfg.setdefault("responseModalities", ["TEXT", "IMAGE"])
            iconf = dict(cfg.get("imageConfig") or {})
            iconf.setdefault("aspectRatio", "16:9")     # 文档默认值
            iconf.setdefault("imageSize", "2K")         # 文档默认值
            cfg["imageConfig"] = iconf
        else:
            url, up, meta = protocols.build_openai_images(pp, body, edit)
            for field, default in (("n", 1), ("quality", "auto"),
                                   ("output_format", "jpeg"), ("moderation", "auto")):
                if up.get(field) in (None, ""):
                    up[field] = default
        meta["face"] = face
        # 异步任务轮询地址（文档 §2.3 / §3.3：两面的轮询都打 /v1/images/generations/{task_id}）
        meta["poll_base"] = f"{base}/v1/images/generations"
        return url, up, meta

    # ---- 解析 ----
    def parse(self, payload) -> list[dict]:
        got = protocols.parse_gemini_native(payload)
        if got:
            return got
        found = self._walk(payload)
        if found:
            return found
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]

    @classmethod
    def _walk(cls, node, depth: int = 0) -> list[dict]:
        """按文档 §5 的字段清单挖图（b64 与 URL 都认），比 extract_urls 多认 base64。"""
        if depth > 6:
            return []
        if isinstance(node, list):
            out: list[dict] = []
            for it in node:
                out += cls._walk(it, depth + 1)
            return out
        if isinstance(node, str):
            return [{"url": node}] if node.startswith("http") else []
        if not isinstance(node, dict):
            return []
        for k in B64_KEYS:
            v = node.get(k)
            if isinstance(v, str) and len(v) > 64 and _B64_RE.match(v[:200]):
                return [{"b64_json": v}]
        for k in URL_KEYS:
            v = node.get(k)
            if isinstance(v, dict) and isinstance(v.get("url"), str) and v["url"].startswith("http"):
                return [{"url": v["url"]}]
            if isinstance(v, str) and v.startswith("http"):
                return [{"url": v}]
        for k in NEST_KEYS:
            if k in node:
                got = cls._walk(node[k], depth + 1)
                if got:
                    return got
        for k in TEXT_KEYS:                       # choices[].message.content 这类「文本里带链接」
            v = node.get(k)
            if isinstance(v, str) and "http" in v:
                found = _URL_RE.findall(v)
                imgs = [u for u in found if _IMG_RE.search(u)] or found
                if imgs:
                    return [{"url": u} for u in dict.fromkeys(imgs)]
            elif isinstance(v, dict):
                got = cls._walk(v, depth + 1)
                if got:
                    return got
        # 兜底：剩下的键也往下走（choices[].message 这类自定义层级）
        for v in node.values():
            if isinstance(v, (dict, list)):
                got = cls._walk(v, depth + 1)
                if got:
                    return got
        return []

    # ---- 可选：异步任务轮询 ----
    def poll(self, first, meta: dict, headers: dict, timeout: float | None = None) -> tuple[str, object]:
        """上游回了 task_id 而不是图 → 轮询到出图。

        返回 (状态, 结果)：OK = 拿到图；SKIP = 这不是异步任务（交回 relay 的常规流程）；
        FAILED / TIMEOUT = 拿不到图（relay 按上游错误处理，日志里能看到原始返回）。
        """
        tid = self.task_id(first)
        base = str((meta or {}).get("poll_base") or "")
        if not tid or not base:
            return "SKIP", None
        status = str((first or {}).get("status") or "").lower()
        if status not in PENDING_STATES:
            return "SKIP", None
        h = {k: v for k, v in headers.items() if k.lower() != "content-type"}
        url = f"{base}/{_safe_id(tid)}"
        deadline = time.time() + float(timeout or protocols.POLL_MAX)
        payload = first
        while time.time() < deadline:
            time.sleep(protocols.POLL_INTERVAL)
            try:
                payload = protocols.HTTP.get(url, headers=h).json()
            except Exception as exc:                      # noqa: BLE001 —— 轮询失败按继续等处理
                payload = {"status": "pending", "error": f"poll failed: {exc!r}"}
            if self.parse(payload):
                return "OK", payload
            state = str((payload or {}).get("status") or "").lower()
            if state in FAIL_STATES:
                return "FAILED", payload
        return "TIMEOUT", payload

    @staticmethod
    def task_id(payload) -> str:
        """从上游返回里找任务号（task_id / taskId / data.task_id / id+status）。"""
        if not isinstance(payload, dict):
            return ""
        for k in ("task_id", "taskId", "id"):
            v = payload.get(k)
            if isinstance(v, str) and v and payload.get("status"):
                return v
        for k in ("data", "task", "result"):
            v = payload.get(k)
            if isinstance(v, dict):
                for kk in ("task_id", "taskId", "id"):
                    if isinstance(v.get(kk), str) and v[kk]:
                        return v[kk]
        return ""


CHANNEL = Aicost()
