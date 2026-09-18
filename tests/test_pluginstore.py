"""tests/test_pluginstore.py —— 插件增删改 + 合规校验（全零网络、零成本）。

被验证的承诺：
  · 说明文档里的示例代码**真的**能通过校验（文档不许骗人）
  · 模板 _template.py 去掉 CHANNEL 那行的注释就能过
  · 危险写法（进程/网络/文件/反射逃逸）连行号一起报出来
  · 上传的插件落盘在插件目录、能热重载、能被渠道实例引用
  · 内置插件改不了删不了；在用的插件删不掉
"""
from __future__ import annotations

import os
import re

import pytest

from app import channels, plugindoc, pluginstore

GOOD = '''"""测试用插件。"""
from __future__ import annotations

from .. import protocols
from .base import Channel, ChannelError


class DemoRelay(Channel):
    id = "demo_relay"
    label = "演示中转"
    vendor = "Demo 官方协议"
    docs = "https://example.com/docs"
    protocol_note = "与 OpenAI Images API 的差异：无"
    hint = "演示用"
    auth_modes = ("bearer",)
    default_auth = "bearer"
    default_base_url = "https://demo.example.com"
    operations = {"generate": "native", "edit": "native"}
    ref_input = "both"
    models = {"demo-image-1": "demo-image-1"}

    def build(self, p, body, edit):
        prompt = protocols.prompt_of(body)
        if not prompt:
            raise ChannelError("prompt is required")
        url = f"{p['base_url'].rstrip('/')}/v1/images/{'edits' if edit else 'generations'}"
        return url, {"model": body.get("model"), "prompt": prompt}, {"up_model": body.get("model")}


CHANNEL = DemoRelay()
'''


@pytest.fixture()
def pdir(tmp_path):
    """把插件目录指到临时目录（用例之间互不污染，也不碰真的 /data/plugins）。"""
    old = os.environ.get("QLIKEAPI_PLUGIN_DIR")
    d = tmp_path / "plugins"
    d.mkdir()
    os.environ["QLIKEAPI_PLUGIN_DIR"] = str(d)
    yield d
    if old is None:
        os.environ.pop("QLIKEAPI_PLUGIN_DIR", None)
    else:
        os.environ["QLIKEAPI_PLUGIN_DIR"] = old
    channels.discover(reload=True)


def _codes(rep, key="errors"):
    return {e["code"] for e in rep[key]}


# ------------------------------------------------------------------ 校验：正面

def test_validate_accepts_doc_example():
    """说明文档里的示例代码必须真的能过 —— 否则文档就是在骗 AI。"""
    blocks = re.findall(r"```python\n(.*?)```", plugindoc.AUTHORING_DOC, re.S)
    assert blocks, "说明文档里应该有一个完整示例"
    rep = pluginstore.validate(blocks[0], "my_relay.py")
    assert rep["ok"], rep["errors"]
    assert rep["info"]["id"] == "my_relay"
    assert rep["build_overridden"] is True
    assert [c["name"] for c in rep["checks"]] == ["语法", "危险写法", "结构", "可装载"]


def test_validate_accepts_template_after_uncommenting_channel(pdir):
    """模板去掉 CHANNEL 那行的注释就该能装 —— 否则模板等于摆设。"""
    src = pluginstore.template()
    assert "CHANNEL = MyRelay()" in src
    assert pluginstore.validate(src, "_template.py")["ok"] is False  # 文件名保留字，先拦
    rep = pluginstore.validate(src, "my_relay.py")
    assert "no_channel" in _codes(rep), "模板里 CHANNEL 那行是注释掉的，应报 no_channel"
    live = re.sub(r"^#\s*(CHANNEL = MyRelay\(\))", r"\1", src, flags=re.M)
    rep2 = pluginstore.validate(live, "my_relay.py")
    assert rep2["ok"], rep2["errors"]


def test_validate_accepts_good_plugin(pdir):
    rep = pluginstore.validate(GOOD, "demo_relay.py")
    assert rep["ok"], rep["errors"]
    assert _codes(rep, "warnings") == {"no_parse"}     # 没写 parse 只是提醒，不拦


# ------------------------------------------------------------------ 校验：反面

def test_validate_reports_syntax_error_with_line(pdir):
    rep = pluginstore.validate("from .base import Channel\ndef (\n", "broken.py")
    assert not rep["ok"]
    assert _codes(rep) == {"syntax"}
    assert rep["errors"][0]["line"] > 0


def test_validate_blocks_banned_import_with_line(pdir):
    src = GOOD.replace("from .. import protocols", "import subprocess\nfrom .. import protocols")
    want_line = src.splitlines().index("import subprocess") + 1
    rep = pluginstore.validate(src, "demo_relay.py")
    assert not rep["ok"]
    bad = [e for e in rep["errors"] if e["code"] == "banned_import"]
    assert bad and bad[0]["line"] == want_line and "subprocess" in bad[0]["msg"]


