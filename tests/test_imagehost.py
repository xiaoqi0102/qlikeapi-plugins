"""图床适配层单测：**全零网络**（httpx.MockTransport 拦掉一切真实请求）。

覆盖口径来源：`盐值AI免费图床对接说明`（2026-09-18 实测版）—— 字段名、端点、
返回形态、有效期约束都按那份文档写死，改代码前先看这里。
"""
from __future__ import annotations

import base64
import json
import urllib.parse

import httpx
import pytest

from app import imagehost as ih

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)          # 够像 PNG 的最小字节串
PNG_B64 = base64.b64encode(PNG).decode()
DATA_URI = "data:image/png;base64," + PNG_B64


@pytest.fixture()
def transport(monkeypatch):
    """装一个假图床：记录请求，按 host 返回各家真实形态的响应。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen.append({"url": str(request.url), "method": request.method,
                     "body": body, "headers": dict(request.headers)})
        if request.method == "GET":                    # 上传后回读校验
            return httpx.Response(200, content=PNG)
        u = str(request.url)
        if "imgbb" in u:
            return httpx.Response(200, json={"data": {"url": "https://i.ibb.co/abc/ref.png",
                                                      "image": {"url": "https://i.ibb.co/abc/ref.png"}}})
        if "litterbox" in u:
            return httpx.Response(200, text="https://litter.catbox.moe/ref.png")
        if "uguu" in u:
            return httpx.Response(200, json={"success": True,
                                             "files": [{"url": "https://d.uguu.se/ref.png"}]})
        if "catbox" in u:
            return httpx.Response(200, text="https://files.catbox.moe/ref.png")
        return httpx.Response(200, text="https://0x0.st/ref.png")

    monkeypatch.setattr(ih, "TRANSPORT", httpx.MockTransport(handler))
    return seen


def _cfg(**kw):
    cfg = dict(ih.DEFAULTS)
    cfg.update({"enabled": True, "imgbb_key": "test-key", "verify": False})
    cfg.update(kw)
    return cfg


# ------------------------------------------------------------------ 识别与校验

def test_detect_data_uri_and_bare_base64():
    mime, raw = ih.detect(DATA_URI)
    assert mime == "image/png" and raw == PNG
    mime2, raw2 = ih.detect(PNG_B64)
    assert mime2 == "image/png" and raw2 == PNG


def test_detect_rejects_junk_and_empty():
    for bad in ("", "   ", "hello world", "data:text/plain;base64,aGVsbG8="):
        with pytest.raises(ih.NotAnImage):
            ih.detect(bad)


def test_is_public_url_blocks_private_and_loopback():
    assert ih.is_public_url("https://d.uguu.se/x.png")
    assert ih.is_public_url("http://example.com/x.png")
    for bad in ("http://localhost/x.png", "http://127.0.0.1:8000/x.png",
                "http://10.0.0.5/x.png", "http://192.168.1.5/x.png",
                "http://172.16.3.4/x.png", "http://169.254.1.1/x.png",
                "file:///tmp/x.png", "ftp://example.com/x.png", "not a url"):
        assert not ih.is_public_url(bad), bad


# ------------------------------------------------------------------ 返回解析（双模）

def test_parse_plain_text_url():
    assert ih.parse_returned_url("https://litter.catbox.moe/ref.png\n") == "https://litter.catbox.moe/ref.png"


def test_parse_uguu_json_and_escaped_json():
    """Uguu 返回 JSON，URL 在 files[0].url；有时是被转义过的 JSON 字符串。"""
    assert ih.parse_returned_url('{"success":true,"files":[{"url":"https://d.uguu.se/a.png"}]}') \
        == "https://d.uguu.se/a.png"
    esc = json.dumps('{"files":[{"url":"https://h.uguu.se/b.png"}]}')
    assert ih.parse_returned_url(esc) == "https://h.uguu.se/b.png"


def test_parse_garbage_returns_none():
    assert ih.parse_returned_url("") is None
    assert ih.parse_returned_url("oops") is None
    assert ih.parse_returned_url('{"error":"quota"}') is None


def test_find_url_prefers_known_keys():
    assert ih.find_url({"a": {"b": {"url": "https://x.test/1.png"}}}) == "https://x.test/1.png"
    assert ih.find_url({"image": {"image_url": "https://x.test/2.png"}}) == "https://x.test/2.png"


# ------------------------------------------------------------------ 各家上传（字段名照抄文档）

def test_upload_litterbox_uses_reqtype_time_filetoupload(transport):
    url = ih.upload(PNG, "image/png", "litterbox", _cfg(litterbox_time="72h"))
    assert url == "https://litter.catbox.moe/ref.png"
    body = transport[0]["body"]
    assert transport[0]["url"].endswith("/resources/internals/api.php")
    assert b'name="reqtype"' in body and b"fileupload" in body
    assert b'name="time"' in body and b"72h" in body
    assert b'name="fileToUpload"' in body and b"ref-" in body


def test_upload_uguu_uses_bracketed_files_field(transport):
    """坑：Uguu 的字段名是 `files[]`，带方括号；少写就 400。"""
    assert ih.upload(PNG, "image/png", "uguu", _cfg()) == "https://d.uguu.se/ref.png"
    assert transport[0]["url"].endswith("/upload.php")
    assert b'name="files[]"' in transport[0]["body"]


def test_upload_imgbb_key_in_query_and_base64_field(transport):
    assert ih.upload(PNG, "image/png", "imgbb", _cfg()) == "https://i.ibb.co/abc/ref.png"
    assert "key=test-key" in transport[0]["url"]        # Key 放 query，照文档
    body = transport[0]["body"].decode()                # base64 走表单字段 image（会被 URL 编码）
    assert body.startswith("image=")
    assert urllib.parse.quote_plus(PNG_B64) in body


def test_upload_imgbb_without_key_fails_fast(transport):
    with pytest.raises(ih.UploadFailed) as e:
        ih.upload(PNG, "image/png", "imgbb", _cfg(imgbb_key=""))
    assert "未配置 Key" in str(e.value)
    assert not transport                      # 不发请求


def test_upload_http_error_is_reported(monkeypatch):
    monkeypatch.setattr(ih, "TRANSPORT",
                        httpx.MockTransport(lambda r: httpx.Response(503, text="uploads disabled")))
    with pytest.raises(ih.UploadFailed) as e:
        ih.upload(PNG, "image/png", "zero_x0", _cfg())
    assert "503" in str(e.value)


# ------------------------------------------------------------------ 降级链

def test_chain_skips_imgbb_without_key():
    assert ih.chain_of(_cfg(imgbb_key="")) == ["litterbox", "uguu"]
    assert ih.chain_of(_cfg()) == ["imgbb", "litterbox", "uguu"]


def test_upload_with_fallback_uses_next_host_and_records_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=PNG)
        if "imgbb" in str(request.url):
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="https://litter.catbox.moe/ref.png")

    monkeypatch.setattr(ih, "TRANSPORT", httpx.MockTransport(handler))
    url, host, failures = ih.upload_with_fallback(PNG, "image/png", _cfg(), ["imgbb", "litterbox"])
    assert (url, host) == ("https://litter.catbox.moe/ref.png", "litterbox")
    assert failures and "ImgBB" in failures[0] and "500" in failures[0]


def test_upload_with_fallback_all_fail_raises_with_detail(monkeypatch):
    monkeypatch.setattr(ih, "TRANSPORT",
                        httpx.MockTransport(lambda r: httpx.Response(500, text="nope")))
    with pytest.raises(ih.UploadFailed) as e:
        ih.upload_with_fallback(PNG, "image/png", _cfg(), ["litterbox", "uguu"])
    msg = str(e.value)
    assert "Litterbox" in msg and "Uguu" in msg


def test_upload_with_fallback_rejects_broken_upload(monkeypatch):
    """上传返回 200 但回读不是图片 → 换下一家（防「上传成功但上游拉不到」）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            # 只有 litterbox 那条回读被拦，uguu 的正常
            if "litter" in str(request.url):
                return httpx.Response(200, content=b"<html>blocked</html>")
            return httpx.Response(200, content=PNG)
        if "litterbox" in str(request.url):
            return httpx.Response(200, text="https://litter.catbox.moe/ref.png")
        return httpx.Response(200, json={"files": [{"url": "https://d.uguu.se/ref.png"}]})

    monkeypatch.setattr(ih, "TRANSPORT", httpx.MockTransport(handler))
    url, host, failures = ih.upload_with_fallback(PNG, "image/png", _cfg(verify=True),
                                                 ["litterbox", "uguu"])
    assert host == "uguu"
    assert failures and "回读校验失败" in failures[0]


