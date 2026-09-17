"""utils.py —— 参数换算与参考图处理的小工具（不依赖任何其它模块）。"""
from __future__ import annotations

import base64
import math
import re
from typing import Any

RATIOS = [("21:9", 21 / 9), ("16:9", 16 / 9), ("3:2", 1.5), ("4:3", 4 / 3), ("5:4", 1.25),
          ("1:1", 1.0), ("4:5", 0.8), ("3:4", 0.75), ("2:3", 2 / 3), ("9:16", 9 / 16),
          ("4:1", 4.0), ("1:4", 0.25), ("8:1", 8.0), ("1:8", 0.125)]
GPT_SIZES = [(1024, 1024), (1536, 1024), (1024, 1536), (2048, 2048),
             (2048, 1152), (1152, 2048), (3840, 2160), (2160, 3840)]
REF_FIELDS = ("image", "images", "image_urls", "image_refs", "reference_images", "mask", "mask_url")


def parse_size(size: Any) -> tuple[int, int] | None:
    if isinstance(size, (list, tuple)) and len(size) == 2:
        try:
            return int(size[0]), int(size[1])
        except Exception:
            return None
    if not isinstance(size, str):
        return None
    m = re.fullmatch(r"\s*(\d{2,5})\s*[xX*×]\s*(\d{2,5})\s*", size)
    return (int(m.group(1)), int(m.group(2))) if m else None


def nearest_ratio(w: int, h: int) -> str:
    if h <= 0:
        return "1:1"
    t = w / h
    return min(RATIOS, key=lambda r: abs(math.log(t) - math.log(r[1])))[0]


def resolution_of(w: int, h: int) -> str:
    edge = max(w, h)
    if edge <= 512:
        return "0.5K"
    if edge <= 1536:
        return "1K"
    if edge <= 2048:
        return "2K"
    return "4K"


def gpt_safe_size(size: Any) -> str:
    """OpenAI 系上游对尺寸有整除/面积/比例限制，做一次安全吸附。"""
    wh = parse_size(size)
    if not wh:
        return "auto"
    w, h = wh
    if (w % 16 == 0 and h % 16 == 0 and max(w, h) <= 3840 and max(w, h) / min(w, h) <= 3.0
            and 655_360 <= w * h <= 8_294_400):
        return f"{w}x{h}"
    t = w / h
    best = min(GPT_SIZES, key=lambda s: abs(math.log(t) - math.log(s[0] / s[1])))
    return f"{best[0]}x{best[1]}"


def normalize_quality(q: Any) -> str | None:
    if q is None:
        return None
    if not isinstance(q, str):
        return "auto"
    q = q.strip().lower()
    if q in ("standard", "auto", "default", ""):
        return "auto"
    if q in ("hd", "high"):
        return "high"
    return q if q in ("low", "medium", "xhigh", "max") else "auto"


def collect_refs(body: dict) -> list[str]:
    """从各种客户端写法里收集参考图（URL / data URI / 裸 base64）。"""
    refs: list[str] = []
    for field in REF_FIELDS:
        v = body.get(field)
        if v is None:
            continue
        for it in (v if isinstance(v, list) else [v]):
            if isinstance(it, dict):
                it = it.get("image_url") or it.get("url") or it.get("data") or ""
            if isinstance(it, str) and it.strip():
                refs.append(it.strip())
    return refs


def to_raw_b64(ref: str) -> tuple[str, str] | None:
    if ref.startswith("data:"):
        m = re.match(r"data:([^;,]+);base64,(.+)", ref, re.S)
        return (m.group(1), re.sub(r"\s+", "", m.group(2))) if m else None
    if ref.startswith("http://") or ref.startswith("https://"):
        return None
    if len(ref) > 200 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", ref):
        return "image/png", re.sub(r"\s+", "", ref)
    return None


def fetch_as_b64(ref: str, http) -> tuple[str, str] | None:
    """URL / data URI / 裸 base64 → (mime, base64)。http 为 httpx.Client。"""
    direct = to_raw_b64(ref)
    if direct:
        return direct
    if ref.startswith("http"):
        try:
            r = http.get(ref, timeout=90)
            if r.status_code == 200:
                mime = (r.headers.get("content-type") or "image/png").split(";")[0]
                return mime, base64.b64encode(r.content).decode()
        except Exception:
            return None
    return None


def mask(secret: str | None, keep: int = 6) -> str:
    s = secret or ""
    if len(s) <= keep + 4:
        return (s[:2] + "…") if s else ""
    return f"{s[:keep]}…{s[-4:]}"
