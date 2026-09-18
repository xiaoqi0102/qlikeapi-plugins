"""上游模型列表拉取（零成本 GET）+ 模型名去重 的测试。

借鉴 New API / sub2api 的渠道页逻辑：直接拉上游 /v1/models，勾选写进映射，不用手敲模型名。
"""
from __future__ import annotations

from urllib.parse import urlparse

from app import protocols

MASTER = {"x-qlikeapi-token": "unit-test-master-token"}


class _Resp:
    def __init__(self, status, payload, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeHTTP:
    """按 URL 片段匹配的假 HTTP 客户端；记录所有请求，方便断言「只发 GET、不出图」。"""

    def __init__(self, routes):
        self.routes = routes
        self.seen = []

    def get(self, url, headers=None, timeout=None):
        self.seen.append({"method": "GET", "url": url, "headers": headers})
        path = urlparse(url).path
        for frag, resp in self.routes.items():
            if path == frag:                      # 按路径精确匹配（子串匹配会让 /models 撞上 /v1/models）
                return resp
        return _Resp(404, None, "not found")


# ------------------------------------------------------------------ 解析 + 去重

def test_model_ids_from_openai_shape():
    payload = {"object": "list", "data": [{"id": "gpt-image-2"}, {"id": "gpt-image-2"}, {"id": "banana"}]}
    assert protocols.model_ids_from(payload) == ["gpt-image-2", "banana"]     # 同名去重


def test_model_ids_from_other_shapes():
    assert protocols.model_ids_from(["a", "b", "a"]) == ["a", "b"]            # 裸数组
    assert protocols.model_ids_from({"models": [{"name": "m1"}, {"model": "m2"}]}) == ["m1", "m2"]
    assert protocols.model_ids_from({"data": [{"slug": "s"}, {"id": ""}, 123]}) == ["s"]
    assert protocols.model_ids_from({"model": "solo"}) == ["solo"]
    assert protocols.model_ids_from({"nope": 1}) == []
    assert protocols.model_ids_from(None) == []


def test_model_list_dedupes_names():
    """同一平台重复命名（含前后空格）的模型只留一条。"""
    p = {"model_map": {"a": "up-a", " a ": "up-a", "": "x", "b": "b"}}
    assert [m["id"] for m in protocols.model_list(p)] == ["a", "b"]


# ------------------------------------------------------------------ 拉取

def _provider(**kw):
    base = {"key": "demo", "base_url": "https://api.example.com", "auth_mode": "bearer", "options": {}}
    base.update(kw)
    return base


def test_fetch_upstream_models_ok(monkeypatch):
    fake = _FakeHTTP({"/v1/models": _Resp(200, {"data": [{"id": "z"}, {"id": "a"}, {"id": "a"}]})})
    monkeypatch.setattr(protocols, "HTTP", fake)
    res = protocols.fetch_upstream_models(_provider(), key="sk-x")
    assert res["ok"] is True
    assert res["models"] == ["a", "z"]                 # 去重 + 排序
    assert res["count"] == 2
    assert res["url"] == "https://api.example.com/v1/models"
    assert fake.seen[0]["method"] == "GET"             # 只发 GET：零成本，不可能出图
    assert fake.seen[0]["headers"]["Authorization"] == "Bearer sk-x"


def test_fetch_upstream_models_uses_configured_path(monkeypatch):
    fake = _FakeHTTP({"/openapi/models": _Resp(200, {"data": [{"id": "m"}]})})
    monkeypatch.setattr(protocols, "HTTP", fake)
    res = protocols.fetch_upstream_models(_provider(options={"models_path": "/openapi/models"}), key="k")
    assert res["ok"] is True and res["url"].endswith("/openapi/models")


def test_fetch_upstream_models_falls_back_to_second_path(monkeypatch):
    fake = _FakeHTTP({"/models": _Resp(200, {"data": [{"id": "m"}]})})   # /v1/models 会 404
    monkeypatch.setattr(protocols, "HTTP", fake)
    res = protocols.fetch_upstream_models(_provider(), key="k")
    assert res["ok"] is True and res["url"].endswith("/models")
    assert [s["url"].split("/", 3)[-1] for s in fake.seen] == ["v1/models", "models"]


def test_fetch_upstream_models_reports_failure(monkeypatch):
    fake = _FakeHTTP({})                                # 全都 404
    monkeypatch.setattr(protocols, "HTTP", fake)
    res = protocols.fetch_upstream_models(_provider(), key="k")
    assert res["ok"] is False and "拉取失败" in res["error"]


def test_fetch_upstream_models_needs_key_and_base():
    assert protocols.fetch_upstream_models(_provider(), key="")["ok"] is False
    assert protocols.fetch_upstream_models(_provider(base_url=""), key="k")["ok"] is False


# ------------------------------------------------------------------ 接口

def test_fetch_models_endpoint(login, make_provider, monkeypatch):
    make_provider(key="demo", model_map={"gpt-image-2": "openai/gpt-image-2"})
    fake = _FakeHTTP({"/v1/models": _Resp(200, {"data": [{"id": "gpt-image-2"}, {"id": "banana"}]})})
    monkeypatch.setattr(protocols, "HTTP", fake)
    r = login.post("/api/providers/demo/fetch-models")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["models"] == ["banana", "gpt-image-2"]
    assert d["existing"] == ["gpt-image-2"]             # 前端据此标「已在映射里」


def test_fetch_models_endpoint_unknown_provider(login):
    r = login.post("/api/providers/nope/fetch-models")
    assert r.status_code == 404
    assert "不存在" in r.json()["error"]


def test_fetch_models_endpoint_needs_login(client, make_provider):
    make_provider(key="demo")
    assert client.post("/api/providers/demo/fetch-models").status_code in (401, 403)


def test_fetch_models_endpoint_surfaces_upstream_error(login, make_provider, monkeypatch):
    make_provider(key="demo")
    monkeypatch.setattr(protocols, "HTTP", _FakeHTTP({}))
    r = login.post("/api/providers/demo/fetch-models")
    assert r.status_code == 400
    assert "拉取失败" in r.json()["error"]


def test_sysinfo_exposes_gate_and_router(login):
    d = login.get("/api/sysinfo").json()
    assert d["gate"]["global_limit"] >= 0
    assert d["router"]["max_attempts"] >= 1
    assert 429 in d["router"]["retryable"]


# ------------------------------------------------------------------ 多把 key 取并集（分组密钥）

class _FakeHTTPByKey:
    """按 Authorization 里的 key 返回不同模型列表 —— 模拟 sub2api 系「密钥绑分组」。

    实测：change2pro 的 gemini 组 key 只看到 4 个 gemini 模型、gpt 组 key 只看到 3 个 gpt 模型。
    """

    def __init__(self, by_key):
        self.by_key = by_key
        self.seen = []

    def get(self, url, headers=None, timeout=None):
        self.seen.append({"method": "GET", "url": url, "headers": headers})
        key = str((headers or {}).get("Authorization") or "").replace("Bearer ", "")
        models = self.by_key.get(key)
        if models is None:
            return _Resp(404, None, "not found")
        return _Resp(200, {"data": [{"id": m} for m in models]})


def _two_key_provider():
    return _provider(base_url="https://api.change2pro.com")


def test_fetch_upstream_models_multi_unions_key_groups(monkeypatch):
    """必须逐把 key 拉并取并集：只拉第一把会丢掉其它分组的模型（用户实测的 bug）。"""
    fake = _FakeHTTPByKey({"sk-gemini": ["gemini-3-pro-image", "gemini-3.1-flash-image"],
                           "sk-gpt": ["gpt-image-2", "gpt-image-2.5-flare"]})
    monkeypatch.setattr(protocols, "HTTP", fake)
    entries = [{"label": "gemini", "key": "sk-gemini"}, {"label": "gpt", "key": "sk-gpt"}]
    res = protocols.fetch_upstream_models_multi(_two_key_provider(), entries)
    assert res["ok"] is True
    assert res["models"] == ["gemini-3-pro-image", "gemini-3.1-flash-image",
                             "gpt-image-2", "gpt-image-2.5-flare"]          # 并集 + 排序
    assert res["count"] == 4
    assert res["groups"]["gemini"] == ["gemini-3-pro-image", "gemini-3.1-flash-image"]
    assert res["groups"]["gpt"] == ["gpt-image-2", "gpt-image-2.5-flare"]
    assert res["errors"] == {}
    assert len(fake.seen) == 2 and all(s["method"] == "GET" for s in fake.seen)   # 零成本


def test_fetch_upstream_models_multi_group_filter_and_partial_failure(monkeypatch):
    """带 group 参数只拉那一把；某把失败时其余照收，失败原因进 errors。"""
    fake = _FakeHTTPByKey({"sk-gemini": ["m-gemini"], "sk-bad": None})
    monkeypatch.setattr(protocols, "HTTP", fake)
    entries = [{"label": "gemini", "key": "sk-gemini"}, {"label": "gpt", "key": "sk-bad"}]

    only = protocols.fetch_upstream_models_multi(_two_key_provider(), entries, group="gemini")
    assert only["ok"] is True and only["models"] == ["m-gemini"] and len(fake.seen) == 1

    both = protocols.fetch_upstream_models_multi(_two_key_provider(), entries)
    assert both["ok"] is True and both["models"] == ["m-gemini"]
    assert "拉取失败" in both["errors"]["gpt"]

    none = protocols.fetch_upstream_models_multi(_two_key_provider(), [], group="")
    assert none["ok"] is False and "还没配 API key" in none["error"]


def test_fetch_models_endpoint_unions_all_key_groups(login, make_provider, monkeypatch):
    make_provider(key="c2p", api_key="gemini::sk-gemini\ngpt::sk-gpt",
                  base_url="https://api.change2pro.com")
    fake = _FakeHTTPByKey({"sk-gemini": ["gemini-3-pro-image"], "sk-gpt": ["gpt-image-2"]})
    monkeypatch.setattr(protocols, "HTTP", fake)
    d = login.post("/api/providers/c2p/fetch-models").json()
    assert d["ok"] is True
    assert d["models"] == ["gemini-3-pro-image", "gpt-image-2"]      # 两把 key 的并集
    assert sorted(d["groups"]) == ["gemini", "gpt"]

    one = login.post("/api/providers/c2p/fetch-models?group=gpt").json()
    assert one["models"] == ["gpt-image-2"] and len(fake.seen) == 3
