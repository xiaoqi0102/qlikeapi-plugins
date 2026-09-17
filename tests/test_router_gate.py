"""阶段 1 路由语义测试：并发闸门 + 优先级分档降级 + 路由决策响应头。

全部零成本：上游一律被 monkeypatch 拦住，用例里没有任何真实网络请求。
"""
from __future__ import annotations

import threading

import pytest

from app import protocols, relay, store

MASTER = {"x-qlikeapi-token": "unit-test-master-token"}


@pytest.fixture(autouse=True)
def clean_gate():
    """闸门是进程级单例，用例之间必须清干净。"""
    g = relay.gate
    g._busy.clear()
    g._waiting = 0
    g.rejected = 0
    yield g
    g._busy.clear()
    g._waiting = 0
    g.rejected = 0


def _prov(key, priority=0, **opt):
    return {"key": key, "priority": priority, "options": opt}


# ------------------------------------------------------------------ 闸门本体

def test_gate_off_when_limit_zero(clean_gate):
    """默认不限并发：limit<=0 直接放行，行为与老版本一致。"""
    assert clean_gate.acquire("a", 0) is None
    assert clean_gate.stats()["busy"] == {}


def test_gate_blocks_then_releases(clean_gate, monkeypatch):
    monkeypatch.setattr(relay, "QUEUE_WAIT", 0.1)
    assert clean_gate.acquire("a", 1) is None
    assert "排队" in clean_gate.acquire("a", 1)          # 槽位被占 → 排队超时被拒
    clean_gate.release("a")
    assert clean_gate.acquire("a", 1) is None            # 释放后又能拿到
    clean_gate.release("a")


def test_gate_queue_cap(clean_gate, monkeypatch):
    """等待队列满 → 直接拒（不无限堆积）。"""
    monkeypatch.setattr(relay, "MAX_WAITING", 0)
    assert "队列已满" in clean_gate.acquire("a", 1)


def test_gate_counts_waiting_threads(clean_gate, monkeypatch):
    """一个线程占着槽位时，另一个线程会进等待队列（waiting 计数可见）。"""
    monkeypatch.setattr(relay, "QUEUE_WAIT", 0.6)
    assert clean_gate.acquire("a", 1) is None
    seen = {}

    def _worker():
        seen["rej"] = clean_gate.acquire("a", 1)

    t = threading.Thread(target=_worker)
    t.start()
    for _ in range(50):
        if clean_gate.stats()["waiting"]:
            break
        threading.Event().wait(0.01)
    assert clean_gate.stats()["waiting"] == 1
    clean_gate.release("a")                              # 让等待的线程拿到槽位
    t.join(timeout=3)
    assert seen.get("rej") is None
    clean_gate.release("a")


def test_limit_of_prefers_provider_option(clean_gate, monkeypatch):
    monkeypatch.setattr(relay, "GLOBAL_LIMIT", 5)
    assert clean_gate.limit_of(_prov("a")) == 5                    # 渠道没配 → 用全局
    assert clean_gate.limit_of(_prov("a", max_concurrency=2)) == 2  # 渠道配了 → 用渠道
    assert clean_gate.limit_of(_prov("a", max_concurrency="x")) == 5  # 脏值 → 退回全局


# ------------------------------------------------------------------ 分档 / 尝试序列

def test_tiers_group_by_priority():
    chain = [_prov("a", 20), _prov("b", 20), _prov("c", 10)]
    tiers = relay._tiers(chain)
    assert [[p["key"] for p in t] for t in tiers] == [["a", "b"], ["c"]]


def test_attempt_sequence_degrades_then_retries():
    """同档 retry=0：每家只试一次；retry=1：本档试两轮再降档。"""
    chain = [_prov("a", 20), _prov("b", 10), _prov("c", 10)]
    seq = relay._attempt_sequence(relay._tiers(chain))
    assert [(p["key"], ti) for p, ti in seq] == [("a", 0), ("b", 1), ("c", 1)]

    chain2 = [_prov("a", 20, retry=1), _prov("b", 10)]
    seq2 = relay._attempt_sequence(relay._tiers(chain2))
    assert [(p["key"], ti) for p, ti in seq2] == [("a", 0), ("a", 0), ("b", 1)]


def test_tier_retry_is_capped():
    """重试次数上限 2，避免上游抖一下就把同一个请求打很多遍（重复扣费风险）。"""
    assert relay._tier_retry([_prov("a", retry=9)]) == 2
    assert relay._tier_retry([_prov("a", retry="bad")]) == 0


def test_attempt_sequence_is_capped(monkeypatch):
    monkeypatch.setattr(relay, "MAX_ROUTE_ATTEMPTS", 3)
    chain = [_prov("a", 30), _prov("b", 20), _prov("c", 10), _prov("d", 5)]
    assert len(relay._attempt_sequence(relay._tiers(chain))) == 3


# ------------------------------------------------------------------ 路由决策可观测

def test_router_reports_decision_headers(client, make_provider, fake_upstream):
    """首档即成功：Degrade=0、Attempt=1、Chain 是完整候选链。"""
    make_provider(key="a", priority=20, model_map={"gpt-image-2": "gpt-image-2"})
    make_provider(key="b", priority=10, model_map={"gpt-image-2": "gpt-image-2"})
    fake_upstream(200, {"data": [{"url": "https://k.example.com/1.png"}]}, '{"data":[]}')
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"}, headers=MASTER)
    assert r.status_code == 200
    assert r.headers["X-QLike-Provider"] == "a"
    assert r.headers["X-QLike-Chain"] == "a,b"
    assert r.headers["X-QLike-Attempt"] == "1"
    assert r.headers["X-QLike-Degrade"] == "0"
    assert r.headers["X-QLike-Queue-Ms"].isdigit()


