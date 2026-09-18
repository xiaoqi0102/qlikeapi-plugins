"""同一个渠道里的「多分组密钥」——sub2api 系上游按分组区分可用模型。

背景（用户反馈 + 实测）：
  sub2api 上游的 key 是**绑分组**的，gemini 与 gpt 常常不在同一分组（这点和 New API 不一样）。
  所以一个渠道里会放多把不同分组的 key，写法 `分组标签::密钥`；
  路由按「模型 → 分组」挑对应那组密钥，**不把渠道拆成多个**。

全程零成本：上游一律被 monkeypatch 拦住，没有任何真实网络请求。
"""
from __future__ import annotations

import httpx

from app import protocols, store, utils

MASTER = {"x-qlikeapi-token": "unit-test-master-token"}
RULES = {"key_groups": {"gemini-*": "gemini", "gpt*": "gpt"}}


# ------------------------------------------------------------------ 解析与规则

def test_parse_key_lines_keeps_labels_and_order():
    got = utils.parse_key_lines("gemini::sk-a\n gpt :: sk-b \nsk-c\n\n# 注释行\n")
    assert got == [{"label": "gemini", "key": "sk-a"},
                   {"label": "gpt", "key": "sk-b"},
                   {"label": None, "key": "sk-c"}]


def test_parse_key_lines_plain_keys_still_work():
    """老写法（一行一把、没有标签）必须原样解析，标签为 None。"""
    assert utils.parse_key_lines("sk-1\nsk-2") == [{"label": None, "key": "sk-1"},
                                                   {"label": None, "key": "sk-2"}]


def test_key_group_prefers_explicit_rules_then_discovery(make_provider):
    p = make_provider(options=RULES)
    assert protocols.key_group(p, "gemini-3.1-flash-image") == "gemini"
    assert protocols.key_group(p, "gpt-image-2") == "gpt"
    assert protocols.key_group(p, "sora-2") is None
    # 上游真名也能命中（客户端名和上游名不一致时）
    q = make_provider(key="q", options={"key_models": {"gpt": ["openai/gpt-image-2"]}})
    assert protocols.key_group(q, "gpt-image-2", "openai/gpt-image-2") == "gpt"


def test_pick_keys_filters_then_falls_back(make_provider):
    p = make_provider(api_key="gemini::sk-g\ngpt::sk-p\n", options=RULES)
    assert [e["key"] for e in protocols.pick_keys(p, "gpt-image-2")] == ["sk-p"]
    assert [e["key"] for e in protocols.pick_keys(p, "gemini-3-pro-image")] == ["sk-g"]
    # 规则没命中且没有「无标签」的 key → 退化成全部，绝不因配置不全而断路
    assert [e["key"] for e in protocols.pick_keys(p, "unknown")] == ["sk-g", "sk-p"]


def test_pick_keys_prefers_unlabeled_when_no_group_match(make_provider):
    p = make_provider(api_key="gemini::sk-g\nsk-any\n", options=RULES)
    assert [e["key"] for e in protocols.pick_keys(p, "unknown-model")] == ["sk-any"]


# ------------------------------------------------------------------ 端到端：真调用按分组挑 key

def test_relay_picks_key_by_group(client, make_provider, fake_upstream):
    make_provider(key="p", priority=20, protocol="change2pro",
                  api_key="gemini::sk-gemini\ngpt::sk-gpt\n",
                  model_map={"gpt-image-2": "gpt-image-2",
                             "gemini-3.1-flash-image": "gemini-3.1-flash-image"},
                  options=RULES)
    calls = fake_upstream(200, {"data": [{"url": "https://k.example.com/a.png"}]}, '{"data":[]}')

    r = client.post("/v1/images/generations",
                    json={"model": "gpt-image-2", "prompt": "猫"}, headers=MASTER)
    assert r.status_code == 200
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-gpt"

    calls.clear()
    r = client.post("/v1/images/generations",
                    json={"model": "gemini-3.1-flash-image", "prompt": "猫"}, headers=MASTER)
    assert r.status_code == 200
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-gemini"


def test_relay_uses_discovered_groups_without_manual_rules(client, make_provider, fake_upstream):
    """只做过「探测分组」、没手写 key_groups 时，路由也要能挑对 key。"""
    make_provider(key="p", priority=20,
                  api_key="gemini::sk-gemini\ngpt::sk-gpt\n",
                  model_map={"gpt-image-2": "gpt-image-2", "gemini-3.1-flash-image": "gemini-3.1-flash-image"},
                  options={"key_models": {"gemini": ["gemini-3.1-flash-image"], "gpt": ["gpt-image-2"]}})
    calls = fake_upstream(200, {"data": [{"url": "https://k.example.com/a.png"}]}, '{"data":[]}')
    client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "猫"}, headers=MASTER)
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-gpt"


# ------------------------------------------------------------------ 探测分组（零成本 GET /v1/models）

def test_discover_groups_endpoint_reads_each_key(client, login, make_provider, monkeypatch):
    make_provider(key="p", api_key="gemini::sk-g\ngpt::sk-p", base_url="https://up.example.com")

    def fake_get(url, headers=None, timeout=None):
        key = (headers or {}).get("Authorization", "")
        models = {"Bearer sk-g": ["gemini-3.1-flash-image", "gemini-3-pro-image"],
                  "Bearer sk-p": ["gpt-image-2"]}.get(key, [])

        class R:
            status_code = 200
            text = "{}"

            def json(self):
                return {"data": [{"id": m} for m in models]}

        return R()

    monkeypatch.setattr(httpx, "get", fake_get)
    d = client.post("/api/providers/p/discover-groups").json()
    assert d["ok"] is True
    assert d["groups"] == {"gemini": ["gemini-3.1-flash-image", "gemini-3-pro-image"],
                           "gpt": ["gpt-image-2"]}
    # 落库到 options.key_models
    assert store.get_provider("p")["options"]["key_models"]["gpt"] == ["gpt-image-2"]
    # 列表接口带出分组汇总（只回标签/数量/模型，绝不回密钥）
    row = [x for x in client.get("/api/providers").json() if x["key"] == "p"][0]
    g = {x["label"]: x for x in row["key_groups"]}
    assert g["gpt"] == {"label": "gpt", "keys": 1, "models": ["gpt-image-2"], "labeled": True}
    assert g["gemini"]["keys"] == 1
    assert [k["label"] for k in row["keys"]] == ["gemini", "gpt"]
    assert all("sk-" not in str(k) for k in row["keys"])          # 密钥内容绝不外泄


def test_discover_groups_reports_errors_when_all_keys_fail(client, login, make_provider, monkeypatch):
    make_provider(key="p", api_key="gemini::sk-g")

    def fake_get(url, headers=None, timeout=None):
        class R:
            status_code = 401
            text = "invalid key"

            def json(self):
                return {}

        return R()

    monkeypatch.setattr(httpx, "get", fake_get)
    r = client.post("/api/providers/p/discover-groups")
    assert r.status_code == 400
    assert "HTTP 401" in r.json()["error"]


def test_discover_groups_needs_a_key(client, login, make_provider):
    make_provider(key="p", api_key="")
    assert client.post("/api/providers/p/discover-groups").status_code == 400
