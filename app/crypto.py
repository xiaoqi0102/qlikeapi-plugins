"""crypto.py —— 零依赖的「认证加密」小模块（用来加密落库的上游密钥）。

为什么不用 cryptography / Fernet？
  容器镜像只装了 fastapi/uvicorn/httpx/python-multipart，不想为了一个功能把
  整套加密库塞进去（构建慢、体积大、升级面广）。这里用标准库实现一套
  HMAC-SHA256 密钥流派密码 + Encrypt-then-MAC 认证，安全性满足「密钥不裸奔落盘」：

  · master = SHA256(QLIKEAPI_ENC_KEY 或 QLIKEAPI_SECRET)
  · k_enc  = HMAC(master, "qlikeapi-enc")   k_mac = HMAC(master, "qlikeapi-mac")
  · 密文   = 明文 XOR HMAC(k_enc, nonce ‖ counter)
  · 存储   = "enc:v1:" + base64(nonce ‖ 密文 ‖ HMAC(k_mac, nonce ‖ 密文))

  解密前先验 MAC（常数时间比较），密文被改一个字节就直接判为损坏。
  兼容旧库：没有 "enc:v1:" 前缀的值按明文处理（并在下次保存时自动加密）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

PREFIX = "enc:v1:"
_MAC_LEN = 32
_NONCE_LEN = 12


_WARNED = False


def _master() -> bytes:
    """主密钥 = SHA256(QLIKEAPI_ENC_KEY 或 QLIKEAPI_SECRET)。

    两个都没配时回退到内置默认值（只为「本地跑起来看看」方便），并告警一次：
    这种情况下落库密钥的加密强度形同虚设，生产必须配置。
    """
    global _WARNED
    raw = os.environ.get("QLIKEAPI_ENC_KEY") or os.environ.get("QLIKEAPI_SECRET")
    if not raw:
        raw = "qlikeapi-plugins-default"
        if not _WARNED:
            _WARNED = True
            print("[qlikeapi] 警告：未设置 QLIKEAPI_SECRET / QLIKEAPI_ENC_KEY，正在使用内置默认密钥派生。"
                  "生产环境请务必配置，否则落库密钥的保护强度不足。", flush=True)
    return hashlib.sha256(raw.encode()).digest()


def _sub(label: bytes) -> bytes:
    return hmac.new(_master(), label, hashlib.sha256).digest()


def _keystream(k_enc: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(k_enc, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def encrypt(plain: str | None) -> str:
    """明文 → 密文。空值原样返回；已加密的值不重复加密。"""
    if not plain:
        return plain or ""
    if is_encrypted(plain):
        return plain
    data = str(plain).encode()
    nonce = secrets.token_bytes(_NONCE_LEN)
    k_enc, k_mac = _sub(b"qlikeapi-enc"), _sub(b"qlikeapi-mac")
    ct = bytes(a ^ b for a, b in zip(data, _keystream(k_enc, nonce, len(data)), strict=False))
    mac = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()
    return PREFIX + base64.b64encode(nonce + ct + mac).decode()


def decrypt(value: str | None) -> str:
    """密文 → 明文。旧数据（无前缀）原样返回；损坏则返回空串（绝不把密文当密钥用）。"""
    if not value:
        return ""
    s = str(value)
    if not is_encrypted(s):
        return s
    if "\n" in s:
        # 多行密文请用 decrypt_lines()；这里直接判废，避免把「第一行」当成整串结果
        return ""
    try:
        blob = base64.b64decode(s[len(PREFIX):])
        nonce, ct, mac = blob[:_NONCE_LEN], blob[_NONCE_LEN:-_MAC_LEN], blob[-_MAC_LEN:]
        k_enc, k_mac = _sub(b"qlikeapi-enc"), _sub(b"qlikeapi-mac")
        want = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, want):
            return ""
        return bytes(a ^ b for a, b in zip(ct, _keystream(k_enc, nonce, len(ct)), strict=False)).decode(errors="replace")
    except Exception:
        return ""


def encrypt_lines(raw: str | None) -> str:
    """多把 key（换行分隔）逐行加密，行结构保留，便于轮换与展示。"""
    if not raw:
        return ""
    return "\n".join(encrypt(line) for line in str(raw).splitlines() if line.strip())


def decrypt_lines(raw: str | None) -> list[str]:
    return [decrypt(x) for x in str(raw or "").splitlines() if x.strip()]


def gen_key(prefix: str = "sk-ql") -> str:
    """生成一把访问令牌（给客户端用）。"""
    return f"{prefix}-{secrets.token_urlsafe(32)}"