# ------------------------------------------------------------------ 与请求体对接

def test_ensure_refs_url_policy_uploads_base64(transport):
    refs, failures, notes = ih.ensure_refs([DATA_URI], "url", _cfg(), edit=True)
    assert refs == ["https://i.ibb.co/abc/ref.png"] and not failures
    assert notes[0]["host"] == "imgbb" and notes[0]["bytes"] == len(PNG)


def test_ensure_refs_never_uploads_http_urls(transport):
    refs, failures, notes = ih.ensure_refs(["https://already.example.com/a.png"], "url", _cfg())
    assert refs == ["https://already.example.com/a.png"]
    assert not transport and not notes


def test_ensure_refs_base64_policy_does_nothing(transport):
    refs, failures, notes = ih.ensure_refs([DATA_URI], "base64", _cfg())
    assert refs == [DATA_URI] and not transport and not notes


def test_ensure_refs_url_policy_raises_when_all_hosts_fail(monkeypatch):
    monkeypatch.setattr(ih, "TRANSPORT",
                        httpx.MockTransport(lambda r: httpx.Response(500, text="nope")))
    with pytest.raises(ih.UploadFailed) as e:
        ih.ensure_refs([DATA_URI], "url", _cfg(), edit=True)
    assert "Litterbox" in str(e.value) or "Uguu" in str(e.value)


