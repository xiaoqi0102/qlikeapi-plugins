#!/usr/bin/env python3
"""零成本验收脚本（严格版）：任何带 prompt 的真实请求都不发出。

只允许三种动作：
  ① 本地校验（缺 prompt 必须 400，且不打上游）
  ② /up/<渠道>/v1/images/preview  dry-run，只回「将要发给上游的报文」，不发送
  ③ /api/providers/<渠道>/test   探活：发出前递归抹掉 prompt，上游必然拒绝，零成本

用法：
    export QLIKEAPI_BASE=http://127.0.0.1:18673
    export QLIKEAPI_ADMIN_USER=admin
    export QLIKEAPI_ADMIN_PASS=你的控制台密码
    python3 scripts/verify_v3.py
"""
import json
import os
import subprocess
import urllib.parse

BASE = os.environ.get("QLIKEAPI_BASE", "http://127.0.0.1:18673")
USER = os.environ.get("QLIKEAPI_ADMIN_USER", "admin")
PASS = os.environ.get("QLIKEAPI_ADMIN_PASS", "")
JAR = os.environ.get("QLIKEAPI_COOKIE_JAR", "/tmp/qlikeapi-verify-cookies.txt")

if not PASS:
    raise SystemExit("请先设置 QLIKEAPI_ADMIN_PASS（控制台登录密码）")


def curl(args, t=120):
    return subprocess.run(["curl", "-sS", "-m", str(t)] + args, capture_output=True, text=True).stdout


def api(path, method="GET", body=None):
    a = ["-X", method, BASE + path, "-H", "Content-Type: application/json", "-b", JAR, "-c", JAR]
    if body is not None:
        a += ["-d", json.dumps(body, ensure_ascii=False)]
    txt = curl(a)
    try:
        return json.loads(txt)
    except Exception:
        return {"raw": txt[:300]}


print("=== 0) 登录 ===")
print(api("/api/login", "POST", {"username": USER, "password": PASS}))

print("\n=== 1) 插件装载 ===")
ch = api("/api/channels")
for c in ch.get("channels", []):
    print(f"  {c['id']:16s} {c['label']:26s} " + " ".join(f"{o['operation']}={o['mode']}" for o in c["operations"]))
print("  装载错误:", ch.get("errors") or "无")

provs = api("/api/providers")
keys = [p["key"] for p in provs]

print("\n=== 2) 缺 prompt 必须本地 400（不打上游，绝不回落默认提示词）===")
for k in keys:
    r = api(f"/up/{k}/v1/images/generations", "POST", {"model": ""})
    msg = (r.get("error") or {}).get("message", json.dumps(r, ensure_ascii=False))[:110]
    print(f"  {k:20s} → {msg}")

print("\n=== 3) 转换预览 dry-run（只回报文，不发送）===")
for k in keys:
    r = api(f"/up/{k}/v1/images/preview", "POST",
            {"model": "", "prompt": "一只橘猫宇航员", "size": "1536x864", "quality": "standard",
             "response_format": "b64_json"})
    print(f"  ▸ {k}")
    print(f"    URL: {r.get('url')}")
    print(f"    报文: {json.dumps(r.get('upstream_body'), ensure_ascii=False)[:230]}")

print("\n=== 4) 探活（发出前抹掉 prompt → 上游必拒，零成本）===")
for k in keys:
    r = api(f"/api/providers/{urllib.parse.quote(k)}/test", "POST")
    print(f"  {k:20s} 可达={r.get('ok')} 上游码={r.get('upstream_status')} {r.get('ms')}ms  "
          f"{(r.get('upstream_message') or r.get('error') or '')[:95]}")

print("\n=== 5) 仪表盘统计字段 ===")
st = api("/api/stats?days=7")
print("  KPI:", json.dumps(st.get("today"), ensure_ascii=False))
print("  24h:", json.dumps(st.get("last24h"), ensure_ascii=False))
print("  逐时点数:", len(st.get("hourly", [])), "| 逐日点数:", len(st.get("series", [])))
print("  渠道用量:", [(p["provider"], p["n"], str(p["success_rate"]) + "%") for p in st.get("per_provider", [])])
print("  模型用量:", [(m["model"], m["n"]) for m in st.get("per_model", [])][:5])
print("  插件:", st.get("plugins"), "| 渠道启用:", st.get("providers_enabled"), "/", st.get("providers_total"))

print("\n=== 6) 模型目录 ===")
for m in api("/api/models")[:8]:
    print(f"  {m['provider']:20s} {m['model']:34s} → {m['upstream']:34s} 映射={m['aliased']}")

print("\n=== 7) 无凭据访问 /up/<已存在渠道> → 401（鉴权门在翻译之前，不会打上游）===")
if keys:
    print(" ", curl(["-X", "POST", BASE + f"/up/{keys[0]}/v1/images/generations",
                     "-H", "Content-Type: application/json",
                     "-d", '{"model":"x","prompt":"y"}'])[:160])
else:
    print("  跳过：没有渠道实例")

print("\n=== 8) 未知渠道 → 404 ===")
print(" ", api("/up/does-not-exist/v1/images/generations", "POST", {"prompt": "x"}))

print("\n=== 9) 最新日志（含 attempts/key_index 字段）===")
for log in api("/api/logs?limit=6"):
    print(f"  #{log['id']} {log['provider']:20s} {str(log['model'])[:20]:20s} {log['http_status']} "
          f"尝试={log.get('attempts')} key#{log.get('key_index')} {log['ms']}ms {str(log.get('error') or '')[:60]}")

print("\n=== 10) 零成本对账提醒 ===")
print("  探活跑完请到上游账单页（或 GET /v1/usage）核对：余额/今日消耗不应有任何变化。")
