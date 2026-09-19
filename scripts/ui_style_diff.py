#!/usr/bin/env python3
"""UI 样式 A/B 回归检查：同一页面状态下切换「新组件库」与「旧 style.css」，逐元素比对计算样式。

为什么这样做：CSS 重构（合并重复规则、拆分文件）会改变同优先级规则的书写顺序，
而 CSS 靠顺序决胜 —— 静态看代码看不出来，只有让浏览器真的算一遍才准。
本脚本保证两次抓取是**同一个 DOM 状态**（不重新加载页面，只换样式表），因此可以按下标一一对齐。

用法：
  # 对照组 = 上一版样式（tokens.css + 上一版 ui-kit.css），不是仓库里那份远古单文件 style.css：
  git show HEAD~1:app/static/css/ui-kit.css > /tmp/uikit_prev.css
  cat app/static/css/tokens.css /tmp/uikit_prev.css > /tmp/baseline.css
  docker cp /tmp/baseline.css <容器>:/app/static/style.css
  # 期望：除「本次新增的组件类」外全站 0 差异；注入过期对照组会得到满屏假差异。
  QLIKEAPI_ADMIN_USER=.. QLIKEAPI_ADMIN_PASS=.. python3 scripts/ui_style_diff.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

BASE = os.environ.get("QLIKE_BASE", "http://127.0.0.1:18673")
USER = os.environ["QLIKEAPI_ADMIN_USER"]
PASS = os.environ["QLIKEAPI_ADMIN_PASS"]
VIEWS = ["overview", "usage", "providers", "models", "plugins", "logs", "jobs", "balances", "settings"]

# 不变量：颜色/字体/间距/边框/圆角/阴影/布局模式/对齐/换行（尺寸类允许因内容变化而不同）
PROPS = """display position float color backgroundColor backgroundImage fontSize fontWeight fontStyle
lineHeight letterSpacing textAlign textTransform textDecorationLine whiteSpace fontFamily
paddingTop paddingRight paddingBottom paddingLeft marginTop marginRight marginBottom marginLeft
borderTopWidth borderRightWidth borderBottomWidth borderLeftWidth borderTopColor borderRightColor
borderBottomColor borderLeftColor borderTopStyle borderTopLeftRadius borderTopRightRadius
borderBottomLeftRadius borderBottomRightRadius boxShadow opacity overflow alignItems justifyContent
flexDirection flexWrap gap gridTemplateColumns verticalAlign visibility""".split()

JS = """() => {
  const props = %s;
  return [...document.querySelectorAll('body *')].map(el => {
    const cs = getComputedStyle(el);
    const o = {tag: el.tagName, cls: (el.className || '').toString().slice(0, 70)};
    o.inKit = !!el.closest('.pk, .pk-pop, .cf, .set-sec, .kv2, .price-cell, .ih-sw');   // 新增组件（多选选择器/确认框/设置页四列区块）内部元素，不参与回归比对
    for (const p of props) o[p] = cs[p];
    return o;
  });
}""" % json.dumps(PROPS)

SWAP_JS = r"""() => {
  // 关掉新组件库，换成旧 style.css（同一个 DOM，只换样式表）
  const kill = [...document.querySelectorAll('link[rel=stylesheet]')]
      .filter(l => /ui-kit\.css|tokens\.css/.test(l.getAttribute('href') || ''));
  kill.forEach(l => { l.disabled = true; });
  let old = document.querySelector('link[data-ab-old]');
  if (!old) {
    old = document.createElement('link');
    old.rel = 'stylesheet'; old.href = '/static/style.css'; old.setAttribute('data-ab-old', '1');
    document.head.appendChild(old);
  }
  return {killed: kill.length, disabledFlags: kill.map(l => l.disabled)};
}"""

RESTORE_JS = """() => {
  document.querySelectorAll('link[href*="/static/css/"]').forEach(l => l.disabled = false);
  const old = document.querySelector('link[data-ab-old]');
  if (old) old.remove();
  return true;
}"""


def diff_rows(a, b):
    """返回 [(下标, 标签.类, 属性, 新值, 旧值)]"""
    out = []
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x.get("inKit") or y.get("inKit"):
            continue          # 新增组件（多选选择器/确认框）内部元素，不做回归比对
        if (x["tag"], x["cls"]) != (y["tag"], y["cls"]):
            out.append((i, f'{x["tag"]}.{x["cls"]}', "(元素错位)", y["cls"], x["cls"]))
            continue
        for p in PROPS:
            if x[p] != y[p]:
                out.append((i, f'{x["tag"]}.{x["cls"]}', p, x[p], y[p]))
    if len(a) != len(b):
        out.append((-1, "(元素数)", "count", str(len(a)), str(len(b))))
    return out


async def capture(pg):
    return await pg.evaluate(JS)


async def main():
    allbad = 0
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        pg = await br.new_page(viewport={"width": 1440, "height": 1000})
        await pg.goto(BASE + "/login", wait_until="networkidle", timeout=60000)
        await pg.fill("input[name=username]", USER)
        await pg.fill("input[name=password]", PASS)
        await pg.click("button[type=submit], .btn-primary")
        await pg.wait_for_timeout(2500)

        states = []
        for v in ([] if os.environ.get("QLIKE_AB_MODAL_ONLY") else VIEWS):
            states.append((f"{v}·浅色·1440", v, False, 1440))
        states += [] if os.environ.get("QLIKE_AB_MODAL_ONLY") else [("providers·深色·1440", "providers", True, 1440),
                   ("balances·深色·1440", "balances", True, 1440),
                   ("overview·深色·1440", "overview", True, 1440),
                   ("providers·浅色·900", "providers", False, 900),
                   ("overview·浅色·900", "overview", False, 900),
                   ("settings·浅色·900", "settings", False, 900)]

        for name, view, dark, width in states:
            await pg.set_viewport_size({"width": width, "height": 1000})
            await pg.goto(f"{BASE}/#{view}", wait_until="networkidle", timeout=60000)
            await pg.wait_for_timeout(2000)
            await pg.evaluate("d => { document.body.classList.toggle('dark', d) }", dark)
            await pg.wait_for_timeout(400)
            new = await capture(pg)
            sw = await pg.evaluate(SWAP_JS)
            assert sw["killed"] >= 2 and all(sw["disabledFlags"]), f"样式表切换失败: {sw}"
            await pg.wait_for_timeout(700)
            old = await capture(pg)
            await pg.evaluate(RESTORE_JS)
            await pg.wait_for_timeout(300)
            d = diff_rows(new, old)
            if d:
                allbad += len(d)
                print(f"\n⚠ {name}: {len(d)} 处差异")
                seen = {}
                for _i, el, p, nv, ov in d:
                    seen.setdefault((el, p), []).append((nv, ov))
                for (el, p), vals in list(seen.items())[:14]:
                    print(f"   {el} → {p}: 新={vals[0][0]!r}  旧={vals[0][1]!r}  (x{len(vals)})")
            else:
                print(f"✓ {name}: 完全一致")

        # ---- 交互态：令牌弹窗（多选选择器）、渠道展开行、路由折叠面板、确认框 ----
        await pg.set_viewport_size({"width": 1440, "height": 1000})
        await pg.goto(BASE + "/#tokens", wait_until="networkidle", timeout=60000)
        await pg.wait_for_timeout(2000)
        try:
            await pg.click("text=新建令牌", timeout=8000)
            await pg.wait_for_timeout(1200)
            for sel in ["#pkModels .pk-ctl"]:
                if await pg.query_selector(sel):
                    await pg.click(sel)
                    await pg.wait_for_timeout(600)
            new = await capture(pg)
            await pg.evaluate(SWAP_JS)
            await pg.wait_for_timeout(600)
            old = await capture(pg)
            await pg.evaluate(RESTORE_JS)
            d = diff_rows(new, old)
            # 选择器是新增组件，旧样式下必然无样式 → 只报「非 .pk/.cf 元素」的差异
            # 新组件内部元素已由 inKit 过滤，这里不该再有差异
            if d:
                allbad += len(d)
                print(f"\n⚠ 令牌弹窗（含多选选择器）: {len(d)} 处差异")
                for _i, el, p, nv, ov in d[:14]:
                    print(f"   {el} → {p}: 新={nv!r} 旧={ov!r}")
            else:
                print("✓ 令牌弹窗（含多选选择器）: 除新组件外完全一致")
        except Exception as e:  # noqa: BLE001
            print(f"⚠ 令牌弹窗检查跳过: {type(e).__name__} {e}")
        await br.close()
    print(f"\n合计差异: {allbad}")
    return 1 if allbad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
