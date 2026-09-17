"""utils.py —— 参数换算与参考图处理的小工具（不依赖任何其它模块）。"""
from __future__ import annotations

import base64
import math
import re
from typing import Any

# ---------------------------------------------------------------- 尺寸规则（每条都有官方出处）
# ① OpenAI GPT-Image-2 / 2.5 系（Azure OpenAI《GPT image models》文档）：
#    支持任意 WIDTHxHEIGHT，但有 4 条硬限制 —— 宽高都必须能被 16 整除、长短边比 ≤ 3:1、
#    任一边 ≤ 3840px、总像素 655,360 ~ 8,294,400；超过 2560x1440 属上游「实验档」。
# ② OpenAI GPT-Image-1 / 1.5 / 1-mini：只接受 1024x1024 / 1536x1024 / 1024x1536（或 auto）。
# ③ Google Gemini 图片模型：不能指定任意像素，只能给「分辨率档位 + 宽高比」，
#    由模型按该比例的标准像素出图（GEMINI_SIZES，来源 ai.google.dev 图片生成文档）。
GPT_RULES = dict(edge_mult=16, edge_max=3840, ratio_max=3.0,
                 area_min=655_360, area_max=8_294_400, experimental_edge=2560)
FIXED_SIZE_MODELS = (
    ("gpt-image-1-mini", ((1024, 1024), (1536, 1024), (1024, 1536))),
    ("gpt-image-1.5", ((1024, 1024), (1536, 1024), (1024, 1536))),
    ("gpt-image-1", ((1024, 1024), (1536, 1024), (1024, 1536))),
    ("dall-e-3", ((1024, 1024), (1792, 1024), (1024, 1792))),
)
GPT_SIZES = [(1024, 1024), (1536, 1024), (1024, 1536), (2048, 2048),
             (2048, 1152), (1152, 2048), (3840, 2160), (2160, 3840)]
RATIOS = [("21:9", 21 / 9), ("16:9", 16 / 9), ("3:2", 1.5), ("4:3", 4 / 3), ("5:4", 1.25),
          ("1:1", 1.0), ("4:5", 0.8), ("3:4", 0.75), ("2:3", 2 / 3), ("9:16", 9 / 16),
          ("4:1", 4.0), ("1:4", 0.25), ("8:1", 8.0), ("1:8", 0.125)]
REF_FIELDS = ("image", "images", "image_urls", "image_refs", "reference_images", "mask", "mask_url")

# Gemini：比例 → 各档位的标准输出像素（3.x 系，含 0.5K；Pro 无 0.5K）
GEMINI_TIERS = ("0.5K", "1K", "2K", "4K")
GEMINI_RATIOS_ALL = ("21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16",
                     "4:1", "1:4", "8:1", "1:8")
GEMINI_RATIOS_10 = ("21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16")
GEMINI_SIZES = {
    "3.1": {
        "1:1": {"0.5K": (512, 512), "1K": (1024, 1024), "2K": (2048, 2048), "4K": (4096, 4096)},
        "16:9": {"0.5K": (688, 384), "1K": (1376, 768), "2K": (2752, 1536), "4K": (5504, 3072)},
        "9:16": {"0.5K": (384, 688), "1K": (768, 1376), "2K": (1536, 2752), "4K": (3072, 5504)},
        "21:9": {"0.5K": (792, 168), "1K": (1584, 672), "2K": (3168, 1344), "4K": (6336, 2688)},
        "2:3": {"0.5K": (424, 632), "1K": (848, 1264), "2K": (1696, 2528), "4K": (3392, 5056)},
        "3:2": {"0.5K": (632, 424), "1K": (1264, 848), "2K": (2528, 1696), "4K": (5056, 3392)},
        "3:4": {"0.5K": (448, 600), "1K": (896, 1200), "2K": (1792, 2400), "4K": (3584, 4800)},
        "4:3": {"0.5K": (600, 448), "1K": (1200, 896), "2K": (2400, 1792), "4K": (4800, 3584)},
        "4:5": {"0.5K": (464, 576), "1K": (928, 1152), "2K": (1856, 2304), "4K": (3712, 4608)},
        "5:4": {"0.5K": (576, 464), "1K": (1152, 928), "2K": (2304, 1856), "4K": (4608, 3712)},
        "1:4": {"0.5K": (256, 1024), "1K": (512, 2048), "2K": (1024, 4096), "4K": (2048, 8192)},
        "4:1": {"0.5K": (1024, 256), "1K": (2048, 512), "2K": (4096, 1024), "4K": (8192, 2048)},
        "1:8": {"0.5K": (192, 1536), "1K": (384, 3072), "2K": (768, 6144), "4K": (1536, 12288)},
        "8:1": {"0.5K": (1536, 192), "1K": (3072, 384), "2K": (6144, 768), "4K": (12288, 1536)},
    },
    # Gemini 2.5 Flash Image 只有一档（1024 级），像素表与 3.x 不同
    "2.5": {
        "1:1": {"1K": (1024, 1024)}, "2:3": {"1K": (832, 1248)}, "3:2": {"1K": (1248, 832)},
        "3:4": {"1K": (864, 1184)}, "4:3": {"1K": (1184, 864)}, "4:5": {"1K": (896, 1152)},
        "5:4": {"1K": (1152, 896)}, "9:16": {"1K": (768, 1344)}, "16:9": {"1K": (1344, 768)},
        "21:9": {"1K": (1536, 672)},
    },
}


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


