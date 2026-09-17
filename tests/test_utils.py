"""utils.py —— 尺寸/比例/质量换算与参考图收集。

这些是「客户端写法五花八门」的第一道归一化，回归成本最低、最容易出错，必须锁死。
"""
from __future__ import annotations

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
    ((1536, 864), "1K"),
    ((2048, 2048), "2K"),
    ((3840, 2160), "4K"),
])
def test_resolution_of(wh, res):
    assert utils.resolution_of(*wh) == res


@pytest.mark.parametrize("raw,expect", [
    ("1024x1024", "1024x1024"),          # 合法（16 整除 / 面积比例都合规）→ 原样
    ("1000x1000", "1024x1024"),          # 非 16 整除 → 吸附到最近的合规尺寸
    ("auto", "auto"),
    (None, "auto"),
    ("3840x2160", "3840x2160"),          # 面积正好顶到上限 → 仍算合法
    ("4096x4096", "1024x1024"),          # 超边长上限 → 吸附（保持 1:1）
])
def test_gpt_safe_size(raw, expect):
    assert utils.gpt_safe_size(raw) == expect


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
