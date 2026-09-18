"""尺寸换算的端到端链路：客户端尺寸 → 上游请求体 → 响应头 → 请求日志。

全部零成本：上游一律被 monkeypatch 拦住，用例里没有任何真实网络请求。
"""
from __future__ import annotations

import pytest

from app import store

MASTER = {"x-qlikeapi-token": "unit-test-master-token"}
OK = {"data": [{"url": "https://k.example.com/1.png"}]}


def _last_log():
    return store.one("SELECT * FROM logs WHERE kind='router' ORDER BY id DESC LIMIT 1")


# ------------------------------------------------------------------ GPT 自由尺寸

def test_free_size_snap_is_minimal_and_visible(client, make_provider, fake_upstream):
    """保 1920、只把 1080 修成 16 的倍数；响应头 + 上游报文 + 日志三处都要能看见。"""
    make_provider(key="a", priority=20, model_map={"gpt-image-2": "gpt-image-2"})
    calls = fake_upstream(200, OK, '{"data":[]}')
    r = client.post("/v1/images/generations",
                    json={"model": "gpt-image-2", "prompt": "猫", "size": "1920x1080"}, headers=MASTER)
    assert r.status_code == 200
    assert r.headers["X-QLike-Size"] == "1920x1080->1920x1072"
    assert calls[0]["body"]["size"] == "1920x1072"          # 发上游的确实是修过的尺寸
    row = _last_log()
    assert "1920x1072" in row["response_snippet"]           # 日志里能看到换算说明
    assert "最小改动" in row["response_snippet"]


def test_no_size_header_when_size_already_legal(client, make_provider, fake_upstream):
    """本来就合法的尺寸：原样透传，不产生噪音响应头。"""
    make_provider(key="a", priority=20, model_map={"gpt-image-2": "gpt-image-2"})
    calls = fake_upstream(200, OK, '{"data":[]}')
    r = client.post("/v1/images/generations",
                    json={"model": "gpt-image-2", "prompt": "猫", "size": "2048x1152"}, headers=MASTER)
    assert r.status_code == 200
    assert "X-QLike-Size" not in r.headers
    assert calls[0]["body"]["size"] == "2048x1152"


def test_fixed_size_family_only_three_sizes(client, make_provider, fake_upstream):
    """gpt-image-1 系只认三种尺寸 → 只能在这三种里挑最接近的比例。"""
    make_provider(key="a", priority=20, model_map={"gpt-image-1": "gpt-image-1"})
    calls = fake_upstream(200, OK, '{"data":[]}')
    r = client.post("/v1/images/generations",
                    json={"model": "gpt-image-1", "prompt": "猫", "size": "1536x864"}, headers=MASTER)
    assert r.status_code == 200
    assert calls[0]["body"]["size"] == "1536x1024"
    assert r.headers["X-QLike-Size"] == "1536x864->1536x1024"


# ------------------------------------------------------------------ Gemini 档位 + 比例

