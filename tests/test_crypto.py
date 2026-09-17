"""crypto.py —— 落库密钥的认证加密（Encrypt-then-MAC）。"""
from __future__ import annotations

import base64

import pytest

from app import crypto


def test_roundtrip():
    plain = "sk-live-abcdef1234567890"
    enc = crypto.encrypt(plain)
    assert crypto.is_encrypted(enc)
    assert enc.startswith("enc:v1:")
    assert plain not in enc                      # 明文不出现在密文里
    assert crypto.decrypt(enc) == plain


def test_empty_and_idempotent():
    assert crypto.encrypt("") == ""
    assert crypto.encrypt(None) == ""
    enc = crypto.encrypt("k")
    assert crypto.encrypt(enc) == enc             # 已加密的不再套一层
    assert crypto.decrypt("") == ""


def test_legacy_plaintext_passthrough():
    """旧库里的明文（无前缀）必须原样返回，升级不炸。"""
    assert not crypto.is_encrypted("sk-old-plain")
    assert crypto.decrypt("sk-old-plain") == "sk-old-plain"


def test_tamper_detected():
    """改密文任何一个字节 → MAC 校验失败 → 返回空串（绝不把坏密文当密钥用）。"""
    enc = crypto.encrypt("sk-secret")
    body = bytearray(base64.b64decode(enc[len(crypto.PREFIX):]))
    body[14] ^= 0x01
    bad = crypto.PREFIX + base64.b64encode(bytes(body)).decode()
    assert crypto.decrypt(bad) == ""


def test_wrong_master_key_cannot_decrypt(monkeypatch):
    enc = crypto.encrypt("sk-secret")
    monkeypatch.setenv("QLIKEAPI_ENC_KEY", "another-key")
    monkeypatch.setenv("QLIKEAPI_SECRET", "another-key")
    assert crypto.decrypt(enc) == ""


def test_random_nonce_per_encryption():
    assert crypto.encrypt("same") != crypto.encrypt("same")


def test_lines_multikey_rotation():
    raw = "sk-a\n\nsk-b\nsk-c\n"
    enc = crypto.encrypt_lines(raw)
    assert enc.count("\n") == 2                    # 空行被丢掉，行结构保留
    assert all(x.startswith("enc:v1:") for x in enc.splitlines())
    assert crypto.decrypt_lines(enc) == ["sk-a", "sk-b", "sk-c"]
    assert crypto.decrypt_lines("") == []


def test_multiline_ciphertext_rejected_by_scalar_decrypt():
    """多行密文必须走 decrypt_lines，标量 decrypt 直接判废（别把第一行当结果）。"""
    enc = crypto.encrypt_lines("a\nb")
    assert crypto.decrypt(enc) == ""


def test_gen_key_unique_and_prefixed():
    a, b = crypto.gen_key(), crypto.gen_key()
    assert a.startswith("sk-ql-") and a != b
    assert len(a) > 30


@pytest.mark.parametrize("value", [None, "", "x"])
def test_is_encrypted_on_edge_values(value):
    assert crypto.is_encrypted(value) is False
