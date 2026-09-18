"""app/pluginstore.py —— 渠道插件文件管理 + 合规校验（面板「渠道插件」页用）。

把「加一个渠道」从「进服务器放文件」变成「面板里贴代码 → 校验 → 安装」。

⚠ 安全边界（面板与文档都如实写明，不给虚假安全感）：
   上传插件 = 往本服务里装**可执行代码**，等同于给自己装程序。下面的静态校验只能挡住
   手滑和**明显**的危险写法（进程 / 网络 / 文件 / 系统 / 反射逃逸），**它不是沙箱**。
   只装你能看懂、且来源可信的插件。
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

from . import channels, store

MAX_SOURCE = 256 * 1024
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}\.py$")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
DISABLED_SUFFIX = ".disabled"
RESERVED_FILES = {"__init__.py", "base.py", "_template.py"}
SECRETISH = re.compile(r"TOKEN|SECRET|PASS|KEY|COOKIE|AUTH", re.I)

# 插件不许碰的东西：进程 / 网络 / 文件 / 系统（插件只做协议翻译，I/O 统一走框架）
BANNED_IMPORTS = {
    "subprocess": "起进程", "socket": "开网络连接", "ctypes": "调 C 库", "importlib": "动态导入",
    "multiprocessing": "起进程", "pickle": "反序列化", "marshal": "反序列化", "shutil": "动文件",
    "pty": "开终端", "tty": "开终端", "os": "操作系统", "sys": "动解释器", "pathlib": "动文件",
    "glob": "遍历文件", "tempfile": "写临时文件", "sqlite3": "直连数据库", "asyncio": "自己跑事件循环",
    "threading": "起线程", "concurrent": "起线程", "requests": "自己发请求", "httpx": "自己发请求",
    "aiohttp": "自己发请求", "urllib": "自己发请求", "urllib3": "自己发请求", "http": "自己发请求",
}
BANNED_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint",
                "globals", "locals", "vars", "memoryview"}
BANNED_ATTRS = {"__globals__", "__builtins__", "__subclasses__", "__code__", "__bases__",
                "__mro__", "__reduce__", "__getattribute__", "__dict__"}
WARN_IMPORTS = {"time": "用到时间（插件里通常不需要）", "random": "用到随机数（结果不可复现）",
                "logging": "自己打日志（框架日志更全）"}
ALLOWED_AUTH = {"bearer", "x-goog-api-key", "fal_key", "none"}
ALLOWED_MODES = {"native", "converted", "queue"}
ALLOWED_REFS = {"url", "both", "base64"}
ALLOWED_OPS = {"generate", "edit"}

_PROBE = r'''
import json, pathlib, sys
root, src, stem, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, root)
from app.channels import load_path
from app.channels.base import Channel
try:
    m = load_path(pathlib.Path(src), "app.channels." + stem)
    ch = getattr(m, "CHANNEL", None)
    if ch is None:
        r = {"ok": False, "error": "模块里没有 CHANNEL 对象（末尾别忘了 CHANNEL = 你的类()）"}
    else:
        r = {"ok": True, "info": ch.info(),
             "build_overridden": type(ch).build is not Channel.build,
             "parse_overridden": type(ch).parse is not Channel.parse}
except Exception as e:
    r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
pathlib.Path(out).write_text(json.dumps(r, ensure_ascii=False))
'''


# ------------------------------------------------------------------ 目录与路径

def plugin_dir() -> pathlib.Path:
    """上传插件的存放目录（挂载卷上，重建容器不丢）。"""
    return channels.plugin_dir()


def _safe(filename: str) -> str:
    """文件名白名单：挡路径穿越（../、子目录、隐藏文件）。"""
    name = (filename or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("文件名不合法")
    if name.endswith(DISABLED_SUFFIX):
        name = name[: -len(DISABLED_SUFFIX)]
    if not NAME_RE.match(name):
        raise ValueError("文件名只能是小写字母开头 + 小写字母/数字/下划线，以 .py 结尾（例如 my_relay.py）")
    if name in RESERVED_FILES or name.startswith("_"):
        raise ValueError(f"{name} 是保留文件名，换一个")
    return name


def _builtin_dir() -> pathlib.Path:
    return pathlib.Path(channels.__file__).parent


def _usage() -> dict[str, list[str]]:
    """插件 id → 正在用它的渠道实例（删除/停用前要拦）。"""
    out: dict[str, list[str]] = {}
    try:
        with store.connect() as c:
            for key, proto in c.execute("SELECT key, protocol FROM providers").fetchall():
                out.setdefault(proto or "", []).append(key)
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ 静态扫描

def _scan(tree: ast.AST) -> tuple[list, list, list]:
    imports, calls, attrs = [], [], []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports += [(a.name.split(".")[0], n.lineno) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            if (n.level or 0) == 0 and n.module:
                imports.append((n.module.split(".")[0], n.lineno))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in BANNED_CALLS:
            calls.append((n.func.id, n.lineno))
        elif isinstance(n, ast.Attribute) and n.attr in BANNED_ATTRS:
            attrs.append((n.attr, n.lineno))
    return imports, calls, attrs


def _is_channel_base(b: ast.AST) -> bool:
    if isinstance(b, ast.Name):
        return b.id in ("Channel", "BaseChannel")
    if isinstance(b, ast.Attribute):
        return b.attr in ("Channel", "BaseChannel")
    return False


def _probe(source: str, stem: str, timeout: float = 25.0) -> dict:
    """在子进程里真装一次，拿回 info()。

    子进程 + 超时 + 剥掉密钥类环境变量：校验阶段不该让还没被信任的代码读到本站密钥。
    """
    with tempfile.TemporaryDirectory(prefix="qlplug-") as d:
        src = pathlib.Path(d) / f"{stem}.py"
        out = pathlib.Path(d) / "result.json"
        src.write_text(source)
        env = {k: v for k, v in os.environ.items() if not SECRETISH.search(k)}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            root = str(pathlib.Path(__file__).resolve().parent.parent)
            p = subprocess.run([sys.executable, "-c", _PROBE, root, str(src), stem, str(out)],
                               capture_output=True, text=True, timeout=timeout, env=env, cwd="/")
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"装载超时（>{int(timeout)}s）：插件在 import 阶段卡住了"}
        if out.exists():
            try:
                return json.loads(out.read_text())
            except Exception as e:  # pragma: no cover - 极端情况
                return {"ok": False, "error": f"装载结果读不出来：{e}"}
        tail = [x for x in (p.stderr or "").strip().splitlines() if x.strip()][-3:]
        return {"ok": False, "error": "装载失败：" + (" / ".join(tail) or f"退出码 {p.returncode}")}


# ------------------------------------------------------------------ 校验

def validate(source: str, filename: str = "") -> dict:
    """合规校验：语法 → 危险写法 → 结构 → 子进程真装载 → 元信息。

    返回 {"ok", "errors"[{code,msg,line}], "warnings", "checks", "info"}。
    """
    rep: dict = {"ok": False, "errors": [], "warnings": [], "checks": [], "info": None,
                 "build_overridden": False, "parse_overridden": False}

    def err(code: str, msg: str, line: int = 0) -> None:
        rep["errors"].append({"code": code, "msg": msg, "line": int(line or 0)})

    def warn(code: str, msg: str, line: int = 0) -> None:
        rep["warnings"].append({"code": code, "msg": msg, "line": int(line or 0)})

    def check(name: str, ok: bool, detail: str = "") -> None:
        rep["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    # 文件名 / 体积的问题不提前 return：让用户一次看到全部问题，别修一个报一个
    if filename:
        if not NAME_RE.match(filename):
            err("bad_filename", "文件名要是小写字母开头、以 .py 结尾（只能小写字母/数字/下划线），例如 my_relay.py")
        if filename in RESERVED_FILES or filename.startswith("_"):
            err("reserved_filename", f"{filename} 是保留文件名，换一个")
    if not source.strip():
        err("empty", "源码是空的")
        return rep
    if len(source) > MAX_SOURCE:
        err("too_large", f"源码超过 {MAX_SOURCE // 1024} KB，插件不该这么大")

    try:
        tree = ast.parse(source, filename or "<plugin>")
    except SyntaxError as e:
        err("syntax", f"第 {e.lineno} 行语法错误：{e.msg}", e.lineno or 0)
        return rep
    check("语法", True, f"AST 解析通过（{len(source.splitlines())} 行）")

    imports, calls, attrs = _scan(tree)
    for mod, ln in imports:
        if mod in BANNED_IMPORTS:
            err("banned_import", f"不许 import {mod}（{BANNED_IMPORTS[mod]}）：插件只做协议翻译，"
                                 f"网络/文件/进程统一走框架", ln)
        elif mod in WARN_IMPORTS:
            warn("warn_import", f"用了 {mod}：{WARN_IMPORTS[mod]}", ln)
    for name, ln in calls:
        err("banned_call", f"不许调用 {name}()：插件里不需要，且可能被用来绕过校验", ln)
    for name, ln in attrs:
        err("banned_attr", f"不许访问 {name}（反射逃逸的典型写法）", ln)
    n_bad = len(rep["errors"])
    check("危险写法", n_bad == 0, "没有进程/网络/文件/反射逃逸写法" if n_bad == 0
          else f"发现 {n_bad} 处危险写法")

    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    derived = [c.name for c in classes if any(_is_channel_base(b) for b in c.bases)]
    has_assign = any(isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "CHANNEL" for t in n.targets) for n in tree.body)
    if not derived:
        err("no_class", "必须定义一个继承 Channel 的类：from .base import Channel")
    if not has_assign:
        err("no_channel", "末尾必须写 CHANNEL = 你的类()"
                          "（模板里那行默认是注释掉的，去掉行首的 # 即可）")
    check("结构", bool(derived) and has_assign,
          f"类 {derived[0]} + CHANNEL 赋值都在" if derived and has_assign else "缺类或缺 CHANNEL 赋值")
    if rep["errors"]:
        return rep

    stem = pathlib.Path(filename).stem if filename else "plugin"
    pr = _probe(source, stem)
    if not pr.get("ok"):
        err("load_failed", "真装一次失败：" + str(pr.get("error") or "未知错误"))
        return rep
    info = pr.get("info") or {}
    rep["info"] = info
    rep["build_overridden"] = bool(pr.get("build_overridden"))
    rep["parse_overridden"] = bool(pr.get("parse_overridden"))
    check("可装载", True, f"子进程里装载成功：{info.get('id')} · {info.get('label')}")

    cid = str(info.get("id") or "")
    if not ID_RE.match(cid):
        err("bad_id", f"id「{cid}」不合法：小写字母开头，只能小写字母/数字/下划线（2~41 字符）")
    elif cid in channels.ALIASES:
        err("reserved_id", f"id「{cid}」是历史别名，换一个")
    owner = channels.ORIGIN_FILES.get(cid)
    if owner and (not filename or pathlib.Path(owner).name != filename):
        err("id_taken", f"id「{cid}」已被 {pathlib.Path(owner).name} 占用：一个 id 只能有一个插件")
    if not rep["build_overridden"]:
        err("no_build", "没有实现 build()：渠道插件必须把标准 OpenAI 图片请求翻译成上游报文")
    for field, cn in (("label", "中文名"), ("hint", "一句话说明"), ("vendor", "协议归属方"),
                      ("docs", "官方文档地址"), ("note", "协议说明 protocol_note")):
        if not str(info.get(field) or "").strip():
            err("missing_" + field, f"{cn}（{field}）必填：面板、日志、文档都靠它")
    if info.get("docs") and not str(info["docs"]).startswith("http"):
        err("bad_docs", "docs 要填官方文档的 http(s) 地址")
    if not str(info.get("default_base_url") or "").startswith("http"):
        err("bad_base_url", "default_base_url 要填上游 http(s) 地址（新建渠道实例时的默认值）")
    ops = info.get("operations") or []
    if not ops:
        err("no_ops", 'operations 不能为空：至少声明 {"generate": "native"}')
    for o in ops:
        if o.get("operation") not in ALLOWED_OPS:
            err("bad_op", f"操作名「{o.get('operation')}」不认：只能是 generate / edit")
        if o.get("mode") not in ALLOWED_MODES:
            err("bad_mode", f"操作模式「{o.get('mode')}」不认：只能是 native / converted / queue")
    if (info.get("ref_input") or "") not in ALLOWED_REFS:
        err("bad_ref", "ref_input 只能是 url（只认公网 URL）/ both（都支持）/ base64（只认 base64）")
    for k, v in (info.get("ref_input_faces") or {}).items():
        if v not in ALLOWED_REFS:
            err("bad_ref_face", f"面「{k}」的 ref_input 只能是 url / both / base64")
    if (info.get("default_auth") or "") not in (info.get("auth_modes") or []):
        err("bad_auth", f"default_auth「{info.get('default_auth')}」不在 auth_modes 里")
    if not info.get("models"):
        warn("no_models", "没预置模型名：新建渠道实例时要手填模型映射（也可以在实例里点「拉取上游模型」）")
    if not rep["parse_overridden"]:
        warn("no_parse", "没实现 parse()：用默认解析（URL / b64_json / 常见嵌套都能认），多数情况够用")

    rep["ok"] = not rep["errors"]
    return rep


# ------------------------------------------------------------------ 行数据

def _row(f: pathlib.Path, builtin: bool, used: dict, enabled: bool = True) -> dict:
    """一行的展示数据：文件信息 + （能装载的话）插件 info() 全字段。

    info() 的字段全带上，是为了让「渠道实例」弹窗里那些下拉/说明（label/vendor/docs/
    note/operations/ref_input/default_base_url…）拿到的东西和以前一模一样。
    """
    name = f.name[: -len(DISABLED_SUFFIX)] if f.name.endswith(DISABLED_SUFFIX) else f.name
    stem = pathlib.Path(name).stem
    cid = _BY_FILE().get(f.name) or (stem if enabled else stem)
    ch = channels.REGISTRY.get(cid) if (enabled and cid) else None
    info = ch.info() if ch is not None else {}
    st = f.stat()
    row = dict(info)
    row.update({
        "file": f.name, "stem": stem,
        "id": info.get("id") or stem, "label": info.get("label") or "",
        "builtin": builtin, "uploaded": not builtin, "editable": not builtin,
        "enabled": bool(enabled), "loaded": bool(info),
        "load_error": channels.ERRORS.get(stem, ""), "used_by": used.get(cid, []),
        "size": st.st_size, "mtime": int(st.st_mtime),
    })
    return row


def _BY_FILE() -> dict:
    """文件名 → 插件 id（loader 记的是 id → 文件路径，这里翻过来）。"""
    return {pathlib.Path(path).name: cid for cid, path in channels.ORIGIN_FILES.items()}


# ------------------------------------------------------------------ 文件管理（增删改查）

def list_files() -> list[dict]:
    """内置 + 上传的插件文件（含停用的），供面板插件表展示。"""
    used = _usage()
    out: list[dict] = []
    for d, is_builtin in ((_builtin_dir(), True), (plugin_dir(), False)):
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix != ".py" or f.name in RESERVED_FILES or f.name.startswith("_"):
                continue
            out.append(_row(f, is_builtin, used))
    pd = plugin_dir()
    if pd.is_dir():
        for f in sorted(pd.glob("*" + DISABLED_SUFFIX)):
            out.append(_row(f, False, used, enabled=False))
    return out


def read(filename: str) -> dict:
    """读源码：上传的优先，其次内置（只读查看）。"""
    name = _safe(filename)
    for d, builtin in ((plugin_dir(), False), (_builtin_dir(), True)):
        for cand, enabled in ((d / name, True), (d / (name + DISABLED_SUFFIX), False)):
            if cand.exists():
                return {"file": name, "source": cand.read_text(encoding="utf-8"),
                        "builtin": builtin, "enabled": enabled, "editable": not builtin}
    raise FileNotFoundError(name)


def template() -> str:
    return (_builtin_dir() / "_template.py").read_text(encoding="utf-8")


def save(filename: str, source: str, overwrite: bool = False) -> dict:
    """校验 → 落盘到插件目录 → 热重载。校验不过一律不落盘。"""
    name = _safe(filename)
    if (_builtin_dir() / name).exists():
        raise ValueError(f"{name} 是内置插件，改不了（会被下次构建覆盖）："
                         f"另存为新文件名，比如 {name[:-3]}_my.py")
    rep = validate(source, name)
    if not rep["ok"]:
        return {"ok": False, "report": rep, "error": "校验没通过，未安装"}
    p = plugin_dir() / name
    if p.exists() and not overwrite:
        return {"ok": False, "need_overwrite": True, "report": rep, "error": f"{name} 已存在，确认覆盖后再保存"}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")
    loaded = channels.discover(reload=True)
    load_err = channels.ERRORS.get(name[:-3], "")
    return {"ok": not load_err, "report": rep, "id": (rep["info"] or {}).get("id") or name[:-3],
            "file": name, "loaded": sorted(loaded), "errors": channels.ERRORS, "error": load_err}


def delete(filename: str) -> dict:
    name = _safe(filename)
    if (_builtin_dir() / name).exists():
        raise ValueError(f"{name} 是内置插件，删不了（要停用就在面板上「停用」，或者删掉对应渠道实例）")
    used = _usage().get(pathlib.Path(name).stem, [])
    if used:
        raise ValueError(f"还有渠道实例在用这个插件（{', '.join(used)}）：先改实例的插件或删掉实例")
    live = plugin_dir() / name
    off = live.parent / (name + DISABLED_SUFFIX)
    target = live if live.exists() else (off if off.exists() else None)
    if target is None:                 # 停用中的（.disabled）也要能删掉
        raise FileNotFoundError(name)
    target.unlink()
    channels.discover(reload=True)
    return {"ok": True, "deleted": name, "loaded": sorted(channels.available_ids()),
            "errors": channels.ERRORS}


def set_enabled(filename: str, enabled: bool) -> dict:
    """停用 = 把 x.py 改名成 x.py.disabled（加载器只扫 *.py，自然就不装了）。"""
    name = _safe(filename)
    if (_builtin_dir() / name).exists():
        raise ValueError(f"{name} 是内置插件，不能停用（内置插件是回滚兜底）")
    d = plugin_dir()
    live, off = d / name, d / (name + DISABLED_SUFFIX)
    if enabled:
        if off.exists():
            off.rename(live)
        elif not live.exists():
            raise FileNotFoundError(name)
    else:
        if live.exists():
            live.rename(off)
        elif not off.exists():
            raise FileNotFoundError(name)
    channels.discover(reload=True)
    return {"ok": True, "file": name, "enabled": bool(enabled),
            "loaded": sorted(channels.available_ids()), "errors": channels.ERRORS}
