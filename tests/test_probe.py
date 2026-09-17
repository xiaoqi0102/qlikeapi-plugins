"""零成本探活 —— 花钱事故的守门人。

背景：探活最初只对 gemini 插件抹 prompt，而「合并插件」change2pro 会按模型名分流到
两套协议，漏判一次就是一次真出图真扣费。现在改成「递归抹掉一切 prompt 类字段 + 硬超时」，
这组用例保证这个底线不会被后续改动悄悄破掉。
"""
from __future__ import annotations

import json

import pytest

from app import protocols, relay, store

MASTER = {"x-qlikeapi-token": "unit-test-master-token"}
PROMPT_LIKE = ("prompt", "prompt_text", "text", "content", "contents", "parts", "input",
               "inputs", "image", "images", "mask", "init_image", "reference_images")


def walk_prompt_like(node, path=""):
    """遍历请求体，产出所有「prompt 类字段」的 (路径, 值)。"""
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else k
            if str(k).lower() in PROMPT_LIKE:
                yield here, v
            yield from walk_prompt_like(v, here)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_prompt_like(v, f"{path}[{i}]")


def test_blank_for_probe_clears_every_prompt_like_field():
    body = {
        "model": "m", "size": "1024x1024", "quality": "standard",
        "prompt": "一只橘猫宇航员",
        "contents": [{"role": "user", "parts": [{"text": "一只橘猫宇航员"}]}],
        "image": "https://x.example.com/1.png",
        "nested": {"prompt": "深层提示词", "keep": 1},
        "data": [{"prompt": "列表里的提示词"}],
    }
    out = relay.blank_for_probe(body)
    assert out["model"] == "m" and out["size"] == "1024x1024" and out["quality"] == "standard"
    assert out["prompt"] == ""
    assert out["contents"] == []                     # 结构保留，内容清空
    assert out["image"] == ""
    assert out["nested"]["prompt"] == "" and out["nested"]["keep"] == 1
    assert out["data"] == [{"prompt": ""}]
    assert "橘猫" not in json.dumps(out, ensure_ascii=False)


def test_blank_for_probe_is_pure():
    body = {"prompt": "x", "list": [1, 2]}
    relay.blank_for_probe(body)
    assert body["prompt"] == "x"                     # 不能改到原对象（真请求还要用）


@pytest.fixture()
def probe_calls(db, make_provider, monkeypatch):
    """拦住上游，返回 (calls, provider)。"""
    calls = []

    def _call(url, headers=None, body=None, timeout=None):
        calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        return 400, {"error": {"message": "prompt is required"}}, '{"error":{"message":"prompt is required"}}'

    monkeypatch.setattr(protocols, "call_upstream", _call)
    p = make_provider(key="c2p", protocol="change2pro",
                      model_map={"gemini-3-pro-image": "gemini-3-pro-image",
                                 "gpt-image-2": "gpt-image-2"})
    return calls, p


@pytest.mark.parametrize("model", ["gemini-3-pro-image", "gpt-image-2"])
def test_selftest_sends_no_prompt_at_all(probe_calls, model):
    calls, p = probe_calls
    res = relay.selftest_provider(p, model)
    assert res["ok"] is True and res["upstream_status"] == 400     # 4xx = 链路可达
    assert calls[0]["timeout"] == relay.PROBE_TIMEOUT              # 硬超时
    blob = json.dumps(calls[0]["body"], ensure_ascii=False)
    assert "probe" not in blob                                     # 占位提示词也不许发
    for path, value in walk_prompt_like(calls[0]["body"]):
        assert not value, f"探活请求里 {path} 还带着内容：{value!r}"


def test_selftest_5xx_marks_unreachable(probe_calls, monkeypatch):
    calls, p = probe_calls

    def _call(url, headers=None, body=None, timeout=None):
        calls.append({"url": url, "body": body})
        return 503, {"error": "down"}, "down"

    monkeypatch.setattr(protocols, "call_upstream", _call)
    res = relay.selftest_provider(p, "gpt-image-2")
    assert res["ok"] is False and res["upstream_status"] == 503


def test_selftest_timeout_marks_unreachable(probe_calls, monkeypatch):
    calls, p = probe_calls

    def _call(url, headers=None, body=None, timeout=None):
        raise protocols.httpx.ConnectTimeout("too slow")

    monkeypatch.setattr(protocols, "call_upstream", _call)
    res = relay.selftest_provider(p, "gpt-image-2")
    assert res["ok"] is False and res["timeout"] is True
    assert "超时" in res["error"]


def test_selftest_without_key_fails_fast(db, make_provider, monkeypatch):
    monkeypatch.setattr(protocols, "call_upstream",
                        lambda *a, **k: pytest.fail("没 key 时不许打上游"))
    p = make_provider(key="nokey", protocol="openai_images", api_key="")
    res = relay.selftest_provider(p, "gpt-image-2")
    assert res["ok"] is False and res["error"] == "no api key"


def test_selftest_writes_probe_log(probe_calls):
    _, p = probe_calls
    relay.selftest_provider(p, "gpt-image-2")
    row = store.one("SELECT * FROM logs WHERE kind='probe'")
    assert row is not None and row["provider"] == "c2p" and row["public_path"] == "/probe"


def test_probe_endpoint_is_authenticated(client, make_provider, no_upstream):
    make_provider(key="p1")
    assert client.post("/api/providers/p1/test").status_code == 401


def test_probe_endpoint_returns_per_model_result(client, login, make_provider, monkeypatch):
    seen = []

    def _call(url, headers=None, body=None, timeout=None):
        seen.append({"url": url, "body": body})
        return 400, {"error": {"message": "prompt is required"}}, "{}"

    monkeypatch.setattr(protocols, "call_upstream", _call)
    make_provider(key="p1", model_map={"gpt-image-2": "gpt-image-2"})
    r = client.post("/api/providers/p1/test?model=gpt-image-2")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["upstream_status"] == 400 and body["ms"] >= 0
    assert seen and seen[0]["body"]["model"] == "gpt-image-2"
    assert seen[0]["url"].endswith("/v1/images/generations")


def test_probe_unknown_provider_404(client, login, no_upstream):
    assert client.post("/api/providers/不存在/test").status_code == 404


def test_dry_run_preview_is_zero_cost(client, login, make_provider, no_upstream):
    """转换预览：只回报文，绝不发送。"""
    make_provider(key="p1", model_map={"gpt-image-2": "gpt-image-2"})
    r = client.post("/up/p1/v1/images/preview",
                    json={"model": "gpt-image-2", "prompt": "猫", "size": "1024x1024"}, headers=MASTER)
    assert r.status_code == 200 and r.json()["dry_run"] is True
    assert no_upstream == []
