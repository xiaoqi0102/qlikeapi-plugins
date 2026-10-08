"""newapi.py —— **只读**直连 New API 的 postgres，把「视频生成」请求接进本机面板。

为什么必须直连库：
  视频请求不经过本机网关（客户端 → New API → 任务插件 → 上游），本机一条都不落。
  而 New API 的 HTTP API 不返回任务插件的请求快照（`plugin_state` 不在对外 DTO 里），
  只有直接读 `tasks.private_data` 才拿得到 prompt / 秒数 / 分辨率 / 参考图列表。

安全与稳定性约定：
  · 连接强制 `default_transaction_read_only=on` + `statement_timeout`，只发 SELECT
  · DSN 加密落库（crypto），面板只回掩码，日志/文档里绝不出现明文
  · 任何异常都吞掉并转成「不可用 + 原因」：New API 库挂了只影响视频区，图片日志照常
  · 结果内存缓存（默认 20s），面板翻页不会把库打爆
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import crypto, store

SCOPE = "newapi"
STATEMENT_TIMEOUT_MS = int(os.environ.get("QLIKEAPI_NEWAPI_TIMEOUT_MS", "3000"))
CONNECT_TIMEOUT = int(os.environ.get("QLIKEAPI_NEWAPI_CONNECT_TIMEOUT", "3"))
CACHE_TTL = float(os.environ.get("QLIKEAPI_NEWAPI_CACHE_TTL", "20"))
DEFAULT_QUOTA_PER_UNIT = 500000.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}

VIDEO_KINDS = ("video",)          # 任务类插件（视频/异步任务）——本面板「视频」分类的口径


# ------------------------------------------------------------------ 设置

def settings() -> dict:
    """读数据源配置（DSN 解密后只在进程内使用，绝不外传）。"""
    d = store.get_settings(SCOPE)
    dsn = crypto.decrypt(d.get("dsn") or "") or ""
    return {"enabled": bool(d.get("enabled", True)),
            "dsn": dsn,
            "quota_per_unit": float(d.get("quota_per_unit") or DEFAULT_QUOTA_PER_UNIT),
            "configured": bool(dsn)}


def save_settings(d: dict) -> None:
    cur = store.get_settings(SCOPE)
    out = dict(cur)
    if "enabled" in d:
        out["enabled"] = bool(d["enabled"])
    if d.get("quota_per_unit"):
        out["quota_per_unit"] = float(d["quota_per_unit"])
    if d.get("dsn"):
        out["dsn"] = crypto.encrypt(str(d["dsn"]).strip())
    elif d.get("dsn_clear"):
        out.pop("dsn", None)
    store.set_settings(SCOPE, out)
    clear_cache()


def view() -> dict:
    """给面板看的视图：只回掩码，不回明文。"""
    s = settings()
    return {"enabled": s["enabled"], "configured": s["configured"],
            "dsn_masked": store.mask(_dsn_masked_source()) if s["configured"] else "",
            "quota_per_unit": s["quota_per_unit"]}


def _dsn_masked_source() -> str:
    """掩码用素材：只取 DSN 的 host/db 段，密码一律打掉。"""
    dsn = settings()["dsn"]
    if not dsn:
        return ""
    tail = dsn.split("@")[-1] if "@" in dsn else dsn
    head = dsn.split("://")[0] if "://" in dsn else "postgres"
    return f"{head}://***@{tail}"


def clear_cache() -> None:
    with _lock:
        _cache.clear()


# ------------------------------------------------------------------ 连接与查询

def _connect():
    import psycopg2                                    # 延迟导入：没装也只影响视频区
    s = settings()
    if not s["dsn"]:
        raise RuntimeError("未配置 New API 数据源（设置 → 生成日志数据源）")
    conn = psycopg2.connect(s["dsn"], connect_timeout=CONNECT_TIMEOUT,
                            application_name="qlikeapi-panel-ro")
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
    return conn


def _q(sql: str, args: tuple = ()) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:                              # noqa: BLE001
            pass


def _cached(key: str, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
        if len(_cache) > 200:
            for k in list(_cache)[:100]:
                _cache.pop(k, None)
    return val


def available() -> tuple[bool, str]:
    """数据源可用性（面板顶部提示用）。"""
    s = settings()
    if not s["enabled"]:
        return False, "数据源已关闭"
    if not s["dsn"]:
        return False, "未配置 DSN"
    try:
        def _ping():
            return _q("SELECT current_database() AS db, version() AS v")[0]

        info = _cached("ping", _ping)
        return True, f"{info['db']}"
    except ImportError:
        return False, "容器缺 psycopg2 驱动（需重建镜像）"
    except Exception as exc:                           # noqa: BLE001
        return False, str(exc)[:200]


# ------------------------------------------------------------------ 视频（任务型）请求日志

_TASK_SQL = """
SELECT t.id, t.task_id, t.platform, t.status, t.progress, t.fail_reason, t.action,
       t.channel_id, t.user_id, t.quota, t.group,
       t.created_at, t.submit_time, t.start_time, t.finish_time,
       t.private_data, t.data, t.properties,
       c.name AS channel_name, c.type AS channel_type,
       COALESCE(t.properties::jsonb->>'model', lg.model_name) AS model
