"""utils.py —— 尺寸/比例/质量换算与参考图收集。

这些是「客户端写法五花八门」的第一道归一化，回归成本最低、最容易出错，必须锁死。
"""
from __future__ import annotations

import json

import pytest

from app import utils


@pytest.mark.parametrize("raw,expect", [
    ("1024x1024", (1024, 1024)),
    (" 1536 X 864 ", (1536, 864)),
    ("1024*1024", (1024, 1024)),
    ("1024×1024", (1024, 1024)),
    ([2048, 1152], (2048, 1152)),
])
def test_parse_size_ok(raw, expect):
    assert utils.parse_size(raw) == expect


@pytest.mark.parametrize("raw", [None, "", "auto", "1024", "1024x", [1, 2, 3], {"w": 1}])
def test_parse_size_bad(raw):
    assert utils.parse_size(raw) is None


@pytest.mark.parametrize("wh,ratio", [
    ((1024, 1024), "1:1"),
    ((1536, 864), "16:9"),
    ((864, 1536), "9:16"),
    ((2048, 1152), "16:9"),
    ((1024, 1536), "2:3"),
])
def test_nearest_ratio(wh, ratio):
    assert utils.nearest_ratio(*wh) == ratio


def test_nearest_ratio_zero_height_is_safe():
    assert utils.nearest_ratio(1024, 0) == "1:1"


@pytest.mark.parametrize("wh,res", [
    ((512, 512), "0.5K"),
    ((1024, 1024), "1K"),
    ((1280, 720), "1K"),
    ((1536, 864), "2K"),          # 1536 已过 1K/2K 对数分界（1448）→ 2K 档
    ((1920, 1080), "2K"),         # 客户端认知里的「2K」→ 2K
    ((2048, 2048), "2K"),
    ((2560, 1440), "2K"),         # 3.7MP 不该被顶到 4K 档
    ((3840, 2160), "4K"),         # UHD → 4K
])
def test_resolution_of(wh, res):
    assert utils.resolution_of(*wh) == res


@pytest.mark.parametrize("policy,expect", [
    ("class", "2K"), ("ceil", "2K"), ("nearest", "1K"), ("floor", "1K"),
])
def test_gemini_tier_policy(policy, expect):
    """四种档位策略：默认 class 按档位分类；floor 最省；nearest 取最接近。"""
    assert utils.gemini_tier(1920, 1080, "gemini-3.1-flash-image", policy) == expect


def test_gemini_caps_follow_model_ability():
    """模型能力收敛：Pro 无 0.5K 且不支持 1:4/4:1/1:8/8:1；Lite 只有 1K。"""
    assert utils.gemini_tier(3840, 2160, "gemini-3.1-flash-lite-image") == "1K"
    assert utils.gemini_tier(3840, 2160, "gemini-3-pro-image") == "4K"
    assert utils.gemini_ratio(8000, 1000, "gemini-3-pro-image") == "21:9"     # 8:1 不在 Pro 支持列表
    assert utils.gemini_ratio(8000, 1000, "gemini-3.1-flash-image") == "8:1"  # Flash 支持 8:1


def test_gemini_plan_reports_real_pixels():
    """换算必须说清「实际会出多少像素」——Gemini 给不了任意像素。"""
    plan = utils.gemini_plan(1920, 1080, "gemini-3.1-flash-image")
    assert plan["ratio"] == "16:9" and plan["tier"] == "2K" and plan["pixels"] == (2752, 1536)
    assert "2752x1536" in plan["note"] and "比请求大" in plan["note"]
    plan4k = utils.gemini_plan(3840, 2160, "gemini-3.1-flash-image")
    assert plan4k["tier"] == "4K" and plan4k["pixels"] == (5504, 3072)


def test_gemini_25_flash_has_own_pixels():
    assert utils.gemini_plan(1920, 1080, "gemini-2.5-flash-image")["pixels"] == (1344, 768)


