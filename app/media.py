"""素材上传中转：base64 / data URI / 本地文件 → 公网直链（图片 / 视频 / 音频通吃）。

为什么存在这一层
----------------
部分视频上游（速搭水 / MeAICC / 佳速 等）**只接受公网 http(s) 素材**，由上游服务端自己去抓，
base64 / 本地文件一律不收。而 New API 的 Task Plugin 沙箱里，插件自己**不能发 HTTP**
（只能声明一个请求描述符交给宿主代发），所以「提交时自动把 base64 转直链」在插件里做不到 ——
转换必须发生在网关这一层。

客户端只要把「参考素材中转 / 自定义上传接口」指向这里的 `POST /v1/files`，
本地文件或 base64 就会先变成公网直链，再拿去提交视频任务，对最终用户是透明的。

素材不止图片
------------
视频 / 音频素材走**上游自托管文件站**（速搭水文件站、佳速素材 CDN）—— 直链长期可读、
就在上游自家 CDN 上，比免费临时图床稳得多；ImgBB 只收图片，视频/音频会自动跳过它。

用法
----
    POST /v1/files
    Authorization: Bearer ***        # 也支持 ?token=<令牌>（有些客户端加不了请求头）
    Content-Type: multipart/form-data         # ① 直接传文件（字段名随意）
    Content-Type: application/json            # ② {"data_url":"data:image/png;base64,..."} / {"url":"https://..."}
    Content-Type: video/mp4（裸字节）          # ③ 直接 POST 字节

    可选 ?target=sudashui|jiasu|imgbb|uguu|auto   指定落哪个站（不传=按链路顺序自动挑）
    可选 ?format=text → 只回纯文本 URL（给只认纯文本的客户端）

    返回 {"ok":true,"url":"https://...","host":"sudashui_files","host_label":"速搭水文件站",
          "mime":"video/mp4","bytes":1234,"filename":"a.mp4","expires":"...",
          "attempts":[{"host":"sudashui_files","ok":true,"status":200,"ms":431}],
          "data":{"url":"..."}}

铁律
----
  · 只上传「客户端手里」的素材（base64 / data URI / 裸字节）；已经是 http(s) 的一律原样返回。
  · 素材不落本机磁盘；上游站凭据走 crypto 加密落库，**绝不进日志、绝不进仓库**（日志里一律掩码）。
  · 每次转换的完整链路（候选顺序 / 每站结果 / 耗时 / 最终直链）都写进日志，面板「素材日志」可还原。
  · 站点未启用 / 无可用链路 / 全部失败 → 明确报错，绝不静默成功。
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import time
import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import crypto, imagehost, relay, store

router = APIRouter()

#: 单次上传体积上限（MB）。视频素材比图片大，默认放到 100MB，可用环境变量收紧。
MAX_UPLOAD_MB = float(os.environ.get("QLIKEAPI_UPLOAD_MAX_MB") or 100)

#: 各图床直链的近似有效期（秒）；None = 长期有效
HOST_TTL_SECONDS: dict[str, int | None] = {
    "imgbb": None,
    "litterbox": 72 * 3600,
    "uguu": 3 * 3600,
    "catbox": None,
    "zero_x0": 60 * 86400,
}

#: 上游自托管文件站：比免费图床稳，且直链就在上游自家 CDN 上（视频/音频首选）
STATIONS: dict[str, dict] = {
    "sudashui_files": {
        "label": "速搭水文件站",
        "endpoint": "https://files.sudashuiapi.com",
        "key_field": "sudashui_key",
        "field": "file",
        "limit_mb": 50,
        "limit_mb_by_kind": {"image": 30, "video": 50, "audio": 15},
        "kinds": ("image", "video", "audio"),
        "ttl": "上游自托管（签名直链，约 2 小时）",
        "note": "POST multipart（字段 file）+ Bearer Key → {url,key,size,contentType,expiresAt}",
        "doc": "https://files.sudashuiapi.com",
    },
    "jiasu_media": {
        "label": "佳速素材 CDN",
        "endpoint": "https://ai.jiasuapi.com/v1/media/upload",
        "key_field": "jiasu_key",
        "field": "file",
        "limit_mb": 32,
        "limit_mb_by_kind": {"image": 32, "video": 32, "audio": 32},
        "kinds": ("image", "video", "audio"),
        "ttl": "上游自托管（长期）",
        "note": "POST multipart（字段 file，可带 kind）+ Bearer Key → {success,data:{items:[{url}]}}；"
                "注意 HTTP 恒 200，须判 success",
        "doc": "https://ai.jiasuapi.com/v1/media/upload",
    },
}

#: 上游站别名（客户端 ?target= 常用写法 → 内部 id）
TARGET_ALIASES = {
    "sudashui": "sudashui_files", "sudashui_files": "sudashui_files", "速搭水": "sudashui_files",
    "sd": "sudashui_files", "files.sudashuiapi.com": "sudashui_files",
    "jiasu": "jiasu_media", "jiasu_media": "jiasu_media", "佳速": "jiasu_media",
    "jiasuapi": "jiasu_media", "ai.jiasuapi.com": "jiasu_media",
    "imgbb": "imgbb", "litterbox": "litterbox", "uguu": "uguu",
    "auto": "", "": "", "默认": "",
}

DEFAULT_CHAIN = ["sudashui_files", "jiasu_media", "imgbb", "uguu"]

DEFAULTS = {
    "enabled": True,
    "chain": list(DEFAULT_CHAIN),
    "sudashui_key": "",
    "jiasu_key": "",
    "verify": True,
}

#: imagehost.sniff_mime 只认图片；这里补视频/音频的文件头
EXTRA_MAGIC: list[tuple[bytes, str]] = [
    (b"\x1a\x45\xdf\xa3", "video/webm"),
    (b"OggS", "audio/ogg"),
    (b"ID3", "audio/mpeg"),
    (b"\x00\x00\x01\xba", "video/mpeg"),
    (b"\x00\x00\x01\xb3", "video/mpeg"),
    (b"fLaC", "audio/flac"),
]

PAYLOAD_KEYS = ("data_url", "dataUrl", "dataURI", "data_uri", "data", "base64", "b64",
                "image", "file", "content", "source", "input", "media")

SOURCE_LABELS = {"multipart": "文件上传", "value": "base64 / data URI", "bytes": "裸字节",
                 "url": "公网链接透传"}


# ------------------------------------------------------------------ 配置

def settings() -> dict:
    """读素材中转配置（上游站 Key 加密落库，读出来是明文，只在内存里用）。"""
    raw = store.get_settings("media") or {}
    cfg = dict(DEFAULTS)
    for k, v in raw.items():
        if k in ("sudashui_key", "jiasu_key"):
            continue
        cfg[k] = v
    for k in ("sudashui_key", "jiasu_key"):
        cfg[k] = crypto.decrypt(raw.get(k)) or ""
    if not isinstance(cfg.get("chain"), list):
        cfg["chain"] = list(DEFAULT_CHAIN)
    cfg["chain"] = [h for h in cfg["chain"] if h in STATIONS or h in imagehost.HOSTS]
    return cfg


def save_settings(d: dict) -> dict:
    """写素材中转配置；密钥传空字符串 = 保持原值不变。"""
    cur = store.get_settings("media") or {}
    out = dict(cur)
    for k in ("enabled", "verify"):
        if k in d:
            out[k] = bool(d[k])
    if "chain" in d:
        out["chain"] = [h for h in (d["chain"] or []) if h in STATIONS or h in imagehost.HOSTS]
    for k in ("sudashui_key", "jiasu_key"):
        if d.get(k):
            out[k] = crypto.encrypt(str(d[k]).strip())
        elif d.get(k + "_clear"):
            out[k] = ""
    store.set_settings("media", out)
    return settings()


def host_meta(host: str) -> dict:
    """统一取站点登记信息（上游文件站 + 图床）。"""
    if host in STATIONS:
        s = STATIONS[host]
        return {"label": s["label"], "endpoint": s["endpoint"], "ttl": s["ttl"],
                "note": s["note"], "kinds": s["kinds"], "limit_mb": s["limit_mb"],
                "limit_mb_by_kind": s.get("limit_mb_by_kind") or {},
                "key_field": s["key_field"]}
    h = imagehost.HOSTS.get(host) or {}
    kinds = ("image",) if host == "imgbb" else ("image", "video", "audio")
    return {"label": h.get("label") or host, "endpoint": h.get("endpoint") or "",
            "ttl": h.get("ttl") or "", "note": h.get("note") or "", "kinds": kinds,
            "limit_mb": imagehost.DEFAULT_MAX_MB, "limit_mb_by_kind": {}, "key_field": "imgbb_key"}


def _key_of(host: str, cfg: dict) -> str:
    if host in STATIONS:
        return str(cfg.get(STATIONS[host]["key_field"]) or "")
    return str(cfg.get("imgbb_key") or "")


def chain_for(kind: str, cfg: dict | None = None, target: str = "") -> list[str]:
    """按素材类型 + 目标挑候选链。

    · target 指定了具体站 → 只试它（用户明确要落哪个站就落哪个）
    · 否则按配置的顺序试；跳过「类型不支持」「没配 Key」的站
    """
    cfg = cfg or settings()
    if target:
        return [target] if host_meta(target) else []
    out = []
    for h in cfg.get("chain") or []:
        meta = host_meta(h)
        if not meta.get("label"):
            continue
        if kind not in meta.get("kinds", ("image",)):
            continue
        if h in STATIONS or imagehost.HOSTS.get(h, {}).get("needs_key"):
            if not _key_of(h, cfg):
                continue
        out.append(h)
    return out


def _limit_mb(host: str, kind: str) -> float:
    meta = host_meta(host)
    return float((meta.get("limit_mb_by_kind") or {}).get(kind) or meta.get("limit_mb") or 50)


def kind_of(mime: str) -> str:
    m = (mime or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith("video/"):
        return "video"
    if m.startswith("audio/"):
        return "audio"
    return "file"


# ------------------------------------------------------------------ 小工具

def _err(message: str, status: int, kind: str = "invalid_request_error"):
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def _sniff(raw: bytes) -> str | None:
    """嗅探文件头：先走 imagehost 的图片魔数，再补视频/音频常见魔术。"""
    mime = imagehost.sniff_mime(raw)
    if mime:
        return mime
    for sig, m in EXTRA_MAGIC:
        if raw.startswith(sig):
            return m
    if len(raw) > 12 and raw[:4] == b"RIFF":
        tag = raw[8:12]
        if tag == b"WAVE":
            return "audio/wav"
        if tag == b"AVI ":
            return "video/x-msvideo"
    if len(raw) > 12 and raw[4:8] == b"ftyp":
        brand = raw[8:12].lower()
        if brand.startswith(b"m4a") or brand.startswith(b"m4b"):
            return "audio/mp4"
        if brand.startswith(b"qt"):
            return "video/quicktime"
        return "video/mp4"
    return None


def detect(value: str) -> tuple[str, bytes, str | None]:
    """把 data URI / 裸 base64 解成 (mime, 原始字节, 警告)。"""
    v = (value or "").strip()
    if not v:
        raise imagehost.NotAnImage("素材内容为空")
    if v.lower().startswith("data:"):
        m = re.match(r"data:([^;,]*)((?:;[^;,]*)*),(.*)", v, re.S)
        if not m:
            raise imagehost.NotAnImage("data URI 格式不对（应形如 data:image/png;base64,XXXX）")
        declared = (m.group(1) or "").strip().lower()
        params = (m.group(2) or "").lower()
        payload = m.group(3)
        if "base64" in params:
            raw = base64.b64decode(re.sub(r"\s+", "", payload), validate=False)
        else:  # data:xxx,yyy 的 URL-encoded 形式
            raw = urllib.parse.unquote_to_bytes(payload)
        if not raw:
            raise imagehost.NotAnImage("data URI 里没有内容")
        if not declared or declared == "application/octet-stream":
            sn = _sniff(raw)
            if not sn:
                raise imagehost.NotAnImage("data URI 没声明类型，且嗅探不出文件类型")
            declared = sn
        return declared, raw, None
    if not re.fullmatch(r"[A-Za-z0-9+/=\s]+", v):
        raise imagehost.NotAnImage("素材既不是 http(s) 链接，也不是 base64 字符串")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", v), validate=False)
    except Exception as exc:
        raise imagehost.NotAnImage(f"不是合法的 base64 编码（{type(exc).__name__}），"
                                   "要么给完整 base64，要么直接给 http(s) 链接") from exc
    if not raw:
        raise imagehost.NotAnImage("base64 解码后为空")
    sn = _sniff(raw)
    if not sn:
        raise imagehost.NotAnImage(
            "裸 base64 嗅探不出文件类型：请改传 data URI（data:image/png;base64,...）"
            "或在 JSON 里额外给 mime 字段")
    return sn, raw, None


def _safe_name(name: str, mime: str, kind: str) -> str:
    """给素材起个安全文件名（上游按扩展名判类型，必须有正确后缀）。"""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name or ""))[:80]
    ext = os.path.splitext(name)[1].lower()
    want = imagehost.MIME_EXT.get((mime or "").lower())
    if not ext or (want and ext != want):
        name = (os.path.splitext(name)[0] or kind or "material") + (want or ".bin")
    return name


def _readback_ok(url: str, cfg: dict) -> tuple[bool, str]:
    """上传后回读校验（图片用魔数，其它类型只看能不能拿到字节）。"""
    if not cfg.get("verify", True):
        return True, ""
    try:
        with imagehost._client(cfg) as c:
            r = c.get(url, headers={"Range": "bytes=0-256"})
        if r.status_code >= 400:
            return False, f"回读 HTTP {r.status_code}"
        if not r.content:
            return False, "回读内容为空"
        return True, ""
    except Exception as exc:
        return False, f"回读失败：{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ 上传实现

def _outbound_desc(host: str, filename: str, mime: str, size: int, kind: str) -> dict:
    """出站请求描述（给面板还原「网关 → 站点」的 curl；凭据一律掩码）。"""
    meta = host_meta(host)
    headers = {"Authorization": "Bearer YOUR_UPSTREAM_KEY"}
    data = {"kind": kind} if host in STATIONS and host == "jiasu_media" else {}
    return {"method": "POST", "url": meta["endpoint"], "headers": headers,
            "multipart": [{"field": (STATIONS.get(host) or {}).get("field", "file"),
                           "filename": filename, "content_type": mime, "size": size}],
            "data": data, "host": host, "host_label": meta["label"]}


def _post_station(host: str, raw: bytes, mime: str, filename: str, kind: str,
                  cfg: dict) -> tuple[str, str]:
    """把素材 POST 到上游自托管文件站，返回 (公网直链, 站方给的过期说明)。"""
    s = STATIONS[host]
    key = _key_of(host, cfg)
    if not key:
        raise imagehost.UploadFailed(f"{s['label']}: 未配置 Key（面板「设置 → 素材中转」里填）")
    files = {s["field"]: (filename, raw, mime or "application/octet-stream")}
    data = {"kind": kind} if host == "jiasu_media" else None
    with imagehost._client(cfg) as c:
        r = c.post(s["endpoint"], headers={"Authorization": f"Bearer {key}"},
                   files=files, data=data)
    body = (r.text or "")[:800]
    if r.status_code >= 400:
        raise imagehost.UploadFailed(f"{s['label']}: HTTP {r.status_code} {body[:200]}")
    try:
        j = r.json()
    except Exception:
        raise imagehost.UploadFailed(f"{s['label']}: 返回不是 JSON：{body[:200]}")
    # 佳速：HTTP 恒 200，业务成败在 success 字段
    if host == "jiasu_media":
        if not j.get("success"):
            raise imagehost.UploadFailed(f"{s['label']}: {j.get('message') or 'success=false'}")
        items = ((j.get("data") or {}).get("items") or [])
        url = (items[0] or {}).get("url") if items else ((j.get("data") or {}).get("url"))
    else:
        url = j.get("url")
    if not imagehost.is_http(url or ""):
        raise imagehost.UploadFailed(f"{s['label']}: 响应里没有可用直链：{body[:200]}")
    # 速搭水会给 expiresAt（签名直链，实测约 2 小时）；佳速是长期 CDN
    exp = str(j.get("expiresAt") or j.get("expires_at") or "").strip()
    return str(url), exp


def upload_material(raw: bytes, mime: str, filename: str = "", cfg: dict | None = None,
                    target: str = "", trace: dict | None = None) -> tuple[str, str]:
    """按候选链上传，返回 (公网直链, 站点 id)；全挂抛 UploadFailed。

    trace 传入时会逐站写入尝试记录（面板「素材日志」用它还原完整转换链路）。
    """
    cfg = cfg or settings()
    kind = kind_of(mime)
    if not cfg.get("enabled", True):
        raise imagehost.UploadFailed("素材中转未启用（面板「设置 → 素材中转」里开启）")
    chain = chain_for(kind, cfg, target)
    if not chain:
        if target:
            raise imagehost.UploadFailed(
                f"指定的站点 {target} 不可用：类型 {kind} 不支持，或没配 Key（面板「设置 → 素材中转」）")
        raise imagehost.UploadFailed(
            "没有可用的素材站点：上游文件站没配 Key，且免费图床不在链路里（面板「设置 → 素材中转」）")
    size_mb = len(raw) / 1048576
    failures: list[str] = []
    for host in chain:
        limit = _limit_mb(host, kind)
        label = host_meta(host)["label"]
        rec: dict = {"host": host, "label": label, "kind": kind, "limit_mb": limit}
        if size_mb > limit:
            rec.update({"ok": False, "error": f"素材 {size_mb:.1f}MB 超过该站上限 {limit:g}MB"})
            failures.append(f"{label}: {rec['error']}")
            if trace is not None:
                trace.setdefault("attempts", []).append(rec)
            continue
        t0 = time.time()
        try:
            if host in STATIONS:
                url, exp_hint = _post_station(host, raw, mime, filename, kind, cfg)
            else:
                url, exp_hint = imagehost.upload(raw, mime, host, cfg), ""
        except imagehost.UploadFailed as exc:
            msg = str(exc)
            if msg.startswith(label):          # 站点实现里已经带了前缀，别再叠一层
                msg = msg.split(":", 1)[1].strip()
            rec.update({"ok": False, "ms": int((time.time() - t0) * 1000), "error": msg})
            failures.append(f"{label}: {msg}")
            if trace is not None:
                trace.setdefault("attempts", []).append(rec)
            continue
        ms = int((time.time() - t0) * 1000)
        ok, why = _readback_ok(url, cfg)
        if not ok:
            rec.update({"ok": False, "ms": ms, "url": url, "error": f"上传后回读校验失败（{why}）"})
            failures.append(f"{label}: 上传后回读校验失败（{why}）")
            if trace is not None:
                trace.setdefault("attempts", []).append(rec)
            continue
        rec.update({"ok": True, "status": 200, "ms": ms, "url": url})
        if exp_hint:
            rec["expires_at"] = exp_hint
            if trace is not None:
                trace["station_expires_at"] = exp_hint
        if trace is not None:
            trace.setdefault("attempts", []).append(rec)
        return url, host
    raise imagehost.UploadFailed("；".join(failures) or "所有站点都失败了")


def _expires_info(host: str, cfg: dict, station_expires: str = "",
                  attempts: list | None = None) -> tuple[str, int | None]:
    if host in STATIONS:
        if not station_expires:
            for a in (attempts or []):
                if a.get("host") == host and a.get("expires_at"):
                    station_expires = a["expires_at"]
        if station_expires:
            return f"{STATIONS[host]['ttl']}｜站方 expiresAt={station_expires}", None
        return STATIONS[host]["ttl"], None
    label = (imagehost.HOSTS.get(host) or {}).get("ttl") or ""
    if host == "litterbox":
        raw = str(cfg.get("litterbox_time") or "72h").strip().lower()
        m = re.match(r"(\d+)\s*([hdm])", raw)
        if m:
            n = int(m.group(1))
            secs = n * {"h": 3600, "d": 86400, "m": 60}[m.group(2)]
            return f"{raw}（{label}）", secs
    return label, HOST_TTL_SECONDS.get(host)


def _authorize(request: Request):
    """先走 relay 的三道门；再兜一层 ?token= / ?key=（有些客户端加不了请求头）。"""
    access, err = relay._authorize(request)
    if err is None:
        return access, None
    tok = (request.query_params.get("token") or request.query_params.get("key") or "").strip()
    if tok:
        master = os.environ.get("QLIKEAPI_UP_TOKEN", "")
        if master and hmac.compare_digest(tok, master):
            return {"kind": "internal", "name": "内部主密钥(query)", "id": None, "row": None}, None
        row = store.token_by_value(tok)
        if row and row.get("enabled"):
            return {"kind": "token", "name": row.get("name") or f"令牌#{row['id']}",
                    "id": row["id"], "row": row}, None
    return None, err


async def _read_input(request: Request) -> tuple[str, object, str, str]:
    """解析出入参：(kind, value|bytes, mime, filename)。kind ∈ value|bytes|url。

    注意：main.py 的中间件已经把 /v1/* 的 POST 请求体预解析进 `request.state.json_body`
    （multipart 的文件放在 `["__files"]`）。所以优先读它，读不到再自己解析 body。
    """
    ct = (request.headers.get("content-type") or "").lower()
    pre = getattr(request.state, "json_body", None)
    pre = pre if isinstance(pre, dict) else {}

    files = pre.get("__files")
    if isinstance(files, list) and files:
        _, v = files[0]
        raw = await v.read()                    # type: ignore[union-attr]
        ctype = str(getattr(v, "content_type", "") or "")
        return "multipart", bytes(raw), ctype.split(";")[0].strip(), str(getattr(v, "filename", "") or "")

    data: object = pre if pre else None
    if data is None and "application/json" in ct:
        try:
            data = json.loads((await request.body()) or b"{}")
        except Exception:
            raise ValueError("JSON 解析失败")
    if isinstance(data, str):
        data = {"data_url": data}
    if isinstance(data, dict) and data:
        for key in ("url", "link", "href", "image_url", "source_url"):
            v = data.get(key)
            if isinstance(v, str) and v.strip().lower().startswith(("http://", "https://")):
                return "url", v.strip(), "", ""
        for key in PAYLOAD_KEYS:
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                value = v.strip()
                if value.lower().startswith(("http://", "https://")):
                    return "url", value, "", ""
                mime = str(data.get("mime") or data.get("mime_type") or data.get("content_type") or "").strip()
                name = str(data.get("filename") or data.get("name") or "").strip()
                return "value", value, mime, name
        # 表单里只传了一个无名素材字段（urlencoded / multipart 的非文件字段）
        strings = [v for v in data.values() if isinstance(v, str) and v.strip()]
        if strings:
            value = strings[0].strip()
            if value.lower().startswith(("http://", "https://")):
                return "url", value, "", ""
            return "value", value, "", ""
        if "application/json" in ct or "multipart/form-data" in ct or "x-www-form-urlencoded" in ct:
            raise ValueError("请求里没找到素材：支持文件字段、base64/data URI，或 url / data_url / data / base64 / image / file / content 字段")

    if "multipart/form-data" in ct or "application/x-www-form-urlencoded" in ct:
        form = await request.form()
        for _, v in form.multi_items():
            fname = getattr(v, "filename", None)
            if fname:
                raw = await v.read()            # type: ignore[union-attr]
                ctype = str(getattr(v, "content_type", "") or "")
                return "multipart", bytes(raw), ctype.split(";")[0].strip(), str(fname)
        for _, v in form.multi_items():
            if isinstance(v, str) and v.strip():
                value = v.strip()
                if value.lower().startswith(("http://", "https://")):
                    return "url", value, "", ""
                return "value", value, "", ""
        raise ValueError("multipart 里没有文件字段，也没有 base64/链接字段")
    raw = await request.body()
    if raw:
        return "bytes", raw, ct.split(";")[0].strip(), ""
    raise ValueError("请求体为空：请用 multipart 传文件、JSON 传 base64/链接，或直接 POST 字节")


def _resolve_target(request: Request, pre_data: dict) -> str:
    """取客户端指定的目标站点（query 优先，其次 JSON 里的 target / host / station）。"""
    for src in (request.query_params.get("target"), request.query_params.get("host"),
                request.query_params.get("station"),
                pre_data.get("target") if isinstance(pre_data, dict) else None,
                pre_data.get("station") if isinstance(pre_data, dict) else None):
        if isinstance(src, str) and src.strip():
            return TARGET_ALIASES.get(src.strip().lower(), src.strip().lower())
    return ""


# ------------------------------------------------------------------ 接口

def _logs_payload(kind: str, mime: str, size: int, filename: str, source: str,
                  target: str, cfg: dict) -> dict:
    """客户端 → 网关 的请求留痕（base64 已被 store 的 compact_b64 压成占位）。"""
    return {"kind": source, "source_label": SOURCE_LABELS.get(source, source),
            "mime": mime, "bytes": size, "filename": filename or None,
            "kind_of": kind_of(mime), "target": target or "auto",
            "chain": chain_for(kind_of(mime), cfg, target),
            "limit_mb": MAX_UPLOAD_MB}


@router.post("/files")
async def upload_file(request: Request):
    started = time.time()
    access, err = _authorize(request)
    if err is not None:
        return err
    try:
        source, value, mime, filename = await _read_input(request)
    except ValueError as exc:
        return _err(str(exc), 400)
    as_text = (request.query_params.get("format") or "").lower() in ("text", "plain", "url", "string")
    cfg = settings()
    token_name = (access or {}).get("name")
    token_id = (access or {}).get("id")
    pre = getattr(request.state, "json_body", None)
    target = _resolve_target(request, pre if isinstance(pre, dict) else {})

    # ① 已经是公网直链 → 原样返回（不转存、不浪费站点额度）
    if source == "url":
        url = str(value)
        if not imagehost.is_public_url(url):
            return _err(f"只接受公网 http(s) 素材直链，收到：{url[:80]}", 400)
        trace = {"mode": "material", "source": source, "passthrough": True, "url": url,
                 "target": target or "auto", "attempts": []}
        body = {"ok": True, "url": url, "link": url, "data": {"url": url},
                "host": "passthrough", "host_label": "原样透传", "mime": "",
                "bytes": None, "expires": "", "expires_in": None, "token": token_name,
                "attempts": []}
        store.log_row(provider="material", model="(透传)", path="/v1/files", status=200,
                      up_status=None, ms=int((time.time() - started) * 1000), error=None,
                      req={"kind": "url", "url": url, "target": target or "auto"}, up_req=None,
                      snippet=url, kind="upload", token=token_name, token_id=token_id,
                      imagehost=trace)
        return PlainTextResponse(url) if as_text else JSONResponse(body)

    # ② base64 / data URI / 裸字节 / multipart 文件 → 落站取直链
    try:
        if source in ("bytes", "multipart"):
            raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
            if not mime or mime.startswith("application/octet-stream"):
                mime = _sniff(raw) or mime or "application/octet-stream"
            note = None
        else:
            mime, raw, note = detect(str(value))
            if mime == "application/octet-stream":
                sn = _sniff(raw)
                if sn:
                    mime = sn
    except imagehost.NotAnImage as exc:
        return _err(f"素材无法识别：{exc}", 400)
    except Exception as exc:
        return _err(f"素材解码失败：{type(exc).__name__}: {exc}", 400)

    if not raw:
        return _err("素材内容为空", 400)
    limit = int(MAX_UPLOAD_MB * 1024 * 1024)
    if len(raw) > limit:
        return _err(f"素材 {len(raw) / 1048576:.1f}MB 超过上限 {MAX_UPLOAD_MB:g}MB", 413,
                    "invalid_request_error")

    kind = kind_of(mime)
    filename = _safe_name(filename or "", mime, kind)
    trace: dict = {"mode": "material", "source": source, "filename": filename, "mime": mime,
                   "bytes": len(raw), "kind_of": kind, "target": target or "auto",
                   "chain": chain_for(kind, cfg, target), "attempts": []}
    req_log = _logs_payload(kind, mime, len(raw), filename, source, target, cfg)
    try:
        url, host = upload_material(raw, mime, filename, cfg, target, trace)
    except imagehost.UploadFailed as exc:
        trace["ok"] = False
        store.log_row(provider="material", model=f"({kind})", path="/v1/files", status=503,
                      up_status=None, ms=int((time.time() - started) * 1000), error=str(exc),
                      req=req_log, up_req=None, snippet=None, kind="upload",
                      token=token_name, token_id=token_id, imagehost=trace)
        return _err(f"素材上传失败：{exc}", 503, "upstream_error")

    expires, expires_in = _expires_info(host, cfg, "", trace.get("attempts"))
    meta = host_meta(host)
    trace.update({"ok": True, "host": host, "host_label": meta["label"], "url": url,
                  "expires": expires, "ttl_seconds": expires_in})
    attempts = trace.get("attempts") or []
    warnings = [f"{a['label']}: {a.get('error')}" for a in attempts if not a.get("ok")]
    body = {"ok": True, "url": url, "link": url, "data": {"url": url},
            "host": host, "host_label": meta["label"], "mime": mime, "kind": kind,
            "bytes": len(raw), "filename": filename or None,
            "expires": expires, "expires_in": expires_in, "token": token_name,
            "target": target or "auto", "attempts": attempts}
    if warnings:
        body["warnings"] = warnings
    if note:
        body["note"] = note
    store.log_row(provider="material", model=f"({kind})", path="/v1/files", status=200,
                  up_status=200, ms=int((time.time() - started) * 1000), error=None,
                  req=req_log, up_req=_outbound_desc(host, filename, mime, len(raw), kind),
                  snippet=url, kind="upload", images=1 if kind == "image" else None,
                  token=token_name, token_id=token_id, up_url=meta["endpoint"], up_method="POST",
                  up_headers=_outbound_desc(host, filename, mime, len(raw), kind)["headers"],
                  imagehost=trace)
    return PlainTextResponse(url) if as_text else JSONResponse(body)