FROM tasks t
LEFT JOIN channels c ON c.id = t.channel_id
LEFT JOIN LATERAL (
    SELECT l.model_name FROM logs l
     WHERE (l.other::jsonb)->>'task_id' = t.task_id ORDER BY l.id DESC LIMIT 1
) lg ON true
WHERE TRUE
"""


def _task_rows(where: str, args: list, limit: int, cursor: dict | None) -> list[dict]:
    # 任务表本身不存模型名：properties（公开）+ New API 日志兜底
    sql = _TASK_SQL + (where or "")            # ← where 由 video_logs 拼好（已带 " AND ..." 前缀）
    if cursor and cursor.get("ts"):
        sql += " AND (t.created_at < %s OR (t.created_at = %s AND NOT (t.id = ANY(%s))))"
        args = args + [cursor["ts"], cursor["ts"], cursor.get("seen") or []]
    sql += " ORDER BY t.created_at DESC, t.id DESC LIMIT %s"
    args = args + [limit]
    return _q(sql, tuple(args))


def _log_rows(task_ids: list[str]) -> dict[str, dict]:
    """按 task_id 聚合 New API 的日志：令牌 / 渠道 / 计费 / 上游模型 / 插件版本 / 退款。"""
    if not task_ids:
        return {}
    rows = _q("""
        SELECT (l.other::jsonb)->>'task_id' AS tid,
               max(l.model_name) AS model_name,
               max(l.username) AS username,
               max(l.token_name) AS token_name,
               max(l.channel_name) AS channel_name,
               max(l.request_id) AS request_id,
               max(l.use_time) AS use_time,
               max(l.created_at) AS log_ts,
               max((l.other::jsonb)->>'upstream_model_name') AS upstream_model,
               max((l.other::jsonb)->>'model_price') AS model_price,
               max((l.other::jsonb)->>'request_path') AS request_path,
               max((l.other::jsonb)->>'upstream_task_id') AS upstream_task_id,
               max((l.other::jsonb)->>'reason') AS reason,
               max((l.other::jsonb)#>>'{admin_info,task_plugin,name}') AS plugin_name,
               max((l.other::jsonb)#>>'{admin_info,task_plugin,version}') AS plugin_version,
               max((l.other::jsonb)#>>'{admin_info,task_plugin,key}') AS plugin_key,
               max((l.other::jsonb)#>>'{admin_info,task_plugin,author,name}') AS plugin_author,
               max((l.other::jsonb)#>>'{admin_info,task_plugin,author,url}') AS plugin_author_url,
               max((l.other::jsonb)->>'group_ratio') AS group_ratio,
               sum(CASE WHEN l.type = 2 THEN l.quota ELSE 0 END) AS quota_used,
               sum(CASE WHEN l.type = 6 THEN l.quota ELSE 0 END) AS quota_refund,
               bool_or(l.type = 6) AS refunded,
               json_agg(DISTINCT l.type) AS types
          FROM logs l
         WHERE (l.other::jsonb)->>'task_id' = ANY(%s)
         GROUP BY 1
    """, (task_ids,))
    return {r["tid"]: r for r in rows if r.get("tid")}


def _shrink(v):
    """快照里的大值折叠：base64 / 超长串只报大小（面板绝不能吐原始 base64）。"""
    if isinstance(v, str):
        if v.startswith("data:"):
            return f"<base64/data URI，约 {max(1, len(v) * 3 // 4 // 1024)}KB>"
        return v if len(v) <= 900 else f"<超长值，约 {len(v)} 字符>"
    if isinstance(v, list):
        return [_shrink(x) for x in v[:12]]
    if isinstance(v, dict):
        return {k: _shrink(x) for k, x in list(v.items())[:20]}
    return v


def _snapshot(raw) -> dict:
    """任务私有数据的请求快照：{plugin_state.request} → 面板字段。"""
    if not raw:
        return {}
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:                                  # noqa: BLE001
        return {}
    ps = (d or {}).get("plugin_state") or {}
    req = ps.get("request") or {}
    if not isinstance(req, dict):
        return {}
    keep = ("model", "prompt", "seconds", "duration", "duration_seconds", "resolution",
            "aspect_ratio", "size", "ratio", "mode", "quality", "reference_images",
            "reference_image_fields", "reference_images_sent", "image_urls", "images",
            "audio_urls", "videos", "audios", "seed", "watermark", "generate_audio",
            "reference_image_count", "_uploaded", "_base64", "field_names")
    out = {k: _shrink(req[k]) for k in keep if k in req}
    return out


def _refs(snap: dict) -> list[str]:
    """参考图列表（只留可点的链接，base64 只报大小）。"""
    vals = []
    for key in ("reference_images", "image_urls", "images"):
        v = snap.get(key)
        if isinstance(v, list):
            vals += v
        elif isinstance(v, str) and v.strip():
            vals.append(v)
    out = []
    for it in vals:
        s = it.get("url") if isinstance(it, dict) else it
        if not isinstance(s, str):
            continue
        if s.startswith("data:"):
            out.append(f"<base64/data URI，约 {max(1, len(s) * 3 // 4 // 1024)}KB>")
        elif len(s) > 900:
            out.append(f"<超长值，约 {len(s)} 字符>")
        else:
            out.append(s)
    return out[:12]


def _row_shape(t: dict, lg: dict, with_snapshot: bool) -> dict:
    """一行视频日志 → 面板形状（字段名与图片日志尽量对齐）。"""
    snap = _snapshot(t.get("private_data")) if with_snapshot else {}
    ts = int(t.get("created_at") or t.get("submit_time") or 0)
    quota_used = lg.get("quota_used") or 0
    quota_refund = lg.get("quota_refund") or 0
    price = None
    if lg.get("model_price") not in (None, ""):
        try:
            price = float(lg["model_price"])
        except Exception:                              # noqa: BLE001
            price = None
    s = settings()
    net = (int(quota_used) - int(quota_refund)) / (s["quota_per_unit"] or DEFAULT_QUOTA_PER_UNIT)
    row = {
        "src": "video",
        "id": f"v{t.get('id')}",
        "task_id": t.get("task_id"),
        "ts": ts or int(t.get("submit_time") or 0),
        "model": t.get("model") or lg.get("model_name") or "",
        "provider": lg.get("channel_name") or t.get("channel_name") or t.get("platform") or "",
        "platform": t.get("platform"),
        "status": (t.get("status") or "").upper(),
        "fail_reason": t.get("fail_reason") or lg.get("reason") or "",
        "progress": t.get("progress"),
        "ms": (int(t.get("finish_time") or 0) - int(t.get("start_time") or t.get("created_at") or 0)) * 1000
              if t.get("finish_time") else None,
        "token": lg.get("token_name") or "",
        "username": lg.get("username") or "",
        "cost": price if price is not None else round(net, 6),
        "cost_currency": "USD",
        "quota_used": int(quota_used or 0),
        "quota_refund": int(quota_refund or 0),
        "billing": {"model_price": price, "currency": "USD",
                    "quota_used": int(quota_used or 0), "quota_refund": int(quota_refund or 0),
                    "quota_per_unit": s["quota_per_unit"],
                    "charged_usd": round(int(quota_used or 0) / (s["quota_per_unit"] or DEFAULT_QUOTA_PER_UNIT), 6),
                    "refunded_usd": round(int(quota_refund or 0) / (s["quota_per_unit"] or DEFAULT_QUOTA_PER_UNIT), 6),
                    "net_usd": round(net, 6), "refunded": bool(lg.get("refunded"))},
        "refunded": bool(lg.get("refunded")),
        "upstream_model": lg.get("upstream_model") or "",
        "upstream_task_id": lg.get("upstream_task_id") or "",
        "plugin": {"name": lg.get("plugin_name") or "", "version": lg.get("plugin_version") or "",
                   "key": lg.get("plugin_key") or "", "author": lg.get("plugin_author") or "",
                   "author_url": lg.get("plugin_author_url") or ""},
        "request_path": lg.get("request_path") or "/v1/videos",
        "request_id": lg.get("request_id") or "",
        "channel_id": t.get("channel_id"),
        "user_id": t.get("user_id"),
        "group": t.get("group"),
        "result_urls": _result_urls(t.get("data")),
        "tags": ["视频"],
    }
    if with_snapshot:
        row["client_request"] = snap
        row["reference_images"] = _refs(snap)
        row["upstream_request"] = _upstream_echo(snap, lg, t)
    return row


def _result_urls(raw) -> list[str]:
    if not raw:
        return []
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:                                  # noqa: BLE001
        return []
    urls = (d or {}).get("result_urls") or (d or {}).get("urls") or []
    return [u for u in urls if isinstance(u, str)][:6]


def _upstream_echo(snap: dict, lg: dict, t: dict) -> dict:
    """上游报文：New API 不落上游报文体，这里给「我们确知的上游侧事实」。

    · 上游模型名（客户端模型名经 model_mapping 翻过去的真名）
    · 插件（作者/版本）+ 请求路径
    · 上游任务号（提交回执）
    阶段 3 会让任务插件把「即将发出的 JSON（脱敏）」也写进快照，届时这里换成真报文。
    """
    out = {"upstream_model": lg.get("upstream_model") or "",
           "request_path": lg.get("request_path") or "/v1/videos",
           "plugin": f"{lg.get('plugin_name') or ''} {lg.get('plugin_version') or ''}".strip(),
           "upstream_task_id": lg.get("upstream_task_id") or "",
           "note": "New API 不记录任务上游报文体；下方为插件提交时回执的上游侧信息。"
                   "若需完整上游报文，见「生成日志」阶段 3（插件快照补写脱敏报文）。"}
    if snap:
        out["submit_params"] = {k: v for k, v in snap.items()
                                if k not in ("prompt", "reference_images", "images", "image_urls")}
    return out


def video_logs(limit: int = 50, status: str = "", model: str = "", channel: str = "",
               token: str = "", q: str = "", days: int = 30,
               cursor: dict | None = None) -> dict:
    """视频（任务型）请求：分页 + 筛选，返回面板形状的行 + 下一页游标。"""
    where, args = [], []
    if status == "ok":
        where.append("upper(COALESCE(t.status,'')) IN ('SUCCESS','SUCCEED','COMPLETED')")
    elif status == "err":
        where.append("upper(COALESCE(t.status,'')) IN ('FAILURE','FAILED','ERROR')")
    elif status == "run":
        where.append("upper(COALESCE(t.status,'')) IN ('NOT_START','SUBMITTED','QUEUED','IN_PROGRESS','RUNNING','PROCESSING')")
    if channel:
        where.append("(COALESCE(c.name,'') LIKE %s OR COALESCE(t.platform,'') LIKE %s)")
        args += [f"%{channel}%"] * 2
    if days:
        where.append("t.created_at >= %s")
        args.append(int(time.time()) - int(days) * 86400)
    if model:
        where.append("(COALESCE(t.properties::jsonb->>'model','') LIKE %s OR COALESCE(t.private_data::jsonb#>>'{plugin_state,request,model}','') LIKE %s)")
        args += [f"%{model}%"] * 2
    if q:
        where.append("(COALESCE(t.task_id,'') LIKE %s OR COALESCE(t.private_data::jsonb#>>'{plugin_state,request,prompt}','') LIKE %s "
                     "OR COALESCE(t.fail_reason,'') LIKE %s)")
        args += [f"%{q}%"] * 3
    clause = (" AND " + " AND ".join(where)) if where else ""
    # 令牌筛选只能在 New API 日志里判，取足够多的候选再过滤（任务本身不存令牌）
    fetch = limit + 1 if not token else max(limit * 6, 300)
    rows = _task_rows(clause, args, fetch, cursor)
    logs = _log_rows([r["task_id"] for r in rows if r.get("task_id")])
    out = []
    for t in rows:
        lg = logs.get(t.get("task_id")) or {}
        if token and token.lower() not in str(lg.get("token_name") or "").lower():
            continue
        out.append(_row_shape(t, lg, with_snapshot=True))
    nxt = None
    if len(out) > limit:
        out = out[:limit]
    if out:
        last = out[-1]
        same_ts = [r for r in out if r["ts"] == last["ts"]]
        nxt = {"ts": last["ts"], "seen": [int(r["id"][1:]) for r in same_ts],
               "src": "video"}
    return {"rows": out, "next": nxt}


def video_log(task_id: str) -> dict | None:
    """单条视频日志详情（完整快照）。"""
    rows = _q(_TASK_SQL + " AND t.task_id = %s LIMIT 1", (task_id,))
    if not rows:
        return None
    t = rows[0]
    logs = _log_rows([task_id])
    return _row_shape(t, logs.get(task_id) or {}, with_snapshot=True)


def video_stats(days: int = 7) -> dict:
    """视频区小计（面板顶部/仪表盘用）：条数、成功率、费用。"""
    since = int(time.time()) - int(days) * 86400

    def _calc():
        rs = _q("""
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE upper(COALESCE(status,'')) IN ('SUCCESS','SUCCEED','COMPLETED')) AS ok,
                   count(*) FILTER (WHERE upper(COALESCE(status,'')) IN ('FAILURE','FAILED','ERROR')) AS bad
              FROM tasks WHERE created_at >= %s
        """, (since,))
        base = rs[0] if rs else {"n": 0, "ok": 0, "bad": 0}
        cost = _q("""
            SELECT COALESCE(sum(CASE WHEN type = 2 THEN quota WHEN type = 6 THEN -quota ELSE 0 END), 0) AS q
              FROM logs WHERE created_at >= %s AND ((other::jsonb)->>'task_id') IS NOT NULL
        """, (since,))
        s = settings()
        base["cost_usd"] = round(float((cost[0] or {}).get("q") or 0)
                                 / (s["quota_per_unit"] or DEFAULT_QUOTA_PER_UNIT), 4)
        return base

    try:
        return _cached(f"stats:{days}", _calc)
    except Exception:                                  # noqa: BLE001
        return {"n": 0, "ok": 0, "bad": 0, "cost_usd": 0}
