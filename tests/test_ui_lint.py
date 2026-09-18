"""设计规范守卫的回归用例：把 `scripts/ui_lint.py` 挂进 pytest，改坏样式在本地测试阶段就失败。

对应 docs/DESIGN-SYSTEM.md §2.3 / §10（坑 1 注释闭合、坑 16 深色露白）。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LINT = ROOT / "scripts" / "ui_lint.py"
TOKENS = ROOT / "app" / "static" / "css" / "tokens.css"


def run_lint(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LINT), *args], cwd=ROOT, capture_output=True, text=True)


def test_ui_lint_passes() -> None:
    """硬编码色/字号/圆角/时长、注释闭合、var() 引用、内联 style —— 一项都不许违规。"""
    r = run_lint()
    assert r.returncode == 0, f"设计规范检查失败：\n{r.stdout}\n{r.stderr}"


def test_ui_lint_report_counts_are_zero() -> None:
    r = run_lint("--report")
    assert r.returncode == 0, r.stderr
    for label in ("硬编码颜色", "字号字面量", "圆角字面量", "动效时长字面量"):
        line = next(ln for ln in r.stdout.splitlines() if label in ln)
        assert "0 处" in line, f"{label} 未清零：{line}"


def test_tokens_file_has_balanced_comments_and_root() -> None:
    """tokens.css 被改坏过一次（注释提前闭合 → :root 被浏览器整块丢弃），这里做静态兜底。"""
    css = TOKENS.read_text(encoding="utf-8")
    assert css.count("/*") == css.count("*/"), "tokens.css 注释不闭合：注释里塞了 CSS？"
    # 注释内的 `{`/`}` 不许出现（嵌套注释事故的直接诱因）
    for chunk in css.split("/*")[1:]:
        body = chunk.split("*/")[0]
        assert "{" not in body and "}" not in body, f"注释里出现了花括号：{body[:60]!r}"
    assert ":root{" in css.replace(" ", ""), "tokens.css 缺少 :root{} 变量块"
    for name in ("--ink", "--bg", "--surface", "--line", "--brand", "--r-sm", "--fs-base"):
        assert name in css, f"tokens.css 缺少关键变量 {name}"


def test_dark_block_redefines_same_variable_names() -> None:
    """深色模式只覆盖同名变量：body.dark 里出现新变量名，说明有人绕过规范另起了一套。"""
    css = TOKENS.read_text(encoding="utf-8")
    import re as _re
    # 注意：文件头注释里也提到了 body.dark，必须匹配真正的规则开头
    m = _re.search(r"^body\.dark\s*\{", css, _re.M)
    assert m, "tokens.css 缺少 body.dark 深色变量块"
    i = m.start()
    dark = css[i:]
    root = css[:i]
    root_names = set(__import__("re").findall(r"(--[a-z0-9-]+)\s*:", root))
    dark_names = set(__import__("re").findall(r"(--[a-z0-9-]+)\s*:", dark))
    extra = {n for n in dark_names if n not in root_names}
    assert not extra, f"body.dark 里出现了浅色块没有的变量（应只覆盖同名变量）：{sorted(extra)}"