def fixed_sizes_for(model: str | None) -> tuple[tuple[int, int], ...] | None:
    """只接受固定尺寸的模型（gpt-image-1 系）返回它的合法尺寸表。"""
    m = (model or "").lower()
    for key, sizes in FIXED_SIZE_MODELS:
        if key in m:
            return sizes
    return None


def gemini_caps(model: str | None) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """(像素表族, 可用档位, 可用比例) —— 按模型能力收敛，避免给出上游不认的组合。"""
    m = (model or "").lower()
    if "2.5" in m or "2-5" in m:
        return "2.5", ("1K",), GEMINI_RATIOS_10
    if "flash-lite" in m:
        return "3.1", ("1K",), GEMINI_RATIOS_10
    if "pro" in m:
        return "3.1", ("1K", "2K", "4K"), GEMINI_RATIOS_10
    return "3.1", GEMINI_TIERS, GEMINI_RATIOS_ALL


def gemini_ratio(w: int, h: int, model: str | None = None) -> str:
    if h <= 0:
        return "1:1"
    _, _, allowed = gemini_caps(model)
    t = w / h
    pool = [r for r in RATIOS if r[0] in allowed] or RATIOS
    return min(pool, key=lambda r: abs(math.log(t) - math.log(r[1])))[0]


GEMINI_NOMINAL = {"0.5K": 512, "1K": 1024, "2K": 2048, "4K": 4096}


def gemini_tier(w: int, h: int, model: str | None = None, policy: str = "class") -> str:
    """选分辨率档位（Gemini 只能给档位，不能给任意像素）。

    class（默认）：按尺寸档位分类 —— 分界取相邻档位的对数中点（724 / 1448 / 2896px），
                  所以 1920x1080→2K、3840x2160→4K，与客户端对「2K/4K」的认知一致；
    floor（最省）：向下取档，按档计费时最便宜，绝不越级；
    ceil（保清晰度）：向上取档，输出长边绝不小于请求；
    nearest：按该比例的真实输出像素取对数距离最近的档。
    """
    fam, tiers, _ = gemini_caps(model)
    ratio = gemini_ratio(w, h, model)
    table = GEMINI_SIZES[fam].get(ratio) or GEMINI_SIZES[fam]["1:1"]
    avail = [t for t in tiers if t in table] or list(table)
    edges = {t: max(table[t]) for t in avail}
    target = max(w, h)
    if policy == "floor":
        ok = [t for t in avail if edges[t] <= target]
        return max(ok, key=lambda t: edges[t]) if ok else min(avail, key=lambda t: edges[t])
    if policy == "ceil":
        ok = [t for t in avail if edges[t] >= target]
        return min(ok, key=lambda t: edges[t]) if ok else max(avail, key=lambda t: edges[t])
    if policy == "nearest":
        return min(avail, key=lambda t: abs(math.log(target / edges[t])))
    ordered = sorted(avail, key=lambda t: GEMINI_NOMINAL[t])          # class
    idx = 0
    for i, (a, b) in enumerate(zip(ordered, ordered[1:], strict=False)):
        if target > math.sqrt(GEMINI_NOMINAL[a] * GEMINI_NOMINAL[b]):
            idx = i + 1
    return ordered[idx]


def gemini_plan(w: int, h: int, model: str | None = None, policy: str = "class") -> dict:
    """Gemini 侧完整换算：比例 + 档位 + 实际输出像素（便于日志/预览说清「会变成什么」）。"""
    fam, _, _ = gemini_caps(model)
    ratio = gemini_ratio(w, h, model)
    tier = gemini_tier(w, h, model, policy)
    table = GEMINI_SIZES[fam].get(ratio) or GEMINI_SIZES[fam]["1:1"]
    px = table.get(tier) or next(iter(table.values()))
    how = {"class": "按档位分类", "ceil": "向上取档（不降级）", "floor": "向下取档（最省）",
           "nearest": "取最接近档"}.get(policy, policy)
    bigger = "比请求大" if px[0] * px[1] > w * h else ("比请求小" if px[0] * px[1] < w * h else "与请求等大")
    return {"ratio": ratio, "tier": tier, "pixels": px,
            "note": f"{w}x{h} → {ratio} · {tier} · 实际输出 {px[0]}x{px[1]}（{bigger}；{how}，"
                    f"Gemini 只能给「档位+比例」，给不了任意像素）"}


def nearest_ratio(w: int, h: int, model: str | None = None) -> str:
    return gemini_ratio(w, h, model)


def resolution_of(w: int, h: int, model: str | None = None, policy: str = "class") -> str:
    return gemini_tier(w, h, model, policy)


