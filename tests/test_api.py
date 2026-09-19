"""HTTP 层：鉴权门、本地 400、统一入口、控制台接口。

全部用例都不许真的联网：conftest 的 no_upstream / fake_upstream 夹具会在
任何真实请求发生前把测试打红。上游一律用假的。
"""
from __future__ import annotations

from app import store

MASTER = {"x-qlikeapi-token": "unit-test-master-token"}


# ------------------------------------------------------------------ 基础

def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["app"] == "qlikeapi-plugins"
    assert "change2pro" in body["plugins"] and body["plugin_errors"] == {}


def test_index_shows_login_when_anonymous(client):
    r = client.get("/")
    assert r.status_code == 200 and "登录" in r.text


def test_static_serves_local_vendor_assets(client):
    """前端库必须本地自托管（离线/内网也能开控制台）。"""
    for path in ("/static/vendor/bootstrap.min.css",
                 "/static/vendor/tabler-icons/tabler-icons.min.css",
                 "/static/vendor/chart.umd.min.js"):
        assert client.get(path).status_code == 200, path


def test_component_library_is_served(client):
    """设计变量 + 组件库 + 组件展示页：全部是静态文件，不走后端逻辑。"""
    for path in ("/static/css/tokens.css", "/static/css/ui-kit.css",
                 "/static/js/ui-kit.js", "/static/js/app.js"):
        assert client.get(path).status_code == 200, path
    # 旧的单文件样式已并入组件库，不应再存在（避免两套样式并存）
    assert client.get("/static/style.css").status_code == 404


def test_ui_kit_showcase_page_requires_login(client, login):
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    r = anon.get("/ui-kit", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"
    ok = client.get("/ui-kit")
    assert ok.status_code == 200 and "组件库" in ok.text


def test_meta_options_feeds_the_pickers(client, login, make_provider):
    """令牌弹窗的候选集：模型 + 渠道（多选控件用，只读、不打上游）。"""
    make_provider(key="p1", model_map={"gpt-image-2": "openai/gpt-image-2"})
    r = client.get("/api/meta/options")
    assert r.status_code == 200
    d = r.json()
    assert d["currencies"] == ["CNY", "USD"]
    # aliased=True 表示「对外模型名 ≠ 上游模型名」（模型目录里要标出来）
    assert d["models"] == [{"id": "gpt-image-2", "providers": ["p1"], "aliased": True}]
    p = d["providers"][0]
    assert p["key"] == "p1" and p["label"] and p["models"] == ["gpt-image-2"]


def test_meta_options_requires_login(client):
    assert client.get("/api/meta/options").status_code == 401


def test_static_path_traversal_blocked(client):
    assert client.get("/static/../app/store.py").status_code == 404


# ------------------------------------------------------------------ 鉴权门

def test_up_requires_credential(client, make_provider):
    make_provider(key="p1")
    r = client.post("/up/p1/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"})
    assert r.status_code == 401


def test_unknown_provider_404(client):
    r = client.post("/up/no-such-provider/v1/images/generations", json={"prompt": "x"}, headers=MASTER)
    assert r.status_code == 404


def test_disabled_provider_503(client, make_provider):
    """停用的渠道：不参与路由，直连面也不放行；但要能区分「停用」和「不存在」。"""
    make_provider(key="off", enabled=0)
    r = client.post("/up/off/v1/images/generations", json={"model": "m", "prompt": "x"}, headers=MASTER)
    assert r.status_code == 503
    assert "已停用" in r.json()["error"]["message"]

    r2 = client.post("/up/does-not-exist/v1/images/generations", json={"model": "m", "prompt": "x"}, headers=MASTER)
    assert r2.status_code == 404
    assert "unknown provider" in r2.json()["error"]["message"]


def test_only_masked_keys_reach_the_console(client, login, make_provider):
    make_provider(key="p1", api_key="sk-super-secret-value-1234567890")
    r = client.get("/api/providers")
    assert r.status_code == 200
    blob = r.text
    assert "sk-super-secret-value-1234567890" not in blob          # 明文密钥绝不出口


# ------------------------------------------------------------------ 零成本铁律（HTTP 层）

def test_missing_prompt_is_local_400_and_never_touches_upstream(client, make_provider, no_upstream):
    make_provider(key="p1")
    r = client.post("/up/p1/v1/images/generations", json={"model": "gpt-image-2"}, headers=MASTER)
    assert r.status_code == 400
    assert "prompt is required" in r.json()["error"]["message"]
    assert no_upstream == []                                       # 一个字节都没发出去