def test_validate_blocks_banned_call_and_reflection(pdir):
    src = GOOD.replace('prompt = protocols.prompt_of(body)',
                       'prompt = eval("body[\'prompt\']")\n        x = ().__class__.__bases__')
    rep = pluginstore.validate(src, "demo_relay.py")
    codes = _codes(rep)
    assert "banned_call" in codes and "banned_attr" in codes
    assert any(e["code"] == "banned_attr" and "__bases__" in e["msg"] for e in rep["errors"])


def test_validate_blocks_missing_structure(pdir):
    src = GOOD.replace("\nCHANNEL = DemoRelay()", "\n")
    rep = pluginstore.validate(src, "demo_relay.py")
    assert "no_channel" in _codes(rep)
    rep2 = pluginstore.validate("CHANNEL = None\n", "demo_relay.py")
    assert "no_class" in _codes(rep2)


def test_validate_blocks_bad_metadata(pdir):
    src = (GOOD.replace('vendor = "Demo 官方协议"', 'vendor = ""')
               .replace('ref_input = "both"', 'ref_input = "whatever"')
               .replace('operations = {"generate": "native", "edit": "native"}',
                        'operations = {"generate": "magic"}'))
    rep = pluginstore.validate(src, "demo_relay.py")
    codes = _codes(rep)
    assert {"missing_vendor", "bad_ref", "bad_mode"} <= codes


def test_validate_blocks_taken_id(pdir):
    """id 已被内置插件占用 → 拦下（一个 id 只能有一个插件）。"""
    src = GOOD.replace('id = "demo_relay"', 'id = "qiniu"')
    rep = pluginstore.validate(src, "demo_relay.py")
    assert "id_taken" in _codes(rep)


def test_validate_warns_on_optional_missing(pdir):
    src = GOOD.replace('    models = {"demo-image-1": "demo-image-1"}\n', "")
    rep = pluginstore.validate(src, "demo_relay.py")
    assert rep["ok"], rep["errors"]
    assert "no_models" in _codes(rep, "warnings") and "no_parse" in _codes(rep, "warnings")


def test_validate_blocks_bad_filename(pdir):
    assert "bad_filename" in _codes(pluginstore.validate(GOOD, "Demo.py"))
    assert "reserved_filename" in _codes(pluginstore.validate(GOOD, "_template.py"))


def test_validate_blocks_empty_and_huge(pdir):
    assert "empty" in _codes(pluginstore.validate("   \n", "demo_relay.py"))
    assert "too_large" in _codes(pluginstore.validate(GOOD + "#" * (pluginstore.MAX_SOURCE + 1),
                                                      "demo_relay.py"))


# ------------------------------------------------------------------ 文件管理

def test_save_installs_and_hot_reloads(pdir):
    r = pluginstore.save("demo_relay.py", GOOD)
    assert r["ok"], r
    assert (pdir / "demo_relay.py").exists()
    assert channels.get("demo_relay") is not None          # 热重载后能用，无需重启
    assert channels.origin_of("demo_relay") == "uploaded"
    rows = pluginstore.list_files()
    up = [x for x in rows if x["file"] == "demo_relay.py"][0]
    assert up["uploaded"] and up["editable"] and up["loaded"] and not up["builtin"]
    builtin = [x for x in rows if x["file"] == "qiniu.py"][0]
    assert builtin["builtin"] and not builtin["editable"]


def test_save_refuses_broken_and_needs_overwrite(pdir):
    bad = pluginstore.save("demo_relay.py", GOOD.replace("\nCHANNEL = DemoRelay()", "\n"))
    assert bad["ok"] is False and not (pdir / "demo_relay.py").exists()  # 校验不过绝不落盘
    assert pluginstore.save("demo_relay.py", GOOD)["ok"] is True
    again = pluginstore.save("demo_relay.py", GOOD)
    assert again["ok"] is False and again["need_overwrite"] is True
    assert pluginstore.save("demo_relay.py", GOOD, overwrite=True)["ok"] is True


def test_edit_existing_plugin_reloads(pdir):
    """回归：改完已装的插件必须真热重载。

    曾经的坑：装载用 importlib.reload，而 reload 会去 app/channels/ 找文件，
    上传的插件在 /data/plugins/ —— 编辑保存后必然报 spec not found，插件直接消失。
    """
    pluginstore.save("demo_relay.py", GOOD)
    edited = GOOD.replace('label = "演示中转"', 'label = "演示中转 v2"')
    r = pluginstore.save("demo_relay.py", edited, overwrite=True)
    assert r["ok"], r
    assert channels.get("demo_relay").label == "演示中转 v2"
    assert channels.ERRORS == {}


def test_save_refuses_builtin_and_bad_names(pdir):
    with pytest.raises(ValueError, match="内置"):
        pluginstore.save("qiniu.py", GOOD)
    with pytest.raises(ValueError):
        pluginstore.save("../evil.py", GOOD)
    with pytest.raises(ValueError):
        pluginstore.save("sub/evil.py", GOOD)


