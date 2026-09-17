"""pytest 共享夹具：全部用例「零网络、零成本」，不碰任何真实上游。

约定：
  · 环境变量必须在 import app.* 之前设置好（store/crypto 在导入期读环境）
  · 每个用例一个全新的 SQLite 库（tmp_path），互不污染
  · 任何可能打上游的地方都被 monkeypatch 拦住，跑测试永远不花钱
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="qlikeapi-tests-")
os.environ["QLIKEAPI_DB"] = os.path.join(_TMP, "bootstrap.db")
os.environ["QLIKEAPI_SECRET"] = "unit-test-secret"
os.environ["QLIKEAPI_ENC_KEY"] = "unit-test-enc-key"
os.environ["QLIKEAPI_UP_TOKEN"] = "unit-test-master-token"
os.environ["QLIKEAPI_ADMIN_USER"] = "tester"
os.environ["QLIKEAPI_ADMIN_PASS"] = "unit-test-pass"
os.environ["QLIKEAPI_SECURE_COOKIE"] = "0"      # 测试走 http，不设 Secure
os.environ["QLIKEAPI_AUTO_DISABLE_AFTER"] = "3"
os.environ["QLIKEAPI_AUTO_RECOVER_SEC"] = "600"
os.environ["QLIKEAPI_PROBE_TIMEOUT"] = "5"

MASTER = os.environ["QLIKEAPI_UP_TOKEN"]
MASTER_HEADERS = {"x-qlikeapi-token": MASTER}


@pytest.fixture(autouse=True)
def reset_relay_state():
    """relay 的内存态是进程级单例（key 轮换指针/冷却、并发闸门），用例之间必须清干净。

    不清的话：前一个用例把某渠道的 key 打进冷却 → 后一个用例莫名其妙收到 503。
    """
    from app import relay

    def _clear():
        relay._KEY_STATE.clear()
        relay.gate._busy.clear()
        relay.gate._waiting = 0
        relay.gate.rejected = 0

    _clear()
    yield
    _clear()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """每个用例一个干净的库。"""
    from app import store

    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test.db"))
    store.init_db()
    return store


@pytest.fixture()
def client(db):
    """带控制台的 FastAPI 测试客户端（同一个 DB）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def login(client):
    """登录控制台，返回带会话 Cookie 的同一个 client。"""
    r = client.post("/api/login", json={"username": "tester", "password": "unit-test-pass"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def make_provider(db):
    """造一个渠道实例（字段与线上表结构一致；密钥落库前自动加密）。"""

    def _mk(key="demo", protocol="openai_images", base_url="https://upstream.example.com",
            api_key="sk-test-000", model_map=None, options=None, enabled=1, priority=0, weight=1,
            base_kwargs=None):
        mm = model_map if model_map is not None else {"gpt-image-2": "gpt-image-2"}
        db.execute(
            "INSERT INTO providers(key,label,protocol,base_url,auth_mode,api_key,model_map,options,"
            "enabled,priority,weight,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, key, protocol, base_url, "bearer", db.crypto.encrypt_lines(api_key),
             db.json.dumps(mm, ensure_ascii=False), db.json.dumps(options or {}),
             enabled, priority, weight, int(db.time.time())),
        )
        return db.get_provider(key)

    return _mk


@pytest.fixture()
def no_upstream(monkeypatch):
    """把「打上游」这条路彻底堵死：用例里一旦真的发请求就立刻失败。

    这是「零成本测试」的硬保险，也顺便证明被测代码没有偷偷联网。
    """
    from app import protocols

    calls = []

    def _boom(url, headers=None, body=None, timeout=None):
        calls.append({"url": url, "body": body})
        raise AssertionError(f"测试期间不允许真的请求上游：{url}")

    monkeypatch.setattr(protocols, "call_upstream", _boom)
    return calls


@pytest.fixture()
def fake_upstream(monkeypatch):
    """假上游：记录请求并返回预设响应（默认 400，模拟「上游拒绝了空 prompt」）。"""
    from app import protocols

    calls = []

    def _fake(status=400, payload=None, text=None):
        def _call(url, headers=None, body=None, timeout=None):
            calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
            return status, payload if payload is not None else {"error": {"message": "prompt is required"}}, \
                text if text is not None else '{"error":{"message":"prompt is required"}}'

        monkeypatch.setattr(protocols, "call_upstream", _call)
        return calls

    return _fake
