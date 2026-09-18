"""渠道实例 ↔ 站点余额联动（v3.15.1）。

用户口径：加了渠道，站点余额条目要跟着同步出现；站点要的令牌 / id 由用户手动填。
"""
from app import balances, store


def _sites():
    return store.list_sites()


def test_creates_site_with_plugin_declared_type(db):
    """插件声明了 site_type 就按它建（change2pro→sub2api、aicost→newapi）。"""
    info = balances.ensure_site_for_provider(
        {"key": "cp", "label": "change2pro", "protocol": "change2pro", "base_url": "https://api.change2pro.com"})
    assert info["created"] is True
    assert info["type"] == "sub2api"
    assert info["name"] == "change2pro"
    s = [x for x in _sites() if x["id"] == info["site_id"]][0]
    assert s["base_url"] == "https://api.change2pro.com"
    # 凭据留空，等用户手填
    assert not (s.get("token") or "").strip()
    assert not (s.get("uid") or "").strip()

    info2 = balances.ensure_site_for_provider(
        {"key": "ac", "label": "aicost", "protocol": "aicost", "base_url": "https://www.aicost.me"})
    assert info2["type"] == "newapi"


def test_same_base_url_reused_and_credentials_kept(db):
    """同一个 base_url 只建一个站点，且绝不覆盖用户填好的令牌 / uid。"""
    first = balances.ensure_site_for_provider(
        {"key": "cp", "label": "change2pro", "protocol": "change2pro", "base_url": "https://api.change2pro.com"})
    store.save_site({"id": first["site_id"], "name": "change2pro", "type": "sub2api",
                     "base_url": "https://api.change2pro.com", "token": "sk-user-filled", "uid": "2774"})
    again = balances.ensure_site_for_provider(
        {"key": "cp2", "label": "另一个实例", "protocol": "change2pro", "base_url": "https://api.change2pro.com/"})
    assert again["site_id"] == first["site_id"] and again["created"] is False
    assert len(_sites()) == 1
    s = _sites()[0]
    assert s["token"] == "sk-user-filled" and s["uid"] == "2774"


def test_no_base_url_returns_error(db):
    info = balances.ensure_site_for_provider({"key": "x", "protocol": "openai_images", "base_url": ""})
    assert info.get("error") and not info.get("site_id")
    assert _sites() == []


def test_unknown_protocol_falls_back_to_manual(db):
    """没声明的协议按「手工记账」建，免得余额查询天天报错。"""
    info = balances.ensure_site_for_provider(
        {"key": "z", "label": "杂牌", "protocol": "does_not_exist", "base_url": "https://x.example.com"})
    assert info["type"] == "manual"


def test_provider_save_links_site(client, login):
    """走接口：保存渠道后自动建站点条目，并把 site_id 回填到渠道上。"""
    r = client.post("/api/providers", json={
        "key": "cp3", "label": "change2pro-新", "protocol": "change2pro",
        "base_url": "https://api.change2pro.com", "enabled": True, "priority": 1})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["site"]["created"] is True
    assert d["site"]["type"] == "sub2api"
    p = store.get_provider("cp3")
    assert p["site_id"] == d["site"]["site_id"]


def test_job_id_picks_task_id(db):
    """异步任务页认任务号：插件自己认的优先，再兜底常见字段。"""
    from app import relay

    class _Ch:
        def task_id(self, d):
            return (d or {}).get("task_id") or ""

    assert relay._job_id(_Ch(), {"task_id": "abc123", "status": "pending"}) == "abc123"
    assert relay._job_id(None, {"request_id": "req-9"}) == "req-9"
    assert relay._job_id(None, {"status": "pending"}) == ""
