#!/usr/bin/env python3
"""ui_lint.py —— 设计规范守卫：把「样式必须走 tokens.css」变成可执行的检查。

为什么要有它（真实事故）：
  2026-09-18 往 `tokens.css` 加变量时，把 CSS 插进了文件头的**注释内部** ——
  CSS 注释不能嵌套，`*/` 提前闭合后面那段就成了非法 CSS；浏览器把之后**一整个 `:root{}` 块丢掉**，
  `--ink/--bg/--surface/--line/--brand` 全部未定义 → 主内容区变白、文字变黑（用户截图反馈「改坏了」）。
  当时没有任何检查能拦住它。本脚本就是那个检查。

检查项（前 3 项是硬失败，后 2 项按阈值）：
  1. tokens.css 注释必须闭合且无嵌套 —— 直接防住上面那类事故；
  2. 任何 `var(--x)` 必须在 tokens.css 里有定义（否则该属性在浏览器里直接失效）；
  3. tokens.css 的每个声明都必须写在规则块里（禁止注释外的裸声明）；
  4. 非 tokens.css 文件里不许出现硬编码颜色（阈值见 ALLOWED，存量收敛后应为 0）；
  5. 字号/圆角/动效时长只允许用 tokens 变量（同样按阈值收敛）。

用法：
  python3 scripts/ui_lint.py            # 打印报告，超阈值退出 1
  python3 scripts/ui_lint.py --report   # 只报告不判失败（存量清点用）
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
TOKENS = STATIC / "css" / "tokens.css"

# 允许的硬编码残留（收敛目标：全 0；改完一批就把它调小，防止回退）
ALLOWED = {"hard_colors": 0, "font_literals": 0, "radius_literals": 0, "duration_literals": 0}

# 扫描范围：自己写的样式与页面（vendor 是第三方，不查）
TARGETS = [STATIC / "css" / "ui-kit.css", STATIC / "css" / "login.css",
           STATIC / "index.html", STATIC / "login.html", STATIC / "ui-kit.html"]
# 内联 style（HTML/JS 拼出来的模板）也要管：颜色与字号必须是 var(...)
INLINE_TARGETS = [STATIC / "ui-kit.html", STATIC / "index.html", STATIC / "login.html",
                  STATIC / "js" / "app.js", STATIC / "js" / "ui-kit.js"]
INLINE_RE = re.compile(r'style="([^"]*)"')
CSS_TARGETS = [p for p in TARGETS if p.suffix == ".css"]

# 允许的例外：功能性色值（不能用主题色替换的）
COLOR_ALLOW = {
    "transparent", "currentColor", "inherit", "none", "initial", "unset", "auto",
}
# 出现在这些行里的颜色/字号不算违规（例如给 browser 的兼容写法、渐变遮罩由渐变色组成）
LINE_ALLOW = (
    "/* ui-lint-allow */",
)


def read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


# ---------------------------------------------------------------- 检查 1：注释
def check_comments(text: str, rel: str) -> list[str]:
    """注释必须闭合、且不能嵌套（嵌套会让后面的规则被整体丢弃）。"""
    bad = []
    if len(re.findall(r"/\*", text)) != len(re.findall(r"\*/", text)):
        bad.append(f"{rel}: 注释不闭合（/* 与 */ 数量不等）")
    i, depth = 0, 0
    while i < len(text) - 1:
        two = text[i:i + 2]
        if two == "/*":
            depth += 1
            if depth > 1:
                line = text[:i].count("\n") + 1
                bad.append(f"{rel}:{line} 注释里又开了注释（CSS 注释不能嵌套，会导致后面规则被吞掉）")
            i += 2
            continue
        if two == "*/":
            depth = max(0, depth - 1)
            i += 2
            continue
        i += 1
    return bad


# ---------------------------------------------------------------- 检查 2/3：变量
def defined_vars(tokens: str) -> set[str]:
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", tokens))


def check_var_refs(files: list[pathlib.Path], defined: set[str]) -> list[str]:
    bad = []
    for p in files:
        for m in re.finditer(r"var\((--[a-z0-9-]+)", read(p)):
            if m.group(1) not in defined:
                line = read(p)[:m.start()].count("\n") + 1
                bad.append(f"{p.relative_to(ROOT)}:{line} 用了未定义的变量 {m.group(1)}")
    return bad


def check_bare_declarations(tokens: str) -> list[str]:
    """tokens.css 里不允许出现「不在规则块内」的裸声明（注释事故的典型症状）。"""
    body = strip_comments(tokens)
    bad, depth = [], 0
    for n, line in enumerate(body.splitlines(), 1):
        depth += line.count("{") - line.count("}")
        if depth == 0 and re.match(r"\s*--[a-z0-9-]+\s*:", line):
            bad.append(f"tokens.css:{n} 裸声明（在规则块外）：{line.strip()[:60]}")
        if depth < 0:
            bad.append(f"tokens.css:{n} 花括号不匹配（多余的 }}）")
            depth = 0
    if depth != 0:
        bad.append(f"tokens.css 花括号不匹配（结尾深度 {depth}）")
    return bad


# ---------------------------------------------------------------- 检查 4/5：硬编码
COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|(?<![\w-])rgba?\((?!\s*var\()[^)]*\)")
FONT_RE = re.compile(r"font-size:\s*([0-9.]+(?:px|rem|em|pt))")
RADIUS_RE = re.compile(r"border-radius:\s*([^;]+);")
DURATION_RE = re.compile(r"(?:transition|animation)(?:-[a-z]+)?:\s*([^;]*\b[0-9.]+m?s\b[^;]*);")


def scan_hardcoded(files: list[pathlib.Path]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"hard_colors": [], "font_literals": [], "radius_literals": [], "duration_literals": []}
    for p in files:
        for n, raw in enumerate(read(p).splitlines(), 1):
            if any(a in raw for a in LINE_ALLOW) or raw.strip().startswith(("/*", "*", "//")):
                continue
            line = strip_comments(raw)
            for m in COLOR_RE.finditer(line):
                if m.group(0) in COLOR_ALLOW:
                    continue
                # tokens.css 自己不算
                if p.name != "tokens.css":
                    out["hard_colors"].append(f"{p.name}:{n} {m.group(0)}")
            fm = FONT_RE.search(line)
            if fm and "var(" not in line:
                out["font_literals"].append(f"{p.name}:{n} {fm.group(1)}")
            rm = RADIUS_RE.search(line)
            if rm:
                val = rm.group(1)
                if "var(" not in val and "%" not in val:
                    out["radius_literals"].append(f"{p.name}:{n} {val.strip()}")
            dm = DURATION_RE.search(line)
            if dm and "var(" not in line:
                out["duration_literals"].append(f"{p.name}:{n} {dm.group(1).strip()[:60]}")
    return out


def main() -> int:
    report_only = "--report" in sys.argv
    tokens = read(TOKENS)
    defined = defined_vars(tokens)
    scan_files = CSS_TARGETS

    problems: list[str] = []
    problems += check_comments(tokens, "tokens.css")
    problems += check_bare_declarations(tokens)
    for p in scan_files + [STATIC / "js" / "app.js", STATIC / "js" / "ui-kit.js"]:
        if p.suffix == ".css":
            problems += check_comments(read(p), p.relative_to(ROOT).as_posix())
    problems += check_var_refs(scan_files + [STATIC / "js" / "app.js", STATIC / "js" / "ui-kit.js"], defined)

    # 内联 style 只查颜色与字号（宽度/布局类内联是刻意为之）
    inline_bad = []
    for p in INLINE_TARGETS:
        for m in INLINE_RE.finditer(read(p)):
            body = m.group(1)
            if COLOR_RE.search(body) and "var(" not in body.split(":")[-1]:
                for c in COLOR_RE.finditer(body):
                    inline_bad.append(f"{p.name}: 内联 style 里的颜色 {c.group(0)}")
            if FONT_RE.search(body) and "var(" not in body:
                inline_bad.append(f"{p.name}: 内联 style 里的字号 {FONT_RE.search(body).group(1)}")
    problems += inline_bad

    hard = scan_hardcoded(scan_files)
    over = {k: v for k, v in hard.items() if len(v) > ALLOWED.get(k, 0)}

    print(f"设计规范检查（ui_lint）—— tokens.css 定义 {len(defined)} 个变量，扫描 {len(scan_files)} 个文件")
    if problems:
        print("\n✗ 硬错误：")
        for p in problems:
            print("  ", p)
    else:
        print("✓ 变量结构 / 注释 / var() 引用 / 内联 style：全部通过")
    for k, label in (("hard_colors", "硬编码颜色"), ("font_literals", "字号字面量"),
                     ("radius_literals", "圆角字面量"), ("duration_literals", "动效时长字面量")):
        n, limit = len(hard[k]), ALLOWED.get(k, 0)
        mark = "✓" if n <= limit else "✗"
        print(f"{mark} {label}: {n} 处（阈值 {limit}）")
        if n > limit:
            for item in hard[k][:8]:
                print("     ", item)
            if n > 8:
                print(f"      …… 其余 {n - 8} 处")

    failed = bool(problems) or bool(over)
    if report_only:
        return 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
