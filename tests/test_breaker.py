"""渠道熔断语义测试（v3.14.2）。

背景：change2pro 的 gpt-image-2 分组账号池是空的，上游一直回 503。
一连串请求（包括我自己的零成本探针）把渠道级 `fail_streak` 顶到 5，
于是**整条渠道**（含它工作正常的 gemini 面）被自动停用 10 分钟 —— 用户实测踩到。

本用例锁住两点：
  1. 真打过上游的失败才计数（每次 +1）；
  2. 全部 key 已在冷却里的「短路」请求（attempts=0）**不再重复计数** ——
     否则一串请求就能把渠道熔断掉。

零成本：上游被 fake_upstream 拦住，用例里没有任何真实网络请求。
"""
from __future__ import annotations

from app import store


def _hit(client, key="c", model="gpt-image-2"):
    return client.post(f"/up/{key}/v1/images/generations", json={"model": model, "prompt": "画一只猫"})


def test_all_keys_cooling_does_not_bump_breaker(client, login, make_provider, fake_upstream):
    make_provider(key="c", protocol="change2pro", base_url="https://api.change2pro.com",
                  model_map={"gpt-image-2": "gpt-image-2"})
    fake_upstream(503, {"message": "No available compatible accounts"})

    # 第 1 次：真打了上游 → 计 1 次失败
    r = _hit(client)
    assert r.status_code == 503, r.text
    assert store.get_provider("c")["fail_streak"] == 1

    # 之后 key 已在冷却里 → 短路（不打上游），不该再累计
    for _ in range(4):
        assert _hit(client).status_code == 503
    p = store.get_provider("c")
    assert p["fail_streak"] == 1, "冷却短路被重复计入熔断 —— 一串请求就能把渠道停掉"
    assert p["enabled"] == 1


def test_repeated_real_failures_still_trip_the_breaker(client, login, make_provider, fake_upstream):
    """真失败够阈值仍要熔断（别把护栏改没了）。"""
    make_provider(key="c2", protocol="change2pro", base_url="https://api.change2pro.com",
                  model_map={"gpt-image-2": "gpt-image-2"})
    calls = fake_upstream(503, {"message": "No available compatible accounts"})
    from app import relay

    for _ in range(store.AUTO_DISABLE_AFTER):
        _hit(client, key="c2")
        relay._KEY_STATE.get("c2", {}).get("cooldown", {}).clear()   # 强制下一次仍真打上游
    p = store.get_provider("c2")
    assert p["fail_streak"] >= store.AUTO_DISABLE_AFTER
    assert p["enabled"] == 0 and p["auto_disabled_at"], "够数应当自动停用"
    assert calls, "应当真的打过上游"
