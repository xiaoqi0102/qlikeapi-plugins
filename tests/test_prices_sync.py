"""价格同步（按渠道独立）：从每个渠道「自己配置的平台」直读真实单价。

覆盖三件事：
  1. newapi 系站点 → 读 /api/pricing 的按次报价（quota_type=1 的 model_price）；
  2. 落库用「客户端模型名」（模型目录表格按这个名字查价，用上游名会显示未定价）；
  3. 只同步被点的那一个渠道，别的渠道一行价都不碰。
"""
from app import balances, store


def _site(name="aicost.me", stype="newapi", base="https://www.aicost.me"):
    return store.save_site({"name": name, "type": stype, "base_url": base, "token": "tk-000", "uid": "2774"})


def _bind(key, sid):
    store.execute("UPDATE providers SET site_id=? WHERE key=?", (sid, key))


def test_newapi_price_sync_uses_client_model_names(db, make_provider, monkeypatch):
    make_provider(key="aicost", protocol="aicost", base_url="https://www.aicost.me",
                  model_map={"gpt-image-2": "gpt-image-2",
                             "gemini-3-pro-image": "gemini-3-pro-image-preview"})
    _bind("aicost", _site())
    monkeypatch.setattr(balances, "fetch_newapi_pricing", lambda s, api_key='', groups=None: {
        "prices": {"gpt-image-2": {"price": 0.445},
                   "gemini-3-pro-image-preview": {"price": 0.12},
                   "some-chat-model": {"price": 0.01}},
        "currency": "USD", "total": 64})

    res = balances.sync_provider_prices("aicost")

    assert res["ok"] is True and res["source"] == "pricing"
    rows = {r["model"]: r for r in store.list_prices() if r["provider"] == "aicost"}
    assert rows["gpt-image-2"]["price"] == 0.445
    assert rows["gpt-image-2"]["currency"] == "USD"
    assert rows["gpt-image-2"]["source"] == "platform"
    # 客户端名落库（不是上游名 gemini-3-pro-image-preview）
    assert rows["gemini-3-pro-image"]["price"] == 0.12
    # 该渠道不对外暴露的模型不写价
    assert "some-chat-model" not in rows


def test_price_sync_only_touches_the_clicked_channel(db, make_provider, monkeypatch):
    make_provider(key="aicost", protocol="aicost", base_url="https://www.aicost.me")
    make_provider(key="qnaigc", protocol="qiniu", base_url="https://api.qnaigc.com")
    _bind("aicost", _site())
    monkeypatch.setattr(balances, "fetch_newapi_pricing", lambda s, api_key='', groups=None: {
        "prices": {"gpt-image-2": {"price": 0.445}}, "currency": "USD", "total": 1})

    balances.sync_provider_prices("aicost")

    assert [r["model"] for r in store.list_prices() if r["provider"] == "aicost"] == ["gpt-image-2"]
    assert [r for r in store.list_prices() if r["provider"] == "qnaigc"] == []


def test_price_sync_without_site_reports_readable_error(db, make_provider, monkeypatch):
    make_provider(key="lonely", protocol="openai_images", base_url="https://no-site.example.com")

    res = balances.sync_provider_prices("lonely")

    assert res["ok"] is False and "没关联站点" in res["error"]
    assert store.list_prices() == []


def test_unsupported_site_type_is_reported_not_silently_empty(db, make_provider, monkeypatch):
    make_provider(key="odd", protocol="openai_images", base_url="https://odd.example.com")
    _bind("odd", _site(name="手工站", stype="manual", base="https://odd.example.com"))

    res = balances.sync_provider_prices("odd")

    assert res["ok"] is False and "不支持价格直读" in res["error"]


