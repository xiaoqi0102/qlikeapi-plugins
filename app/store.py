"""store.py —— SQLite 数据层：渠道实例、日志、用户、会话、健康度、异步任务、访问令牌。

表结构与 v2 完全兼容（老库原地升级、不丢数据），只多做「加列」式迁移。

v3.1 新增（借鉴 New API / gpt-load / LiteLLM）：
  · tokens 表             —— 访问令牌体系（额度 / 过期 / 限模型 / 限渠道 / IP 白名单）
  · providers.weight      —— 同优先级渠道按权重分流
  · 渠道自动禁用/自动恢复 —— fail_streak + cooldown_until + disabled_reason
  · 密钥加密落库          —— api_key / sites.token 走 crypto.py，明文不再进 DB
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time

from . import crypto

DB_PATH = os.environ.get("QLIKEAPI_DB", "/data/qlikeapi.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    key TEXT PRIMARY KEY,        -- 渠道实例名，如 change2pro-banana
    label TEXT,                  -- 显示名
    protocol TEXT,               -- 渠道插件 id，如 gemini_native / qiniu
    base_url TEXT,
    auth_mode TEXT,              -- bearer | x-goog-api-key | fal_key
    api_key TEXT,                -- 多把 key 用换行分隔，按顺序轮换
    model_map TEXT,              -- {"客户端模型名": "上游真实名" 或 {...端点覆盖}}
    options TEXT,                -- 渠道级微调（drop_fields / generations_path 等）
    enabled INTEGER DEFAULT 1,
    priority INTEGER DEFAULT 0,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, provider TEXT,
    model TEXT, public_path TEXT, http_status INTEGER,
    upstream_status INTEGER, ms INTEGER, error TEXT,
    request_json TEXT, upstream_request TEXT, response_snippet TEXT,
    upstream_url TEXT, upstream_method TEXT, upstream_headers TEXT,
    kind TEXT DEFAULT 'relay'
);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, pass_hash TEXT, created_at INTEGER, last_login INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, username TEXT, created_at INTEGER, expires_at INTEGER, ua TEXT
);
CREATE TABLE IF NOT EXISTS health (
    provider TEXT PRIMARY KEY, ok INTEGER, upstream_status INTEGER,
    ms INTEGER, message TEXT, checked_at INTEGER
);
CREATE TABLE IF NOT EXISTS model_health (
    provider TEXT, model TEXT, ok INTEGER, upstream_status INTEGER,
    ms INTEGER, message TEXT, checked_at INTEGER,
    PRIMARY KEY (provider, model)
);
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,               -- 站点显示名
    type TEXT DEFAULT 'newapi',       -- 取数器类型：newapi|sub2api|deepseek|siliconflow|openai_billing|custom|manual
    base_url TEXT,                    -- 站点地址（manual 类型可留空）
    token TEXT,                       -- API key / token / cookie
    uid TEXT,                         -- New API 系需要的用户 ID
    extra TEXT,                       -- JSON：headers / jsonpath / threshold / unit / manual_balance
    enabled INTEGER DEFAULT 1,
    last_balance REAL, last_unit TEXT, last_used REAL, last_plan TEXT,
    last_checked INTEGER, last_error TEXT, last_raw TEXT,
    created_at INTEGER, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS model_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,              -- 客户端模型名；'*' 表示兜底价
    provider TEXT DEFAULT '*',        -- 指定渠道生效，'*' 为全局
    price REAL DEFAULT 0,             -- 单价
    currency TEXT DEFAULT 'CNY',      -- CNY | USD
    unit TEXT DEFAULT 'image',        -- image=按张
    source TEXT,                      -- upstream=上游实测 | newapi=从 New API 同步 | manual=手工
    note TEXT,
    updated_at INTEGER,
    UNIQUE(model, provider)
);
CREATE TABLE IF NOT EXISTS routes (
    model TEXT PRIMARY KEY,           -- 客户端模型名；'*' 为兜底链
    chain TEXT NOT NULL,              -- JSON 数组：渠道实例 key，按优先级从前到后
    note TEXT,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,               -- 令牌名称（标识调用方，如 "comfyui-本机"）
    token TEXT NOT NULL,              -- 令牌本体（客户端当 API key 用）
    enabled INTEGER DEFAULT 1,        -- 启停
    quota REAL DEFAULT 0,             -- 额度上限（按累计费用；0 = 不限）
    quota_currency TEXT DEFAULT 'CNY',
    note TEXT,
    expires_at INTEGER,               -- 过期时间戳；NULL/0 = 永不过期
    allowed_models TEXT,              -- JSON 数组：允许的模型；空 = 全部
    allowed_providers TEXT,           -- JSON 数组：允许的渠道实例；空 = 全部
    ip_whitelist TEXT,                -- 逗号分隔 IP/前缀；空 = 不限
    qps REAL DEFAULT 0,               -- 每秒请求上限（0 = 不限，轻量限流）
    created_at INTEGER, last_used INTEGER,
    used_requests INTEGER DEFAULT 0, used_images INTEGER DEFAULT 0, used_cost REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
    request_id TEXT PRIMARY KEY, provider TEXT, model TEXT, status TEXT,
    submit_at INTEGER, finish_at INTEGER, result TEXT, error TEXT
);
"""

