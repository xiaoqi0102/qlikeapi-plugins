"""模型白名单 / 模型映射（对齐 sub2api 的口径）。

sub2api 用一个对象 {请求模型: 实际模型} 存两件事：
  · from == to  → 白名单（精确放行）
  · from != to  → 映射（把请求模型改写成实际发送的模型）
并支持左侧通配符（`claude-*`，* 只能有一个且在末尾），右侧不允许通配符。
本服务沿用同一套结构，这里把「解析 + 校验 + 上游真实名」的链路钉住。

全程零成本：上游一律被 monkeypatch 拦住。
"""
from __future__ import annotations

from app import protocols

MASTER = {"x-qlikeapi-token": "unit-test-master-token"}


# ------------------------------------------------------------------ 校验

def test_is_valid_wildcard_only_trailing_single_star():
    assert protocols.is_valid_wildcard("gemini-3*") is True
    assert protocols.is_valid_wildcard("gpt-image-2") is True          # 无通配符也合法
    assert protocols.is_valid_wildcard("gemini-*-image") is False      # * 不在末尾
    assert protocols.is_valid_wildcard("*gemini") is False             # * 在开头（不在末尾）
    assert protocols.is_valid_wildcard("a*b") is False                 # 夹在中间
    assert protocols.is_valid_wildcard("a**") is False                 # 两个 *


def test_validate_model_map_rejects_bad_patterns():
    assert protocols.validate_model_map({"gemini-3*": "gemini-3.1-flash-image"}) is None
    assert "通配符格式不对" in protocols.validate_model_map({"a*b": "c"})
    assert "不能含通配符" in protocols.validate_model_map({"a": "b*"})
    assert protocols.validate_model_map("不是对象") is not None


# ------------------------------------------------------------------ 解析（含通配符）

def test_match_model_prefers_exact_then_longest_wildcard(make_provider):
    p = make_provider(model_map={"gpt-image-2": "openai/gpt-image-2",
                                 "gemini-3*": "gemini-3.1-flash-image",
                                 "gemini-3-pro*": "gemini-3-pro-image"})
    assert protocols.match_model(p, "Gpt-Image-2") == "gpt-image-2"        # 精确（大小写不敏感）
    assert protocols.match_model(p, "gemini-3-pro-preview") == "gemini-3-pro*"   # 更长（更具体）的规则优先
    assert protocols.match_model(p, "gemini-3-flash-preview") == "gemini-3*"
    assert protocols.match_model(p, "unknown-model") == "unknown-model"   # 都不命中 → 原样


def test_default_model_skips_wildcards(make_provider):
    p = make_provider(model_map={"gemini-3*": "gemini-3.1-flash-image", "gpt-image-2": "openai/gpt-image-2"})
    assert protocols.default_model(p) == "gpt-image-2"


def test_model_list_marks_wildcards(make_provider):
    p = make_provider(model_map={"gemini-3*": "gemini-3.1-flash-image", "gpt-image-2": "gpt-image-2"})
    rows = {m["id"]: m for m in protocols.model_list(p)}
    assert rows["gemini-3*"]["wildcard"] is True and rows["gemini-3*"]["aliased"] is True
    assert rows["gpt-image-2"]["wildcard"] is False and rows["gpt-image-2"]["aliased"] is False


# ------------------------------------------------------------------ 端到端：通配符真的作用到上游请求

def test_wildcard_mapping_reaches_upstream(client, make_provider, fake_upstream):
    make_provider(key="p", priority=20, protocol="gemini_native",
                  model_map={"gemini-3*": "gemini-3.1-flash-image"})
    calls = fake_upstream(200, {"candidates": [{"content": {"parts": [
        {"inlineData": {"data": "aGk=", "mimeType": "image/png"}}]}}]}, '{}')
    r = client.post("/v1/images/generations",
                    json={"model": "gemini-3-pro-image", "prompt": "猫"}, headers=MASTER)
    assert r.status_code == 200
    assert "gemini-3.1-flash-image" in calls[0]["url"]          # 通配符规则把模型改写到了上游真名


# ------------------------------------------------------------------ 面板要用的预置模型（「同步最新支持模型」/ 预设药丸）

def test_channels_expose_preset_model_map(client, login):
    body = client.get("/api/channels").json()
    ch = {c["id"]: c for c in body["channels"]}
    assert ch["gemini_native"]["model_map"]["gemini-3.1-flash-image"] == "gemini-3.1-flash-image"
    assert ch["qiniu"]["model_map"]["gpt-image-2"] == "openai/gpt-image-2"
    assert ch["qiniu_fal"]["model_map"]["gpt-image-2"] == "openai/gpt-image-2"
    assert set(ch["openai_images"]["model_map"]) == set(ch["openai_images"]["models"])


def test_provider_save_rejects_invalid_wildcard(client, login):
    bad = client.post("/api/providers", json={"key": "w1", "protocol": "gemini_native",
                                              "base_url": "https://up.example.com",
                                              "model_map": {"gemini-*x": "gemini-3.1-flash-image"}})
    assert bad.status_code == 400 and "通配符" in bad.json()["error"]
    ok = client.post("/api/providers", json={"key": "w2", "protocol": "gemini_native",
                                             "base_url": "https://up.example.com",
                                             "model_map": {"gemini-3*": "gemini-3.1-flash-image"}})
    assert ok.status_code == 200