def test_price_sync_keeps_manual_price_and_reports_skipped(db, make_provider, monkeypatch):
    """用户手工填过的价：同步要跳过它，并在结果里报「跳过了几条」。"""
    make_provider(key="aicost", protocol="aicost", base_url="https://www.aicost.me",
                  model_map={"gpt-image-2": "gpt-image-2", "gemini-3-pro-image": "gemini-3-pro-image-preview"})
    _bind("aicost", _site())
    # 手工价：gpt-image-2 用户自己填的（0.02），另一个模型没填
    store.set_price_full("gpt-image-2", "aicost", 0.02, "USD", source="manual", note="我按 Ozon 主图结算")
    monkeypatch.setattr(balances, "fetch_newapi_pricing", lambda s, api_key='', groups=None: {
        "prices": {"gpt-image-2": {"price": 0.445},
                   "gemini-3-pro-image-preview": {"price": 0.12}},
        "currency": "USD", "total": 2})

    res = balances.sync_provider_prices("aicost")

    assert res["ok"] is True
    assert res["skipped"] == ["gpt-image-2@aicost"]          # 手工价被跳过并报出来
    rows = {r["model"]: r for r in store.list_prices() if r["provider"] == "aicost"}
    assert rows["gpt-image-2"]["price"] == 0.02               # 手工价原封不动
    assert rows["gpt-image-2"]["source"] == "manual"
    assert rows["gemini-3-pro-image"]["price"] == 0.12        # 没手工价的照常同步
    assert rows["gemini-3-pro-image"]["source"] == "platform"


def test_sub2api_sync_keeps_manual_price(db, make_provider, monkeypatch):
    """用量反推那条路也要保手工价。"""
    make_provider(key="change2pro", protocol="change2pro", base_url="https://api.change2pro.com",
                  model_map={"gemini-3-pro-image": "gemini-3-pro-image-preview"})
    _bind("change2pro", _site(name="change2pro", stype="sub2api", base="https://api.change2pro.com"))
    store.set_price_full("gemini-3-pro-image", "change2pro", 0.077, "USD", source="manual")
    monkeypatch.setattr(balances, "fetch_sub2api", lambda s, api_key='', groups=None: {
        "balance": 2.66, "raw": {"model_usage": [
            {"model": "gemini-3-pro-image-preview", "requests": 7, "cost": 0.42}]}})

    res = balances.sync_provider_prices("change2pro")

    assert res["skipped"] == ["gemini-3-pro-image@change2pro"]
    row = [r for r in store.list_prices() if r["provider"] == "change2pro"][0]
    assert row["price"] == 0.077 and row["source"] == "manual"


def test_sub2api_sync_still_works_and_maps_to_client_names(db, make_provider, monkeypatch):
    make_provider(key="change2pro", protocol="change2pro", base_url="https://api.change2pro.com",
                  model_map={"gemini-3-pro-image": "gemini-3-pro-image-preview"})
    _bind("change2pro", _site(name="change2pro", stype="sub2api", base="https://api.change2pro.com"))
    monkeypatch.setattr(balances, "fetch_sub2api", lambda s, api_key='', groups=None: {
        "balance": 2.66, "raw": {"model_usage": [
            {"model": "gemini-3-pro-image-preview", "requests": 7, "cost": 0.42}]}})

    res = balances.sync_provider_prices("change2pro")

    assert res["ok"] is True and res["source"] == "usage"
    row = [r for r in store.list_prices() if r["provider"] == "change2pro"][0]
    assert row["model"] == "gemini-3-pro-image" and row["price"] == 0.06
    assert row["source"] == "upstream"


