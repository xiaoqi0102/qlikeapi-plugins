"""静态资源静态检查：专治「页面在浏览器里报语法错但没人发现」这一类问题。

起因（v3.4.0 真实事故）：
  组件展示页的 HTML 是拼在 JS 单引号字符串里的。写成 `onclick="UI.toast(\\'x\\')"` 时，反斜杠会
  **原样进入 HTML 属性**（`\'` 在 JS 里是合法转义，产出的字符就是 `\\'`），浏览器再把它当 JS 解析 →
  `SyntaxError` → **整个内联脚本块失效**，页面上所有 `demo*` 函数变成 undefined（按钮点了没反应，
  只有控制台能看到报错）。正确写法：属性里的引号用 HTML 实体 `&#39;`。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
HTML_FILES = sorted(STATIC.glob("*.html"))
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
NODE = shutil.which("node")


def test_html_files_exist():
    names = {p.name for p in HTML_FILES}
    assert {"index.html", "login.html", "ui-kit.html"} <= names


def test_onclick_attr_has_no_backslash():
    """onclick 属性值里不允许出现反斜杠 —— 那说明它是从 JS 字符串里"漏"进 HTML 的转义符。"""
    bad = []
    for f in HTML_FILES:
        for m in re.finditer(r'onclick="([^"]*)"', f.read_text(encoding="utf-8")):
            if "\\" in m.group(1):
                bad.append(f"{f.name}: {m.group(1)[:70]}")
    assert not bad, "onclick 里的引号请用 &#39;，不要留反斜杠：\n" + "\n".join(bad)


@pytest.mark.skipif(NODE is None, reason="需要 node 才能校验内联 JS 语法（CI 上自带 node）")
def test_inline_scripts_parse_as_js():
    """每个内联 <script> 都要能通过 `node --check` —— 这条能直接抓住上面那种事故。"""
    errors = []
    for f in HTML_FILES:
        for i, body in enumerate(INLINE_SCRIPT.findall(f.read_text(encoding="utf-8"))):
            if not body.strip():
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
                fh.write(body)
                tmp = fh.name
            r = subprocess.run([NODE, "--check", tmp], capture_output=True, text=True)
            Path(tmp).unlink(missing_ok=True)
            if r.returncode:
                errors.append(f"{f.name} 第 {i + 1} 个内联脚本：{r.stderr.strip().splitlines()[0:3]}")
    assert not errors, "内联脚本有 JS 语法错误（浏览器里会导致整块脚本失效）：\n" + "\n".join(errors)


def test_inline_handlers_are_defined():
    """onclick 里调用的本地函数必须在同一个文件的内联脚本里定义过。"""
    missing = []
    for f in HTML_FILES:
        text = f.read_text(encoding="utf-8")
        scripts = "\n".join(INLINE_SCRIPT.findall(text))
        defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)", scripts))
        defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:function|\()", scripts))
        for m in re.finditer(r'onclick="([A-Za-z_$][\w$]*)\(', text):
            if m.group(1) not in defined:      # UI.* / act.* 这类来自外部脚本，跳过
                missing.append(f"{f.name}: {m.group(1)}()")
    assert not missing, "onclick 调用了未定义的函数：\n" + "\n".join(sorted(set(missing)))


def test_pages_reference_component_library():
    """控制台页面必须引用组件库（tokens/ui-kit 与 UI.*），否则新组件会在线上裸奔。"""
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    for needle in ("css/tokens.css", "css/ui-kit.css", "js/ui-kit.js", "js/app.js"):
        assert needle in index, f"index.html 缺少引用：{needle}"
    kit = (STATIC / "ui-kit.html").read_text(encoding="utf-8")
    assert "js/ui-kit.js" in kit and "UI.picker" in kit
