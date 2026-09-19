"""store.py —— 数据层：渠道/令牌/路由/价格/熔断/加密落库。"""
from __future__ import annotations

import time

from app import crypto, store

# ------------------------------------------------------------------ 渠道实例

def test_init_db_seeds_admin_and_tables(db):
    u = store.one("SELECT * FROM users WHERE username=?", ("tester",))
    assert u and u["pass_hash"]
    tables = {r["name"] for r in store.rows("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"providers", "logs", "sites", "model_prices", "routes", "tokens", "health"} <= tables


def test_init_db_migrates_legacy_columns(db, tmp_path, monkeypatch):
    """老库（没有 weight/fail_streak 等列）升级后必须能读出这些列。"""
    import sqlite3
    legacy = tmp_path / "legacy.db"
    c = sqlite3.connect(legacy)
    c.execute("CREATE TABLE providers (key TEXT PRIMARY KEY, label TEXT, protocol TEXT, base_url TEXT,"
              " auth_mode TEXT, api_key TEXT, model_map TEXT, options TEXT, enabled INTEGER, priority INTEGER,"
              " updated_at INTEGER)")
    c.execute("INSERT INTO providers(key,label,protocol,base_url,auth_mode,api_key,model_map,options,"
              "enabled,priority,updated_at) VALUES('old','老渠道','openai_images','https://a','bearer',"
              "'sk-plain','{}','{}',1,0,0)")
    c.commit()
    c.close()
    monkeypatch.setattr(store, "DB_PATH", str(legacy))
    store.init_db()
    cols = {r["name"] for r in store.rows("PRAGMA table_info(providers)")}
    assert {"weight", "fail_streak", "site_id", "cooldown_until"} <= cols
    # 老库里的明文密钥在初始化时被自动加密（且仍能正常读回）
    raw = store.one("SELECT api_key FROM providers WHERE key='old'")["api_key"]
    assert crypto.is_encrypted(raw)
    assert store.get_provider("old")["api_key"] == "sk-plain"


def test_provider_key_roundtrip_and_mask(db, make_provider):
    make_provider(key="p1", api_key="sk-aaaaaaaaaaaaaaaaaaaa\nsk-bbbbbbbbbbbbbbbbbbbb")
    p = store.get_provider("p1")
    assert store.provider_keys(p) == ["sk-aaaaaaaaaaaaaaaaaaaa", "sk-bbbbbbbbbbbbbbbbbbbb"]
    assert store.mask(p["api_key"].splitlines()[0]) == "sk-aaa…aaaa"
    # 落库的是密文
    assert crypto.is_encrypted(store.one("SELECT api_key FROM providers WHERE key='p1'")["api_key"])


def test_provider_model_map_and_options_json(db, make_provider):
    make_provider(key="p2", model_map={"a": "up-a"}, options={"drop_fields": ["x"]})
    p = store.get_provider("p2")
    assert p["model_map"] == {"a": "up-a"} and p["options"] == {"drop_fields": ["x"]}


def test_list_providers_orders_by_priority_desc(db, make_provider):
    make_provider(key="low", priority=1)
    make_provider(key="high", priority=20)
    make_provider(key="disabled", priority=99, enabled=0)
    assert [p["key"] for p in store.list_providers()] == ["disabled", "high", "low"]
    assert store.list_providers(only_enabled=True)[0]["key"] == "high"


def test_get_provider_without_keys(db, make_provider):
    make_provider(key="p3")
    assert "api_key" not in store.get_provider("p3", with_keys=False)
    assert store.get_provider("不存在") is None


# ------------------------------------------------------------------ 请求日志 / 健康

def test_log_row_writes_accounting_fields(db):
    store.log_row("p1", "gpt-image-2", "/v1/images/generations", 200, 200, 1234, None,
                  {"prompt": "猫"}, {"prompt": "猫"}, '{"data":[]}', attempts=2, key_index=1,
                  images=2, cost=0.12, cost_currency="USD", token="comfyui", token_id=7)
    row = store.rows("SELECT * FROM logs")[0]
    assert row["provider"] == "p1" and row["http_status"] == 200 and row["attempts"] == 2
    assert row["images"] == 2 and row["cost"] == 0.12 and row["cost_currency"] == "USD"
    assert row["token"] == "comfyui" and row["token_id"] == 7 and row["kind"] == "relay"


def test_log_row_never_raises(db, monkeypatch):
    """记账失败绝不能影响主流程。"""
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "connect", boom)
    store.log_row("p", "m", "/x", 500, None, 0, "err", {}, None, None)


def test_set_health_upserts(db):
    store.set_health("p1", {"ok": True, "upstream_status": 400, "ms": 88, "upstream_message": "prompt is required"})
    store.set_health("p1", {"ok": False, "upstream_status": 503, "ms": 900, "error": "上游异常"})
    h = store.one("SELECT * FROM health WHERE provider='p1'")
    assert h["ok"] == 0 and h["upstream_status"] == 503 and h["message"] == "上游异常"


# ------------------------------------------------------------------ 模型路由

def test_routes_crud(db):
    assert store.get_chain("gpt-image-2") is None
    store.set_chain("gpt-image-2", ["a", "b"], note="手工链")
    assert store.get_chain("gpt-image-2") == ["a", "b"]
    assert store.list_routes()[0]["note"] == "手工链"
    store.set_chain("gpt-image-2", ["b"])
    assert store.get_chain("gpt-image-2") == ["b"]
    assert store.list_routes()[0]["note"] == ""            # 不带备注 → 覆盖为空
    store.delete_chain("gpt-image-2")
    assert store.get_chain("gpt-image-2") is None


# ------------------------------------------------------------------ 价格

def test_price_lookup_priority(db):
    store.set_price_full("*", "*", 0.5, "CNY", source="manual", note="兜底")
    store.set_price_full("gpt-image-2", "*", 0.03, "CNY", source="manual")
    store.set_price_full("gpt-image-2", "c2p", 0.06, "USD", source="upstream")
    # 渠道专属 > 全局模型价 > 全局兜底
    assert store.price_for("gpt-image-2", "c2p") == 0.06
    assert store.price_for("gpt-image-2", "other") == 0.03
    assert store.price_for("完全没定价的模型", "other") == 0.5
    assert store.price_row("gpt-image-2", "c2p")["currency"] == "USD"


def test_price_exact_does_not_fall_back(db):
    """price_exact 只认「模型 + 渠道」精确命中 —— 手工价保命靠它，回落会把全局价误判成手工价。"""
    store.set_price_full("*", "*", 0.5, "CNY", source="manual", note="兜底")
    store.set_price_full("gpt-image-2", "*", 0.03, "CNY", source="manual")
    store.set_price_full("gpt-image-2", "c2p", 0.06, "USD", source="upstream")
    assert store.price_exact("gpt-image-2", "c2p")["source"] == "upstream"
    assert store.price_exact("gpt-image-2", "c2p")["currency"] == "USD"
    # 渠道没有专属行 → None（不拿全局价 / 兜底价顶）
    assert store.price_exact("gpt-image-2", "other") is None
    assert store.price_exact("完全没定价的模型", "other") is None


def test_estimate_cost(db):
    store.set_price_full("gpt-image-2", "c2p", 0.06, "USD", source="upstream")
    assert store.estimate_cost("c2p", "gpt-image-2", 3) == (0.18, "USD")
    assert store.estimate_cost("c2p", "gpt-image-2", None) == (0.06, "USD")   # 没给张数按 1 张
    assert store.estimate_cost("c2p", "no-price-model", 2) == (0.0, "CNY")    # 没定价 → 0


def test_prices_never_fabricated(db):
    """没配价格就是 0 —— 绝不猜价、绝不做汇率换算。"""
    assert store.list_prices() == []
    assert store.estimate_cost("任何", "任何", 1) == (0.0, "CNY")


# ------------------------------------------------------------------ 访问令牌

def test_token_crud_and_masking(db):
    tid = store.save_token({"name": "comfyui-本机", "note": "本机作图", "allowed_models": ["gpt-image-2"]})
    row = store.get_token(tid)
    assert row["name"] == "comfyui-本机"
    assert row["token"].startswith("sk-ql-")
    assert "…" in row["token_masked"] and row["token"] not in row["token_masked"]
    assert row["token_masked"] == store.mask(row["token"])
    assert row["allowed_models"] == ["gpt-image-2"]
    assert row["unlimited"] is True and row["over_quota"] is False and row["expired"] is False
    assert store.token_by_value(row["token"])["id"] == tid
    assert store.token_by_value("") is None and store.token_by_value("不存在") is None


def test_token_patch_and_quota_state(db):
    tid = store.save_token({"name": "t", "quota": 1.0, "quota_currency": "USD"})
    assert store.patch_token(tid, {"enabled": False, "quota": 0.5, "allowed_models": ["a"],
                                   "ip_whitelist": "10.0.0.1, 127.0.0.1"}) is True
    row = store.get_token(tid)
    assert row["enabled"] == 0 and row["quota"] == 0.5 and row["allowed_models"] == ["a"]
    assert row["ips"] == ["10.0.0.1", "127.0.0.1"]
    assert store.patch_token(tid, {}) is False          # 没字段可改


def test_token_expiry_and_over_quota_flags(db):
    tid = store.save_token({"name": "过期", "expires_at": int(time.time()) - 10, "quota": 1})
    row = store.get_token(tid)
    assert row["expired"] is True
    store.bump_token_usage(tid, images=3, cost=2.0)
    row = store.get_token(tid)
    assert row["used_images"] == 3 and row["used_cost"] == 2.0 and row["used_requests"] == 1
    assert row["over_quota"] is True


def test_bump_token_usage_noop_without_id(db):
    store.bump_token_usage(None, images=1, cost=1)       # 主密钥调用不记账，也不能报错


def test_delete_token(db):
    tid = store.save_token({"name": "x"})
    store.delete_token(tid)
    assert store.get_token(tid) is None and store.list_tokens() == []


# ------------------------------------------------------------------ 自动熔断 / 恢复

def test_bump_provider_fail_disables_after_threshold(db, make_provider):
    """连续失败到阈值 → 自动停用 + 冷却；人工停用的渠道不受影响。"""
    make_provider(key="flaky")
    store.execute("UPDATE providers SET site_id=? WHERE key='flaky'", (1,))
    for _ in range(2):
        assert store.bump_provider_fail("flaky", "连接失败", disconnect=True)["auto_disabled"] is False
    out = store.bump_provider_fail("flaky", "连接失败", disconnect=True)
    assert out["auto_disabled"] is True and out["fail_streak"] == 3
    p = store.get_provider("flaky")
    assert p["enabled"] == 0 and p["disabled_reason"] == "连接失败" and p["cooldown_until"] > time.time()
    # 冷却没到 → 不进候选
    assert store.auto_recover_candidates() == []


def test_bump_provider_fail_without_disconnect_only_counts(db, make_provider):
    make_provider(key="p")
    for _ in range(5):
        out = store.bump_provider_fail("p", "4xx")       # 客户端参数错不算渠道故障
    assert out["auto_disabled"] is False
    assert store.get_provider("p")["enabled"] == 1
    assert store.bump_provider_fail("不存在", "x")["fail_streak"] == 0


def test_clear_and_enable_reset_state(db, make_provider):
    make_provider(key="p")
    store.bump_provider_fail("p", "x", disconnect=True)
    store.clear_provider_fail("p")
    assert store.get_provider("p")["fail_streak"] == 0
    store.disable_provider("p", "余额不足", cooldown=60)
    assert store.get_provider("p")["enabled"] == 0
    store.enable_provider("p")
    p = store.get_provider("p")
    assert p["enabled"] == 1 and p["disabled_reason"] is None and p["cooldown_until"] is None


def test_auto_recover_candidates_after_cooldown(db, make_provider):
    make_provider(key="p")
    for _ in range(3):
        store.bump_provider_fail("p", "连接失败", disconnect=True)
    store.execute("UPDATE providers SET cooldown_until=? WHERE key='p'", (int(time.time()) - 1,))
    assert store.auto_recover_candidates() == ["p"]
    assert store.auto_recover_providers() == ["p"]        # 人工触发才会真的放回
    assert store.get_provider("p")["enabled"] == 1


def test_manual_disable_is_never_auto_recovered(db, make_provider):
    """人手停用的渠道（auto_disabled_at 为空）不许被自动恢复器动到。"""
    make_provider(key="p", enabled=0)
    store.disable_provider("p", "手工停用", manual=True)
    assert store.auto_recover_candidates() == []
    assert store.auto_recover_providers() == []
    assert store.get_provider("p")["enabled"] == 0


def test_extend_cooldown_keeps_disabled(db, make_provider):
    make_provider(key="p", enabled=0)
    store.disable_provider("p", "自动停用", cooldown=10, manual=False)
    store.extend_cooldown("p", 600)
    p = store.get_provider("p")
    assert p["enabled"] == 0 and p["cooldown_until"] > time.time() + 300


# ------------------------------------------------------------------ 加密健康度 / 站点

def test_enc_health_reports_plaintext_and_encrypted(db, make_provider):
    make_provider(key="ok", api_key="sk-1")
    store.execute("INSERT INTO providers(key,label,protocol,base_url,auth_mode,api_key,model_map,options,"
                  "enabled,priority,weight,updated_at) VALUES('plain','裸奔','openai_images','https://a',"
                  "'bearer','sk-plaintext','{}','{}',1,0,1,0)")
    h = store.enc_health()
    assert h["plaintext"] == 1 and h["encrypted"] == 1 and h["broken"] == 0


def test_enc_health_counts_broken_when_master_key_changed(db, make_provider, monkeypatch):
    make_provider(key="p", api_key="sk-1")
    monkeypatch.setenv("QLIKEAPI_ENC_KEY", "换了个主密钥")
    monkeypatch.setenv("QLIKEAPI_SECRET", "换了个主密钥")
    assert store.enc_health()["broken"] == 1


def test_site_crud_and_balance_threshold(db):
    sid = store.save_site({"name": "某中转站", "type": "manual", "extra": {"threshold": 5, "manual_balance": 12.5}})
    s = store.get_site(sid)
    assert s["name"] == "某中转站" and s["extra"]["threshold"] == 5
    store.update_site_result(sid, {"balance": 3.2, "unit": "USD", "used": 1.0, "plan": ""})
    s = store.get_site(sid)
    assert s["last_balance"] == 3.2 and s["last_unit"] == "USD"
    assert store.list_sites()[0]["id"] == sid
    assert store.get_site(9999) is None


def test_site_token_is_encrypted(db):
    sid = store.save_site({"name": "s", "type": "newapi", "token": "plain-token-value"})
    assert crypto.is_encrypted(store.one("SELECT token FROM sites WHERE id=?", (sid,))["token"])
    assert store.get_site(sid)["token"] == "plain-token-value"


# ------------------------------------------------------------------ 任务表

def test_connections_are_usable_as_context_managers(db):
    with store.connect() as c:
        c.execute("INSERT INTO health(provider,ok,checked_at) VALUES('x',1,0)")
    assert store.one("SELECT ok FROM health WHERE provider='x'")["ok"] == 1