@pytest.mark.parametrize("raw,expect", [
    ("1024x1024", "1024x1024"),          # 合法（16 整除 / 面积比例都合规）→ 原样
    ("1000x1000", "992x992"),            # 非 16 整除 → 就近修（平手向下，宁可略小）
    ("1920x1080", "1920x1072"),          # ★ 保 1920，只把 1080 修成 16 的倍数（不跳 2048x1152）
    ("1920x1088", "1920x1088"),          # 已是 16 的倍数 → 原样
    ("2048x1152", "2048x1152"),
    ("auto", "auto"),
    (None, "auto"),
    ("3840x2160", "3840x2160"),          # 面积正好顶到上限 → 仍算合法
    ("4096x4096", "2880x2880"),          # 超边长/面积上限 → 等比缩到合法（仍 1:1）
    ("8000x8000", "2880x2880"),          # 远超前限 → 同一条等比缩路径
    ("400x400", "816x816"),              # 面积不足下限 → 等比放大到刚好达标
])
def test_gpt_safe_size(raw, expect):
    assert utils.gpt_safe_size(raw) == expect


def test_snap_size_never_inflates_the_tier():
    """最小改动的核心承诺：不为凑比例把图放大一档（上游按 1K/2K/4K 分档计费＝多扣费）。"""
    dec = utils.snap_size("1920x1080")
    assert dec["size"] == "1920x1072" and dec["changed"] is True
    assert 1920 * 1072 <= 1920 * 1080                      # 只可能变小，不会变大
    assert "最小改动" in dec["note"] and "16 的倍数" in dec["note"]
    assert utils.snap_size("2048x1152")["changed"] is False


def test_snap_size_fixed_models():
    """gpt-image-1 系只认三种尺寸 —— 只能在这三种里挑最接近的比例。"""
    dec = utils.snap_size("1920x1080", "gpt-image-1")
    assert dec["size"] == "1536x1024" and dec["family"] == "fixed"
    assert "只接受" in dec["note"]
    assert utils.snap_size("1024x1536", "gpt-image-1-mini")["size"] == "1024x1536"


def test_snap_size_ratio_and_area_guards():
    """比例超 3:1 / 面积出界都要被拉回来，且拉回后仍合规。"""
    for raw in ("4000x500", "1920x120", "6000x4000", "300x300"):
        dec = utils.snap_size(raw)
        w, h = utils.parse_size(dec["size"])
        assert utils._free_ok(w, h), f"{raw} → {dec['size']} 仍不合规"


def test_snap_size_notes_experimental_band():
    assert "实验档" in utils.snap_size("3840x2160")["note"]


@pytest.mark.parametrize("raw,expect", [
    (None, None),
    ("standard", "auto"), ("auto", "auto"), ("default", "auto"), ("", "auto"),
    ("hd", "high"), ("HIGH", "high"),
    ("low", "low"), ("medium", "medium"), ("xhigh", "xhigh"),
    (123, "auto"), ("怪值", "auto"),
])
def test_normalize_quality(raw, expect):
    assert utils.normalize_quality(raw) == expect


def test_collect_refs_from_all_client_shapes():
    body = {
        "image": "data:image/png;base64,AAAA",
        "images": [{"image_url": "https://a.example.com/1.png"}, "https://a.example.com/2.png"],
        "image_urls": "https://a.example.com/3.png",
        "reference_images": [{"data": "https://a.example.com/4.png"}],
        "mask": "  https://a.example.com/mask.png  ",
        "prompt": "不该被当成参考图",
    }
    refs = utils.collect_refs(body)
    assert refs == [
        "data:image/png;base64,AAAA",
        "https://a.example.com/1.png",
        "https://a.example.com/2.png",
        "https://a.example.com/3.png",
        "https://a.example.com/4.png",
        "https://a.example.com/mask.png",
    ]


def test_collect_refs_empty():
    assert utils.collect_refs({}) == []
    assert utils.collect_refs({"image": "", "images": []}) == []