def test_router_marks_degrade_when_falling_to_next_tier(client, make_provider, monkeypatch):
    """首档 5xx → 降到第二档：Degrade=1、Failover=1。"""
    make_provider(key="a", priority=20, model_map={"gpt-image-2": "gpt-image-2"})
    make_provider(key="b", priority=10, model_map={"gpt-image-2": "gpt-image-2"})
    seq = iter([(503, {"error": {"message": "down"}}, "down"),
                (200, {"data": [{"url": "https://k.example.com/2.png"}]}, "ok")])
    monkeypatch.setattr(protocols, "call_upstream", lambda *a, **k: next(seq))
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"}, headers=MASTER)
    assert r.status_code == 200
    assert r.headers["X-QLike-Provider"] == "b"
    assert r.headers["X-QLike-Degrade"] == "1"
    assert r.headers["X-QLike-Failover"] == "1"
    assert r.headers["X-QLike-Attempt"] == "2"


def test_router_all_saturated_returns_503_with_retry_after(client, make_provider, monkeypatch, clean_gate):
    """所有候选都在排队 → 一次上游都不打，直接 503 + Retry-After（零成本、可退避）。"""
    make_provider(key="a", model_map={"gpt-image-2": "gpt-image-2"}, options={"max_concurrency": 1})
    calls = []
    monkeypatch.setattr(protocols, "call_upstream",
                        lambda *a, **k: calls.append(1) or (200, {"data": []}, "ok"))
    monkeypatch.setattr(relay, "QUEUE_WAIT", 0.1)
    assert clean_gate.acquire("a", 1) is None            # 假装这条渠道已经跑满
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"}, headers=MASTER)
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "3"
    assert r.headers["X-QLike-Chain"] == "a"
    assert calls == []                                   # 关键：没打上游
    assert "并发已满" in r.json()["error"]["message"]
    clean_gate.release("a")


def test_direct_entry_also_respects_gate(client, make_provider, monkeypatch, clean_gate):
    """直连 /up/<渠道>/... 也走闸门（不然绕过统一入口就能把上游打爆）。"""
    make_provider(key="a", model_map={"gpt-image-2": "gpt-image-2"}, options={"max_concurrency": 1})
    monkeypatch.setattr(protocols, "call_upstream", lambda *a, **k: (200, {"data": []}, "ok"))
    monkeypatch.setattr(relay, "QUEUE_WAIT", 0.1)
    assert clean_gate.acquire("a", 1) is None
    r = client.post("/up/a/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"}, headers=MASTER)
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "3"
    clean_gate.release("a")


def test_saturated_tier_switches_to_next_provider(client, make_provider, monkeypatch, clean_gate):
    """某家排队超时 → 换下一家，而不是硬等（降低单点依赖）。"""
    make_provider(key="a", priority=20, model_map={"gpt-image-2": "gpt-image-2"}, options={"max_concurrency": 1})
    make_provider(key="b", priority=10, model_map={"gpt-image-2": "gpt-image-2"})
    monkeypatch.setattr(relay, "QUEUE_WAIT", 0.1)
    seen = []
    monkeypatch.setattr(protocols, "call_upstream",
                        lambda *a, **k: seen.append(1) or (200, {"data": [{"url": "https://k/x.png"}]}, "ok"))
    assert clean_gate.acquire("a", 1) is None            # a 满
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "x"}, headers=MASTER)
    assert r.status_code == 200
    assert r.headers["X-QLike-Provider"] == "b"
    assert len(seen) == 1
    clean_gate.release("a")


# ------------------------------------------------------------------ 日志里的完整请求

def test_log_records_full_upstream_request(client, make_provider, fake_upstream):
    """日志要能还原「完整请求」：方法 / 完整 URL / 请求头（密钥一律占位）。"""
    make_provider(key="a", base_url="https://up.example.com", api_key="sk-secret-123456",
                  model_map={"gpt-image-2": "gpt-image-2"})
    fake_upstream(200, {"data": [{"url": "https://k.example.com/1.png"}]}, '{"data":[]}')
    r = client.post("/v1/images/generations", json={"model": "gpt-image-2", "prompt": "猫"}, headers=MASTER)
    assert r.status_code == 200
    row = store.one("SELECT * FROM logs WHERE kind='relay' ORDER BY id DESC LIMIT 1")
    assert row["upstream_url"] == "https://up.example.com/v1/images/generations"
    assert row["upstream_method"] == "POST"
    headers = store.json.loads(row["upstream_headers"])
    assert headers["Authorization"] == "Bearer YOUR_API_KEY"      # 占位符，绝不落明文密钥
    assert "sk-secret-123456" not in row["upstream_headers"]


def test_log_row_tolerates_missing_upstream_meta(db):
    """老日志（升级前写的）没有这三列 → 前端要能优雅降级，不能报错。"""
    db.log_row("a", "m", "/v1/images", 200, None, 12, None, {"model": "m"}, {"model": "m"}, "{}")
    row = db.one("SELECT * FROM logs ORDER BY id DESC LIMIT 1")
    assert row["upstream_url"] is None and row["upstream_headers"] is None
