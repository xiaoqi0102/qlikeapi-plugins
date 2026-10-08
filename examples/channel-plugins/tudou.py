"""AI-Tudou（土豆API）合并渠道插件。

站点：https://api.ai-tudou.net（Bearer 鉴权，new-api 系）
口径来源：站点方《GPT-Image-2 图像生成 API（异步生图请求）》与《Nano Banana 图像生成 API》

同一个站点、同一把 key，按「模型名」自动分流到两套上游协议：

  · gpt-image-2-all  → 异步：POST /v1/images/generations/async → 轮询 GET /v1/tasks/{task_id}
  · gemini-* 系      → Gemini 原生：POST /v1beta/models/{model}:generateContent（imageConfig 控比例/分辨率）

站点方文档写死的「必填」字段（不发明，照抄文档默认值；客户端显式给了就听客户端的）：
  · gpt 面：resolution 必填（1k/2k/4k）、quality 必填（low/medium/high）、size 传「比例」如 16:9；
            参考图**只能是公网 URL 或 data:image/...;base64**（不收裸 base64、不走 multipart）
  · gemini 面：generationConfig.responseModalities 固定 ["TEXT","IMAGE"]；
            imageConfig.{aspectRatio,imageSize}（imageSize 必须大写 1K/2K/4K）；
            参考图**只能 base64**（inlineData），且**图片 parts 放在文本 prompt 之前**

异步任务：站点先回 {code,data:{id:"task_...",status:"submitted"}}，再轮询 /v1/tasks/{id}，
完成时读 data.result.images[0].url[0]（注意 url 字段本身是数组）。
文档建议：首次查询延迟 10~20s，之后 3~5s 一次。
"""
from __future__ import annotations

import re
import time

from .. import protocols, utils
from .base import Channel, ChannelError

_DEFAULT_BASE = "https://api.ai-tudou.net"

# 站点任务状态词表（文档「任务状态说明」）
_TASK_PENDING = {"submitted", "processing", "queued", "pending", "running", "created", ""}
_TASK_FAILED = {"failed", "error", "cancelled", "canceled", "rejected"}

_QUALITIES = ("low", "medium", "high")
_TIERS = ("1k", "2k", "4k")
_TASK_ID_RE = re.compile(r"[^A-Za-z0-9_.:\-]")
_RATIO_RE = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$")
# 文档「格式 2」：图片塞在 text 的 markdown data URI 里
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*data:(image/[^;)\s]+);base64,([^)\s]+)\s*\)")


