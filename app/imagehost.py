"""imagehost.py —— 参考图 base64 → 公网直链（免费图床适配层）。

**为什么要这一层**：有些上游只认公网 URL（典型：七牛的 fal 异步队列，它只拉 URL，
不接受 base64 / 本地文件）。客户端如果直接把参考图以 base64 / data URI 传进来，
网关就得先把它换成一条公网直链，再交给上游。

**口径来源**：`盐值AI免费图床对接说明`（2026-09-18 实测版）。要点照抄，不猜：
  · Uguu      POST https://uguu.se/upload.php            字段 files[]（带方括号）
              返回 JSON，URL 在 files[0].url；**返回域名在 d./h./n. 之间轮换，禁止硬编码 hostname**
              有效期 ≈3 小时
  · Litterbox POST .../litterbox.catbox.moe/resources/internals/api.php
              **必须带 reqtype=fileupload**，另有 time=1h|12h|24h|72h，字段 fileToUpload
              返回纯文本 URL；最长 72 小时
  · ImgBB     POST https://api.imgbb.com/1/upload?key=<KEY>  Key 放 query，字段 image（base64）
              仅图片；长期有效
  · Catbox    POST https://catbox.moe/user/api.php  reqtype=fileupload + fileToUpload
              返回纯文本 URL；长期 —— 文档实测「本机不可达」，故默认不启用
  · 0x0.st    POST https://0x0.st  字段 file —— 文档实测「服务端已关闭上传」，故默认不启用

**硬约束**：
  · 图片**不落盘、不转存**到本机磁盘；上传产物只在第三方图床，且只在上游任务完成前有效。
  · 只上传**本地参考图**（base64 / data URI）。客户端本来就给 http(s) 链接的，一律原样透传。
  · 缺 Key / 未启用的图床直接跳过，不报错也不白等超时。
  · 凭据（ImgBB Key）走 crypto 加密落库，**绝不进日志、绝不进仓库**。
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import urllib.parse
import uuid

import httpx

from . import crypto, store

UA = "qlikeapi-plugins/1.0 (+https://github.com/xiaoqi0102/qlikeapi-plugins)"

# 图床登记表：label 显示用，ttl 是有效期（面板上给用户看），needs_key 表示必须配 Key
HOSTS: dict[str, dict] = {
    "imgbb": {"label": "ImgBB", "endpoint": "https://api.imgbb.com/1/upload",
              "ttl": "长期（可设自动过期）", "needs_key": True,
              "note": "仅图片；Key 放 query 参数，图片以 base64 提交"},
    "litterbox": {"label": "Litterbox", "endpoint": "https://litterbox.catbox.moe/resources/internals/api.php",
                  "ttl": "1h / 12h / 24h / 72h 可选", "needs_key": False,
                  "note": "必须带 reqtype=fileupload；返回纯文本 URL"},
    "uguu": {"label": "Uguu", "endpoint": "https://uguu.se/upload.php",
             "ttl": "约 3 小时", "needs_key": False,
             "note": "字段名是 files[]（带方括号）；返回域名在 d./h./n. 之间轮换"},
    "catbox": {"label": "Catbox", "endpoint": "https://catbox.moe/user/api.php",
               "ttl": "长期", "needs_key": False,
               "note": "文档实测本机网络不可达（TLS 重置）→ 默认不启用"},
    "zero_x0": {"label": "0x0.st", "endpoint": "https://0x0.st",
                "ttl": "数十天（按体积）", "needs_key": False,
                "note": "文档实测服务端已关闭上传（503 公告）→ 默认不启用"},
}

DEFAULT_CHAIN = ["imgbb", "litterbox", "uguu"]
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_MB = 20

# 测试钩子：单测里塞 httpx.MockTransport 进来，保证零网络
TRANSPORT: httpx.BaseTransport | None = None

EXT_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif",
    ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff", ".heic": "image/heic",
}
MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
            "image/gif": ".gif", "image/avif": ".avif", "image/bmp": ".bmp",
            "image/tiff": ".tiff", "image/heic": ".heic"}

MAGIC = [(b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
         (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"), (b"BM", "image/bmp")]

PRIVATE_HOSTS = {"localhost", "localhost.localdomain", "::1"}

# 面板「上传自检」用的一张 1×1 透明 PNG（真实可解码，不是占位符）
TEST_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")


class NotAnImage(ValueError):
    """客户端给的参考图既不是图片、也不是 http(s) 链接。"""


class UploadFailed(RuntimeError):
    """所有候选图床都失败（错误明细在 args[0]）。"""


# ------------------------------------------------------------------ 配置

DEFAULTS = {
    "enabled": False,
    "chain": list(DEFAULT_CHAIN),
    "imgbb_key": "",
    "litterbox_time": "72h",
    "max_mb": DEFAULT_MAX_MB,
    "timeout_s": DEFAULT_TIMEOUT,
    "verify": True,
}


def settings() -> dict:
    """读图床配置（Key 加密落库，读出来是明文，只在内存里用）。"""
    raw = store.get_settings("imagehost") or {}
    cfg = dict(DEFAULTS)
    for k, v in raw.items():
        if k == "imgbb_key":
            continue
        cfg[k] = v
    cfg["imgbb_key"] = crypto.decrypt(raw.get("imgbb_key")) or ""
    if not isinstance(cfg.get("chain"), list):
        cfg["chain"] = list(DEFAULT_CHAIN)      # 只有「从未配置过」才给默认链；
                                                # 用户手动清空 = 明确不要图床，别偷偷塞回来
    cfg["chain"] = [h for h in cfg["chain"] if h in HOSTS]
    return cfg


def save_settings(d: dict) -> dict:
    """写图床配置；imgbb_key 为空字符串时表示「保持原值不变」。"""
    cur = store.get_settings("imagehost") or {}
    out = dict(cur)
    for k in ("enabled", "litterbox_time", "max_mb", "timeout_s", "verify"):
        if k in d:
            out[k] = d[k]
    if "chain" in d:
        out["chain"] = [h for h in (d["chain"] or []) if h in HOSTS]
    if d.get("imgbb_key"):
        out["imgbb_key"] = crypto.encrypt(str(d["imgbb_key"]).strip())
    elif d.get("imgbb_key_clear"):
        out["imgbb_key"] = ""
    store.set_settings("imagehost", out)
    return settings()


def chain_of(cfg: dict | None = None) -> list[str]:
    """候选链：只保留登记表里有、且没被标记停用的（ImgBB 必须已配 Key）。"""
    cfg = cfg or settings()
    out = []
    for h in cfg.get("chain") or []:
        if h not in HOSTS:
            continue
        if HOSTS[h]["needs_key"] and not cfg.get("imgbb_key"):
            continue
        out.append(h)
    return out


# ------------------------------------------------------------------ 识别 / 校验

def is_http(u: str) -> bool:
    return isinstance(u, str) and u.lower().startswith(("http://", "https://"))


def detect(value: str) -> tuple[str, bytes]:
    """把「data URI / 裸 base64」解成 (mime, 原始字节)。已是 http 链接的由调用方先行过滤。"""
    v = (value or "").strip()
    if not v:
        raise NotAnImage("参考图为空")
    if v.startswith("data:"):
        m = re.match(r"data:([^;,]+);base64,(.*)", v, re.S)
        if not m:
            raise NotAnImage("data URI 里没有 base64 内容")
        mime = (m.group(1) or "image/png").strip().lower()
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=False)
        except Exception as exc:
            raise NotAnImage(f"base64 解码失败：{exc}") from exc
    else:
        if not re.fullmatch(r"[A-Za-z0-9+/=\s]+", v):
            raise NotAnImage("参考图既不是 http(s) 链接，也不是 base64 图片")
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", v), validate=False)
        except Exception as exc:
            raise NotAnImage(f"base64 解码失败：{exc}") from exc
        mime = sniff_mime(raw) or "image/png"
    if not raw:
        raise NotAnImage("参考图内容为空")
    if not mime.startswith("image/") and not sniff_mime(raw):
        raise NotAnImage(f"只支持图片参考图，收到 {mime}")
    return (mime if mime.startswith("image/") else (sniff_mime(raw) or "image/png")), raw


def sniff_mime(raw: bytes) -> str | None:
    for sig, mime in MAGIC:
        if raw.startswith(sig):
            return mime
    if raw[4:12] == b"ftypavif" or raw[8:12] == b"avif":
        return "image/avif"
    if len(raw) > 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def ext_of(mime: str) -> str:
    return MIME_EXT.get((mime or "").lower(), ".png")


def is_public_url(u: str) -> bool:
    """只认公网 http(s) 直链：拒内网 / 回环 / 链路本地（免得把上游引到我们内网）。"""
    try:
        sp = urllib.parse.urlsplit(u)
    except Exception:
        return False
    if sp.scheme not in ("http", "https") or not sp.hostname:
        return False
    host = sp.hostname.lower()
    if host in PRIVATE_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass
    return True


# ------------------------------------------------------------------ 返回解析

URL_KEYS = ("url", "imageurl", "image_url", "downloadurl", "download_url",
            "link", "src", "display_url", "direct_url")


def find_url(obj, depth: int = 5) -> str | None:
    """在任意 JSON 结构里递归挖第一条 http(s) 直链（优先常见键名）。"""
    if depth <= 0:
        return None
    if isinstance(obj, str):
        s = obj.strip()
        return s if is_http(s) else None
    if isinstance(obj, list):
        for it in obj:
            got = find_url(it, depth - 1)
            if got:
                return got
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in URL_KEYS and isinstance(v, str) and is_http(v.strip()):
                return v.strip()
        for v in obj.values():
            got = find_url(v, depth - 1)
            if got:
                return got
    return None


def parse_returned_url(text: str, endpoint: str = "") -> str | None:
    """双模解析：① 整体就是一条 URL（litterbox / catbox / 0x0）② 否则递归挖 JSON（Uguu 的 files[0].url）。

    注意 Uguu 的返回里 URL 可能是**被转义过的 JSON 字符串**，所以先尝试反解一层。
    """
    s = (text or "").strip()
    if not s:
        return None
    if is_http(s) and "\n" not in s:
        return s
    cand = s
    try:
        got = find_url(json.loads(s))
        if got:
            return got
    except Exception:
        pass
    if s.startswith('"') and s.endswith('"'):
        try:
            cand = json.loads(s)
        except Exception:
            cand = s.strip('"')
        if is_http(cand.strip()):
            return cand.strip()
    m = re.search(r"\{.*\}", cand, re.S)
    if not m:
        return None
    try:                                   # 被转义/带前后缀时，先还原成 JSON 再挖
        return find_url(json.loads(m.group(0)))
    except Exception:
        return None


# ------------------------------------------------------------------ 上传

def _client(cfg: dict) -> httpx.Client:
    t = float(cfg.get("timeout_s") or DEFAULT_TIMEOUT)
    return httpx.Client(timeout=t, follow_redirects=True, transport=TRANSPORT,
                        headers={"User-Agent": UA})


def _file_entry(raw: bytes, mime: str, name: str) -> tuple[str, bytes, str]:
    return (name, raw, mime or "application/octet-stream")


def upload(raw: bytes, mime: str, host: str, cfg: dict | None = None,
           client: httpx.Client | None = None) -> str:
    """上传单张图到指定图床，返回公网直链；失败抛异常（由上层决定降级还是报错）。"""
    cfg = cfg or settings()
    meta = HOSTS.get(host)
    if not meta:
        raise UploadFailed(f"未知图床 {host}")
    if meta["needs_key"] and not cfg.get("imgbb_key"):
        raise UploadFailed(f"{meta['label']} 未配置 Key")
    name = f"ref-{uuid.uuid4().hex[:16]}{ext_of(mime)}"
    ep = meta["endpoint"]
    own = client is None
    c = client or _client(cfg)
    try:
        if host == "imgbb":
            r = c.post(ep, params={"key": cfg["imgbb_key"]},
                       data={"image": base64.b64encode(raw).decode()})
        elif host == "litterbox":
            # 字段顺序照抄文档：reqtype → time → fileToUpload
            r = c.post(ep, data={"reqtype": "fileupload",
                                 "time": str(cfg.get("litterbox_time") or "72h")},
                       files={"fileToUpload": _file_entry(raw, mime, name)})
        elif host == "uguu":
            r = c.post(ep, files={"files[]": _file_entry(raw, mime, name)})
        elif host == "catbox":
            r = c.post(ep, data={"reqtype": "fileupload"},
                       files={"fileToUpload": _file_entry(raw, mime, name)})
        elif host == "zero_x0":
            r = c.post(ep, files={"file": _file_entry(raw, mime, name)})
        else:
            raise UploadFailed(f"图床 {host} 未实现")
        if r.status_code >= 400:
            raise UploadFailed(f"HTTP {r.status_code} {r.text[:160]}")
        url = parse_returned_url(r.text, ep)
        if not url or not is_public_url(url):
            raise UploadFailed(f"返回里没找到公网直链：{r.text[:160]}")
        return url
    except UploadFailed:
        raise
    except Exception as exc:
        raise UploadFailed(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if own:
            c.close()


def verify_url(url: str, cfg: dict | None = None) -> tuple[bool, str]:
    """上传后二次校验：GET 回来 + 按文件头魔数嗅探（防「上传成功但上游拉不到」）。"""
    cfg = cfg or settings()
    if not cfg.get("verify", True):
        return True, ""
    try:
        with _client(cfg) as c:
            r = c.get(url, headers={"Range": "bytes=0-64"})
        if r.status_code >= 400:
            return False, f"回读 HTTP {r.status_code}"
        if not sniff_mime(r.content):
            return False, "回读内容不是图片（可能已被拦截）"
        return True, ""
    except Exception as exc:
        return False, f"回读失败：{type(exc).__name__}: {exc}"


def upload_with_fallback(raw: bytes, mime: str, cfg: dict | None = None,
                         chain: list[str] | None = None) -> tuple[str, str, list[str]]:
    """按候选链逐家试，第一个成功就返回 (url, host, failures)。全挂抛 UploadFailed。"""
    cfg = cfg or settings()
    chain = chain or chain_of(cfg)
    if not chain:
        raise UploadFailed("没有可用的图床（图床未启用或 ImgBB 未配 Key）")
    failures: list[str] = []
    for host in chain:
        try:
            url = upload(raw, mime, host, cfg)
            ok, why = verify_url(url, cfg)
            if not ok:
                failures.append(f"{HOSTS[host]['label']}: 上传后回读校验失败（{why}）")
                continue
            return url, host, failures
        except UploadFailed as exc:
            failures.append(f"{HOSTS[host]['label']}: {exc}")
    raise UploadFailed("；".join(failures) or "所有图床都失败了")


# ------------------------------------------------------------------ 与请求体对接

def ensure_refs(refs: list[str], policy: str, cfg: dict | None = None,
                edit: bool = False) -> tuple[list[str], list[str], list[dict]]:
    """把请求体里的参考图按渠道能力换成公网直链。

    policy：url = 只认公网 URL（换不了就报错）；both = 优先公网 URL（换不了回落 base64）；
            base64 = 只认 base64（不动）。
    返回 (换好的 refs, 失败明细, 转换说明)；失败明细非空且 policy=url 时由上层报 400。
    """
    cfg = cfg or settings()
    notes: list[dict] = []
    if policy == "base64" or not refs:
        return list(refs), [], notes
    if not cfg.get("enabled"):
        if policy == "url" and any(not is_http(r) for r in refs):
            raise UploadFailed("参考图是 base64，但目标渠道只认公网 URL，而图床未启用"
                               "（面板「设置 → 图床」开启后可自动转换）")
        return list(refs), [], notes
    max_bytes = int(cfg.get("max_mb") or DEFAULT_MAX_MB) * 1024 * 1024
    cache: dict[str, tuple[str, str]] = {}
    failures: list[str] = []
    out: list[str] = []
    for r in refs:
        if is_http(r):
            out.append(r)                      # 客户端本来就给链接 → 一律不上传
            continue
        try:
            mime, raw = detect(r)
            if len(raw) > max_bytes:
                raise UploadFailed(f"参考图 {len(raw) / 1048576:.1f}MB 超过上限 "
                                   f"{cfg.get('max_mb')}MB")
            key = hashlib.sha256(raw).hexdigest()
            if key not in cache:
                url, host, soft = cache[key] = upload_with_fallback(raw, mime, cfg)
                notes.append({"host": host, "mime": mime, "bytes": len(raw),
                              "url": url, "warnings": soft})
            url, host = cache[key][0], cache[key][1]
            out.append(url)
        except (UploadFailed, NotAnImage) as exc:
            failures.append(str(exc))
            out.append(r)
    if failures and policy == "url":
        raise UploadFailed("；".join(failures))
    if failures:
        notes.append({"fallback": True, "warnings": failures})
    return out, failures, notes


def apply_to_body(body: dict, policy: str, cfg: dict | None = None,
                  edit: bool = False) -> tuple[dict, list[str], list[dict]]:
    """就地把 body 里所有参考图字段的 base64 换成图床直链（保持原字段结构）。

    替换按**值**匹配（原值 → 新值），不按位置：同一个字段里混着 URL、base64、
    甚至非图片字符串时也不会串位。
    """
    from . import utils
    if policy == "base64":
        return body, [], []
    refs = utils.collect_refs(body)
    if not refs:
        return body, [], []
    new_refs, failures, notes = ensure_refs(refs, policy, cfg, edit)
    mapping = {o: n for o, n in zip(refs, new_refs, strict=False) if o != n}
    if not mapping:
        return body, failures, notes
    for field in utils.REF_FIELDS:
        v = body.get(field)
        if v is None:
            continue
        if isinstance(v, str):
            body[field] = mapping.get(v, v)
        elif isinstance(v, list):
            out = []
            for it in v:
                if isinstance(it, str):
                    out.append(mapping.get(it, it))
                elif isinstance(it, dict):
                    it = dict(it)
                    for k, vv in list(it.items()):
                        if isinstance(vv, str) and vv in mapping:
                            it[k] = mapping[vv]
                    out.append(it)
                else:
                    out.append(it)
            body[field] = out
    return body, failures, notes
    idx = 0
    for field in utils.REF_FIELDS:
        v = body.get(field)
        if v is None:
            continue
        if isinstance(v, list):
            items = []
            for it in v:
                if isinstance(it, dict):
                    for k in ("image_url", "url", "data"):
                        if isinstance(it.get(k), str) and idx < len(new_refs):
                            it[k] = new_refs[idx]
                            idx += 1
                            break
                    items.append(it)
                else:
                    items.append(new_refs[idx] if idx < len(new_refs) else it)
                    idx += 1
            body[field] = items
        elif isinstance(v, str):
            if idx < len(new_refs):
                body[field] = new_refs[idx]
                idx += 1
    return body, failures, notes
