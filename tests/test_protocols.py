"""protocols.py —— 三种上游协议的翻译与解析（纯函数，不联网）。

重点锁死三条：
  · 缺 prompt 一律本地报错（绝不回落默认提示词 → 绝不真出图）
  · 模型名大小写不敏感 + 统一模型名
  · 嵌套字段删除（New API 只支持顶层，这里是本项目的差异点）
"""
from __future__ import annotations

import pytest

from app import protocols

GEMINI_P = {
    "key": "c2p", "protocol": "change2pro", "base_url": "https://api.example.com/",
    "auth_mode": "bearer",
    "model_map": {"gemini-3-pro-image": "gemini-3-pro-image", "gpt-image-2": "gpt-image-2"},
    "options": {},
}


# ------------------------------------------------------------------ 鉴权头

@pytest.mark.parametrize("mode,head", [
    ("bearer", "Authorization"),
    ("x-goog-api-key", "x-goog-api-key"),
    ("fal_key", "Authorization"),
])
def test_auth_headers(mode, head):
    h = protocols.auth_headers(mode, "SECRET")
    assert head in h and h["Content-Type"] == "application/json"


def test_auth_headers_fal_prefix():
    assert protocols.auth_headers("fal_key", "K")["Authorization"] == "Key K"
    assert protocols.auth_headers("bearer", "K")["Authorization"] == "Bearer K"


def test_mask_headers_hides_secrets():
    m = protocols.mask_headers({"Authorization": "Bearer sk-abcdef123456", "Content-Type": "application/json"})
    assert m["Authorization"].startswith("Bearer s") and m["Authorization"].endswith("…")
    assert m["Content-Type"] == "application/json"


# ------------------------------------------------------------------ 模型名归一

def test_match_model_case_insensitive():
    p = {"model_map": {"GPT-Image-2": "openai/gpt-image-2"}}
    assert protocols.match_model(p, " gpt-image-2 ") == "GPT-Image-2"
    assert protocols.match_model(p, "GPT-IMAGE-2") == "GPT-Image-2"
    assert protocols.match_model(p, "unknown-model") == "unknown-model"      # 原样返回，让上游报错更直观


def test_resolve_model_shapes():
    p = {"model_map": {"a": "up-a", "b": {"upstream": "up-b", "submit": "/queue/x"}}}
    assert protocols.resolve_model(p, "a") == {"upstream": "up-a"}
    assert protocols.resolve_model(p, "b")["submit"] == "/queue/x"
    assert protocols.resolve_model(p, "c") == {"upstream": "c"}              # 未映射 → 原样


def test_upstream_and_default_model():
    p = {"model_map": {"first": "up-first", "second": "up-second"}}
    assert protocols.upstream_model(p, "second") == "up-second"
    assert protocols.default_model(p) == "first"
    assert protocols.default_model({}) == ""


def test_model_list_marks_aliases():
    p = {"model_map": {"same": "same", "alias": "openai/real"}}
    got = {m["id"]: m for m in protocols.model_list(p)}
    assert got["same"]["aliased"] is False
    assert got["alias"]["aliased"] is True and got["alias"]["upstream"] == "openai/real"


def test_unify_model_rewrites_upstream_name_back():
    p = {"model_map": {"client-name": "openai/real-name"}, "options": {}}
    payload = {"model": "openai/real-name", "data": []}
    out = protocols.unify_model(p, {"model": "client-name"}, payload)
    assert out["model"] == "client-name"


def test_unify_model_can_be_disabled():
    p = {"model_map": {"client-name": "openai/real-name"}, "options": {"unify_model": False}}
    payload = {"model": "openai/real-name"}
    assert protocols.unify_model(p, {"model": "client-name"}, payload)["model"] == "openai/real-name"


# ------------------------------------------------------------------ 嵌套字段读写/删除

def test_remove_path_nested_dict():
    obj = {"generationConfig": {"thinkingConfig": {"thinkingBudget": 100}, "imageConfig": {"imageSize": "1K"}}}
    assert protocols.remove_path(obj, "generationConfig.thinkingConfig") is True
    assert "thinkingConfig" not in obj["generationConfig"]
    assert obj["generationConfig"]["imageConfig"]["imageSize"] == "1K"
    assert protocols.remove_path(obj, "generationConfig.nope") is False


def test_remove_path_list_wildcard():
    obj = {"data": [{"revised_prompt": "x", "url": "u1"}, {"revised_prompt": "y", "url": "u2"}]}
    assert protocols.remove_path(obj, "data.*.revised_prompt") is True
    assert obj["data"] == [{"url": "u1"}, {"url": "u2"}]


def test_remove_path_list_index():
    obj = {"data": [{"a": 1}, {"a": 2}]}
    assert protocols.remove_path(obj, "data.1") is True
    assert obj["data"] == [{"a": 1}]
    assert protocols.remove_path(obj, "data.9") is False


def test_apply_removals_reports_hits_only():
    obj = {"a": 1, "b": {"c": 2}}
    hit = protocols.apply_removals(obj, ["a", "b.c", "x.y", "b.zz"])
    assert hit == ["a", "b.c"]
    assert obj == {"b": {}}
    assert protocols.apply_removals(obj, None) == []