def test_ensure_refs_both_policy_falls_back_to_base64(monkeypatch):
    monkeypatch.setattr(ih, "TRANSPORT",
                        httpx.MockTransport(lambda r: httpx.Response(500, text="nope")))
    refs, failures, notes = ih.ensure_refs([DATA_URI], "both", _cfg())
    assert refs == [DATA_URI]                       # 回落：原样 base64，请求照发
    assert failures and notes[-1].get("fallback") is True


def test_ensure_refs_url_policy_without_enabled_host_raises(db):
    """图床没启用 + 渠道只认 URL → 本地报错，说清去哪开。"""
    with pytest.raises(ih.UploadFailed) as e:
        ih.ensure_refs([DATA_URI], "url", _cfg(enabled=False))
    assert "图床未启用" in str(e.value)


def test_ensure_refs_size_limit(monkeypatch, transport):
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024)).decode()
    with pytest.raises(ih.UploadFailed) as e:
        ih.ensure_refs(["data:image/png;base64," + big], "url", _cfg(max_mb=1))
    assert "超过上限" in str(e.value)


def test_apply_to_body_replaces_in_place(transport):
    body = {"model": "m", "image": [DATA_URI], "image_urls": [DATA_URI]}
    out, failures, notes = ih.apply_to_body(body, "url", _cfg(), edit=True)
    assert out["image"] == ["https://i.ibb.co/abc/ref.png"]
    assert out["image_urls"] == ["https://i.ibb.co/abc/ref.png"]
    assert not failures
    # 同一张图只上传一次（sha256 去重）
    assert len([s for s in transport if s["method"] == "POST"]) == 1


def test_apply_to_body_handles_dict_form(transport):
    body = {"model": "m", "image": [{"image_url": DATA_URI}]}
    out, _, _ = ih.apply_to_body(body, "url", _cfg(), edit=True)
    assert out["image"][0]["image_url"] == "https://i.ibb.co/abc/ref.png"


# ------------------------------------------------------------------ 配置落库

def test_settings_encrypt_imgbb_key_at_rest(db):
    ih.save_settings({"enabled": True, "imgbb_key": "super-secret-key"})
    row = db.one("SELECT data FROM settings WHERE scope='imagehost'")
    assert "super-secret-key" not in row["data"]          # 落库必须是密文
    assert db.crypto.is_encrypted(json.loads(row["data"])["imgbb_key"])
    cfg = ih.settings()
    assert cfg["enabled"] is True and cfg["imgbb_key"] == "super-secret-key"
    assert cfg["chain"] == ih.DEFAULT_CHAIN and cfg["max_mb"] == 20


def test_save_settings_keeps_key_when_blank(db):
    ih.save_settings({"imgbb_key": "k1"})
    ih.save_settings({"enabled": True})                    # 不带 key → 保持原值
    assert ih.settings()["imgbb_key"] == "k1"
    ih.save_settings({"imgbb_key_clear": True})
    assert ih.settings()["imgbb_key"] == ""


# ------------------------------------------------------------------ 下载内联（URL → base64）

def test_inline_url_refs_downloads_and_inlines(transport):
    """只认 base64 的渠道：公网 URL → 下载 → data URI（给 aicost 的 gpt-image-2 编辑面用）。"""
    body = {"model": "gpt-image-2", "prompt": "x", "image": ["https://i.ibb.co/abc/ref.png"]}
    out, fail, notes = ih.inline_url_refs(body, _cfg())
    assert fail == [] and notes and notes[0]["mode"] == "inline"
    v = out["image"][0]
    assert v.startswith("data:image/png;base64,")
    assert base64.b64decode(v.split(",", 1)[1]) == PNG
    assert "https://i.ibb.co/abc/ref.png" not in json.dumps(out)     # 原 URL 已被替换
    assert [s["method"] for s in transport] == ["GET"]               # 只下载，不上传


def test_inline_url_refs_keeps_base64_untouched(transport):
    """客户端本来就给 base64 / data URI → 原样保留，一个请求都不发。"""
    body = {"image": DATA_URI, "images": [PNG_B64]}
    out, fail, notes = ih.inline_url_refs(body, _cfg())
    assert out == body and fail == [] and notes == []
    assert transport == []


def test_inline_url_refs_reports_download_failure(monkeypatch):
    """下载失败 → 记进 failures 并**保留原值**（由 relay 决定本地报错）。"""
    monkeypatch.setattr(ih, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(404, text="gone")))
    body = {"image": ["https://i.ibb.co/gone.png"]}
    out, fail, notes = ih.inline_url_refs(body, _cfg())
    assert fail and "404" in fail[0] and notes == []
    assert out["image"] == ["https://i.ibb.co/gone.png"]


def test_inline_url_refs_rejects_non_image_download(monkeypatch):
    monkeypatch.setattr(ih, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, text="<html>not an image</html>",
                                 headers={"content-type": "text/html"})))
    out, fail, _ = ih.inline_url_refs({"image": ["https://i.ibb.co/x"]}, _cfg())
    assert fail and "不是图片" in fail[0]
