"""渠道插件 —— 注册表、契约、以及「绝不真出图」的硬底线。"""
from __future__ import annotations

import pytest

from app import channels

BUILTIN = ("gemini_native", "openai_images", "qiniu_fal", "qiniu", "change2pro")


def provider_for(cid: str) -> dict:
    return {"key": cid, "label": cid, "protocol": cid, "base_url": "https://up.example.com",
            "auth_mode": "bearer", "api_key": "sk-x", "model_map": {}, "options": {}}


def test_registry_discovers_builtin_plugins():
    assert channels.ERRORS == {}, f"插件装载出错：{channels.ERRORS}"
    ids = channels.available_ids()
    for want in BUILTIN:
        assert want in ids, f"内置插件 {want} 没被装载"


def test_template_file_is_not_registered():
    """_template.py 只是给人复制的样板，不该被当成插件。"""
    assert "_template" not in channels.available_ids()


def test_plugin_contract_shape():
    for cid in sorted(channels.available_ids()):
        c = channels.get(cid)
        info = c.info()
        assert info["id"] and info["label"]
        assert info["default_auth"] in ("bearer", "x-goog-api-key", "fal_key")
        assert info["operations"], f"{info['id']} 必须声明支持的操作"
        for op in info["operations"]:
            assert set(op) == {"operation", "mode"}
            assert op["operation"] in ("generate", "edit")
            assert op["mode"] in ("native", "converted", "queue", "unsupported")


def test_unknown_plugin_lookup_returns_none():
    assert channels.get("nope") is None
    assert channels.get("") is None


@pytest.mark.parametrize("cid", sorted(BUILTIN))
@pytest.mark.parametrize("body", [
    {"model": "any-model"},
    {"model": "any-model", "prompt": ""},
    {"model": "any-model", "prompt": "   \n "},        # 纯空白也算「没有」
    {"model": "any-model", "text": "\t"},
])
def test_every_plugin_refuses_missing_prompt(cid, body):
    """铁律回归：任何插件 + 任何空 prompt 写法 → 本地报错，一个字节都不发上游。

    这条用例是花钱事故（合并插件漏抹 prompt → 真出图）的守门人，不许删。
    """
    ch = channels.get(cid)
    model = next(iter(ch.models), "any-model")
    with pytest.raises(ValueError, match="prompt is required"):
        ch.build(provider_for(cid), dict(body, model=model), False)


def test_gemini_face_allows_reference_only_request():
    """唯一的例外：gemini 面带参考图、不带文字，是合法的「改这张图」请求。

    参考图走 data URI → 本地就能解 base64，不需要联网。
    """
    ch = channels.get("gemini_native")
    url, up, meta = ch.build(provider_for("gemini_native"),
                             {"model": "gemini-3.1-flash-image", "image": "data:image/png;base64,QUJD"}, False)
    assert meta["refs"] == 1
    assert up["contents"][0]["parts"] == [{"inlineData": {"mimeType": "image/png", "data": "QUJD"}}]


def test_change2pro_merges_two_faces_by_model():
    """合并插件：同一个实例按模型名自动分到 gemini 面 / image2 面。"""
    ch = channels.get("change2pro")
    p = provider_for("change2pro")
    url_g, up_g, meta_g = ch.build(p, {"model": "gemini-3-pro-image", "prompt": "x"}, False)
    assert meta_g["face"] == "gemini_native"
    assert url_g.endswith("/v1beta/models/gemini-3-pro-image:generateContent")
    assert "contents" in up_g

    url_o, up_o, meta_o = ch.build(p, {"model": "gpt-image-2", "prompt": "x"}, False)
    assert meta_o["face"] == "openai_images"
    assert url_o.endswith("/images/generations")          # image2 面没有 /v1 前缀
    assert up_o["prompt"] == "x"