def test_compact_b64_only_shortens_long_payloads():
    """日志里的长 base64 / data URI 换成占位符；短字符串与普通文本一律不动。"""
    from app import utils
    long_b64 = "A" * 600
    assert utils.compact_b64({"image": [long_b64]}) == {"image": ["<参考图 base64 数据，约 1KB>"]}
    assert utils.compact_b64({"image": ["data:image/png;base64," + long_b64]}) == {
        "image": ["<参考图 base64 数据，约 1KB>"]}
    keep = {"model": "gpt-image-2", "prompt": "猫", "image": ["https://i.ibb.co/x.png"], "n": 1}
    assert utils.compact_b64(keep) == keep
    assert utils.compact_b64({"images": ["QUJD"]}) == {"images": ["QUJD"]}      # 短的照样留着


def test_to_raw_b64_variants():
    assert utils.to_raw_b64("data:image/webp;base64,QQ==") == ("image/webp", "QQ==")
    assert utils.to_raw_b64("https://x.example.com/a.png") is None       # URL 要走下载
    assert utils.to_raw_b64("A" * 300)[0] == "image/png"                 # 裸 base64
    assert utils.to_raw_b64("太短") is None


def test_to_raw_b64_strips_whitespace_in_data_uri():
    mime, b64 = utils.to_raw_b64("data:image/png;base64,AA\nBB\nCC")
    assert (mime, b64) == ("image/png", "AABBCC")


@pytest.mark.parametrize("secret,keep,want", [
    ("", 6, ""),
    (None, 6, ""),
    ("abcd", 6, "ab…"),
    ("sk-test-1234567890abcdef", 6, "sk-tes…cdef"),
])
def test_mask(secret, keep, want):
    assert utils.mask(secret, keep) == want


# ---------------------------------------------------------------- v3.7.0：官方尺寸/档位口径
def test_snap_size_passthrough_keeps_client_pixels():
    """passthrough = 一个像素都不改（给「上游实际接受更大尺寸」的渠道用）。"""
    dec = utils.snap_size("4096x4096", "gpt-image-2", "passthrough")
    assert dec["size"] == "4096x4096"
    assert dec["changed"] is False and dec["family"] == "passthrough"
    assert "原样透传" in dec["note"]


def test_snap_mode_is_still_the_default():
    """默认仍是吸附：4096x4096 超出官方上限（边长 ≤3840、面积 ≤8,294,400）→ 方形最大 2880x2880。"""
    dec = utils.snap_size("4096x4096", "gpt-image-2")
    assert dec["size"] == "2880x2880"
    assert dec["changed"] is True
    assert "边长超 3840" in dec["note"] and "总像素出界" in dec["note"]


def test_official_gpt_sizes_are_all_legal_and_labelled():
    """官方「常用尺寸」表里的每一个都必须自己合法，且都有中文标签（面板 chips 用）。"""
    for a, b in utils.GPT_SIZES:
        assert utils._free_ok(a, b), f"{a}x{b} 不符合官方约束"
        assert f"{a}x{b}" in utils.OFFICIAL_GPT_LABEL


def test_gemini_tokens_match_official_table():
    assert utils.gemini_tokens("gemini-3.1-flash-image")["2K"] == 1680
    assert utils.gemini_tokens("gemini-3-pro-image")["2K"] == 1120
    assert utils.gemini_tokens("gemini-2.5-flash-image")["1K"] == 1290


def test_gemini_plan_exposes_official_tier_table():
    """官方档位表必须能被摊开：16:9 的 2K 就是 2752x1536，且**没有** 3840x2160 这一档。"""
    plan = utils.gemini_plan(1920, 1080, "gemini-3.1-flash-image")
    assert plan["tiers"] == {"0.5K": "688x384", "1K": "1376x768", "2K": "2752x1536", "4K": "5504x3072"}
    assert plan["tokens"] == 1680 and plan["nominal"] == 2048
    assert "3840" not in json.dumps(plan["tiers"])


def test_gemini_plan_pro_has_no_half_k():
    plan = utils.gemini_plan(1920, 1080, "gemini-3-pro-image")
    assert "0.5K" not in plan["tiers"] and plan["tiers"]["4K"] == "5504x3072"
