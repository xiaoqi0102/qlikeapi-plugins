"""admin.py —— 控制台 API：账号密码登录、渠道实例、插件管理、日志、监控。

保持 v2 的登录方式：用户名 + 密码（scrypt 哈希存 SQLite，会话走 HttpOnly Cookie）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import balances, channels, crypto, protocols, relay, store, utils

router = APIRouter(prefix="/api")

SECRET = os.environ.get("QLIKEAPI_SECRET", "")
COOKIE = "qlikeapi_session"
SESSION_DAYS = int(os.environ.get("QLIKEAPI_SESSION_DAYS", "30"))
SECURE_COOKIE = os.environ.get("QLIKEAPI_SECURE_COOKIE", "1") == "1"


# ------------------------------------------------------------------ 口令 / 会话

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$%s$%s" % (salt.hex(), dk.hex())


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, want = stored.split("$")
        dk = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def new_session(username: str, ua: str = "") -> str:
    tok = secrets.token_urlsafe(24)
    with store.connect() as c:
        c.execute("INSERT INTO sessions(token,username,created_at,expires_at,ua) VALUES(?,?,?,?,?)",
                  (tok, username, int(time.time()), int(time.time()) + SESSION_DAYS * 86400, ua[:120]))
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    return tok


def current_user(request: Request) -> str | None:
    tok = request.cookies.get(COOKIE) or ""
    if not tok:
        return None
    row = store.one("SELECT username,expires_at FROM sessions WHERE token=?", (tok,))
    if not row or row["expires_at"] < time.time():
        return None
    return row["username"]


def need_user(request: Request):
    u = current_user(request)
    if not u:
        return None, JSONResponse({"error": "unauthorized"}, status_code=401)
    return u, None


@router.post("/login")
async def api_login(request: Request):
    d = await request.json()
    u = (d.get("username") or "").strip()
    p = d.get("password") or ""
    row = store.one("SELECT * FROM users WHERE username=?", (u,))
    if not row or not verify_password(p, row["pass_hash"]):
        time.sleep(0.4)
        return JSONResponse({"error": "用户名或密码不对"}, status_code=401)
    store.execute("UPDATE users SET last_login=? WHERE username=?", (int(time.time()), u))
    tok = new_session(u, request.headers.get("user-agent") or "")
    resp = JSONResponse({"ok": True, "username": u})
    resp.set_cookie(COOKIE, tok, max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=SECURE_COOKIE, path="/")
    return resp


@router.post("/logout")
def api_logout(request: Request):
    tok = request.cookies.get(COOKIE) or ""
    if tok:
        store.execute("DELETE FROM sessions WHERE token=?", (tok,))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/me")
def api_me(request: Request):
    u, err = need_user(request)
    return err or {"username": u}


@router.post("/password")
async def api_password(request: Request):
    u, err = need_user(request)
    if err:
        return err
    d = await request.json()
    row = store.one("SELECT * FROM users WHERE username=?", (u,))
    if not row or not verify_password(d.get("old") or "", row["pass_hash"]):
        return JSONResponse({"error": "原密码不对"}, status_code=400)
    new = (d.get("new") or "").strip()
    if len(new) < 6:
        return JSONResponse({"error": "新密码至少 6 位"}, status_code=400)
    store.execute("UPDATE users SET pass_hash=? WHERE username=?", (hash_password(new), u))
    store.execute("DELETE FROM sessions WHERE username=?", (u,))
    return {"ok": True, "note": "已改密，需要重新登录"}


# ------------------------------------------------------------------ 渠道插件

@router.get("/channels")
def api_channels(request: Request):
    """可用渠道插件清单（放 .py 到 app/channels/ 即可扩展）。"""
    u, err = need_user(request)
    if err:
        return err
    return {"channels": channels.list_channels(), "errors": channels.ERRORS}


@router.post("/channels/reload")
def api_channels_reload(request: Request):
    """热重载渠道插件：加/改插件文件后点一下即可生效，无需重启容器。"""
    u, err = need_user(request)
    if err:
        return err
    channels.discover(reload=True)
    return {"ok": True, "loaded": channels.available_ids(), "errors": channels.ERRORS}


# ------------------------------------------------------------------ 渠道实例

@router.get("/providers")
def api_providers(request: Request):
    u, err = need_user(request)
    if err:
        return err
    out = []
    for prow in store.list_providers():
        p = store.get_provider(prow["key"]) or prow      # 要统计密钥数量，这里带密钥读取（只回掩码）
        ch = channels.get(p.get("protocol") or "")
        keys = store.provider_keys(p)
        stats = store.one("""SELECT COUNT(*) n, SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok,
                                    AVG(ms) avg_ms, MAX(ts) last_ts FROM logs
                             WHERE kind='relay' AND provider=?""", (p["key"],)) or {}
        out.append({
            "key": p["key"], "label": p["label"], "protocol": p["protocol"], "enabled": bool(p["enabled"]),
            "plugin_label": ch.label if ch else f"⚠ 插件 {p['protocol']} 未注册",
            "plugin_ok": bool(ch), "base_url": p["base_url"], "auth_mode": p["auth_mode"],
            "priority": p.get("priority") or 0, "options": p.get("options") or {},
            "weight": max(1, int(p.get("weight") or 1)),
            "fail_streak": int(p.get("fail_streak") or 0),
            "disabled_reason": p.get("disabled_reason") or "",
            "auto_disabled": bool(p.get("auto_disabled_at")),
            "cooldown_until": p.get("cooldown_until"),
            "site_id": p.get("site_id"),
            "site_name": (store.get_site(int(p["site_id"])) or {}).get("name") if p.get("site_id") else None,
            "models": protocols.model_list(p),
            "operations": ch.info()["operations"] if ch else [],
            "keys": [{"index": i, "masked": store.mask(k)} for i, k in enumerate(keys)],
            "health": store.one("SELECT * FROM health WHERE provider=?", (p["key"],)),
            "stats": stats,
            "endpoint": f"/up/{p['key']}",
        })
    return out


@router.post("/providers")
async def api_provider_upsert(request: Request):
    u, err = need_user(request)
    if err:
        return err
    d = await request.json()
    key = (d.get("key") or "").strip()
    if not key:
        return JSONResponse({"error": "key required"}, status_code=400)
    old = store.get_provider(key) or {}
    keys_in = d.get("api_key")
    if keys_in is None:
        api_key = old.get("api_key") or ""
    elif isinstance(keys_in, list):
        api_key = "\n".join(str(x).strip() for x in keys_in if str(x).strip())
    else:
        api_key = str(keys_in).strip()
    with store.connect() as c:
        c.execute("""INSERT INTO providers(key,label,protocol,base_url,auth_mode,api_key,model_map,options,
                     priority,enabled,weight,site_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(key) DO UPDATE SET label=excluded.label,protocol=excluded.protocol,
                     base_url=excluded.base_url,auth_mode=excluded.auth_mode,api_key=excluded.api_key,
                     model_map=excluded.model_map,options=excluded.options,priority=excluded.priority,
                     enabled=excluded.enabled,weight=excluded.weight,site_id=excluded.site_id,
                     updated_at=excluded.updated_at,
                     disabled_reason=NULL, auto_disabled_at=NULL, cooldown_until=NULL, fail_streak=0""",
                  (key, d.get("label") or key, d.get("protocol") or old.get("protocol") or "openai_images",
                   (d.get("base_url") or old.get("base_url") or "").rstrip("/"),
                   d.get("auth_mode") or old.get("auth_mode") or "bearer",
                   crypto.encrypt_lines(api_key),          # ← 密钥加密落库
                   json.dumps(d.get("model_map") if d.get("model_map") is not None else (old.get("model_map") or {}),
                              ensure_ascii=False),
                   json.dumps(d.get("options") if d.get("options") is not None else (old.get("options") or {}),
                              ensure_ascii=False),
                   int(d.get("priority") or 0), 1 if d.get("enabled", True) else 0,
                   max(1, int(d.get("weight") or 1)),
                   int(d["site_id"]) if str(d.get("site_id") or "").strip() not in ("", "None") else None,
                   int(time.time())))
    return {"ok": True, "key": key}


@router.post("/providers/{key}/patch")
async def api_provider_patch(key: str, request: Request):
    """轻量更新：只动 enabled / priority / label，不碰密钥与模型映射。"""
    u, err = need_user(request)
    if err:
        return err
    d = await request.json()
    cur = store.get_provider(key)
    if not cur:
        return JSONResponse({"error": "not found"}, status_code=404)
    sets, args = [], []
    if "enabled" in d:
        if d["enabled"]:      # 人工启用 → 顺手清掉「自动熔断」状态
            sets += ["disabled_reason=NULL", "auto_disabled_at=NULL", "cooldown_until=NULL", "fail_streak=0"]
        sets.append("enabled=?")
        args.append(1 if d["enabled"] else 0)
    if "priority" in d:
        try:
            sets.append("priority=?")
            args.append(int(d["priority"]))
        except Exception:
            return JSONResponse({"error": "priority must be int"}, status_code=400)
    if d.get("label"):
        sets.append("label=?")
        args.append(str(d["label"]).strip())
    if "weight" in d:
        try:
            sets.append("weight=?")
            args.append(max(1, int(d["weight"])))
        except Exception:
            return JSONResponse({"error": "weight must be int"}, status_code=400)
    if "site_id" in d:
        v = str(d.get("site_id") or "").strip()
        sets.append("site_id=?")
        args.append(int(v) if v.isdigit() else None)
    if d.get("clear_auto"):
        # 人工解除「连续失败自动停用」，并复位失败计数
        store.enable_provider(key)
        return {"ok": True, "key": key, "auto_cleared": True}
    if sets:
        sets.append("updated_at=?")
        args.append(int(time.time()))
        args.append(key)
        with store.connect() as c:
            c.execute(f"UPDATE providers SET {', '.join(sets)} WHERE key=?", args)
    if "enabled" in d and not d["enabled"]:
        store.disable_provider(key, str(d.get("reason") or "人工停用")[:200], manual=True)
    return {"ok": True, "key": key}


@router.delete("/providers/{key}")
def api_provider_delete(key: str, request: Request):
    u, err = need_user(request)
    if err:
        return err
    with store.connect() as c:
        c.execute("DELETE FROM providers WHERE key=?", (key,))
        c.execute("DELETE FROM health WHERE provider=?", (key,))
        # 渠道没了，它的专属价格行就是孤儿价 —— 一起删掉，否则「模型单价」页会留下
        # 一堆和已不存在渠道同名的重复行（banana/image2 合并后就是这样堆起来的）。
        n = c.execute("SELECT COUNT(*) FROM model_prices WHERE provider=?", (key,)).fetchone()[0]
        c.execute("DELETE FROM model_prices WHERE provider=?", (key,))
    return {"ok": True, "prices_removed": n}


@router.post("/providers/{key}/test")
def api_provider_test(key: str, request: Request, model: str = ""):
    """单个渠道零成本探活；带 ?model= 时只探那个模型（前端逐模型显示进度）。

    探活语义：不带 prompt 打上游 → 4xx 表示「网络/鉴权/模型名」都通，5xx 才是上游异常。
    绝不真出图、不扣费。指定 model 时不动渠道整体健康态（避免单模型结果覆盖汇总）。"""
    u, err = need_user(request)
    if err:
        return err
    p = store.get_provider(key)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    res = relay.selftest_provider(p, model or None)
    if not model:
        store.set_health(key, res)
    return res


@router.post("/providers/{key}/fetch-models")
def api_provider_fetch_models(key: str, request: Request):
    """拉取上游模型列表（零成本：只发一个 GET /v1/models，绝不出图）。

    借鉴 New API 渠道页的「获取模型列表」：直接拉上游清单，勾选后一键写进模型映射，
    省掉手工敲模型名；同一平台重复命名的模型会自动去重。
    路径优先取渠道 options.models_path，其次依次试 /v1/models、/models。
    """
    u, err = need_user(request)
    if err:
        return err
    p = store.get_provider(key)
    if not p:
        return JSONResponse({"error": f"渠道 '{key}' 不存在（先保存渠道，再拉取模型）"}, status_code=404)
    keys = store.provider_keys(p)
    if not keys:
        return JSONResponse({"error": "这个渠道还没配 API key，先填 key 并保存"}, status_code=400)
    res = protocols.fetch_upstream_models(p, key=keys[0])
    if not res.get("ok"):
        return JSONResponse({"error": res.get("error") or "拉取失败"}, status_code=400)
    res["existing"] = list((p.get("model_map") or {}).keys())
    res["provider"] = key
    return res


# ------------------------------------------------------------------ 日志 / 监控 / 任务

@router.get("/logs")
def api_logs(request: Request, limit: int = 50, offset: int = 0, provider: str = "", status: str = "",
             q: str = "", kind: str = "", token: str = ""):
    u, err = need_user(request)
    if err:
        return err
    where, args = [], []
    if provider:
        where.append("provider=?")
        args.append(provider)
    if kind:
        where.append("kind=?")
        args.append(kind)
    if token == "0":
        where.append("COALESCE(token_id,0)=0")
    elif token:
        where.append("token_id=?")
        args.append(int(token))
    if status == "ok":
        where.append("http_status<400")
    elif status == "err":
        where.append("http_status>=400")
    if q:
        where.append("(COALESCE(model,'') LIKE ? OR COALESCE(error,'') LIKE ? OR COALESCE(public_path,'') LIKE ?)")
        args += [f"%{q}%"] * 3
    sql = ("SELECT id,ts,provider,model,public_path,http_status,upstream_status,ms,error,kind,attempts,"
           "key_index,response_snippet,token,token_id,images,cost,cost_currency FROM logs")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [min(limit, 500), offset]
    return store.rows(sql, tuple(args))


@router.get("/logs/{log_id}")
def api_log_detail(log_id: int, request: Request):
    u, err = need_user(request)
    if err:
        return err
    row = store.one("SELECT * FROM logs WHERE id=?", (log_id,))
    return row or JSONResponse({"error": "not found"}, status_code=404)


@router.get("/jobs")
def api_jobs(request: Request, limit: int = 50, provider: str = ""):
    u, err = need_user(request)
    if err:
        return err
    if provider:
        return store.rows("SELECT * FROM jobs WHERE provider=? ORDER BY submit_at DESC LIMIT ?", (provider, limit))
    return store.rows("SELECT * FROM jobs ORDER BY submit_at DESC LIMIT ?", (limit,))


@router.get("/stats")
def api_stats(request: Request, days: int = 7):
    """仪表盘数据：KPI、逐时/逐日趋势、渠道用量、模型用量、P95 时延。

    借鉴 gpt-load 的首页/监控指标口径，但只保留有用的那几个。
    """
    u, err = need_user(request)
    if err:
        return err
    now = int(time.time())
    day0 = now - now % 86400
    hour0 = now - now % 3600

    def window(where: str, args: tuple) -> dict:
        r = store.one(f"""SELECT COUNT(*) n, SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok,
                                 AVG(ms) avg_ms, MAX(ms) max_ms, SUM(COALESCE(attempts,1)) attempts
                          FROM logs WHERE kind='relay' AND {where}""", args) or {}
        n = r.get("n") or 0
        ok = r.get("ok") or 0
        p95 = 0
        if n:
            dur = [x["ms"] or 0 for x in store.rows(
                f"SELECT ms FROM logs WHERE kind='relay' AND {where} ORDER BY ms LIMIT 5000", args)]
            if dur:
                p95 = dur[min(len(dur) - 1, int(len(dur) * 0.95))]
        return {"n": n, "ok": ok, "err": n - ok, "avg_ms": round(r.get("avg_ms") or 0),
                "max_ms": round(r.get("max_ms") or 0), "p95_ms": p95,
                "attempts": int(r.get("attempts") or 0),
                "success_rate": round(ok / n * 100, 1) if n else 0}

    today = window("ts>=?", (day0,))
    last24h = window("ts>=?", (now - 86400,))
    total = window("1=1", ())

    # 逐时（最近 24 小时）
    hourly_raw = {int(r["h"]): r for r in store.rows(
        """SELECT ts/3600 h, COUNT(*) n, SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok,
                  AVG(ms) avg_ms FROM logs WHERE kind='relay' AND ts>=? GROUP BY h ORDER BY h""",
        (hour0 - 23 * 3600,))}
    hourly = []
    for i in range(23, -1, -1):
        h = (hour0 - i * 3600) // 3600
        v = hourly_raw.get(h) or {}
        hourly.append({"ts": h * 3600, "label": time.strftime("%H:00", time.gmtime(h * 3600)),
                       "n": v.get("n") or 0, "ok": v.get("ok") or 0,
                       "avg_ms": round(v.get("avg_ms") or 0)})

    # 逐日（最近 N 天）
    day_raw = {int(r["d"]): r for r in store.rows(
        """SELECT ts/86400 d, COUNT(*) n, SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok,
                  AVG(ms) avg_ms FROM logs WHERE kind='relay' AND ts>=? GROUP BY d ORDER BY d""",
        (day0 - (days - 1) * 86400,))}
    series = []
    for i in range(days - 1, -1, -1):
        d = (day0 - i * 86400) // 86400
        v = day_raw.get(d) or {}
        n, ok = v.get("n") or 0, v.get("ok") or 0
        series.append({"ts": d * 86400, "label": time.strftime("%m-%d", time.gmtime(d * 86400)),
                       "n": n, "ok": ok, "err": n - ok,
                       "success_rate": round(ok / n * 100, 1) if n else 0,
                       "avg_ms": round(v.get("avg_ms") or 0)})

    per_provider = store.rows(
        """SELECT provider, protocol_hint, COUNT(*) n, SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok,
                  AVG(ms) avg_ms, MAX(ts) last_ts
           FROM (SELECT *, '' AS protocol_hint FROM logs WHERE kind='relay')
           GROUP BY provider ORDER BY n DESC""")
    for p in per_provider:
        p["success_rate"] = round((p["ok"] or 0) / p["n"] * 100, 1) if p["n"] else 0
        p.pop("protocol_hint", None)

    per_model = store.rows(
        """SELECT model, COUNT(*) n, SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok, AVG(ms) avg_ms
           FROM logs WHERE kind='relay' AND COALESCE(model,'')<>'' GROUP BY model ORDER BY n DESC LIMIT 12""")
    for m in per_model:
        m["success_rate"] = round((m["ok"] or 0) / m["n"] * 100, 1) if m["n"] else 0

    recent_errors = store.rows("""SELECT id,ts,provider,model,http_status,error FROM logs
                                  WHERE kind='relay' AND http_status>=400 ORDER BY id DESC LIMIT 10""")
    health = store.rows("SELECT * FROM health ORDER BY provider")
    jobs = store.one("""SELECT COUNT(*) n, SUM(CASE WHEN status='RUNNING' THEN 1 ELSE 0 END) running,
                               SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) done FROM jobs""") or {}
    return {"today": today, "last24h": last24h, "total": total, "hourly": hourly, "series": series,
            "per_provider": per_provider, "per_model": per_model, "recent_errors": recent_errors,
            "health": health, "jobs": jobs,
            "providers_total": store.one("SELECT COUNT(*) n FROM providers")["n"],
            "providers_enabled": store.one("SELECT COUNT(*) n FROM providers WHERE enabled=1")["n"],
            "plugins": channels.available_ids()}


@router.get("/models")
def api_models(request: Request):
    """模型目录：每个渠道实例对外暴露的模型名 → 上游真实名（借鉴 gpt-load 的模型页）。"""
    u, err = need_user(request)
    if err:
        return err
    out = []
    for p in store.list_providers():
        ch = channels.get(p.get("protocol") or "")
        for m in protocols.model_list(p):
            pr = store.price_row(m["id"], p["key"]) or {}
            out.append({"provider": p["key"], "provider_label": p["label"], "plugin": p.get("protocol"),
                        "plugin_label": ch.label if ch else p.get("protocol"),
                        "model": m["id"], "upstream": m["upstream"], "aliased": m["aliased"],
                        "enabled": bool(p["enabled"]), "priority": p.get("priority") or 0,
                        "price": pr.get("price"), "currency": pr.get("currency"),
                        "source": pr.get("source"), "price_note": pr.get("note"),
                        "operations": [o["operation"] for o in (ch.info()["operations"] if ch else [])]})
    return out


@router.get("/meta/options")
def api_meta_options(request: Request):
    """给前端选择器用的轻量选项表（模型 / 渠道 / 币种）。

    与 /models 的区别：这里不查价格、不查健康，只回「有哪些可选项」，
    供令牌弹窗的多选控件一次性拉取（比 /providers 轻得多）。
    """
    u, err = need_user(request)
    if err:
        return err
    models: dict = {}
    providers = []
    for p in store.list_providers():
        ch = channels.get(p.get("protocol") or "")
        ids = []
        for m in protocols.model_list(p):
            ids.append(m["id"])
            row = models.setdefault(m["id"], {"id": m["id"], "providers": [], "aliased": False})
            row["providers"].append(p["key"])
            row["aliased"] = row["aliased"] or bool(m["aliased"])
        providers.append({"key": p["key"], "label": p["label"], "enabled": bool(p["enabled"]),
                          "plugin": p.get("protocol"), "plugin_label": ch.label if ch else None,
                          "priority": p.get("priority") or 0, "models": ids})
    providers.sort(key=lambda x: (-x["priority"], x["key"]))
    return {"models": sorted(models.values(), key=lambda x: x["id"]),
            "providers": providers,
            "currencies": ["CNY", "USD"]}


# ------------------------------------------------------------------ 路由规则

def _chain_item(p: dict) -> dict:
    h = store.one("SELECT ok, checked_at, message FROM health WHERE provider=?", (p["key"],)) or {}
    return {"provider": p["key"], "label": p["label"], "priority": p.get("priority") or 0,
            "plugin": p.get("protocol"), "base_url": p.get("base_url"),
            "keys": len(store.provider_keys(p)), "enabled": bool(p.get("enabled")),
            "healthy": h.get("ok"), "checked_at": h.get("checked_at"),
            "size_mode": protocols.size_mode(p), "gemini_size_policy": protocols.gemini_policy(p)}


@router.get("/routes")
def api_routes(request: Request):
    """路由视图：每个模型的候选渠道链（显式规则优先，否则按优先级自动排）。"""
    u, err = need_user(request)
    if err:
        return err
    models: list[str] = []
    for p in store.list_providers(only_enabled=True):
        for m in (p.get("model_map") or {}):
            if m not in models:
                models.append(m)
    out = []
    for m in models:
        explicit = store.get_chain(m)
        out.append({"model": m, "mode": "explicit" if explicit else "auto",
                    "explicit_chain": explicit or [],
                    "chain": [_chain_item(x) for x in relay.resolve_chain(m)]})
    for r in store.list_routes():
        if r["model"] not in models and r["model"] != "*":
            out.append({"model": r["model"], "mode": "explicit", "explicit_chain": r["chain"],
                        "chain": [_chain_item(x) for x in relay.resolve_chain(r["model"])],
                        "note": r.get("note")})
    return out


@router.post("/routes")
async def api_route_set(request: Request):
    u, err = need_user(request)
    if err:
        return err
    d = await request.json()
    model = (d.get("model") or "").strip()
    chain = d.get("chain") or []
    if not model:
        return JSONResponse({"error": "model 必填"}, status_code=400)
    if not isinstance(chain, list):
        return JSONResponse({"error": "chain 必须是数组（渠道实例 key 数组）"}, status_code=400)
    clean = []
    for k in chain:
        if not isinstance(k, str) or not k.strip():
            return JSONResponse({"error": "chain 里出现了空的渠道 key（请重新选渠道再保存）"}, status_code=400)
        clean.append(k.strip())
    chain = clean
    bad = [k for k in chain if not store.get_provider(k)]
    if bad:
        return JSONResponse({"error": f"渠道实例不存在：{', '.join(bad)}"}, status_code=400)
    if not chain:
        store.delete_chain(model)
        return {"ok": True, "note": f"已删除 {model} 的显式规则，恢复自动路由"}
    store.set_chain(model, chain, d.get("note") or "")
    return {"ok": True}


@router.delete("/routes/{model}")
def api_route_delete(model: str, request: Request):
    u, err = need_user(request)
    if err:
        return err
    store.delete_chain(model)
    return {"ok": True}


@router.get("/routes/preview")
def api_route_preview(model: str, request: Request):
    """零成本：看某个模型会按什么顺序走哪些渠道。"""
    u, err = need_user(request)
    if err:
        return err
    return {"model": model, "mode": "explicit" if store.get_chain(model) else "auto",
            "chain": [_chain_item(p) for p in relay.resolve_chain(model)],
            "note": "前一个渠道遇到 429/5xx/超时/连不上会自动换下一个；参数类错误（400）直接返回不切换"}


# ------------------------------------------------------------------ 站点余额

@router.get("/sites")
def api_sites(request: Request):
    u, err = need_user(request)
    if err:
        return err
    out = []
    for s in store.list_sites():
        thr = balances.threshold_of(s)
        out.append({**{k: v for k, v in s.items() if k != "token"},
                    "token_masked": store.mask(s.get("token")),
                    "type_label": balances.TYPE_META.get(s.get("type"), {}).get("label", s.get("type")),
                    "threshold": thr,
                    "low": bool(thr and (s.get("last_balance") or 0) < thr)})
    return out


@router.get("/sites/types")
def api_site_types(request: Request):
    u, err = need_user(request)
    if err:
        return err
    return [{"type": k, **v} for k, v in balances.TYPE_META.items()]


@router.post("/sites")
async def api_site_save(request: Request):
    u, err = need_user(request)
    if err:
        return err
    d = await request.json()
    if not d.get("name"):
        return JSONResponse({"error": "站点名必填"}, status_code=400)
    sid = store.save_site(d)
    return {"ok": True, "id": sid}


@router.delete("/sites/{sid}")
def api_site_delete(sid: int, request: Request):
    u, err = need_user(request)
    if err:
        return err
    store.execute("DELETE FROM sites WHERE id=?", (sid,))
    return {"ok": True}


@router.post("/sites/{sid}/check")
def api_site_check(sid: int, request: Request):
    u, err = need_user(request)
    if err:
        return err
    site = store.get_site(sid)
    if not site:
        return JSONResponse({"error": "not found"}, status_code=404)
    return balances.check_site(site)


@router.post("/sites/check")
def api_sites_check(request: Request):
    """全部站点刷新余额（都是只读 GET，零成本）。"""
    u, err = need_user(request)
    if err:
        return err
    res = balances.check_all()
    bad = [r for r in res if r.get("error")]
    breaker = _balance_breaker()            # 余额不足 → 自动停用关联渠道
    return {"ran": len(res), "failed": len(bad), "results": res, "breaker": breaker}


@router.post("/sites/import-env")
def api_sites_import_env(request: Request):
    """从宿主机 .env 一键导入已配置的站点（只读挂载 /env/hermes.env）。"""
    u, err = need_user(request)
    if err:
        return err
    return balances.import_from_env()




# ------------------------------------------------------------------ 访问令牌（借鉴 New API「令牌管理」）

@router.get("/tokens")
def api_tokens(request: Request):
    u, err = need_user(request)
    if err:
        return err
    items = store.list_tokens()
    # 近 7 天按令牌聚合，方便看谁在用
    for t in items:
        st = store.one("""SELECT COUNT(*) n, SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok,
                                 SUM(COALESCE(images,0)) images, SUM(COALESCE(cost,0)) cost,
                                 MAX(ts) last_ts FROM logs
                          WHERE token_id=? AND ts>=?""", (t["id"], int(time.time()) - 7 * 86400)) or {}
        t["stats7d"] = {"requests": st.get("n") or 0, "success": st.get("ok") or 0,
                        "images": int(st.get("images") or 0), "cost": round(st.get("cost") or 0, 4),
                        "last_ts": st.get("last_ts")}
    return {"items": items, "master_set": bool(os.environ.get("QLIKEAPI_UP_TOKEN", "")),
            "master_masked": store.mask(os.environ.get("QLIKEAPI_UP_TOKEN", "")),
            "endpoint": "http://qlikeapi-plugins:18673/v1"}


@router.post("/tokens")
async def api_token_save(request: Request):
    u, err = need_user(request)
    if err:
        return err
    d = await request.json()
    tid = d.get("id")
    if tid:
        store.patch_token(int(tid), d)
        return {"ok": True, "id": int(tid)}
    if not (d.get("name") or "").strip():
        return JSONResponse({"error": "请给令牌起个名字（标识调用方）"}, status_code=400)
    tok = store.save_token(d)
    row = store.get_token(tok)
    return {"ok": True, "id": tok, "token": row["token"]}      # 只在创建时回一次明文


@router.post("/tokens/{tid}/patch")
async def api_token_patch(tid: int, request: Request):
    u, err = need_user(request)
    if err:
        return err
    d = await request.json()
    if not store.get_token(tid):
        return JSONResponse({"error": "not found"}, status_code=404)
    store.patch_token(tid, d)
    return {"ok": True, "id": tid}


@router.post("/tokens/{tid}/reset")
def api_token_reset(tid: int, request: Request):
    """清零该令牌的累计用量与费用（额度重新开始算）。"""
    u, err = need_user(request)
    if err:
        return err
    with store.connect() as c:
        c.execute("UPDATE tokens SET used_requests=0, used_images=0, used_cost=0 WHERE id=?", (tid,))
    return {"ok": True, "id": tid}


@router.delete("/tokens/{tid}")
def api_token_delete(tid: int, request: Request):
    u, err = need_user(request)
    if err:
        return err
    store.delete_token(tid)
    return {"ok": True}


# ------------------------------------------------------------------ 渠道余额熔断（余额不足自动降权/停用）

def _balance_breaker() -> list[dict]:
    """站点余额低于阈值 → 自动停用关联渠道；余额恢复 → 自动放回。

    渠道实例的 site_id 指向「站点余额」里的某个站点；站点 extra.threshold 是预警线。
    """
    events = []
    for p in store.list_providers():
        if not p.get("site_id"):
            continue
        site = store.get_site(int(p["site_id"]))
        if not site:
            continue
        thr = float((site.get("extra") or {}).get("threshold") or 0)
        bal = site.get("last_balance")
        if bal is None or site.get("last_error"):
            continue
        low = thr > 0 and float(bal) < thr
        if low and p.get("enabled"):
            store.disable_provider(p["key"], f"站点「{site['name']}」余额 {bal} 低于阈值 {thr}")
            events.append({"provider": p["key"], "action": "disabled", "balance": bal, "threshold": thr})
        elif not low and not p.get("enabled") and (p.get("disabled_reason") or "").startswith("站点「"):
            store.enable_provider(p["key"])
            events.append({"provider": p["key"], "action": "enabled", "balance": bal, "threshold": thr})
    return events

# ------------------------------------------------------------------ 用量统计（对标 gpt-load 的 usage 页）

def _range(days: int, start: int | None, end: int | None) -> tuple[int, int]:
    now = int(time.time())
    if start and end:
        return int(start), int(end)
    if days <= 1:
        day0 = now - now % 86400
        return day0, now
    if days == 7:
        return now - 6 * 86400, now
    return now - (days - 1) * 86400, now


def _usage_where(start: int, end: int, provider: str, model: str, kind: str) -> tuple[str, tuple]:
    where = ["ts>=?", "ts<=?"]
    args: list = [start, end]
    if kind:
        where.append("kind=?")
        args.append(kind)
    else:
        where.append("kind='relay'")            # 默认只看真实转发（不含探活/本地校验）
    if provider:
        where.append("provider=?")
        args.append(provider)
    if model:
        where.append("model=?")
        args.append(model)
    return " AND ".join(where), tuple(args)


@router.get("/usage")
def api_usage(request: Request, days: int = 7, start: int | None = None, end: int | None = None,
              provider: str = "", model: str = "", kind: str = "", bucket: str = "auto"):
    """用量统计：总量、时间序列、以及按渠道/模型/操作/类型的分布。

    指标口径对标 gpt-load：请求数、成功/失败、用量、估算费用；图片业务把「用量」换成「产出张数」。
    """
    u, err = need_user(request)
    if err:
        return err
    start, end = _range(days, start, end)
    where, args = _usage_where(start, end, provider, model, kind)
    span = end - start
    if bucket == "auto":
        bucket = "hour" if span <= 2 * 86400 else "day"

    def agg(w: str, a: tuple) -> dict:
        r = store.one(f"""SELECT COUNT(*) n,
                                 SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok,
                                 SUM(COALESCE(images,0)) images, SUM(COALESCE(cost,0)) cost,
                                 AVG(ms) avg_ms, MAX(ms) max_ms
                          FROM logs WHERE {w}""", a) or {}
        n = r.get("n") or 0
        ok = r.get("ok") or 0
        p95 = 0
        if n:
            dur = [x["ms"] or 0 for x in store.rows(
                f"SELECT ms FROM logs WHERE {w} ORDER BY ms LIMIT 5000", a)]
            if dur:
                p95 = dur[min(len(dur) - 1, int(len(dur) * 0.95))]
        by_cur = {x["c"]: round(x["v"] or 0, 4) for x in store.rows(
            f"SELECT COALESCE(cost_currency,'CNY') c, SUM(COALESCE(cost,0)) v FROM logs WHERE {w} GROUP BY c", a)}
        return {"requests": n, "success": ok, "failure": n - ok,
                "success_rate": round(ok / n * 100, 1) if n else 0,
                "images": int(r.get("images") or 0),
                "cost": round(r.get("cost") or 0, 4),
                "cost_by_currency": by_cur,
                "avg_ms": round(r.get("avg_ms") or 0), "max_ms": round(r.get("max_ms") or 0),
                "p95_ms": p95}

    summary = agg(where, args)

    unit = 3600 if bucket == "hour" else 86400
    base = (end // unit) * unit
    buckets = {}
    for r in store.rows(f"""SELECT ts/{unit} b, COUNT(*) n,
                                   SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok,
                                   SUM(COALESCE(images,0)) images, SUM(COALESCE(cost,0)) cost,
                                   AVG(ms) avg_ms
                            FROM logs WHERE {where} GROUP BY b ORDER BY b""", args):
        buckets[int(r["b"])] = r
    series = []
    count = min(span // unit + 1, 24 if bucket == "hour" else 90)
    for i in range(count - 1, -1, -1):
        b = base - i * unit
        v = buckets.get(b) or {}
        n, ok = v.get("n") or 0, v.get("ok") or 0
        series.append({"ts": b, "label": time.strftime("%H:00" if bucket == "hour" else "%m-%d",
                                                       time.gmtime(b)),
                       "requests": n, "success": ok, "failure": n - ok,
                       "success_rate": round(ok / n * 100, 1) if n else 0,
                       "images": int(v.get("images") or 0), "cost": round(v.get("cost") or 0, 4),
                       "avg_ms": round(v.get("avg_ms") or 0)})

    def dist(col: str, limit: int = 20) -> list[dict]:
        rows_ = store.rows(f"""SELECT COALESCE(NULLIF({col},''),'(空)') name, COUNT(*) n,
                                      SUM(CASE WHEN http_status<400 THEN 1 ELSE 0 END) ok,
                                      SUM(COALESCE(images,0)) images, SUM(COALESCE(cost,0)) cost,
                                      AVG(ms) avg_ms
                               FROM logs WHERE {where} GROUP BY name ORDER BY n DESC LIMIT ?""",
                           args + (limit,))
        for r in rows_:
            r["success_rate"] = round((r["ok"] or 0) / r["n"] * 100, 1) if r["n"] else 0
            r["cost"] = round(r["cost"] or 0, 4)
            r["images"] = int(r["images"] or 0)
            r["avg_ms"] = round(r["avg_ms"] or 0)
        return rows_

    # 未定价的模型（对标 gpt-load 的 unpriced 提示）
    unpriced = [r["model"] for r in store.rows(
        f"""SELECT DISTINCT model FROM logs WHERE {where} AND COALESCE(model,'')<>''""", args)
        if store.price_for(r["model"], provider or "") == 0 and
        store.price_for(r["model"], "*") == 0]

    return {"range": {"start": start, "end": end, "days": days, "bucket": bucket},
            "summary": summary, "series": series,
            "distributions": {"provider": dist("provider"), "model": dist("model"),
                              "operation": dist("public_path"), "kind": dist("kind"),
                              "token": dist("token")},
            "unpriced_models": unpriced, "prices": store.list_prices()}


@router.get("/usage.csv")
def api_usage_csv(request: Request, days: int = 7, provider: str = "", model: str = "", kind: str = ""):
    """导出明细 CSV（给对账/做表用）。"""
    u, err = need_user(request)
    if err:
        return err
    start, end = _range(days, None, None)
    where, args = _usage_where(start, end, provider, model, kind)
    rows_ = store.rows(f"""SELECT ts,provider,model,public_path,http_status,upstream_status,ms,
                                  images,cost,attempts,key_index,error
                           FROM logs WHERE {where} ORDER BY id DESC LIMIT 20000""", args)
    head = "时间,渠道,模型,路径,状态,上游码,耗时ms,张数,费用元,尝试,密钥序号,错误\n"
    lines = [head]
    for r in rows_:
        lines.append(",".join([
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["provider"] or "",
            (r["model"] or "").replace(",", " "), r["public_path"] or "", str(r["http_status"] or ""),
            str(r["upstream_status"] or ""), str(r["ms"] or ""), str(r["images"] or 0),
            f"{(r['cost'] or 0):.4f}", str(r["attempts"] or ""), str(r["key_index"] if r["key_index"] is not None else ""),
            (r["error"] or "").replace(",", " ").replace("\n", " ")[:120]]))
    from fastapi.responses import Response
    return Response("\n".join(lines), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="qlikeapi-usage.csv"'})


@router.get("/prices")
def api_prices(request: Request):
    u, err = need_user(request)
    if err:
        return err
    return store.list_prices()


@router.post("/prices")
async def api_price_set(request: Request):
    u, err = need_user(request)
    if err:
        return err
    d = await request.json()
    if not d.get("model"):
        return JSONResponse({"error": "model 必填"}, status_code=400)
    store.set_price_full(d["model"], d.get("provider") or "*", float(d.get("price") or 0),
                         d.get("currency") or "CNY", d.get("source") or "manual", d.get("note") or "")
    return {"ok": True}


@router.post("/prices/sync")
def api_prices_sync(request: Request):
    """从上游站点实测价格（sub2api 的 /v1/usage 里有每模型累计消耗与请求数）。"""
    u, err = need_user(request)
    if err:
        return err
    return balances.sync_prices_from_sites()


@router.delete("/prices/{pid}")
def api_price_delete(pid: int, request: Request):
    u, err = need_user(request)
    if err:
        return err
    store.execute("DELETE FROM model_prices WHERE id=?", (pid,))
    return {"ok": True}


@router.post("/prices/prune")
def api_prices_prune(request: Request):
    """清理孤儿价：渠道已被删除/改名后遗留的价格行（provider 不是 * 且当前不存在）。"""
    u, err = need_user(request)
    if err:
        return err
    rows = store.rows("SELECT id, model, provider FROM model_prices WHERE provider IS NOT NULL AND provider<>'*'")
    alive = {r["key"] for r in store.rows("SELECT key FROM providers")}
    gone = [r for r in rows if r["provider"] not in alive]
    for r in gone:
        store.execute("DELETE FROM model_prices WHERE id=?", (r["id"],))
    return {"removed": len(gone), "items": [{"id": r["id"], "model": r["model"], "provider": r["provider"]}
                                            for r in gone]}


@router.get("/size-plan")
def api_size_plan(request: Request, model: str = "", size: str = "", policy: str = "", mode: str = ""):
    """尺寸换算（零成本、不出图）：这个模型 + 这个尺寸，最终会变成什么。"""
    u, err = need_user(request)
    if err:
        return err
    mode = mode if mode in ("snap", "passthrough") else "snap"
    wh = utils.parse_size(size)
    if not wh:
        return {"model": model, "size": size, "error": "size 需要写成 1920x1080 这种形式"}
    w, h = wh
    fam = utils.fixed_sizes_for(model)
    if fam:
        dec = utils.snap_size(size, model, mode)
        return {"model": model, "size": size, "family": "fixed", "final": dec["size"],
                "changed": dec["changed"], "note": dec["note"],
                "allowed": [f"{a}x{b}" for a, b in fam]}
    m = (model or "").lower()
    is_gemini = "gemini" in m and "image" in m
    if is_gemini:
        pol = policy or "class"
        plan = utils.gemini_plan(w, h, model, pol)
        return {"model": model, "size": size, "family": "gemini", "policy": pol, "mode": mode,
                "ratio": plan["ratio"], "tier": plan["tier"],
                "final": f"{plan['pixels'][0]}x{plan['pixels'][1]}",
                "changed": f"{plan['pixels'][0]}x{plan['pixels'][1]}" != f"{w}x{h}",
                "note": plan["note"], "tiers": plan["tiers"], "tokens": plan["tokens"],
                "tokens_all": plan["tokens_all"], "nominal": plan["nominal"],
                "image_size_values": list(utils.GEMINI_TIERS),
                "source": "ai.google.dev《Image generation》—— image_size 只接受 512px/1K/2K/4K" +
                          "（大写 K），输出像素由「档位+比例」决定，给不了任意像素"}
    dec = utils.snap_size(size, model, mode)
    official = [{"size": f"{a}x{b}", "label": utils.OFFICIAL_GPT_LABEL.get(f"{a}x{b}", "")}
                for a, b in utils.GPT_SIZES]
    near = min(utils.GPT_SIZES, key=lambda s: abs(math.log((w * h) / (s[0] * s[1]))))
    return {"model": model, "size": size, "family": "free", "mode": mode, "final": dec["size"],
            "changed": dec["changed"], "note": dec["note"] or f"{dec['size']}（已合规，原样透传）",
            "official": official,
            "nearest_official": {"size": f"{near[0]}x{near[1]}",
                                 "label": utils.OFFICIAL_GPT_LABEL.get(f"{near[0]}x{near[1]}", "")},
            "rules": {"edge_multiple": 16, "edge_max": 3840, "ratio_max": "3:1",
                      "area_min": 655_360, "area_max": 8_294_400,
                      "source": "OpenAI《Image generation》/ Azure OpenAI《GPT image models》"}}


@router.post("/health/run")
def api_health_run(request: Request):
    """全部渠道批量探活（零成本）。"""
    u, err = need_user(request)
    if err:
        return err
    out = []
    for row in store.rows("SELECT key FROM providers WHERE enabled=1 ORDER BY key"):
        p = store.get_provider(row["key"])          # 探活要用 key，这里必须带密钥读取
        if not p:
            continue
        res = relay.selftest_provider(p)
        store.set_health(p["key"], res)
        out.append({"provider": p["key"], **res})
    return {"ran": len(out), "results": out}


@router.get("/sysinfo")
def api_sysinfo(request: Request):
    u, err = need_user(request)
    if err:
        return err
    counts = dict((r["name"], r["n"]) for r in store.rows(
        "SELECT 'providers' name, COUNT(*) n FROM providers UNION ALL "
        "SELECT 'jobs', COUNT(*) FROM jobs UNION ALL "
        "SELECT 'logs', COUNT(*) FROM logs UNION ALL SELECT 'sessions', COUNT(*) FROM sessions "
        "UNION ALL SELECT 'tokens', COUNT(*) FROM tokens"))
    master = os.environ.get("QLIKEAPI_UP_TOKEN", "")
    # 版本号只有一个来源：app/main.py 的 FastAPI(version=...)；避免两处不一致
    from .main import app as _app
    return {"version": _app.version, "db": store.DB_PATH, "master_token_set": bool(master),
            "master_token_masked": store.mask(master), "counts": counts, "now": int(time.time()),
            "enc": {"enabled": True, "source": "QLIKEAPI_ENC_KEY" if os.environ.get("QLIKEAPI_ENC_KEY") else "QLIKEAPI_SECRET",
                    **store.enc_health()},
            "breaker": {"after": store.AUTO_DISABLE_AFTER, "cooldown": store.AUTO_RECOVER_SEC},
            "plugins": channels.available_ids(), "plugin_errors": channels.ERRORS,
            # 阶段 1：并发闸门 + 路由决策参数（控制台「系统信息」页直接展示）
            "gate": relay.gate.stats(),
            "router": {"max_attempts": relay.MAX_ROUTE_ATTEMPTS,
                       "retryable": list(relay.RETRYABLE),
                       "global_limit": relay.GLOBAL_LIMIT}}