def test_change2pro_face_of():
    ch = channels.get("change2pro")
    assert ch.face_of("gemini-3-pro-image") == "gemini_native"
    assert ch.face_of("GEMINI-anything") == "gemini_native"
    assert ch.face_of("openai/gpt-image-2") == "openai_images"
    assert ch.face_of("") == "openai_images"


def test_change2pro_parse_handles_both_faces():
    ch = channels.get("change2pro")
    gemini_payload = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": "QUJD"}}]}}]}
    assert ch.parse(gemini_payload) == [{"b64_json": "QUJD", "mime_type": "image/png"}]
    openai_payload = {"data": [{"url": "https://k.example.com/1.png"}]}
    assert ch.parse(openai_payload) == [{"url": "https://k.example.com/1.png"}]
    fal_like = {"images": [{"url": "https://k.example.com/2.png"}]}
    assert ch.parse(fal_like) == [{"url": "https://k.example.com/2.png"}]
    assert ch.parse({}) == []


def test_qiniu_fal_declares_queue_mode():
    """七牛异步面：按来源命名（不是 fal.ai 官方协议），并声明 queue 走法。"""
    ch = channels.get("qiniu_fal")
    assert ch.id == "qiniu_fal"
    assert ch.route_mode("generate") == "queue"
    assert ch.supports("edit") is True
    assert ch.supports("nope") is False
    assert "fal.ai" in (ch.protocol_note or "") or "非 fal.ai" in (ch.protocol_note or "")


def test_legacy_plugin_id_still_resolves():
    """改名后老数据（providers.protocol='fal_queue'）仍能解析到新插件。"""
    assert channels.get("fal_queue") is channels.get("qiniu_fal")


def test_qiniu_merged_plugin_routes_by_model():
    """七牛合并插件：gemini 系走异步队列，gpt-image 系走同步面，且各自声明鉴权方式。"""
    ch = channels.get("qiniu")
    p = provider_for("qiniu")
    url_f, up_f, meta_f = ch.build(p, {"model": "gemini-3.1-flash-image-preview", "prompt": "x"}, False)
    assert meta_f["face"] == "fal" and meta_f["mode"] == "queue" and meta_f["auth_mode"] == "fal_key"
    assert "/queue/" in url_f
    url_s, up_s, meta_s = ch.build(p, {"model": "gpt-image-2", "prompt": "x"}, False)
    assert meta_s["face"] == "sync" and meta_s["auth_mode"] == "bearer"
    assert "response_format" not in up_s          # 七牛同步面不认这个字段
    assert "/images/generations" in url_s


def test_plugin_metadata_carries_vendor_and_docs():
    """每个插件都要写清「谁家的协议 + 官方文档」，防止把形似协议混为一谈。"""
    for cid in channels.available_ids():
        c = channels.get(cid).info()
        assert c["vendor"], f"{cid} 缺 vendor"
        assert c["docs"].startswith("http"), f"{cid} 缺 docs"
        assert c["hint"], f"{cid} 缺 hint"


def test_unsupported_operation_mode():
    """不支持的操作要能提前看出来（由 relay 直接 400，不打上游）。"""
    ch = channels.get("gemini_native")
    assert ch.route_mode("什么鬼") == "unsupported"


def test_every_channel_declares_ref_input(db):
    """每个插件都必须按官方文档声明参考图形态，且值合法（面板/日志都靠它）。"""
    from app import channels
    for cid in channels.available_ids():
        info = channels.get(cid).info()
        assert info["ref_input"] in ("url", "both", "base64"), cid
        for face, v in (info["ref_input_faces"] or {}).items():
            assert v in ("url", "both", "base64"), (cid, face)
        assert info["ref_input_note"], cid              # 面板要给人话说明
    assert channels.get("qiniu_fal").info()["ref_input"] == "url"
    assert channels.get("gemini_native").info()["ref_input"] == "base64"
    assert channels.get("qiniu").info()["ref_input_faces"] == {"异步面": "url", "同步面": "base64"}