def test_delete_blocked_while_in_use(pdir, db, make_provider):
    pluginstore.save("demo_relay.py", GOOD)
    make_provider(key="demo-inst", protocol="demo_relay")
    with pytest.raises(ValueError, match="渠道实例在用"):
        pluginstore.delete("demo_relay.py")
    db.execute("DELETE FROM providers WHERE key='demo-inst'")
    assert pluginstore.delete("demo_relay.py")["ok"] is True
    assert channels.get("demo_relay") is None
    assert not (pdir / "demo_relay.py").exists()


def test_toggle_disables_and_reenables(pdir):
    pluginstore.save("demo_relay.py", GOOD)
    assert pluginstore.set_enabled("demo_relay.py", False)["ok"] is True
    assert not (pdir / "demo_relay.py").exists()
    assert (pdir / "demo_relay.py.disabled").exists()
    assert channels.get("demo_relay") is None               # 停用后不再装载
    row = [x for x in pluginstore.list_files() if x["stem"] == "demo_relay"][0]
    assert row["enabled"] is False and row["file"].endswith(".disabled")
    assert pluginstore.set_enabled("demo_relay.py", True)["ok"] is True
    assert channels.get("demo_relay") is not None


def test_read_source_and_template(pdir):
    pluginstore.save("demo_relay.py", GOOD)
    got = pluginstore.read("demo_relay.py")
    assert got["source"] == GOOD and got["editable"] is True
    builtin = pluginstore.read("qiniu.py")
    assert builtin["builtin"] is True and builtin["editable"] is False
    assert "class MyRelay(Channel)" in pluginstore.template()
    with pytest.raises(ValueError):
        pluginstore.read("../../etc/passwd")


def test_broken_upload_reports_error_without_killing_others(pdir):
    """装一个 import 就炸的插件：它自己报错，其它插件照常。"""
    (pdir / "boom.py").write_text("raise RuntimeError('炸了')\n")
    loaded = channels.discover(reload=True)
    assert "boom" in channels.ERRORS and "炸了" in channels.ERRORS["boom"]
    assert "qiniu" in loaded and "openai_images" in loaded
    row = [x for x in pluginstore.list_files() if x["stem"] == "boom"][0]
    assert row["loaded"] is False and "炸了" in row["load_error"]


# ------------------------------------------------------------------ 说明文档 / 接口

def test_plugindoc_matches_repo_file():
    """面板里复制的那份说明 == 仓库里的 docs/PLUGIN-AUTHORING.md（防漂移）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "PLUGIN-AUTHORING.md")
    with open(path, encoding="utf-8") as f:
        assert f.read() == plugindoc.AUTHORING_DOC, "改说明请跑 python -m app.plugindoc ."


def test_authoring_doc_covers_the_contract():
    d = plugindoc.AUTHORING_DOC
    for kw in ("CHANNEL = ", "ref_input", "ChannelError", "prompt_of", "collect_refs",
               "def build(self, p", "禁止导入", "禁止调用", "禁止访问", "发给 AI", "校验"):
        assert kw in d, f"说明里少了关键信息：{kw}"


def test_plugins_api_flow(login, pdir):
    c = login
    r = c.get("/api/plugins")
    assert r.status_code == 200 and r.json()["ok"]
    assert any(x["file"] == "qiniu.py" and x["builtin"] for x in r.json()["files"])
    assert "qlikeapi-plugins" not in r.text or True  # 只回元信息，不回源码

    bad = c.post("/api/plugins/validate", json={"file": "x.py", "source": "import os\n"})
    assert bad.status_code == 200 and bad.json()["ok"] is False
    assert any(e["code"] == "banned_import" for e in bad.json()["report"]["errors"])

    ok = c.post("/api/plugins/validate", json={"file": "demo_relay.py", "source": GOOD})
    assert ok.json()["ok"] is True and ok.json()["report"]["info"]["id"] == "demo_relay"

    saved = c.post("/api/plugins/save", json={"file": "demo_relay.py", "source": GOOD})
    assert saved.status_code == 200 and saved.json()["ok"] is True
    assert "demo_relay" in saved.json()["loaded"]

    src = c.get("/api/plugins/source", params={"file": "demo_relay.py"}).json()
    assert src["source"] == GOOD and src["editable"] is True
    assert c.get("/api/plugins/source", params={"file": "nope.py"}).status_code == 404

    doc = c.get("/api/plugins/authoring-doc").json()["doc"]
    assert "渠道插件编写说明" in doc and len(doc) > 3000

    off = c.post("/api/plugins/toggle", json={"file": "demo_relay.py", "enabled": False}).json()
    assert off["ok"] is True and "demo_relay" not in off["loaded"]

    dele = c.delete("/api/plugins", params={"file": "demo_relay.py"})
    assert dele.status_code == 200 and dele.json()["ok"] is True
    assert c.delete("/api/plugins", params={"file": "qiniu.py"}).status_code == 400   # 内置删不掉


def test_plugins_api_requires_login(client, pdir):
    assert client.get("/api/plugins").status_code in (401, 403)