def test_whitespace_prompt_is_also_rejected(client, make_provider, no_upstream):
    make_provider(key="p1")
    r = client.post("/up/p1/v1/images/generations",
                    json={"model": "gpt-image-2", "prompt": "   \n "}, headers=MASTER)
    assert r.status_code == 400 and no_upstream == []


def test_unified_entry_requires_model(client, no_upstream):
    r = client.post("/v1/images/generations", json={"prompt": "x"}, headers=MASTER)
    assert r.status_code == 400 and "model is required" in r.json()["error"]["message"]


def test_unified_entry_routes_to_chain(client, make_provider, fake_upstream):
    make_provider(key="p1", model_map={"gpt-image-2": "gpt-image-2"})
    calls = fake_upstream(200, {"data": [{"url": "https://k.example.com/1.png"}]}, '{"data":[]}')
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "猫"}, headers=MASTER)
    assert r.status_code == 200
    assert r.json()["data"][0]["url"] == "https://k.example.com/1.png"
    assert r.headers["X-QLike-Provider"] == "p1"
    assert calls and calls[0]["url"] == "https://upstream.example.com/v1/images/generations"


def test_route_preview_is_dry(client, make_provider, no_upstream):
    make_provider(key="p1", model_map={"gpt-image-2": "gpt-image-2"})
    r = client.get("/v1/route-preview?model=gpt-image-2", headers=MASTER)
    assert r.status_code == 200
    body = r.json()
    assert body["rule"].startswith("auto") and [c["provider"] for c in body["chain"]] == ["p1"]
    assert no_upstream == []


def test_preview_endpoint_shows_upstream_body_without_sending(client, make_provider, no_upstream):
    make_provider(key="p1")
    r = client.post("/up/p1/v1/images/preview", json={"model": "gpt-image-2", "prompt": "猫"}, headers=MASTER)
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True and body["url"].endswith("/v1/images/generations")
    assert no_upstream == []


def test_edits_preview_shows_edit_url_and_coerced_numbers(client, make_provider, no_upstream):
    """改图面的 dry-run：URL 走 edits，且字符串数字已被规范化（Go 上游要 int）。"""
    make_provider(key="p1")
    r = client.post("/up/p1/v1/images/edits/preview",
                    json={"model": "gpt-image-2", "prompt": "换装", "n": "1", "seed": "42"},
                    headers=MASTER)
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True and body["url"].endswith("/v1/images/edits")
    assert body["upstream_body"]["n"] == 1 and isinstance(body["upstream_body"]["n"], int)
    assert body["upstream_body"]["seed"] == 42
    assert no_upstream == []


def test_models_endpoint_lists_configured_models(client, make_provider):
    make_provider(key="p1", model_map={"alias-model": "openai/real"})
    r = client.get("/v1/models", headers=MASTER)
    ids = {m["id"] for m in r.json()["data"]}
    assert "alias-model" in ids


# ------------------------------------------------------------------ 访问令牌一起走通