def _round16(x: float, prefer: str = "nearest") -> int:
    lo = max(16, int(x) // 16 * 16)
    hi = lo + 16
    if prefer == "down":
        return lo
    if prefer == "up":
        return hi
    return lo if (x - lo) <= (hi - x) else hi


def _free_ok(w: int, h: int) -> bool:
    R = GPT_RULES
    return bool(w > 0 and h > 0 and w % R["edge_mult"] == 0 and h % R["edge_mult"] == 0
                and max(w, h) <= R["edge_max"] and max(w, h) / min(w, h) <= R["ratio_max"]
                and R["area_min"] <= w * h <= R["area_max"])


def _fit_free(w: int, h: int) -> tuple[int, int]:
    """把任意尺寸压进 GPT 自由尺寸的合法区间：保比例，且**最小改动**。"""
    R = GPT_RULES
    fw, fh = float(w), float(h)
    if fw / fh > R["ratio_max"]:
        fw = fh * R["ratio_max"]
    elif fh / fw > R["ratio_max"]:
        fh = fw * R["ratio_max"]
    if max(fw, fh) > R["edge_max"]:
        k = R["edge_max"] / max(fw, fh)
        fw, fh = fw * k, fh * k
    area = fw * fh
    if area > R["area_max"]:
        k = math.sqrt(R["area_max"] / area)
        fw, fh = fw * k, fh * k
    elif area < R["area_min"]:
        k = math.sqrt(R["area_min"] / area)
        fw, fh = fw * k, fh * k
    nw, nh = int(round(fw)), int(round(fh))
    nw = nw if nw % 16 == 0 else _round16(nw)
    nh = nh if nh % 16 == 0 else _round16(nh)
    if nw * nh < R["area_min"]:                       # 修完跌破面积下限 → 短边向上补一档
        if nw <= nh:
            nw = _round16(nw, "up")
        else:
            nh = _round16(nh, "up")
    # 取整会把比例/面积又顶出界（例：4000x500 压到 3:1 后取整成 1504x496，比例 3.03），
    # 所以取整后必须再校一遍，按「优先缩长边」的方向微调到真合规为止。
    for _ in range(64):
        if nw * nh < R["area_min"]:
            if nw <= nh:
                nw += 16
            else:
                nh += 16
        elif nw * nh > R["area_max"] or max(nw, nh) > R["edge_max"]:
            if nw >= nh:
                nw = max(16, nw - 16)
            else:
                nh = max(16, nh - 16)
        elif max(nw, nh) / min(nw, nh) > R["ratio_max"]:
            if nw >= nh:
                nw = max(16, nw - 16)
            else:
                nh = max(16, nh - 16)
        else:
            break
    return nw, nh


def snap_size(size: Any, model: str | None = None) -> dict:
    """尺寸换算（GPT 系）：返回 {size, original, changed, family, note}。

    关键：**最小改动** —— 只把不合法的那一边就近修到 16 的倍数（平手向下），
    绝不为了凑比例把整张图放大一档（上游按 1K/2K/4K 分档计费时，放大一档＝多扣费）。
    """
    wh = parse_size(size)
    if not wh:
        return {"size": "auto", "original": None, "changed": False, "family": "auto", "note": ""}
    w, h = wh
    fixed = fixed_sizes_for(model)
    if fixed:
        best = min(fixed, key=lambda s: abs(math.log((w / h) / (s[0] / s[1]))))
        changed = (w, h) != best
        opts = " / ".join(f"{a}x{b}" for a, b in fixed)
        note = "" if not changed else f"{w}x{h} → {best[0]}x{best[1]}（该模型只接受 {opts}）"
        return {"size": f"{best[0]}x{best[1]}", "original": f"{w}x{h}", "changed": changed,
                "family": "fixed", "note": note}
    nw, nh = _fit_free(w, h) if not _free_ok(w, h) else (w, h)
    changed = (nw, nh) != (w, h)
    note = ""
    if changed:
        why = []
        if w % 16 or h % 16:
            why.append("宽高需为 16 的倍数")
        if max(w, h) > GPT_RULES["edge_max"]:
            why.append("边长超 3840")
        if max(w, h) / min(w, h) > GPT_RULES["ratio_max"]:
            why.append("长短边比超 3:1")
        if not (GPT_RULES["area_min"] <= w * h <= GPT_RULES["area_max"]):
            why.append("总像素出界")
        note = f"{w}x{h} → {nw}x{nh}（{'、'.join(why) or '就近合规'}；最小改动、不跨档）"
    elif max(nw, nh) > GPT_RULES["experimental_edge"]:
        note = f"{nw}x{nh}（超 2560 属上游实验档）"
    return {"size": f"{nw}x{nh}", "original": f"{w}x{h}", "changed": changed, "family": "free", "note": note}


def gpt_safe_size(size: Any, model: str | None = None) -> str:
    """兼容旧调用：只要最终尺寸字符串。"""
    return snap_size(size, model)["size"]


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