MIGRATIONS = [
    ("providers", "priority", "INTEGER DEFAULT 0"),
    ("logs", "images", "INTEGER"),
    ("logs", "cost", "REAL"),
    ("logs", "cost_currency", "TEXT"),
    ("model_prices", "currency", "TEXT DEFAULT 'CNY'"),
    ("model_prices", "source", "TEXT"),
    ("logs", "kind", "TEXT DEFAULT 'relay'"),
    ("logs", "attempts", "INTEGER"),
    ("logs", "key_index", "INTEGER"),
    # v3.1：渠道分流与自动熔断
    ("providers", "weight", "INTEGER DEFAULT 1"),
    ("providers", "fail_streak", "INTEGER DEFAULT 0"),
    ("providers", "auto_disabled_at", "INTEGER"),
    ("providers", "cooldown_until", "INTEGER"),
    ("providers", "disabled_reason", "TEXT"),
    ("providers", "site_id", "INTEGER"),          # 关联站点余额：余额不足自动熔断
    # v3.1：日志记账到「访问令牌」
    ("logs", "token", "TEXT"),
    ("logs", "token_id", "INTEGER"),
    # v3.4.2：日志详情要能还原「完整请求」（url / method / 请求头，凭据已脱敏）
    ("logs", "upstream_url", "TEXT"),
    ("logs", "upstream_method", "TEXT"),
    ("logs", "upstream_headers", "TEXT"),
]


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _columns(c: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
        for table, col, ddl in MIGRATIONS:
            if col not in _columns(c, table):
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        c.execute("UPDATE logs SET kind='relay' WHERE kind IS NULL")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tokens_token ON tokens(token)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_logs_token ON logs(token_id)")
        # 存量明文密钥 → 密文（幂等：已加密的会原样返回）
        for tbl, col in (("providers", "api_key"), ("sites", "token")):
            for r in c.execute(f"SELECT rowid rid, {col} v FROM {tbl} WHERE {col} IS NOT NULL AND {col}<>''").fetchall():
                if not crypto.is_encrypted(r[1]):
                    c.execute(f"UPDATE {tbl} SET {col}=? WHERE rowid=?", (crypto.encrypt_lines(r[1]), r[0]))
        if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            u = os.environ.get("QLIKEAPI_ADMIN_USER") or "admin"
            p = os.environ.get("QLIKEAPI_ADMIN_PASS") or ""
            generated = ""
            if not p:
                # 不在代码里留任何默认口令：没配置就随机生成，并只在首次启动时打印一次
                p = secrets.token_urlsafe(12)
                generated = p
            salt = secrets.token_bytes(16)
            dk = hashlib.scrypt(p.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
            c.execute("INSERT INTO users(username,pass_hash,created_at) VALUES(?,?,?)",
                      (u, "scrypt$%s$%s" % (salt.hex(), dk.hex()), int(time.time())))
            if generated:
                print(f"[qlikeapi] 未设置 QLIKEAPI_ADMIN_PASS，已为账号 {u} 随机生成控制台密码：{generated}"
                      "（登录后请立即改密，或写进 .env 后重建容器）", flush=True)


# ------------------------------------------------------------------ 读写

def rows(sql: str, args: tuple = ()) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict | None:
    r = rows(sql, args)
    return r[0] if r else None


def execute(sql: str, args: tuple = ()) -> None:
    with connect() as c:
        c.execute(sql, args)


def get_provider(key: str, with_keys: bool = True) -> dict | None:
    r = one("SELECT * FROM providers WHERE key=?", (key,))
    if not r:
        return None
    r["api_key"] = "\n".join(crypto.decrypt_lines(r.get("api_key")))
    for f in ("model_map", "options"):
        try:
            r[f] = json.loads(r.get(f) or "{}")
        except Exception:
            r[f] = {}
    if not with_keys:
        r.pop("api_key", None)
    return r


def list_providers(only_enabled: bool = False) -> list[dict]:
    sql = "SELECT key FROM providers" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY priority DESC, key"
    out = []
    for r in rows(sql):
        p = get_provider(r["key"], with_keys=False)
        if p:
            out.append(p)
    return out


def provider_keys(p: dict) -> list[str]:
    """渠道实例的 key 列表（换行分隔，保持顺序；分组标签会被剥掉）。"""
    return [e["key"] for e in key_entries(p)]


def key_entries(p: dict) -> list[dict]:
    """渠道实例的密钥条目：[{'idx': 序号, 'label': 分组标签或 None, 'key': 明文}]。

    `idx` 是「在渠道内的稳定序号」，key 冷却（cooldown）按它记账，因此加/删别的 key 不影响。
    """
    from . import utils

    return [{"idx": i, **e} for i, e in enumerate(utils.parse_key_lines(p.get("api_key") or ""))]


def log_row(provider, model, path, status, up_status, ms, error, req, up_req, snippet,
            kind="relay", attempts=None, key_index=None, images=None, cost=None,
            cost_currency=None, token=None, token_id=None,
            up_url=None, up_method=None, up_headers=None) -> None:
    """写请求日志；任何异常都吞掉，绝不影响主流程。"""
    try:
        with connect() as c:
            c.execute(
                "INSERT INTO logs(ts,provider,model,public_path,http_status,upstream_status,ms,error,"
                "request_json,upstream_request,response_snippet,kind,attempts,key_index,images,cost,"
                "cost_currency,token,token_id,upstream_url,upstream_method,upstream_headers)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(time.time()), provider, model, path, status, up_status, ms, error,
                 json.dumps(req, ensure_ascii=False)[:8000],
                 json.dumps(up_req, ensure_ascii=False)[:8000], (snippet or "")[:4000], kind,
                 attempts, key_index, images, cost, cost_currency, token, token_id,
                 up_url, up_method,
                 json.dumps(up_headers, ensure_ascii=False) if up_headers else None))
            c.execute("DELETE FROM logs WHERE id < (SELECT MAX(id) FROM logs) - 5000")
    except Exception:
        pass


def set_health(provider: str, res: dict) -> None:
    try:
        with connect() as c:
            c.execute(
                "INSERT INTO health(provider,ok,upstream_status,ms,message,checked_at) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(provider) DO UPDATE SET ok=excluded.ok, upstream_status=excluded.upstream_status,"
                " ms=excluded.ms, message=excluded.message, checked_at=excluded.checked_at",
                (provider, 1 if res.get("ok") else 0, res.get("upstream_status"), res.get("ms"),
                 (res.get("upstream_message") or res.get("error") or "")[:400], int(time.time())))
    except Exception:
        pass


def set_model_health(provider: str, model: str, res: dict) -> None:
    """逐模型探活落地，并重算渠道汇总健康态。

    面板「探活」是**按模型逐条**探的；以前带 model 的探活什么都不写，
    所以渠道行永远停在「未探测」。现在逐模型写 model_health，再按
    「所有已探模型都通过才算健康」重算 health 汇总 —— 点一次就更新。
    """
    try:
        with connect() as c:
            c.execute(
                "INSERT INTO model_health(provider,model,ok,upstream_status,ms,message,checked_at)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(provider,model) DO UPDATE SET ok=excluded.ok,"
                " upstream_status=excluded.upstream_status, ms=excluded.ms,"
                " message=excluded.message, checked_at=excluded.checked_at",
                (provider, model, 1 if res.get("ok") else 0, res.get("upstream_status"),
                 res.get("ms"), (res.get("upstream_message") or res.get("error") or "")[:400],
                 int(time.time())))
    except Exception:
        pass
    recompute_health(provider)


def model_health_rows(provider: str) -> list[dict]:
    return rows("SELECT * FROM model_health WHERE provider=? ORDER BY model", (provider,))


def recompute_health(provider: str) -> None:
    """按 model_health 重算渠道汇总：已探模型全通过才 ok=1。

    只统计**当前渠道仍在用的模型**（改过模型配置后，历史行自动忽略）。
    """
    keep = set((get_provider(provider) or {}).get("model_map") or {})
    rs = [r for r in model_health_rows(provider) if not keep or r["model"] in keep]
    if not rs:
        return
    bad = [r for r in rs if not r["ok"]]
    st = next((r["upstream_status"] for r in rs if r["upstream_status"]), None)
    msg = ("；".join(f"{r['model']}: {(r['message'] or '失败')[:120]}" for r in bad)[:400]
           if bad else f"{len(rs)} 个模型全部通过")
    set_health(provider, {"ok": 0 if bad else 1, "upstream_status": st,
                          "ms": max((r["ms"] or 0) for r in rs) or None,
                          "upstream_message": msg})


# ------------------------------------------------------------------ 模型单价 / 计价

def list_prices() -> list[dict]:
    return rows("SELECT * FROM model_prices ORDER BY provider, model")


def set_price(model: str, provider: str = "*", price: float = 0.0, unit: str = "image",
              note: str = "") -> None:
    with connect() as c:
        c.execute("""INSERT INTO model_prices(model,provider,price,unit,note,updated_at)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(model,provider) DO UPDATE SET price=excluded.price,
                     unit=excluded.unit,note=excluded.note,updated_at=excluded.updated_at""",
                  (model, provider or "*", float(price or 0), unit or "image", note or "",
                   int(time.time())))


def price_row(model: str, provider: str) -> dict | None:
    """取单价行：渠道专属 > 全局模型价 > 全局兜底 '*'。"""
    best = None          # 全局模型价：model 匹配、渠道为 *
    fallback = None      # 全局兜底：* 配 *
    for r in rows("SELECT * FROM model_prices WHERE model IN (?, '*')", (model or "",)):
        if r["provider"] == provider and r["model"] == model:
            return r
        if r["provider"] == "*" and r["model"] == model and best is None:
            best = r
        if r["provider"] == "*" and r["model"] == "*" and fallback is None:
            fallback = r
    # ⚠ 兜底必须排在最后：SQL 没写 ORDER BY，行序不保证；此前两种情况共用 best，
    #    先读到的 '*' 兜底行会把「模型专属价」顶掉。
    return best or fallback


def price_for(model: str, provider: str) -> float:
    r = price_row(model, provider)
    return float(r["price"] or 0) if r else 0.0


def estimate_cost(provider: str, model: str, images: int | None) -> tuple[float, str]:
    """返回 (估算金额, 币种)。没有定价则 0。"""
    row = price_row(model or "", provider)
    n = max(1, int(images or 1))
    if not row:
        return 0.0, "CNY"
    return round(float(row["price"] or 0) * n, 6), (row.get("currency") or "CNY")


def set_price_full(model: str, provider: str, price: float, currency: str = "CNY",
                   source: str = "manual", note: str = "") -> None:
    with connect() as c:
        c.execute("""INSERT INTO model_prices(model,provider,price,currency,unit,source,note,updated_at)
                     VALUES(?,?,?,?,'image',?,?,?)
                     ON CONFLICT(model,provider) DO UPDATE SET price=excluded.price,
                     currency=excluded.currency,source=excluded.source,note=excluded.note,
                     updated_at=excluded.updated_at""",
                  (model, provider or "*", float(price or 0), currency or "CNY", source, note or "",
                   int(time.time())))


# ------------------------------------------------------------------ 路由规则

def list_routes() -> list[dict]:
    out = []
    for r in rows("SELECT * FROM routes ORDER BY model"):
        r["chain"] = _json_or(r.get("chain"), [])
        out.append(r)
    return out


def get_chain(model: str) -> list[str] | None:
    r = one("SELECT chain FROM routes WHERE model=?", (model,))
    if not r:
        return None
    return _json_or(r.get("chain"), [])


def set_chain(model: str, chain: list[str], note: str = "") -> None:
    with connect() as c:
        c.execute("""INSERT INTO routes(model,chain,note,updated_at) VALUES(?,?,?,?)
                     ON CONFLICT(model) DO UPDATE SET chain=excluded.chain,note=excluded.note,
                     updated_at=excluded.updated_at""",
                  (model, json.dumps(chain, ensure_ascii=False), note or "", int(time.time())))


def delete_chain(model: str) -> None:
    execute("DELETE FROM routes WHERE model=?", (model,))


# ------------------------------------------------------------------ 站点余额

def list_sites(only_enabled: bool = False) -> list[dict]:
    sql = "SELECT * FROM sites" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY id"
    out = []
    for r in rows(sql):
        r["extra"] = _json_or(r.get("extra"), {})
        r["token"] = crypto.decrypt(r.get("token"))
        out.append(r)
    return out


def get_site(sid: int) -> dict | None:
    r = one("SELECT * FROM sites WHERE id=?", (sid,))
    if r:
        r["extra"] = _json_or(r.get("extra"), {})
        r["token"] = crypto.decrypt(r.get("token"))
    return r


def _json_or(raw, default):
    try:
        return json.loads(raw) if raw else default
    except Exception:
        return default


def save_site(d: dict) -> int:
    now = int(time.time())
    extra = d.get("extra")
    if not isinstance(extra, str):
        extra = json.dumps(extra or {}, ensure_ascii=False)
    with connect() as c:
        if d.get("id"):
            c.execute("""UPDATE sites SET name=?, type=?, base_url=?, token=?, uid=?, extra=?, enabled=?,
                         updated_at=? WHERE id=?""",
                      (d.get("name") or "", d.get("type") or "newapi", d.get("base_url") or "",
                       crypto.encrypt_lines(d.get("token") or ""), d.get("uid") or "", extra,
                       1 if d.get("enabled", True) else 0, now, int(d["id"])))
            return int(d["id"])
        cur = c.execute("""INSERT INTO sites(name,type,base_url,token,uid,extra,enabled,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (d.get("name") or "", d.get("type") or "newapi", d.get("base_url") or "",
                         crypto.encrypt_lines(d.get("token") or ""), d.get("uid") or "", extra,
                         1 if d.get("enabled", True) else 0, now, now))
        return int(cur.lastrowid)


def update_site_result(sid: int, res: dict) -> None:
    with connect() as c:
        c.execute("""UPDATE sites SET last_balance=?, last_unit=?, last_used=?, last_plan=?,
                     last_checked=?, last_error=?, last_raw=?, updated_at=? WHERE id=?""",
                  (res.get("balance"), res.get("unit"), res.get("used"), res.get("plan"),
                   int(time.time()), (res.get("error") or "")[:400],
                   json.dumps(res.get("raw"), ensure_ascii=False)[:3000] if res.get("raw") is not None else None,
                   int(time.time()), sid))


def mask(secret: str | None, keep: int = 6) -> str:
    s = secret or ""
    if not s:
        return ""
    if len(s) <= keep + 4:
        return s[:2] + "…"
    return f"{s[:keep]}…{s[-4:]}"


# ------------------------------------------------------------------ 访问令牌（借鉴 New API 的「令牌管理」）
# 一把令牌 = 一个调用方。可限额度、限过期、限模型、限渠道、限 IP，日志按令牌记账。

def token_public(row: dict) -> dict:
    """给控制台看的令牌视图：key 打码，JSON 字段解析成数组。"""
    if not row:
        return {}
    out = dict(row)
    out["token_masked"] = mask(row.get("token"))
    for f in ("allowed_models", "allowed_providers"):
        out[f] = _json_or(row.get(f), [])
    out["ips"] = [x.strip() for x in str(row.get("ip_whitelist") or "").split(",") if x.strip()]
    exp = row.get("expires_at")
    out["expired"] = bool(exp and exp < time.time())
    out["over_quota"] = bool((row.get("quota") or 0) > 0 and (row.get("used_cost") or 0) >= (row.get("quota") or 0))
    out["unlimited"] = not (row.get("quota") or 0)
    return out


def list_tokens() -> list[dict]:
    return [token_public(r) for r in rows("SELECT * FROM tokens ORDER BY id")]


def get_token(tid: int) -> dict | None:
    r = one("SELECT * FROM tokens WHERE id=?", (tid,))
    return token_public(r) if r else None


def token_by_value(value: str) -> dict | None:
    v = (value or "").strip()
    if not v:
        return None
    return one("SELECT * FROM tokens WHERE token=?", (v,))


def save_token(d: dict) -> int:
    now = int(time.time())
    tok = (d.get("token") or "").strip() or crypto.gen_key()
    exp = d.get("expires_at") or None
    if isinstance(exp, str) and exp.isdigit():
        exp = int(exp)
    with connect() as c:
        cur = c.execute("""INSERT INTO tokens(name,token,enabled,quota,quota_currency,note,expires_at,
                             allowed_models,allowed_providers,ip_whitelist,qps,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (d.get("name") or "未命名令牌", tok, 1 if d.get("enabled", True) else 0,
                         float(d.get("quota") or 0), d.get("quota_currency") or "CNY", d.get("note") or "",
                         exp, json.dumps(d.get("allowed_models") or [], ensure_ascii=False),
                         json.dumps(d.get("allowed_providers") or [], ensure_ascii=False),
                         ",".join(d.get("ips") or []), float(d.get("qps") or 0), now))
        return int(cur.lastrowid)


def patch_token(tid: int, d: dict) -> bool:
    sets, args = [], []
    for f in ("name", "note", "quota_currency", "ip_whitelist"):
        if f in d:
            sets.append(f"{f}=?")
            args.append(d[f])
    if "enabled" in d:
        sets.append("enabled=?")
        args.append(1 if d["enabled"] else 0)
    if "quota" in d:
        sets.append("quota=?")
        args.append(float(d["quota"] or 0))
    if "qps" in d:
        sets.append("qps=?")
        args.append(float(d["qps"] or 0))
    if "expires_at" in d:
        sets.append("expires_at=?")
        args.append(d["expires_at"] or None)
    for f in ("allowed_models", "allowed_providers"):
        if f in d:
            sets.append(f"{f}=?")
            args.append(json.dumps(d[f] or [], ensure_ascii=False))
    if not sets:
        return False
    args.append(tid)
    with connect() as c:
        c.execute(f"UPDATE tokens SET {', '.join(sets)} WHERE id=?", args)
    return True


def delete_token(tid: int) -> None:
    execute("DELETE FROM tokens WHERE id=?", (tid,))


def bump_token_usage(tid: int | None, images: int = 0, cost: float = 0.0) -> None:
    if not tid:
        return
    try:
        with connect() as c:
            c.execute("""UPDATE tokens SET used_requests=COALESCE(used_requests,0)+1,
                         used_images=COALESCE(used_images,0)+?, used_cost=COALESCE(used_cost,0)+?,
                         last_used=? WHERE id=?""", (int(images or 0), float(cost or 0), int(time.time()), tid))
    except Exception:
        pass


# ------------------------------------------------------------------ 渠道自动熔断 / 自动恢复

AUTO_DISABLE_AFTER = int(os.environ.get("QLIKEAPI_AUTO_DISABLE_AFTER", "5"))
AUTO_RECOVER_SEC = int(os.environ.get("QLIKEAPI_AUTO_RECOVER_SEC", "600"))


def bump_provider_fail(key: str, reason: str = "", disconnect: bool = False) -> dict:
    """记一次渠道级失败；达到阈值就自动停用（并给一个冷却恢复时间）。"""
    out = {"fail_streak": 0, "auto_disabled": False}
    try:
        with connect() as c:
            r = c.execute("SELECT fail_streak,enabled,auto_disabled_at FROM providers WHERE key=?", (key,)).fetchone()
            if not r:
                return out
            streak = int(r[0] or 0) + 1
            if disconnect and streak >= AUTO_DISABLE_AFTER:
                until = int(time.time()) + AUTO_RECOVER_SEC
                c.execute("""UPDATE providers SET fail_streak=?, enabled=0, auto_disabled_at=?,
                             cooldown_until=?, disabled_reason=? WHERE key=?""",
                          (streak, int(time.time()), until, (reason or "连续失败自动停用")[:200], key))
                out.update(fail_streak=streak, auto_disabled=True, cooldown_until=until)
            else:
                c.execute("UPDATE providers SET fail_streak=? WHERE key=?", (streak, key))
                out["fail_streak"] = streak
    except Exception:
        pass
    return out


def clear_provider_fail(key: str) -> None:
    """渠道成功一次 → 失败计数清零。"""
    try:
        execute("UPDATE providers SET fail_streak=0 WHERE key=? AND COALESCE(fail_streak,0)<>0", (key,))
    except Exception:
        pass


def disable_provider(key: str, reason: str = "", cooldown: int = 0, manual: bool = True) -> None:
    with connect() as c:
        c.execute("""UPDATE providers SET enabled=0, disabled_reason=?, cooldown_until=?,
                     auto_disabled_at=? WHERE key=?""",
                  (reason[:200], int(time.time()) + cooldown if cooldown else None,
                   None if manual else int(time.time()), key))


def enable_provider(key: str) -> None:
    with connect() as c:
        c.execute("""UPDATE providers SET enabled=1, disabled_reason=NULL, cooldown_until=NULL,
                     auto_disabled_at=NULL, fail_streak=0 WHERE key=?""", (key,))


def auto_recover_providers() -> list[str]:
    """把过了冷却期的「自动停用」渠道重新放回路由（人工停用的不动）。"""
    now = int(time.time())
    out = []
    for r in rows("""SELECT key FROM providers WHERE enabled=0 AND auto_disabled_at IS NOT NULL
                     AND (cooldown_until IS NULL OR cooldown_until<=?)""", (now,)):
        enable_provider(r["key"])
        out.append(r["key"])
    return out


def auto_recover_candidates() -> list[str]:
    """冷却到期的「自动停用」渠道（还没恢复，等上层探活通过再放回）。"""
    now = int(time.time())
    return [r["key"] for r in rows("""SELECT key FROM providers WHERE enabled=0 AND auto_disabled_at IS NOT NULL
                                     AND (cooldown_until IS NULL OR cooldown_until<=?)""", (now,))]


def extend_cooldown(key: str, sec: int) -> None:
    """探活没过 → 再冷却一轮（不改变停用状态）。"""
    execute("UPDATE providers SET cooldown_until=? WHERE key=?", (int(time.time()) + max(30, int(sec)), key))


def enc_health() -> dict:
    """密钥加密健康度：明文残留 / 解不开的条数（QLIKEAPI_SECRET 变过就会出现解不开）。"""
    out = {"plaintext": 0, "broken": 0, "encrypted": 0}
    for tbl, col in (("providers", "api_key"), ("sites", "token")):
        for r in rows(f"SELECT {col} v FROM {tbl} WHERE {col} IS NOT NULL AND {col}<>''"):
            v = r["v"]
            if not crypto.is_encrypted(v):
                out["plaintext"] += 1
            elif crypto.decrypt_lines(v) and any(crypto.decrypt_lines(v)):
                out["encrypted"] += 1
            else:
                out["broken"] += 1
    return out
