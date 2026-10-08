"""v3.21 生成日志：游标 merge、分类标签、素材转换行 —— 纯函数级测试（零网络零成本）。"""
import json

from app import admin, newapi


def test_cursor_roundtrip():
    cur = {"i": [1791451804, 512], "v": {"ts": 1791451804, "seen": [101]}}
    back = admin._cursor_decode(admin._cursor_encode(cur))
    assert back == cur


def test_cursor_decode_bad_input():
    assert admin._cursor_decode("") == {}
    assert admin._cursor_decode("!!!not-base64!!!") == {}


def test_shape_local_tags_and_ids():
    row = {"id": 7, "ts": 100, "kind": "relay", "provider": "aicost", "model": "gpt-image-2",
           "public_path": "/v1/images/generations", "http_status": 200, "ms": 12,
           "cost": 0.19, "cost_currency": "USD", "imagehost": "", "parent_log_id": None}
    s = admin._shape_local(row)
    assert s["src"] == "image" and s["id"] == "i7"
    assert s["tags"] == ["图片"]
    assert s["public_path"] == "/v1/images/generations"


def test_image_kinds_exclude_material():
    """生成日志只放生成类 kind；upload/convert 属于素材日志。"""
    assert "upload" not in admin.IMAGE_KINDS and "convert" not in admin.IMAGE_KINDS
    assert set(admin.IMAGE_KINDS) <= {"relay", "router", "client", "probe"}


def test_video_row_shape_tags_billing(db):
    task = {"id": 42, "task_id": "task_x", "status": "FAILURE", "created_at": 1000,
            "submit_time": 1000, "start_time": 1001, "finish_time": 1003, "quota": 350000,
            "channel_id": 20, "user_id": 1, "private_data": json.dumps({"plugin_state": {"request": {
     "model": "seedance-2.0-900-0.70", "prompt": "一只猫", "seconds": 5,
     "resolution": "720p", "aspect_ratio": "16:9", "size": "1280x720"}}}),
            "data": json.dumps({"status": "FAILURE", "reason": "内容未通过审核"}),
            "fail_reason": "内容未通过审核",
            "properties": json.dumps({"model": "seedance-2.0-900-0.70"})}
    lg = {"model_name": "seedance-2.0-900-0.70", "channel_name": "佳速API-视频", "token_name": "默认",
          "username": "admin", "quota_used": 350000, "quota_refund": 350000, "refunded": True,
          "model_price": "0.7", "upstream_model": "seedance-2.0-900", "request_path": "/v1/videos",
          "plugin_name": "佳速API", "plugin_version": "1.0.11", "types": [2, 6]}
    r = newapi._row_shape(task, lg, with_snapshot=True)
    assert r["tags"] == ["视频"] and r["src"] == "video"
    assert r["cost"] == 0.7 and r["refunded"] is True
    assert r["billing"]["charged_usd"] == 0.7 and r["billing"]["net_usd"] == 0.0
    assert r["client_request"]["prompt"] == "一只猫"
    assert r["upstream_request"]["upstream_model"] == "seedance-2.0-900"
    assert r["fail_reason"] == "内容未通过审核"


def test_snapshot_never_leaks_base64(db):
    """快照里的 base64 / 超长值必须被折叠成占位说明（面板不能吐原始 base64）。"""
    big = "data:image/png;base64," + "A" * 5000
    raw = json.dumps({"plugin_state": {"request": {
        "model": "seedance-2.0-900-0.70", "prompt": "x",
        "reference_images": [big, "https://a/b.png"]}}})
    snap = newapi._snapshot(raw)
    assert snap["model"] == "seedance-2.0-900-0.70"
    assert all("AAAA" not in str(v) for v in snap["reference_images"])
    assert "https://a/b.png" in snap["reference_images"]
    assert any("base64" in str(v) for v in snap["reference_images"])


def test_log_row_returns_id_for_parent_link(db):
    """log_row 要回写入库 id，convert 行才能挂 parent_log_id。"""
    store = db
    pid = store.log_row("aicost", "m", "/p", 200, None, 10, None, {}, None, None, kind="relay")
    assert isinstance(pid, int) and pid > 0
    cid = store.log_row("aicost", "m", "/p", 200, None, 10, None,
                        {"kind": "convert"}, None, None, kind="convert", parent_log_id=pid)
    assert cid and cid > pid
    row = store.one("SELECT kind, parent_log_id FROM logs WHERE id=?", (cid,))
    assert row["kind"] == "convert" and row["parent_log_id"] == pid


def test_convert_detail_notes_shape():
    """转换行 JSON：方向 / 张数 / 站点 / 直链 / 明细，素材日志据此渲染。"""
    from app import relay
    notes = [{"mode": "imgbb", "host": "sudashui_files", "mime": "image/png", "bytes": 2048,
              "url": "https://f/x.png", "warnings": []},
             {"mode": "inline", "from": "https://f/y.png", "bytes": 1024, "mime": "image/png"}]
    d = relay._convert_detail(notes)
    assert d["mode"] == "convert" and d["source"] == "auto"
    assert d["count"] == 2 and d["bytes"] == 3072
    assert "base64→公网直链" in d["direction"] and "URL→内联base64" in d["direction"]
    assert d["hosts"] == ["sudashui_files"] and d["urls"] == ["https://f/x.png"]
    row = relay._convert_material_row({"imagehost": notes}, "aicost", "gpt-image-2", "默认", 1)
    assert row["kind"] == "convert" and row["status"] == 200
    assert row["req"]["items"] == notes