class Tudou(Channel):
    id = "tudou"
    label = "AI-Tudou（土豆API：gpt-image-2-all + Nano Banana 合一）"
    vendor = "AI-Tudou（api.ai-tudou.net）"
    docs = "https://api.ai-tudou.net"
    hint = "同一站点按模型自动分流：gpt-image-2-all 走异步队列，gemini 系走原生 generateContent"
    protocol_note = (
        "站点自有口径：两套协议同一把 Bearer key。**两面参考图形态不同** —— "
        "gemini 面只吃 base64（inlineData），gpt 面吃公网 URL 或 data:image/...;base64（不收裸 base64）。"
        "gpt 面的 resolution(1k/2k/4k) / quality(low/medium/high) 是站点「必填」字段，"
        "缺省时本插件按文档补默认值（quality 缺省 medium、resolution 按像素尺寸推档，推不出按 2k）。"
        "gpt 面是异步队列：先拿 task_id 再轮询 /v1/tasks/{id}，由本插件的 poll() 钩子兜住，客户端仍拿同步结果。"
    )
    auth_modes = ("bearer",)
    default_auth = "bearer"
    default_base_url = _DEFAULT_BASE
    site_type = "newapi"
    operations = {"generate": "converted", "edit": "converted"}
    models = {
        # 客户端模型名 → 站点真实模型名
        "gemini-3-pro-image-preview": "gemini-3-pro-image-preview",
        "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image-preview",
        "gemini-nano-banana-2.1": "gemini-nano-banana-2.1",
        "gpt-image-2-all": "gpt-image-2-all",
    }
    ref_input_faces = {"gemini 面": "base64", "gpt-image-2-all 面": "both"}

    # ---------------------------------------------------------------- 分流

    def declared_ref_input(self, p: dict, body: dict, edit: bool) -> str:
        """gemini 面只吃 base64（inlineData）；gpt 面 URL / data URI 都行。"""
        up_model = protocols.upstream_model(p, body.get("model") or "")
        return "base64" if self.face_of(up_model) == "gemini_native" else "both"

    @staticmethod
    def face_of(upstream_model: str) -> str:
        return "gemini_native" if "gemini" in (upstream_model or "").lower() else "gpt_image_async"

    # ---------------------------------------------------------------- build

    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        prompt = protocols.prompt_of(body)          # 空串 / 纯空白都算「没有提示词」
        if not prompt:
            raise ChannelError("prompt is required")
        up_model = protocols.upstream_model(p, body.get("model") or "")
        if self.face_of(up_model) == "gemini_native":
            return self._build_gemini(p, body, up_model, prompt)
        return self._build_gpt(p, body, up_model, prompt)

    def _build_gpt(self, p: dict, body: dict, up_model: str, prompt: str) -> tuple[str, dict, dict]:
        base = str(p.get("base_url") or self.default_base_url).rstrip("/")
        wh = utils.parse_size(body.get("size"))
        up = {
            "model": up_model,
            "prompt": prompt,
            "size": self._ratio(body.get("size"), wh),
            "resolution": self._tier(body, wh),
            "quality": self._quality(body.get("quality")),
        }
        refs = self._gpt_refs(body)
        if refs:
            up["images"] = refs
        meta = {
            "up_model": up_model,
            "face": "gpt_image_async",
            "refs": len(refs),
            "poll_base": f"{base}/v1/tasks",     # poll() 用它拼轮询地址
        }
        return f"{base}/v1/images/generations/async", up, meta

    def _build_gemini(self, p: dict, body: dict, up_model: str, prompt: str) -> tuple[str, dict, dict]:
        base = str(p.get("base_url") or self.default_base_url).rstrip("/")
        parts: list[dict] = []
        refs = 0
        # 文档：图片 parts 放前面，文本 prompt 放最后
        for ref in utils.collect_refs(body):
            got = utils.fetch_as_b64(ref, protocols.HTTP)
            if got:
                parts.append({"inlineData": {"mimeType": got[0], "data": got[1]}})
                refs += 1
        parts.append({"text": prompt})

        gen: dict = {"responseModalities": ["TEXT", "IMAGE"]}   # 文档：固定值
        wh = utils.parse_size(body.get("size"))
        if wh:
            plan = utils.gemini_plan(wh[0], wh[1], model=up_model, policy=protocols.gemini_policy(p))
            gen["imageConfig"] = {"aspectRatio": plan["ratio"], "imageSize": plan["tier"]}
        opts = p.get("options") or {}
        if opts.get("image_size_override"):
            gen.setdefault("imageConfig", {})["imageSize"] = str(opts["image_size_override"])

        up: dict = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
        removed = protocols.apply_removals(up, opts.get("remove_params") or [])
        meta = {"up_model": up_model, "face": "gemini_native", "refs": refs, "removed": removed}
        return f"{base}/v1beta/models/{up_model}:generateContent", up, meta

    # ---------------------------------------------------------------- 参数纠偏

    @staticmethod
    def _ratio(size, wh: tuple[int, int] | None) -> str:
        """站点 gpt 面的 size 是「比例」；客户端给宽高就吸附到最近比例。"""
        if wh:
            try:
                return str(utils.nearest_ratio(wh[0], wh[1]))
            except Exception:                       # noqa: BLE001 —— 吸附失败就按 1:1
                return "1:1"
        m = _RATIO_RE.match(str(size or ""))
        if m:
            return f"{int(m.group(1))}:{int(m.group(2))}"
        return "1:1"                                # 文档：auto 按 1:1 处理

    @staticmethod
    def _tier(body: dict, wh: tuple[int, int] | None) -> str:
        raw = str(body.get("resolution") or "").strip().lower()
        if raw == "0.5k":
            return "1k"
        if raw in _TIERS:
            return raw
        if wh:
            got = str(utils.resolution_of(wh[0], wh[1])).strip().lower()
            if got in _TIERS:
                return got
        return "2k"                                 # 文档：异步任务常用档位

    @staticmethod
    def _quality(q) -> str:
        got = utils.normalize_quality(q)            # standard/auto→auto、hd→high
        return got if got in _QUALITIES else "medium"

    @staticmethod
    def _gpt_refs(body: dict) -> list[str]:
        """站点只吃公网 URL 或 data:image/...;base64 —— 裸 base64 补上 MIME 前缀。"""
        out: list[str] = []
        for ref in utils.collect_refs(body):
            if ref.startswith("http") or ref.startswith("data:"):
                out.append(ref)
                continue
            got = utils.to_raw_b64(ref)
            if got:
                out.append(f"data:{got[0]};base64,{got[1]}")
        return out

    # ---------------------------------------------------------------- parse

    @staticmethod
    def _node(payload) -> dict:
        if not isinstance(payload, dict):
            return {}
        inner = payload.get("data")
        return inner if isinstance(inner, dict) else payload

    @classmethod
    def _extract(cls, payload) -> list[dict]:
        node = cls._node(payload)
        # 1) 站点异步结果：data.result.images[].url[]（url 本身是数组）
        res = node.get("result")
        if isinstance(res, dict):
            urls: list[str] = []
            for it in (res.get("images") or []):
                if isinstance(it, dict):
                    u = it.get("url")
                    if isinstance(u, list):
                        urls += [x for x in u if isinstance(x, str) and x.startswith("http")]
                    elif isinstance(u, str) and u.startswith("http"):
                        urls.append(u)
                elif isinstance(it, str) and it.startswith("http"):
                    urls.append(it)
            if urls:
                return [{"url": u} for u in dict.fromkeys(urls)]
        # 2) Gemini 原生：candidates[].content.parts[].inlineData
        got = protocols.parse_gemini_native(payload)
        if got:
            return got
        # 3) Gemini「格式 2」：图片塞在 text 的 markdown data URI 里
        out: list[dict] = []
        for cand in (node.get("candidates") or []):
            for part in ((cand.get("content") or {}).get("parts") or []):
                if part.get("thought"):             # 跳过 thinking 过程
                    continue
                txt = part.get("text")
                if isinstance(txt, str):
                    for mime, b64 in _MD_IMG_RE.findall(txt):
                        out.append({"b64_json": b64, "mime_type": mime})
        if out:
            return out
        # 4) 通用兜底
        urls, _ = protocols.extract_urls(payload)
        return [{"url": u} for u in urls]

    def parse(self, payload) -> list[dict]:
        return self._extract(payload)

    # ---------------------------------------------------------------- 异步轮询

    @staticmethod
    def _state(payload) -> str:
        if not isinstance(payload, dict):
            return ""
        return str((Tudou._node(payload) or {}).get("status") or "").strip().lower()

    @staticmethod
    def _task_id(payload) -> str:
        node = Tudou._node(payload)
        for k in ("id", "task_id", "taskId"):
            v = node.get(k)
            if isinstance(v, str) and v:
                return v
        return ""

    def poll(self, first, meta: dict, headers: dict, timeout: float | None = None) -> tuple[str, object]:
        """gpt 面：站点先回 task_id → 轮询到出图。gemini 面没有 poll_base，直接 SKIP。

        返回 (状态, 结果)：OK = 拿到图；SKIP = 不是异步任务（交回 relay 常规流程）；
        FAILED / TIMEOUT = 拿不到图（relay 按上游错误处理，日志里能看到原始返回）。
        """
        base = str((meta or {}).get("poll_base") or "")
        if not base:
            return "SKIP", None
        state = self._state(first)
        if state not in _TASK_PENDING:
            return "SKIP", None
        tid = self._task_id(first)
        if not tid:
            return "SKIP", None
        h = {k: v for k, v in headers.items() if k.lower() != "content-type"}
        url = f"{base}/{_TASK_ID_RE.sub('', str(tid))}"
        deadline = time.time() + float(timeout or protocols.POLL_MAX)
        payload = first
        # 文档建议：提交后等 10~20s 再查第一枪
        time.sleep(min(10, max(0.0, deadline - time.time())))
        while time.time() < deadline:
            try:
                payload = protocols.HTTP.get(url, headers=h).json()
            except Exception as exc:                # noqa: BLE001 —— 轮询失败按继续等处理
                payload = {"status": "processing", "error": f"poll failed: {exc!r}"}
            if self._extract(payload):
                return "OK", payload
            state = self._state(payload)
            if state in _TASK_FAILED:
                return "FAILED", payload
            time.sleep(protocols.POLL_INTERVAL)
        return "TIMEOUT", payload


CHANNEL = Tudou()
