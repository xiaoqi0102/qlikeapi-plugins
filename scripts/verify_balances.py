#!/usr/bin/env python3
"""站点余额功能验收：从 .env 导入 → 逐个查余额 → 打印结果。全部只读 GET，零成本。

用法：
    export QLIKEAPI_ADMIN_USER=admin QLIKEAPI_ADMIN_PASS=你的控制台密码
    python3 scripts/verify_balances.py
"""
import json
import os
import subprocess

BASE = os.environ.get("QLIKEAPI_BASE", "http://127.0.0.1:18673")
USER = os.environ.get("QLIKEAPI_ADMIN_USER", "admin")
PASS = os.environ.get("QLIKEAPI_ADMIN_PASS", "")
JAR = os.environ.get("QLIKEAPI_COOKIE_JAR", "/tmp/qlikeapi-verify-cookies.txt")

if not PASS:
    raise SystemExit("请先设置 QLIKEAPI_ADMIN_PASS（控制台登录密码）")


def api(path, method="GET", body=None, t=90):
    args = ["-X", method, BASE + path, "-H", "Content-Type: application/json", "-b", JAR, "-c", JAR]
    if body is not None:
        args += ["-d", json.dumps(body, ensure_ascii=False)]
    out = subprocess.run(["curl", "-sS", "-m", str(t)] + args, capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except Exception:
        return {"raw": out[:300]}


print("=== 0) 登录 ===")
print(api("/api/login", "POST", {"username": USER, "password": PASS}))

print("\n=== 1) 取数器类型 ===")
for t in api("/api/sites/types"):
    print(f"  {t['type']:16s} {t['label']:28s} 需要：{t['need']}")

print("\n=== 2) 从宿主机 .env 一键导入 ===")
r = api("/api/sites/import-env", "POST")
print("  新增:", r.get("added"), "| 已存在跳过:", r.get("skipped"), "| .env 键数:", r.get("env_keys"), r.get("error", ""))

print("\n=== 3) 全部刷新余额（只读 GET，零成本）===")
r = api("/api/sites/check", "POST", t=180)
res = r.get("results", [])
for x in res:
    if x.get("error"):
        print(f"  ✗ {x['name']:16s} [{x.get('type')}] {str(x['error'])[:90]}")
    else:
        cur = "¥" if (x.get("unit") == "CNY") else "$"
        used = f" 已用 {cur}{x['used']}" if x.get("used") is not None else ""
        print(f"  ✓ {x['name']:16s} [{x.get('type')}] 余额 {cur}{x.get('balance')}{used} {x.get('plan') or ''} ({x.get('ms')}ms)")
print(f"  共 {r.get('ran')} 个站点，失败 {r.get('failed')}")

print("\n=== 4) 列表接口回读（含低余额标记）===")
for s in api("/api/sites"):
    cur = "¥" if s.get("last_unit") == "CNY" else "$"
    bal = "—" if s.get("last_balance") is None else f"{cur}{s['last_balance']}"
    print(f"  #{s['id']:2d} {s['name']:16s} {s.get('type_label','')[:22]:24s} 余额={bal:>10s} "
          f"阈值={s.get('threshold')} 低={s.get('low')} 检查={s.get('last_checked') or '—'}")