def test_group_ratio_follows_token_group_order():
    """aicost 实测口径（用户日志复现）：裸价 $0.445 的 gpt-image-2，令牌分组顺序里
    第一个能卖它的分组是 gpt-image-2-主（倍率 0.023）→ 实付 $0.010235。
    不同分组倍率不同，所以价必须乘倍率，不能拿裸价。"""
    from app import balances
    models = [
        {"model_name": "gpt-image-2", "model_price": 0.445, "quota_type": 1,
         "enable_groups": ["gpt-image-2-主", "gpt-image-2-备", "adobe低价渠道"]},
        {"model_name": "gemini-3-pro-image-preview", "model_price": 0.12, "quota_type": 1,
         "enable_groups": ["nano-banana", "nano-banana渠道2"]},
        {"model_name": "按量计费的对话模型", "model_price": 0.5, "quota_type": 0},
        {"model_name": "没定价的模型", "model_price": 0, "quota_type": 1},
    ]
    ratios = {"即梦便宜900分组": 1.0, "gpt-image-2-主": 0.023, "gpt-image-2-备": 0.18,
              "adobe低价渠道": 0.135, "nano-banana": 1.0, "nano-banana渠道2": 0.6}
    groups = ["即梦便宜900分组", "gpt-image-2-主", "nano-banana渠道2"]
    out = balances._pricing_effective(models, ratios, groups)
    assert out["gpt-image-2"]["price"] == 0.010235
    assert out["gpt-image-2"]["raw"] == 0.445 and out["gpt-image-2"]["ratio"] == 0.023
    assert out["gpt-image-2"]["group"] == "gpt-image-2-主" and out["gpt-image-2"]["how"] == "token"
    assert out["gemini-3-pro-image-preview"]["price"] == 0.072      # 0.12 × 0.6
    assert "按量计费的对话模型" not in out and "没定价的模型" not in out
    # 令牌分组读不到 → 退化成「可售分组里最便宜的」，并标 how=cheapest（界面会写清是估算）
    out2 = balances._pricing_effective(models, ratios, [])
    assert out2["gpt-image-2"]["how"] == "cheapest" and out2["gpt-image-2"]["price"] == 0.010235
    # 连可售分组都没有 → 倍率 1，不编造
    out3 = balances._pricing_effective([{"model_name": "m", "model_price": 0.3, "quota_type": 1}],
                                       ratios, groups)
    assert out3["m"]["price"] == 0.3 and out3["m"]["ratio"] == 1.0


class _FakeResp:
    status_code = 200
    text = ""

    def json(self):
        return {"success": True, "data": {"items": [
            {"key": "other****ZZZZ", "group": "无关分组"},
            {"key": "bKcI****WreC", "groups": ["即梦便宜900分组", "gpt-image-2-主", "nano-banana渠道2"]},
            {"key": "bKcI****WreC", "group": "即梦便宜900分组,gpt-image-2-主"},
        ]}}


def test_token_groups_parsed_in_order(monkeypatch):
    """令牌列表里 key 是打码的 → 用尾 4 位匹配；多分组按顺序解析。"""
    from app import balances
    monkeypatch.setattr(balances.HTTP, "get", lambda url, headers=None, **kw: _FakeResp())
    site = {"base_url": "https://www.aicost.me", "token": "tk", "uid": "2774"}
    # groups（多分组数组）优先，保持选择顺序 —— aicost 实测就是这个字段
    assert balances._newapi_token_groups(site, api_key="sk-bKcIabcdefWreC") == \
        ["即梦便宜900分组", "gpt-image-2-主", "nano-banana渠道2"]
    assert balances._newapi_token_groups(site, api_key="sk-unknown0000") == []


def test_manual_ratio_override(db, make_provider):
    """渠道选项里填了 price_ratio 就以它为准（用户想锁死某个分组的倍率时用）。"""
    from app import balances
    p = make_provider(key="aicost", protocol="aicost", base_url="https://www.aicost.me")
    assert balances._manual_ratio(p) is None
    assert balances._manual_ratio({"options": {"price_ratio": 0.023}}) == 0.023
    assert balances._manual_ratio({"options": '{"price_ratio": "0.5"}'}) == 0.5
    assert balances._manual_ratio({"options": {"price_ratio": 0}}) is None
    assert balances._manual_ratio({"options": "不是json"}) is None


def test_manual_groups_override(db, make_provider):
    """手工指定令牌分组顺序 → 不依赖 /api/token/ 也能按真实分组算倍率。"""
    from app import balances
    assert balances._manual_groups({"options": {"price_groups": ["a", "b"]}}) == ["a", "b"]
    assert balances._manual_groups({"options": {"price_groups": "a,b"}}) == ["a", "b"]
    assert balances._manual_groups({"options": {"price_groups": "a，b"}}) == ["a", "b"]
    assert balances._manual_groups({"options": "{}"}) == []
    assert balances._manual_groups({"options": "不是json"}) == []