def test_set_path_dict_and_list():
    obj: dict = {}
    protocols.set_path(obj, "generationConfig.imageConfig.imageSize", "2K")
    assert obj["generationConfig"]["imageConfig"]["imageSize"] == "2K"
    protocols.set_path(obj, "data.0.url", "u")     # 只按路径写值，不负责造列表
    assert obj["data"]["0"]["url"] == "u"


# ------------------------------------------------------------------ gemini 面

def test_build_gemini_native_ok():
    body = {"model": "gemini-3-pro-image", "prompt": "一只橘猫宇航员", "size": "1536x864"}
    url, up, meta = protocols.build_gemini_native(GEMINI_P, body)
    assert url == "https://api.example.com/v1beta/models/gemini-3-pro-image:generateContent"
    assert up["contents"][0]["parts"][0]["text"] == "一只橘猫宇航员"
    assert up["generationConfig"]["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "1K"}
    assert meta["up_model"] == "gemini-3-pro-image" and meta["refs"] == 0


def test_build_gemini_native_uses_upstream_name():
    p = dict(GEMINI_P, model_map={"gemini-3.1-flash-image-preview": "gemini-3.1-flash-image"})
    url, _, _ = protocols.build_gemini_native(p, {"model": "gemini-3.1-flash-image-preview", "prompt": "x"})
    assert url.endswith("/v1beta/models/gemini-3.1-flash-image:generateContent")


def test_build_gemini_native_requires_prompt():
    with pytest.raises(ValueError, match="prompt is required"):
        protocols.build_gemini_native(GEMINI_P, {"model": "gemini-3-pro-image"})


def test_build_gemini_native_accepts_data_uri_reference_without_network():
    body = {"model": "gemini-3-pro-image", "prompt": "", "image": "data:image/png;base64,QUJD"}
    url, up, meta = protocols.build_gemini_native(GEMINI_P, body)
    parts = up["contents"][0]["parts"]
    assert parts[0]["inlineData"]["data"] == "QUJD"          # 裸 base64，禁 data URI 前缀
    assert meta["refs"] == 1


def test_parse_gemini_native():
    payload = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": "QUJD"}}, {"text": "忽略我"}]}}]}
    got = protocols.parse_gemini_native(payload)
    assert got == [{"b64_json": "QUJD", "mime_type": "image/png"}]
    assert protocols.parse_gemini_native({}) == []


# ------------------------------------------------------------------ openai 图片面

def test_build_openai_images_drops_and_normalizes():
    p = dict(GEMINI_P, options={"drop_fields": ["user", "response_format"], "generations_path": "/images/generations"})
    body = {"model": "gpt-image-2", "prompt": "猫", "size": "1000x1000", "quality": "hd",
            "user": "u1", "response_format": "b64_json", "extra_body": {"x": 1}, "n": 2}
    url, up, _ = protocols.build_openai_images(p, body)
    assert url == "https://api.example.com/images/generations"
    assert up["model"] == "gpt-image-2"
    assert up["size"] == "1024x1024"          # 吸附到合规尺寸
    assert up["quality"] == "high"            # hd → high
    assert "user" not in up and "response_format" not in up and "extra_body" not in up
    assert up["n"] == 2


def test_build_openai_images_requires_prompt():
    with pytest.raises(ValueError, match="prompt is required"):
        protocols.build_openai_images(GEMINI_P, {"model": "gpt-image-2", "prompt": "   "})


def test_build_openai_images_edit_path_and_force_fields():
    p = dict(GEMINI_P, options={"edits_path": "/images/edits", "force_fields": {"response_format": "url"}})
    url, up, _ = protocols.build_openai_images(p, {"model": "gpt-image-2", "prompt": "改图"}, edit=True)
    assert url.endswith("/images/edits")
    assert up["response_format"] == "url"


def test_build_openai_images_nested_removal():
    """嵌套删除是本项目相对 New API 的差异点（New API 只能删顶层字段）。"""
    p = dict(GEMINI_P, options={"remove_params": ["metadata.foo", "options.*.trace"]})
    url, up, meta = protocols.build_openai_images(
        p, {"model": "gpt-image-2", "prompt": "x", "metadata": {"foo": 1, "bar": 2},
            "options": [{"trace": "t1"}, {"trace": "t2", "keep": True}]})
    assert up["metadata"] == {"bar": 2}
    assert up["options"] == [{}, {"keep": True}]      # 列表元素只删字段，不删元素本身
    assert meta["removed"] == ["metadata.foo", "options.*.trace"]


def test_build_openai_images_always_drops_internal_fields():
    """__files / extra_body / image_config / user 是内部字段，永远不打给上游。"""
    p = dict(GEMINI_P, options={})
    _, up, _ = protocols.build_openai_images(p, {"model": "gpt-image-2", "prompt": "x", "user": "u",
                                                "extra_body": {"a": 1}, "image_config": {"b": 2}})
    for f in ("__files", "extra_body", "image_config", "user"):
        assert f not in up


def test_build_openai_images_keeps_client_reference_field():
    """客户端已经用了 image/images 字段 → 保持它自己的写法（宽松上游直接认）。"""
    body = {"model": "gpt-image-2", "prompt": "x", "image": "https://a.example.com/1.png"}
    _, up, _ = protocols.build_openai_images(GEMINI_P, body)
    assert up["image"] == "https://a.example.com/1.png"


def test_build_openai_images_normalizes_other_reference_field_names():
    """客户端用的是别的参考图字段名 → 额外补一份标准的 image。"""
    body = {"model": "gpt-image-2", "prompt": "x", "reference_images": ["https://a.example.com/1.png"]}
    _, up, _ = protocols.build_openai_images(GEMINI_P, body)
    assert up["image"] == ["https://a.example.com/1.png"]


# ------------------------------------------------------------------ fal 队列面

FAL_P = {
    "key": "fal", "protocol": "fal_queue", "base_url": "https://fal.example.com",
    "model_map": {"gemini-3-pro-image-preview": "fal-ai/gemini-3-pro-image-preview",
                  "gpt-image-2": "openai/gpt-image-2"},
    "options": {},
}


def test_build_fal_queue_gemini_shape():
    url, up, meta = protocols.build_fal_queue(
        FAL_P, {"model": "gemini-3-pro-image-preview", "prompt": "雪山", "size": "1536x864"})
    assert url == "https://fal.example.com/queue/fal-ai/gemini-3-pro-image-preview"
    assert up["prompt"] == "雪山"
    assert up["aspect_ratio"] == "16:9" and up["resolution"] == "1K"
    assert "num_images" not in up                       # 非 gpt 系不传
    assert meta["poll_base"] == "https://fal.example.com/queue/fal-ai/gemini-3-pro-image-preview"


def test_build_fal_queue_gpt_shape():
    _, up, _ = protocols.build_fal_queue(
        FAL_P, {"model": "gpt-image-2", "prompt": "x", "size": "1024x1024", "quality": "hd",
                "n": 9, "background": "transparent"})
    assert up["image_size"] == "1024x1024"
    assert up["quality"] == "high"
    assert up["num_images"] == 4                        # 夹到 1..4
    assert up["background"] == "transparent"
    assert "aspect_ratio" not in up


def test_build_fal_queue_requires_prompt():
    with pytest.raises(ValueError, match="prompt is required"):
        protocols.build_fal_queue(FAL_P, {"model": "gpt-image-2"})


def test_build_fal_queue_edit_needs_public_url():
    with pytest.raises(ValueError, match="公网 URL"):
        protocols.build_fal_queue(FAL_P, {"model": "gpt-image-2", "prompt": "x"}, edit=True)
    _, up, _ = protocols.build_fal_queue(
        FAL_P, {"model": "gpt-image-2", "prompt": "x", "image": "https://a.example.com/1.png"}, edit=True)
    assert up["image_urls"] == ["https://a.example.com/1.png"]


def test_fal_endpoints_override():
    p = dict(FAL_P, model_map={"m": {"upstream": "openai/gpt-image-2", "submit": "/queue/custom",
                                     "submit_edit": "/queue/custom-edit", "poll_base": "/queue/custom"}})
    assert protocols.fal_endpoints(p, "m", False)[1] == "/queue/custom"
    assert protocols.fal_endpoints(p, "m", True)[1] == "/queue/custom-edit"


# ------------------------------------------------------------------ 响应挖掘

def test_extract_urls_from_many_shapes():
    payload = {"images": [{"url": "https://k.example.com/1.png"}, {"url": "https://k.example.com/2.jpeg"}],
               "request_id": "abc"}
    urls, err = protocols.extract_urls(payload)
    assert urls == ["https://k.example.com/1.png", "https://k.example.com/2.jpeg"]
    assert err == ""


def test_extract_urls_dedupes_and_finds_error():
    payload = {"data": [{"url": "https://k.example.com/1.png"}, {"url": "https://k.example.com/1.png"}],
               "error": {"message": "模型名不对"}}
    urls, err = protocols.extract_urls(payload)
    assert urls == ["https://k.example.com/1.png"]
    assert err == "模型名不对"


def test_extract_urls_by_extension_on_unknown_keys():
    """不认得的字段名 → 靠图片扩展名兜住。"""
    urls, _ = protocols.extract_urls({"output": "https://x.example.com/a.webp?sign=1",
                                      "docs": "https://x.example.com/api/docs"})
    assert urls == ["https://x.example.com/a.webp?sign=1"]


def test_extract_urls_takes_url_key_verbatim():
    """认得的字段名（url/image/image_url/download_url）→ 不做扩展名判断，直接收。"""
    urls, _ = protocols.extract_urls({"url": "https://x.example.com/anything"})
    assert urls == ["https://x.example.com/anything"]


def test_extract_urls_empty_payload():
    assert protocols.extract_urls(None) == ([], "")
    assert protocols.extract_urls({}) == ([], "")