def test_gemini_plan_reaches_upstream(client, make_provider, fake_upstream):
    """Gemini 只能给「档位 + 比例」：上游拿到的是 imageConfig，不是像素。"""
    make_provider(key="c2p", protocol="change2pro", priority=20,
                  model_map={"gemini-3.1-flash-image": "gemini-3.1-flash-image"})
    calls = fake_upstream(200, {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": "aGk="}}]}}]}, "{}")
    r = client.post("/v1/images/generations",
                    json={"model": "gemini-3.1-flash-image", "prompt": "猫", "size": "1920x1080"},
                    headers=MASTER)
    assert r.status_code == 200
    cfg = calls[0]["body"]["generationConfig"]["imageConfig"]
    assert cfg == {"aspectRatio": "16:9", "imageSize": "2K"}
    assert "size" not in calls[0]["body"]                     # 像素尺寸不该出现在 Gemini 请求里
    assert r.headers["X-QLike-Size"] == "1920x1080->2752x1536 (16:9@2K)"
    assert "2752x1536" in _last_log()["response_snippet"]     # 实际输出像素写进日志


def test_gemini_policy_from_provider_options(client, make_provider, fake_upstream):
    """渠道实例可以用 options.gemini_size_policy 覆盖档位策略（floor = 最省）。"""
    make_provider(key="c2p", protocol="change2pro", priority=20,
                  model_map={"gemini-3.1-flash-image": "gemini-3.1-flash-image"},
                  options={"gemini_size_policy": "floor"})
    calls = fake_upstream(200, {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": "aGk="}}]}}]}, "{}")
    r = client.post("/v1/images/generations",
                    json={"model": "gemini-3.1-flash-image", "prompt": "猫", "size": "1920x1080"},
                    headers=MASTER)
    assert r.status_code == 200
    assert calls[0]["body"]["generationConfig"]["imageConfig"]["imageSize"] == "1K"
    assert r.headers["X-QLike-Size"] == "1920x1080->1376x768 (16:9@1K)"


def test_gemini_dirty_policy_falls_back_to_class(client, make_provider, fake_upstream):
    """脏策略值不许把请求打挂：退回默认 class。"""
    make_provider(key="c2p", protocol="change2pro", priority=20,
                  model_map={"gemini-3.1-flash-image": "gemini-3.1-flash-image"},
                  options={"gemini_size_policy": "???"})
    calls = fake_upstream(200, {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": "aGk="}}]}}]}, "{}")
    r = client.post("/v1/images/generations",
                    json={"model": "gemini-3.1-flash-image", "prompt": "猫", "size": "1920x1080"},
                    headers=MASTER)
    assert r.status_code == 200
    assert calls[0]["body"]["generationConfig"]["imageConfig"]["imageSize"] == "2K"


# ------------------------------------------------------------------ 面板接口

@pytest.mark.parametrize("model,size,policy,expect_final", [
    ("gpt-image-2", "1920x1080", "class", "1920x1072"),
    ("gpt-image-2", "4096x4096", "class", "2880x2880"),
    ("gpt-image-1", "1920x1080", "class", "1536x1024"),
    ("gemini-3.1-flash-image", "1920x1080", "class", "2752x1536"),
    ("gemini-3.1-flash-image", "1920x1080", "floor", "1376x768"),
    ("gemini-3.1-flash-image", "3840x2160", "class", "5504x3072"),
])
def test_size_plan_api(login, model, size, policy, expect_final):
    """尺寸换算器接口（纯计算，零成本不出图）。"""
    d = login.get(f"/api/size-plan?model={model}&size={size}&policy={policy}").json()
    assert d["final"] == expect_final
    assert d["size"] == size and d["model"] == model


def test_size_plan_api_rejects_bad_input(login):
    assert login.get("/api/size-plan?model=gpt-image-2&size=abc").json().get("error")
    # 模型名可以留空（按通用 GPT 自由尺寸规则算），不算错误
    d = login.get("/api/size-plan?model=&size=1920x1080").json()
    assert d["final"] == "1920x1072" and not d.get("error")


def test_prune_removes_orphan_prices(login, db):
    """孤儿价（渠道已不存在）能被一键清理，全局兜底价和有效渠道价不动。"""
    db.execute("INSERT INTO model_prices(model,provider,price,currency,note) VALUES(?,?,?,?,?)",
               ("m1", "ghost", 1.0, "CNY", "渠道早就没了"))
    db.execute("INSERT INTO model_prices(model,provider,price,currency,note) VALUES(?,?,?,?,?)",
               ("*", "*", 2.0, "CNY", "全局兜底"))
    r = login.post("/api/prices/prune").json()
    assert r["removed"] == 1
    left = [x["provider"] for x in db.rows("SELECT provider FROM model_prices")]
    assert left == ["*"]


# ---------------------------------------------------------------- v3.7.0：原样透传 + 官方表可见
def test_passthrough_channel_never_touches_the_size(client, make_provider, fake_upstream):
    """渠道 options.size_mode=passthrough → 4096x4096 原样发上游，且不该出现 X-QLike-Size。"""
    make_provider(key="pt", priority=20, model_map={"gpt-image-2": "gpt-image-2"},
                  options={"size_mode": "passthrough"})
    calls = fake_upstream(200, {"data": [{"url": "https://k.example.com/pt.png"}]}, '{"data":[]}')
    r = client.post("/v1/images/generations",
                    json={"model": "gpt-image-2", "prompt": "猫", "size": "4096x4096"}, headers=MASTER)
    assert r.status_code == 200
    assert "X-QLike-Size" not in r.headers
    assert calls[0]["body"]["size"] == "4096x4096"


def test_snap_channel_still_snaps(client, make_provider, fake_upstream):
    """同一个尺寸，默认渠道（snap）依然吸附到官方上限内的最大方形。"""
    make_provider(key="snap", priority=20, model_map={"gpt-image-2": "gpt-image-2"})
    calls = fake_upstream(200, {"data": [{"url": "https://k.example.com/s.png"}]}, '{"data":[]}')
    r = client.post("/v1/images/generations",
                    json={"model": "gpt-image-2", "prompt": "猫", "size": "4096x4096"}, headers=MASTER)
    assert r.status_code == 200
    assert r.headers["X-QLike-Size"] == "4096x4096->2880x2880"
    assert calls[0]["body"]["size"] == "2880x2880"


def test_size_plan_exposes_official_tables(login):
    """换算接口要把官方口径摊开：Gemini 档位表 + token；GPT 官方常用尺寸 + 最近的官方尺寸。"""
    d = login.get("/api/size-plan?model=gemini-3.1-flash-image&size=1920x1080").json()
    assert d["tiers"]["2K"] == "2752x1536" and d["tokens"] == 1680
    assert "3840" not in str(d["tiers"]) and "image_size" in d["source"]
    g = login.get("/api/size-plan?model=gpt-image-2&size=4096x4096").json()
    assert g["final"] == "2880x2880" and g["mode"] == "snap"
    assert g["nearest_official"]["size"] == "3840x2160" and g["nearest_official"]["label"] == "4K 横"
    assert {o["size"] for o in g["official"]} == {"1024x1024", "1536x1024", "1024x1536",
                                                 "2048x2048", "2048x1152", "1152x2048",
                                                 "3840x2160", "2160x3840"}
    pt = login.get("/api/size-plan?model=gpt-image-2&size=4096x4096&mode=passthrough").json()
    assert pt["final"] == "4096x4096" and pt["mode"] == "passthrough"
