"""参考图能力协商的端到端（进程内）用例：渠道只认 URL 时自动转图床直链，只认 base64 时不绕路。

零网络：上游用 fake_upstream 拦，图床用 httpx.MockTransport 拦。
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app import imagehost as ih

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
DATA_URI = "data:image/png;base64," + base64.b64encode(PNG).decode()
UPLOADED = "https://litter.catbox.moe/ref.png"


@pytest.fixture()
def hosts(monkeypatch):
    """假图床：记录上传请求，返回公网直链。"""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=PNG)
        seen.append(str(request.url))
        return httpx.Response(200, text=UPLOADED)

    monkeypatch.setattr(ih, "TRANSPORT", httpx.MockTransport(handler))
    return seen


@pytest.fixture()
def enabled(db):
    ih.save_settings({"enabled": True, "imgbb_key": "k", "chain": ["litterbox"]})
    return ih.settings()


def _gen(client, key, model, extra=None):
    body = {"model": model, "prompt": "把这张图改成夜景", "size": "1024x1024",
            "image": [DATA_URI]}
    body.update(extra or {})
    return client.post(f"/up/{key}/v1/images/generations", json=body)


def test_url_only_channel_converts_base64_to_public_url(client, login, make_provider,
                                                        fake_upstream, hosts, enabled, db):
    """七牛 fal 异步面：只认公网 URL —— base64 参考图必须被换成图床直链。"""
    calls = fake_upstream(200, {"images": [{"url": "https://kodo.example.com/out.png"}]})
    make_provider(key="q", protocol="qiniu_fal", base_url="https://api.qnaigc.com",
                  model_map={"gemini-3.1-flash-image-preview": "fal-ai/gemini-3.1-flash-image-preview"})
    r = _gen(client, "q", "gemini-3.1-flash-image-preview")
    assert r.status_code == 200, r.text
    assert calls[0]["body"]["image_urls"] == [UPLOADED]      # 上游收到的是公网直链
    assert calls[0]["url"].endswith("/queue/fal-ai/gemini-3.1-flash-image-preview")
    assert hosts                                             # 确实上传了一次
    assert r.headers["X-QLike-Imagehost"] == "litterbox"     # 可观测：告诉客户端走了图床
    # 日志详情那一行「内联 base64（约 NKB）→ 图床直链（litterbox）」靠这条记录
    notes = json.loads(db.one("SELECT imagehost FROM logs WHERE kind='relay' "
                              "ORDER BY id DESC LIMIT 1")["imagehost"] or "[]")
    assert notes and notes[0]["mode"] == "imgbb" and notes[0]["host"] == "litterbox"
    assert notes[0]["bytes"] == len(PNG)


def test_url_only_channel_without_imagehost_fails_locally(client, login, make_provider,
                                                          no_upstream, hosts, db):
    """图床没启用 + 渠道只认 URL → 本地 400 说清原因，绝不把 base64 硬发给上游。"""
    make_provider(key="q", protocol="qiniu_fal", base_url="https://api.qnaigc.com",
                  model_map={"gemini-3.1-flash-image-preview": "fal-ai/gemini-3.1-flash-image-preview"})
    r = _gen(client, "q", "gemini-3.1-flash-image-preview")
    assert r.status_code == 400
    assert "图床未启用" in r.json()["error"]["message"]
    assert not hosts and not no_upstream                     # 没上传、也没打上游


def test_base64_only_channel_never_uploads(client, login, make_provider, fake_upstream,
                                           hosts, enabled):
    """Gemini 官方只吃 inlineData → 一个字节都不上传，参考图原样内联。"""
    calls = fake_upstream(200, {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}]}}]})
    make_provider(key="g", protocol="gemini_native",
                  base_url="https://generativelanguage.googleapis.com",
                  model_map={"gemini-3.1-flash-image": "gemini-3.1-flash-image"})
    r = _gen(client, "g", "gemini-3.1-flash-image")
    assert r.status_code == 200, r.text
    assert not hosts                                         # 没上传
    assert "X-QLike-Imagehost" not in r.headers
    parts = calls[0]["body"]["contents"][0]["parts"]
    assert parts[1]["inlineData"]["data"] == base64.b64encode(PNG).decode()


def test_both_channel_prefers_url_then_falls_back(client, login, make_provider,
                                                  fake_upstream, enabled, monkeypatch):
    """两者都支持的渠道：优先公网 URL；图床全挂 → 回落 base64 内联，请求照发不失败。"""
    monkeypatch.setattr(ih, "TRANSPORT",
                        httpx.MockTransport(lambda r: httpx.Response(500, text="down")))
    calls = fake_upstream(200, {"data": [{"url": "https://up.example.com/out.png"}]})
    make_provider(key="o", protocol="openai_images", base_url="https://up.example.com",
                  model_map={"gpt-image-2": "gpt-image-2"})
    r = _gen(client, "o", "gpt-image-2")
    assert r.status_code == 200, r.text
    assert calls[0]["body"]["image"] == [DATA_URI]            # 回落：仍是 base64，但请求成功


def test_both_channel_uploads_when_hosts_work(client, login, make_provider,
                                              fake_upstream, hosts, enabled):
    calls = fake_upstream(200, {"data": [{"url": "https://up.example.com/out.png"}]})
    make_provider(key="o", protocol="openai_images", base_url="https://up.example.com",
                  model_map={"gpt-image-2": "gpt-image-2"})
    r = _gen(client, "o", "gpt-image-2")
    assert r.status_code == 200, r.text
    assert calls[0]["body"]["image"] == [UPLOADED]


def test_client_supplied_url_is_passed_through_untouched(client, login, make_provider,
                                                         fake_upstream, hosts, enabled):
    calls = fake_upstream(200, {"data": [{"url": "https://up.example.com/out.png"}]})
    make_provider(key="o", protocol="openai_images", base_url="https://up.example.com",
                  model_map={"gpt-image-2": "gpt-image-2"})
    r = _gen(client, "o", "gpt-image-2", {"image": ["https://cdn.example.com/a.png"]})
    assert r.status_code == 200
    assert calls[0]["body"]["image"] == ["https://cdn.example.com/a.png"]
    assert not hosts                                          # 本来就是链接 → 一律不上传


def test_merged_channel_policy_follows_face(client, login, make_provider, db):
    """合并插件按「面」协商：七牛异步面 = url，同步面 = base64。"""
    from app import channels
    q = channels.get("qiniu")
    p = make_provider(key="q", protocol="qiniu", base_url="https://api.qnaigc.com",
                      model_map={"gemini-3.1-flash-image-preview": "fal-ai/gemini-3.1-flash-image-preview",
                                 "gpt-image-2": "openai/gpt-image-2"})
    assert q.ref_policy(p, {"model": "gemini-3.1-flash-image-preview"}, True) == "url"
    assert q.ref_policy(p, {"model": "gpt-image-2"}, True) == "base64"
    c2p = channels.get("change2pro")
    assert c2p.ref_policy(p, {"model": "gemini-3.1-flash-image"}, True) == "base64"
    assert c2p.ref_policy(p, {"model": "gpt-image-2"}, True) == "both"


def test_instance_switch_can_force_inline(client, login, make_provider, fake_upstream,
                                          hosts, enabled):
    """渠道实例级应急阀门：options.ref_prefer=inline → 即使只认 URL 的渠道也不上传。"""
    make_provider(key="q", protocol="qiniu_fal", base_url="https://api.qnaigc.com",
                  model_map={"gemini-3.1-flash-image-preview": "fal-ai/gemini-3.1-flash-image-preview"},
                  options={"ref_prefer": "inline"})
    fake_upstream(200, {"images": [{"url": "https://kodo.example.com/out.png"}]})
    r = _gen(client, "q", "gemini-3.1-flash-image-preview")
    assert r.status_code == 400                               # 退回插件原有报错
    assert "必须是公网 URL" in r.json()["error"]["message"]
    assert not hosts


# ------------------------------------------------------------------ 面板接口

def test_settings_api_never_leaks_key(client, login, db, hosts):
    """面板接口：Key 只回掩码；保存后落库是密文。"""
    from app import store
    r = client.post("/api/settings/imagehost", json={"enabled": True, "imgbb_key": "super-secret-key",
                                                     "chain": ["imgbb", "uguu"]})
    assert r.status_code == 200
    d = r.json()
    assert d["imgbb_key_set"] is True and "super-secret-key" not in r.text
    assert d["imgbb_key_masked"].endswith("key") and "super-secret" not in d["imgbb_key_masked"]
    assert d["cfg"]["chain"] == ["imgbb", "uguu"]
    raw = (store.get_settings("imagehost") or {}).get("imgbb_key") or ""
    assert raw and "super-secret-key" not in raw          # 落库是密文
    r2 = client.get("/api/settings/imagehost")
    assert r2.json()["cfg"]["chain"] == ["imgbb", "uguu"]  # 读回一致


def test_settings_api_requires_login(client, db):
    assert client.get("/api/settings/imagehost").status_code == 401


def test_selfcheck_uploads_and_reports(client, login, db, hosts):
    """面板「上传 1×1 自检图」：真上传一张 1×1 PNG，回报图床与直链。"""
    client.post("/api/settings/imagehost", json={"enabled": True, "chain": ["litterbox"]})
    r = client.post("/api/settings/imagehost/test", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["host"] == "litterbox" and d["url"] == UPLOADED
    assert d["verified"] is True and d["bytes"] < 200        # 真的只有 1×1 那么小
    assert len(hosts) == 1


def test_selfcheck_reports_failure_clearly(client, login, db, monkeypatch):
    """图床全挂时自检要给出明确原因，不能只回一个 false。"""
    from app import imagehost as ih2
    monkeypatch.setattr(ih2, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    client.post("/api/settings/imagehost", json={"enabled": True, "chain": ["litterbox"]})
    r = client.post("/api/settings/imagehost/test", json={})
    assert r.status_code == 400
    msg = r.json()["error"]["message"]
    assert "Litterbox" in msg and "500" in msg        # 指明是哪家、什么错