def test_access_token_auth_and_accounting(client, login, make_provider, fake_upstream):
    """访问令牌鉴权 + 记账（按令牌统计请求数/张数/花费）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    fake_upstream(200, {"data": [{"url": "https://k.example.com/1.png"}]})
    make_provider(key="p1", model_map={"gpt-image-2": "gpt-image-2"})
    created = client.post("/api/tokens", json={"name": "comfyui-本机"}).json()
    tok = created["token"]
    assert tok.startswith("sk-ql-")
    store.set_price_full("gpt-image-2", "p1", 0.06, "USD", source="upstream")

    # ⚠ 必须用「没登录过」的新 client：控制台会话的优先级高于请求里带的令牌，
    #   否则会走成控制台身份，令牌记账就测不到了。
    anon = TestClient(app)
    r = anon.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "猫"},
                  headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200

    row = store.get_token(created["id"])
    assert row["used_requests"] == 1 and row["used_cost"] == 0.06
    log = store.one("SELECT * FROM logs WHERE kind='relay' ORDER BY id DESC")
    assert log["token"] == "comfyui-本机" and log["token_id"] == created["id"]


def test_disabled_token_rejected(client, make_provider):
    make_provider(key="p1")
    tid = store.save_token({"name": "停用", "enabled": False})
    tok = store.get_token(tid)["token"]
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403)


def test_expired_token_rejected(client, make_provider):
    make_provider(key="p1")
    tid = store.save_token({"name": "过期", "expires_at": 1})
    tok = store.get_token(tid)["token"]
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403)


# ------------------------------------------------------------------ 控制台会话

def test_login_required_for_admin_api(client):
    assert client.get("/api/providers").status_code == 401
    assert client.get("/api/tokens").status_code == 401
    assert client.get("/api/logs").status_code == 401


def test_login_wrong_password(client):
    r = client.post("/api/login", json={"username": "tester", "password": "错的"})
    assert r.status_code == 401


def test_login_logout_flow(client):
    assert client.post("/api/login", json={"username": "tester", "password": "unit-test-pass"}).status_code == 200
    assert client.get("/api/me").json()["username"] == "tester"
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_providers_list_shape(client, login, make_provider):
    make_provider(key="p1", model_map={"gpt-image-2": "gpt-image-2"}, priority=10)
    row = client.get("/api/providers").json()[0]
    assert row["key"] == "p1" and row["plugin_ok"] is True
    assert row["plugin_label"] and row["priority"] == 10
    assert row["keys"] == [{"index": 0, "masked": store.mask("sk-test-000"), "label": None}]
    assert row["key_groups"] == [{"label": "（未分组）", "keys": 1, "models": [], "labeled": False}]
    assert row["models"][0]["id"] == "gpt-image-2"


def test_channels_endpoint_lists_plugins(client, login):
    body = client.get("/api/channels").json()
    ids = {c["id"] for c in body["channels"]}
    assert {"change2pro", "gemini_native", "openai_images", "qiniu_fal", "qiniu"} <= ids
    assert body["errors"] == {}


def test_token_create_requires_name(client, login):
    assert client.post("/api/tokens", json={"name": "  "}).status_code == 400


def test_sysinfo_reports_encryption_health(client, login, make_provider):
    make_provider(key="p1")
    body = client.get("/api/sysinfo").json()
    assert body["enc"]["plaintext"] == 0 and body["enc"]["encrypted"] >= 1


def test_stats_endpoint(client, login, make_provider):
    make_provider(key="p1", model_map={"gpt-image-2": "gpt-image-2"})
    store.log_row("p1", "gpt-image-2", "/v1/images/generations", 200, 200, 1200, None, {}, None, None,
                  images=1, cost=0.06, cost_currency="USD")
    body = client.get("/api/stats?days=7").json()
    assert body["providers_total"] >= 1
    assert any(m["model"] == "gpt-image-2" for m in body["per_model"])


def test_password_change_validation(client, login):
    """改密码：字段名是 old/new，旧密码不对或新密码太短 → 400。"""
    r = client.post("/api/password", json={"old": "错的", "new": "newpass123"})
    assert r.status_code == 400 and "原密码" in r.json()["error"]

    r = client.post("/api/password", json={"old": "unit-test-pass", "new": "123"})
    assert r.status_code == 400 and "6" in r.json()["error"]


def test_password_change_invalidates_session(client, login):
    r = client.post("/api/password", json={"old": "unit-test-pass", "new": "newpass123"})
    assert r.status_code == 200
    # 改密后所有会话失效：旧 Cookie 立刻不认
    assert client.get("/api/providers").status_code == 401
    assert client.post("/api/login", json={"username": "tester", "password": "newpass123"}).status_code == 200


def test_multipart_edit_without_prompt_is_rejected_locally(client, make_provider, no_upstream):
    """multipart 传图（标准 OpenAI 编辑请求）：解析要通，缺 prompt 仍然本地 400。"""
    make_provider(key="p1")
    r = client.post("/up/p1/v1/images/edits",
                    files={"image": ("a.png", b"fake-png-bytes", "image/png")},
                    data={"model": "gpt-image-2"},
                    headers=MASTER)
    assert r.status_code == 400 and "prompt is required" in r.json()["error"]["message"]
    assert no_upstream == []


def test_multipart_edit_translates_file_to_upstream_body(client, make_provider, fake_upstream):
    """带 prompt 的 multipart 请求 → 图片被翻成上游要的形态，提示词原样带过去。"""
    make_provider(key="p1", protocol="openai_images",
                  options={"edits_path": "/v1/images/edits"})
    calls = fake_upstream(200, {"data": [{"b64_json": "QUJD"}]}, '{"data":[]}')
    r = client.post("/up/p1/v1/images/edits",
                    files={"image": ("a.png", b"fake-png-bytes", "image/png")},
                    data={"model": "gpt-image-2", "prompt": "改成水彩"},
                    headers=MASTER)
    assert r.status_code == 200
    body = calls[0]["body"]
    assert calls[0]["url"].endswith("/v1/images/edits")
    assert body["prompt"] == "改成水彩"
    assert body["model"] == "gpt-image-2"
    assert "image" in body and str(body["image"])


def test_client_error_does_not_switch_channel(client, make_provider, fake_upstream):
    """400 参数错 → 立即返回，不换下一家（免得把同一个坏请求打到所有渠道）。"""
    make_provider(key="a", priority=20, model_map={"gpt-image-2": "gpt-image-2"})
    make_provider(key="b", priority=10, model_map={"gpt-image-2": "gpt-image-2"})
    calls = fake_upstream(400, {"error": {"message": "invalid size"}})
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"}, headers=MASTER)
    assert r.status_code == 400
    assert len(calls) == 1                          # 只打了第一家


def test_failover_on_5xx(client, make_provider, monkeypatch):
    """5xx → 换下一家；换成功整体 200，响应头标注切换了几家。"""
    from app import protocols

    make_provider(key="a", priority=20, model_map={"gpt-image-2": "gpt-image-2"})
    make_provider(key="b", priority=10, model_map={"gpt-image-2": "gpt-image-2"})
    seq = iter([(503, {"error": {"message": "upstream down"}}, "down"),
                (200, {"data": [{"url": "https://k.example.com/2.png"}]}, "ok")])
    seen = []

    def _call(url, headers=None, body=None, timeout=None):
        seen.append(url)
        return next(seq)

    monkeypatch.setattr(protocols, "call_upstream", _call)
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"}, headers=MASTER)
    assert r.status_code == 200
    assert r.headers["X-QLike-Provider"] == "b"
    assert r.headers["X-QLike-Failover"] == "1"
    assert len(seen) == 2
    # 两家都试过 → 记一条 router 日志（含 failed_over_from）
    assert store.one("SELECT * FROM logs WHERE kind='router'") is not None


def test_no_channel_supports_model_404(client, make_provider, no_upstream):
    make_provider(key="a", model_map={"gpt-image-2": "gpt-image-2"})
    r = client.post("/v1/images/generations", json={"model": "没这个模型", "prompt": "x"}, headers=MASTER)
    assert r.status_code == 404 and no_upstream == []


# ------------------------------------------------------------------ 手动改价（模型目录）

def test_manual_price_roundtrip_and_models_exposes_price_id(client, login, make_provider):
    """手动改价：POST /api/prices 写 source=manual；模型目录要带上 price_id（「清除手工价」用）。"""
    make_provider(key="p1", model_map={"gpt-image-2": "gpt-image-2"})

    r = client.post("/api/prices", json={"model": "gpt-image-2", "provider": "p1", "price": 0.02,
                                         "currency": "USD", "source": "manual", "note": "Ozon 主图"})
    assert r.status_code == 200 and r.json()["ok"] is True
    row = store.price_exact("gpt-image-2", "p1")
    assert row["price"] == 0.02 and row["source"] == "manual" and row["currency"] == "USD"

    m = [x for x in client.get("/api/models").json()
         if x["provider"] == "p1" and x["model"] == "gpt-image-2"][0]
    assert m["source"] == "manual" and m["price"] == 0.02
    assert m["price_id"] == row["id"] and m["currency"] == "USD"

    # 「清除手工价」= 删掉这一行 → 回到未定价（不回落成别的价）
    assert client.delete(f"/api/prices/{row['id']}").status_code == 200
    assert store.price_exact("gpt-image-2", "p1") is None
    m = [x for x in client.get("/api/models").json()
         if x["provider"] == "p1" and x["model"] == "gpt-image-2"][0]
    assert m["price"] is None and m["price_id"] is None


def test_price_set_rejects_missing_model(client, login):
    r = client.post("/api/prices", json={"provider": "p1", "price": 0.02})
    assert r.status_code == 400 and "model" in r.json()["error"]