def test_convert_detail_empty_when_nothing_converted():
    from app import relay
    assert relay._convert_detail([]) == {}
    assert relay._convert_material_row({"imagehost": []}, "aicost", "m", "", None) is None


def test_genlogs_endpoint_shape(login):
    """诚实性：视频列必须带分类标签；数据源未接通时不影响图片日志。"""
    r = login.get("/api/genlogs?source=all&limit=5&days=0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"data", "next_cursor", "counts", "video"}
    assert body["counts"]["image"] + body["counts"]["video"] == len(body["data"])
    for row in body["data"]:
        assert row["tags"] in (["图片"], ["视频"])


def test_genlogs_cursor_paging_no_overlap(login):
    """游标分页：第二页不得与第一页重叠（图片 id 与视频 task 各自 keyset）。"""
    p1 = login.get("/api/genlogs?source=all&limit=3&days=0").json()
    if not p1["next_cursor"]:
        return
    p2 = login.get("/api/genlogs?source=all&limit=3&days=0&cursor=" + p1["next_cursor"]).json()
    key = lambda r: (r["src"], r["id"])
    assert not ({key(r) for r in p1["data"]} & {key(r) for r in p2["data"]})


# ---------------- v3.21.1：插件补写的「上游报文」快照（__outbound） ----------------

def _task_with_outbound(body=None, **extra):
    ob = {"plugin": "jiasuapi", "version": "1.0.12",
          "url": "https://ai.jiasuapi.com/v1/video/generations", "method": "POST", "bytes": 123}
    if body is not None:
        ob["body"] = body
    ob.update(extra)
    return json.dumps({"plugin_state": {
        "request": {"model": "seedance-2.0-900-0.70", "prompt": "一只猫"},
        "__outbound": ob}})


def test_outbound_extracted():
    """插件写的上游报文：url / method / body / 插件名版本 都要能取出来。"""
    ob = newapi._outbound(_task_with_outbound(
        {"model": "seedance-2.0-900", "prompt": "一只猫在跳舞", "seconds": 5,
         "images": ["https://a/b.png"]}))
    assert ob["plugin"] == "jiasuapi" and ob["version"] == "1.0.12"
    assert ob["url"].endswith("/v1/video/generations") and ob["method"] == "POST"
    assert ob["body"]["prompt"] == "一只猫在跳舞" and ob["bytes"] == 123


def test_outbound_missing_for_old_rows():
    """老任务（插件未升级）没有 __outbound → 空 dict，且任何脏输入都不抛。"""
    raw = json.dumps({"plugin_state": {"request": {"model": "m", "prompt": "p"}}})
    assert newapi._outbound(raw) == {}
    assert newapi._outbound(None) == {}
    assert newapi._outbound("not-json") == {}
    assert newapi._outbound(json.dumps({"plugin_state": {"__outbound": "oops"}})) == {}


def test_outbound_never_leaks_base64():
    big = "data:image/png;base64," + "B" * 40000
    ob = newapi._outbound(_task_with_outbound({"model": "m", "images": [big, "https://a/b.png"]}))
    text = json.dumps(ob, ensure_ascii=False)
    assert "BBBB" not in text and "https://a/b.png" in text
    assert any("base64" in str(v) for v in ob["body"]["images"])


def test_upstream_echo_prefers_plugin_payload():
    ob = newapi._outbound(_task_with_outbound({"model": "seedance-2.0-900", "prompt": "跳舞"}))
    up = newapi._upstream_echo({}, {"upstream_model": "seedance-2.0-900", "plugin_version": "1.0.12"}, {}, ob)
    assert up["source"] == "plugin" and up["payload"]["prompt"] == "跳舞"
    assert up["payload_method"] == "POST" and "插件" in up["note"]
    up2 = newapi._upstream_echo({}, {"upstream_model": "m"}, {})
    assert up2["source"] == "log" and "payload" not in up2 and "尚未补写" in up2["note"]


def test_upstream_echo_reports_plugin_error():
    ob = newapi._outbound(_task_with_outbound(None, error="未能复算上游报文:api key is required"))
    up = newapi._upstream_echo({}, {}, {}, ob)
    assert up["source"] == "log" and "api key is required" in up["plugin_error"]


def test_row_shape_exposes_upstream_payload(db):
    task = {"id": 9, "task_id": "task_ob", "status": "SUCCESS", "created_at": 1000, "submit_time": 1000,
            "channel_id": 20, "user_id": 1,
            "private_data": _task_with_outbound({"model": "sd2", "prompt": "上游提示词"}),
            "data": json.dumps({"result_urls": ["https://r/v.mp4"]})}
    r = newapi._row_shape(task, {}, with_snapshot=True)
    assert r["upstream_request"]["payload"]["prompt"] == "上游提示词"
    assert r["upstream_request"]["source"] == "plugin"
    assert r["client_request"]["prompt"] == "一只猫"
    assert r["result_urls"] == ["https://r/v.mp4"] and r["tags"] == ["视频"]
