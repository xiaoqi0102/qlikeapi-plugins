"""素材上传中转：base64 / data URI / 本地文件 → 公网直链。

为什么存在这一层
----------------
部分视频上游（速搭水 / MeAICC 等）**只接受公网 http(s) 素材**，由上游服务端自己去抓，
base64 / 本地文件一律不收。而 New API 的 Task Plugin 沙箱里，插件自己**不能发 HTTP**
（只能声明一个请求描述符交给宿主代发），所以「提交时自动把 base64 转直链」在插件里做不到 ——
转换必须发生在网关这一层。

客户端只要把「参考素材中转 / 自定义上传接口」指向这里的 `POST /v1/files`，
本地文件或 base64 就会先变成公网直链，再拿去提交视频任务，对最终用户是透明的。

用法
----
    POST /v1/files
    Authorization: Bearer <本服务令牌>        # 也支持 ?token=<令牌>（有些客户端加不了请求头）
    Content-Type: multipart/form-data         # ① 直接传文件（字段名随意）
    Content-Type: application/json            # ② {"data_url":"data:image/png;base64,..."} / {"url":"https://..."}
    Content-Type: image/png（裸字节）          # ③ 直接 POST 字节

    返回 {"ok":true,"url":"https://...","host":"litterbox","mime":"image/png","bytes":1234,
          "expires":"72h","expires_in":259200,"data":{"url":"..."}}
    ?format=text → 只回纯文本 URL（给只认纯文本的客户端）

铁律
----
  · 只上传「客户端手里」的素材（base64 / data URI / 裸字节）；已经是 http(s) 的一律原样返回。
  · 素材不落本机磁盘，只落第三方图床；链路与凭据复用 imagehost.py（ImgBB Key 在服务端加密落库）。
  · 图床未启用 / 无可用链路 / 全部失败 → 明确报错，绝不静默成功。
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

from . import imagehost, relay, store

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


def _chain_for(mime: str, cfg: dict) -> list[str]:
    """按素材类型挑图床链：ImgBB 只接图片，视频/音频交给 Litterbox / Uguu。"""
    chain = imagehost.chain_of(cfg)
    if not (mime or "").startswith("image/"):
        chain = [h for h in chain if h != "imgbb"]
    return chain


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


def upload_material(raw: bytes, mime: str, cfg: dict | None = None) -> tuple[str, str]:
    """按候选链上传，返回 (公网直链, 图床名)；全挂抛 UploadFailed。"""
    cfg = cfg or imagehost.settings()
    if not cfg.get("enabled"):
        raise imagehost.UploadFailed("图床未启用（面板「设置 → 图床」里开启后可上传素材）")
    chain = _chain_for(mime, cfg)
    if not chain:
        raise imagehost.UploadFailed("没有可用的图床链路（ImgBB 未配 Key，且 Litterbox/Uguu 不在链路里）")
    failures: list[str] = []
    for host in chain:
        try:
            url = imagehost.upload(raw, mime, host, cfg)
        except imagehost.UploadFailed as exc:
            failures.append(f"{imagehost.HOSTS[host]['label']}: {exc}")
            continue
        ok, why = _readback_ok(url, cfg)
        if not ok:
            failures.append(f"{imagehost.HOSTS[host]['label']}: 上传后回读校验失败（{why}）")
            continue
        return url, host
    raise imagehost.UploadFailed("；".join(failures) or "所有图床都失败了")


def _expires_info(host: str, cfg: dict) -> tuple[str, int | None]:
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
        return "bytes", bytes(raw), ctype.split(";")[0].strip(), str(getattr(v, "filename", "") or "")

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
                return "bytes", bytes(raw), ctype.split(";")[0].strip(), str(fname)
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


# ------------------------------------------------------------------ 接口

@router.post("/files")
async def upload_file(request: Request):
    started = time.time()
    access, err = _authorize(request)
    if err is not None:
        return err
    try:
        kind, value, mime, filename = await _read_input(request)
    except ValueError as exc:
        return _err(str(exc), 400)
    as_text = (request.query_params.get("format") or "").lower() in ("text", "plain", "url", "string")
    cfg = imagehost.settings()
    token_name = (access or {}).get("name")
    token_id = (access or {}).get("id")

    # ① 已经是公网直链 → 原样返回（不转存、不浪费图床额度）
    if kind == "url":
        url = str(value)
        if not imagehost.is_public_url(url):
            return _err(f"只接受公网 http(s) 素材直链，收到：{url[:80]}", 400)
        body = {"ok": True, "url": url, "link": url, "data": {"url": url},
                "host": "passthrough", "host_label": "原样透传", "mime": "",
                "bytes": None, "expires": "", "expires_in": None, "token": token_name}
        store.log_row(provider="imagehost", model="(upload)", path="/v1/files", status=200,
                      up_status=None, ms=int((time.time() - started) * 1000), error=None,
                      req={"kind": "url", "url": url}, up_req=None, snippet=url,
                      kind="upload", token=token_name, token_id=token_id,
                      imagehost={"host": "passthrough", "url": url})
        return PlainTextResponse(url) if as_text else JSONResponse(body)

    # ② base64 / data URI / 裸字节 → 落图床取直链
    try:
        if kind == "bytes":
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
        return _err(f"素材 {len(raw) / 1048576:.1f}MB 超过上限 {MAX_UPLOAD_MB:g}MB", 413, "invalid_request_error")

    try:
        url, host = upload_material(raw, mime, cfg)
    except imagehost.UploadFailed as exc:
        store.log_row(provider="imagehost", model="(upload)", path="/v1/files", status=503,
                      up_status=None, ms=int((time.time() - started) * 1000), error=str(exc),
                      req={"kind": kind, "mime": mime, "bytes": len(raw), "filename": filename},
                      up_req=None, snippet=None, kind="upload",
                      token=token_name, token_id=token_id)
        return _err(f"素材上传失败：{exc}", 503, "upstream_error")

    expires, expires_in = _expires_info(host, cfg)
    body = {"ok": True, "url": url, "link": url, "data": {"url": url},
            "host": host, "host_label": (imagehost.HOSTS.get(host) or {}).get("label") or host,
            "mime": mime, "bytes": len(raw), "filename": filename or None,
            "expires": expires, "expires_in": expires_in, "token": token_name}
    if note:
        body["note"] = note
    store.log_row(provider="imagehost", model="(upload)", path="/v1/files", status=200,
                  up_status=200, ms=int((time.time() - started) * 1000), error=None,
                  req={"kind": kind, "mime": mime, "bytes": len(raw), "filename": filename},
                  up_req=None, snippet=url, kind="upload", images=1,
                  token=token_name, token_id=token_id,
                  imagehost={"host": host, "url": url, "mime": mime})
    return PlainTextResponse(url) if as_text else JSONResponse(body)
