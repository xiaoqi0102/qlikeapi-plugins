"""relay.py —— /up/<渠道实例>/... 转发面（本服务的全部核心就这么点东西）。

链路：New API → /up/<provider>/v1/images/{generations,edits} → 渠道插件翻译 → 上游
      → 响应归一化成 OpenAI 形状回给客户端。

零成本工具（绝不会真出图、不扣费）：
  POST /up/<p>/v1/images/preview   只回「将要发给上游的请求」，不发出去
  POST /up/<p>/v1/images/selftest  不带 prompt 打上游（上游必然拒绝），验证路径/鉴权/模型名
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import random
import threading
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import channels, imagehost, protocols, store
from .channels.base import ChannelError

router = APIRouter()
RETRYABLE = (402, 408, 409, 425, 429, 500, 502, 503, 504, 529)
MAX_KEY_ATTEMPTS = 3

# ---- 并发闸门（阶段 1）参数：默认「不限并发」，按渠道或全局开启 ----
GLOBAL_LIMIT = int(os.getenv("QLIKEAPI_MAX_CONCURRENCY", "0") or 0)      # 0 = 不限制
QUEUE_WAIT = float(os.getenv("QLIKEAPI_QUEUE_WAIT", "30"))               # 排队最多等多久（秒）
MAX_WAITING = int(os.getenv("QLIKEAPI_MAX_WAITING", "32"))               # 等待队列上限
MAX_ROUTE_ATTEMPTS = int(os.getenv("QLIKEAPI_MAX_ROUTE_ATTEMPTS", "6"))  # 一条请求最多打几次上游


class _Gate:
    """并发闸门：槽位 + 排队 + 超时 + 队列上限（语义借鉴 sub2api 的图片并发限流器）。

    - 每个渠道一个计数槽；上限取渠道 options.max_concurrency，没配就退到全局 QLIKEAPI_MAX_CONCURRENCY；
    - 两者都是 0 时不拦截（默认行为不变，按需开启）；
    - 拿不到槽位不会立刻失败：先排队等 QUEUE_WAIT 秒，等不到才拒（429/503 + Retry-After），
      路由层遇到「某个渠道排队超时」会先换下一家，做到「降低单点依赖」。
    """

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._busy: dict[str, int] = {}
        self._waiting = 0
        self.rejected = 0

    def limit_of(self, p: dict) -> int:
        opt = p.get("options") or {}
        try:
            own = int(opt.get("max_concurrency") or 0)
        except Exception:
            own = 0
        return own or GLOBAL_LIMIT

    def acquire(self, key: str, limit: int) -> str | None:
        """拿到槽位返回 None；否则返回拒绝原因（人类可读）。"""
        if limit <= 0:
            return None
        with self._cv:
            if self._waiting >= MAX_WAITING:
                self.rejected += 1
                return f"等待队列已满（{self._waiting}/{MAX_WAITING}）"
            self._waiting += 1
            try:
                deadline = time.time() + QUEUE_WAIT
                while self._busy.get(key, 0) >= limit:
                    left = deadline - time.time()
                    if left <= 0:
                        self.rejected += 1
                        return f"排队 {QUEUE_WAIT:.0f}s 仍未拿到并发槽位（上限 {limit}）"
                    self._cv.wait(min(left, 0.5))
                self._busy[key] = self._busy.get(key, 0) + 1
                return None
            finally:
                self._waiting -= 1

    def release(self, key: str) -> None:
        with self._cv:
            n = self._busy.get(key, 0) - 1
            if n <= 0:
                self._busy.pop(key, None)
            else:
                self._busy[key] = n
            self._cv.notify_all()

    def stats(self) -> dict:
        with self._cv:
            return {"busy": dict(self._busy), "waiting": self._waiting, "rejected": self.rejected,
                    "global_limit": GLOBAL_LIMIT, "queue_wait": QUEUE_WAIT, "max_waiting": MAX_WAITING}


gate = _Gate()
# 内存态：每个渠道的 key 轮换指针 + 冷却时间（重启即重置，轻量够用）
_KEY_STATE: dict[str, dict] = {}


# ------------------------------------------------------------------ 鉴权

def _client_ip(request: Request) -> str:
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "") or ""


def _presented_secret(request: Request) -> str:
    """从各种客户端写法里取出「它自报的密钥」。"""
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        auth = auth[7:].strip()
    for h in ("x-qlikeapi-token", "x-api-key", "x-imggw-token", "api-key"):
        v = (request.headers.get(h) or "").strip()
        if v:
            return v
    return auth


_RATE: dict[int, list[float]] = {}


def _authorize(request: Request):
    """三道门：① 内部主密钥（New API 走内网）② 控制台会话（页面里点预览/探活）③ 签发的访问令牌。

    返回 (access, error_response)。access = {"kind": "internal"|"token", "name", "id", "row"}
    """
    from . import admin  # 延迟导入，避免与 admin 循环依赖
    if admin.current_user(request):
        return {"kind": "internal", "name": "控制台会话", "id": None, "row": None}, None
    secret = _presented_secret(request)
    master = os.environ.get("QLIKEAPI_UP_TOKEN", "")
    if master and secret and hmac.compare_digest(secret, master):
        return {"kind": "internal", "name": "内部主密钥", "id": None, "row": None}, None
    if not master and not secret:
        return {"kind": "internal", "name": "未设防", "id": None, "row": None}, None

    row = store.token_by_value(secret)
    if not row:
        return None, JSONResponse({"error": {"message": "unauthorized（密钥无效：既不是内部主密钥，也不是本服务签发的访问令牌）",
                                             "type": "invalid_request_error"}}, status_code=401)
    if not row.get("enabled"):
        return None, JSONResponse({"error": {"message": f"该访问令牌已被停用：{row.get('name')}",
                                             "type": "invalid_request_error"}}, status_code=401)
    exp = row.get("expires_at")
    if exp and exp < time.time():
        return None, JSONResponse({"error": {"message": f"该访问令牌已于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))} 过期",
                                             "type": "invalid_request_error"}}, status_code=401)
    ips = [x.strip() for x in str(row.get("ip_whitelist") or "").split(",") if x.strip()]
    ip = _client_ip(request)
    if ips and not any(ip == x or ip.startswith(x) for x in ips):
        return None, JSONResponse({"error": {"message": f"该访问令牌不允许来自 IP {ip}",
                                             "type": "invalid_request_error"}}, status_code=403)
    if (row.get("quota") or 0) > 0 and (row.get("used_cost") or 0) >= (row.get("quota") or 0):
        return None, JSONResponse({"error": {"message": f"该访问令牌额度已用尽（{row.get('quota')} {row.get('quota_currency') or 'CNY'}）",
                                             "type": "insufficient_quota"}}, status_code=402)
    qps = float(row.get("qps") or 0)
    if qps > 0:
        now = time.time()
        hist = [t for t in _RATE.get(row["id"], []) if now - t < 1.0]
        if len(hist) >= qps:
            return None, JSONResponse({"error": {"message": f"该访问令牌超过限速（{qps} 次/秒）",
                                                 "type": "rate_limit_error"}}, status_code=429)
        hist.append(now)
        _RATE[row["id"]] = hist
    return {"kind": "token", "name": row.get("name") or f"令牌#{row['id']}", "id": row["id"], "row": row}, None


def _guard_scope(access: dict, model: str, provider: str = ""):
    """令牌的「限模型 / 限渠道」检查。返回错误响应或 None。"""
    if not access or access.get("kind") != "token":
        return None
    row = access.get("row") or {}
    models = store._json_or(row.get("allowed_models"), [])
    if models and model and model not in models:
        return JSONResponse({"error": {"message": f"该访问令牌不允许调用模型 '{model}'（允许：{', '.join(models)}）",
                                       "type": "invalid_request_error"}}, status_code=403)
    provs = store._json_or(row.get("allowed_providers"), [])
    if provs and provider and provider not in provs:
        return JSONResponse({"error": {"message": f"该访问令牌不允许走渠道 '{provider}'",
                                       "type": "invalid_request_error"}}, status_code=403)
    return None


# ------------------------------------------------------------------ key 轮换

def _next_key(provider: str, entries: list[dict], tried: set[int]) -> dict | None:
    """在「该模型可用的密钥条目」里轮换挑一把（冷却按条目的稳定 idx 记账）。"""
    st = _KEY_STATE.setdefault(provider, {"idx": 0, "cooldown": {}})
    now = time.time()
    n = len(entries)
    for _ in range(n):
        i = st["idx"] % n
        st["idx"] = (i + 1) % n
        e = entries[i]
        if e["idx"] in tried:
            continue
        if st["cooldown"].get(e["idx"], 0) > now:
            continue
        return e
    return None


def _cooldown_key(provider: str, idx: int, status: int | None, fail: int = 1) -> None:
    if status in (401, 403):        # key 失效，冷却久一点，等人工换
        wait = 1800
    elif status in RETRYABLE or status is None:
        wait = min(60 * (2 ** max(0, fail - 1)), 900)
    else:
        return                      # 请求本身的问题，不惩罚 key
    st = _KEY_STATE.setdefault(provider, {"idx": 0, "cooldown": {}})
    st["cooldown"][idx] = time.time() + wait


# ------------------------------------------------------------------ 请求翻译

def _merge_files(body: dict) -> dict:
    """/v1/images/edits 是 multipart：把上传的文件转成 data:base64 参考图。"""
    files = body.pop("__files", None)
    if files:
        refs = body.get("image") if isinstance(body.get("image"), list) else []
        for _name, up in files:
            try:
                content = up.file.read()
                mime = getattr(up, "content_type", "image/png") or "image/png"
                refs.append("data:%s;base64,%s" % (mime, base64.b64encode(content).decode()))
            except Exception:
                pass
        if refs:
            body["image"] = refs
    return body


def prepare(p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
    """交给渠道插件翻译；插件不存在或出错都在这里报清楚。

    **参考图能力协商**在翻译之前做：有的上游只认公网 URL（七牛 fal 异步面），
    客户端却给 base64 —— 那就先换成图床直链再翻译；只认 base64 的（Gemini inlineData）
    原样透传，绝不白绕一跳。转换范围由插件按各家官方文档声明（channels.base.ref_input）。
    """
    ch = channels.get(p.get("protocol") or "")
    if not ch:
        raise ValueError(f"渠道插件 '{p.get('protocol')}' 未注册（可用：{', '.join(channels.available_ids())}）")
    notes: list[dict] = []
    policy = ch.ref_policy(p, body, edit)
    if policy == "base64":
        # 只认 base64 的渠道（如 aicost 的 gpt-image-2 编辑面）：客户端给的公网 URL 先下载内联成
        # data URI（内存里做，不落盘）；下载不到就本地报错，别把注定失败的 URL 丢给上游。
        body, fail, notes = imagehost.inline_url_refs(body, None, edit)
        if fail:
            raise ChannelError("参考图转换失败（URL → base64）：" + "；".join(fail)[:300])
    else:
        try:
            body, _fail, notes = imagehost.apply_to_body(body, policy, None, edit)
        except imagehost.UploadFailed as exc:
            raise ChannelError(f"参考图转换失败（base64 → 公网直链）：{exc}") from exc
    url, up, meta = ch.build(p, body, edit)
    if notes:
        meta["imagehost"] = notes
    return url, up, meta


def _job_id(ch, up_json: Any) -> str:
    """上游响应里的任务号：插件自己认的优先（task_id），再兜底扫常见字段名。

    异步任务页要展示「所有渠道」的异步请求，不只 fal 队列，所以这里做通用识别。
    """
    try:
        tid = ch.task_id(up_json) if (ch is not None and hasattr(ch, "task_id")) else None
    except Exception:
        tid = None
    if isinstance(tid, str) and tid.strip():
        return tid.strip()
    if isinstance(up_json, dict):
        for k in ("task_id", "taskId", "request_id", "requestId"):
            v = up_json.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _shape_success(p: dict, body: dict, meta: dict, up_json: Any, headers: dict, provider: str, t0: float):
    """把上游结果归一化成 OpenAI 图片响应形状。"""
    ch = channels.get(p.get("protocol") or "")
    # 走法由「插件本次请求的声明」决定（meta.mode），没有声明的回落到插件默认走法；
    # 这样合并插件（如 qiniu：async + sync 两面）也能在一个插件里共存
    if ((meta or {}).get("mode") or (ch.route_mode("generate") if ch else "")) == "queue":
        rid = (up_json or {}).get("request_id") or (up_json or {}).get("requestId")
        if not rid:
            urls, _ = protocols.extract_urls(up_json)
            return {"created": int(time.time()), "data": [{"url": u} for u in urls]}
        meta["request_id"] = rid
        store.execute("INSERT OR REPLACE INTO jobs(request_id,provider,model,status,submit_at,mode)"
                      " VALUES(?,?,?, 'RUNNING', ?, 'queue')",
                      (rid, provider, body.get("model"), int(time.time())))
        state, payload = protocols.poll_fal(meta, headers)
        urls, err = protocols.extract_urls(payload)
        if state != "OK" or not urls:
            msg = err or f"fal task {rid} {state or 'no image in result'}"
            store.execute("UPDATE jobs SET status=?, finish_at=?, error=? WHERE request_id=?",
                          (state or "FAILED", int(time.time()), msg[:600], rid))
            store.log_row(provider, body.get("model"), "/up/%s/v1/images" % provider, 502, None,
                          int((time.time() - t0) * 1000), msg, body, None,
                          json.dumps(payload, ensure_ascii=False)[:1500])
            return JSONResponse({"error": {"message": msg, "type": "upstream_error", "request_id": rid}},
                                status_code=502)
        store.execute("UPDATE jobs SET status='DONE', finish_at=?, result=? WHERE request_id=?",
                      (int(time.time()), json.dumps(urls, ensure_ascii=False)[:2000], rid))
        return {"created": int(time.time()), "data": [{"url": u} for u in urls], "request_id": rid}
    data = ch.parse(up_json) if ch else []
    if not data and isinstance(up_json, dict) and isinstance(up_json.get("data"), list):
        data = up_json["data"]
    if not data and ch is not None and ch.has_async_poll():
        # 上游回了任务号而不是图（如 aicost.me）→ 交给插件轮询，客户端照样拿到同步结果。
        # 这类异步请求同样要进「异步任务」页（不只 fal 队列），先记 RUNNING 再按结果收尾。
        tid = _job_id(ch, up_json)
        if tid:
            store.execute("INSERT OR REPLACE INTO jobs(request_id,provider,model,status,submit_at,mode)"
                          " VALUES(?,?,?, 'RUNNING', ?, 'poll')",
                          (tid, provider, body.get("model"), int(time.time())))
        state, polled = ch.poll(up_json, meta or {}, headers)
        if state == "OK":
            up_json = polled
            data = ch.parse(polled) or [{"url": u} for u in protocols.extract_urls(polled)[0]]
            if tid:
                got, _ = protocols.extract_urls(polled)
                store.execute("UPDATE jobs SET status='DONE', finish_at=?, result=? WHERE request_id=?",
                              (int(time.time()), json.dumps(got, ensure_ascii=False)[:2000], tid))
        elif tid and state != "SKIP":
            store.execute("UPDATE jobs SET status=?, finish_at=?, error=? WHERE request_id=?",
                          (state or "FAILED", int(time.time()), f"轮询未取到图（{state}）", tid))
        elif tid:
            store.execute("DELETE FROM jobs WHERE request_id=?", (tid,))   # 不是异步任务，别留脏行
    if not data:
        urls, err = protocols.extract_urls(up_json)
        if urls:
            data = [{"url": u} for u in urls]
        else:
            msg = err or "上游返回里没有图片"
            store.log_row(provider, body.get("model"), "/up/%s/v1/images" % provider, 502, None,
                          int((time.time() - t0) * 1000), msg, body, None,
                          json.dumps(up_json, ensure_ascii=False)[:1500])
            return JSONResponse({"error": {"message": msg, "type": "upstream_error"}}, status_code=502)
    out = {"created": int(time.time()), "data": data}
    resp_format = (body.get("response_format") or "").lower()
    if resp_format == "url" and all(d.get("b64_json") for d in data):
        out["note"] = "上游固定返回 b64_json，客户端按 b64 处理即可（本服务不落盘、不转存）"
    return out



# ------------------------------------------------------------------ 单渠道调用（可被路由层复用）

def _redact_headers(headers: dict, secret: str | None = None) -> dict:
    """把请求头里的凭据换成占位符 —— 日志要能还原「完整请求」，但凭据绝不入库。

    占位符按鉴权方式给不同名字，方便直接复制去实测时替换。
    """
    out = {}
    for k, v in (headers or {}).items():
        if secret and secret in str(v):
            v = str(v).replace(secret, "YOUR_API_KEY")
        elif k.lower() in ("authorization", "x-goog-api-key", "x-api-key", "api-key"):
            v = "YOUR_API_KEY"
        out[k] = v
    return out


def _size_info(meta: dict) -> dict:
    """尺寸换算结果：中文说明给面板/日志，纯 ASCII 给响应头（HTTP 头不能放中文）。"""
    if not (meta or {}).get("size_hdr"):
        return {}
    return {"size_from": meta.get("size_from"), "size_to": meta.get("size_to"),
            "size_note": meta.get("size_note"), "size_hdr": meta.get("size_hdr")}


def _imagehost_info(meta: dict) -> dict:
    """参考图形态转换结果 —— **两个方向都报**：

    · `base64 → 公网直链`（上传图床，给只认 URL 的渠道）
    · `URL → 内联 base64`（下载内联，给只认 base64 的渠道，如 aicost）

    响应头只放 ASCII（host 名 / inline + 张数），中文说明走日志详情与响应 JSON。
    """
    notes = (meta or {}).get("imagehost") or []
    ups = [n for n in notes if n.get("host")]
    ins = [n for n in notes if n.get("mode") == "inline"]
    if not ups and not ins:
        return {}
    hosts = list(dict.fromkeys(n["host"] for n in ups))
    parts = [f"参考图 {n['bytes'] // 1024}KB → 图床直链（{n['host']}）" for n in ups]
    parts += [f"参考图 URL → 内联 base64（{n['bytes'] // 1024}KB）" for n in ins]
    return {"imagehost": ",".join(hosts + (["inline"] if ins else [])),
            "imagehost_n": len(ups) + len(ins),
            "imagehost_note": "；".join(parts),
            "imagehost_raw": notes}          # 原始明细，供路由汇总行落库（面板据此显示转换说明）


def _countable_failure(status: int | None) -> bool:
    """这次失败要不要算到「渠道连续失败」里（够数就自动停用该渠道）。"""
    if status is None:                 # 连接失败/超时
        return True
    return status in (401, 403, 402, 408, 429) or status >= 500


def _retryable(p: dict, status: int | None) -> bool:
    """这次上游结果要不要**换下一个渠道**。

    默认只换「渠道类错误」（`RETRYABLE`：402/408/409/425/429/5xx/529）；
    4xx 里的 400 通常意味着请求本身有问题，换家也一样失败，所以默认不换。
    但有些 400 其实是**渠道口径差异**（同一个请求在别家能成，例如 aicost 的编辑面不吃 URL 参考图、
    change2pro 却吃），渠道实例开 `options.retry_on_4xx` 后 4xx 也换渠道。
    """
    if status in RETRYABLE:
        return True
    opt = (p or {}).get("options") or {}
    return bool(opt.get("retry_on_4xx")) and status is not None and 400 <= status < 500


def invoke_provider(p: dict, body: dict, edit: bool, access: dict | None = None,
                    log_kind: str = "relay", do_log: bool = True) -> tuple[dict | JSONResponse, dict]:
    """把请求打到一个渠道实例上（含 key 轮换重试），返回 (结果 或 错误响应, 元信息).

    access：调用方身份（控制台/主密钥/访问令牌）；令牌会记账到 tokens 表。
    """
    provider = p["key"]
    tk_name = (access or {}).get("name")
    tk_id = (access or {}).get("id")
    t0 = time.time()
    try:
        url, up_body, meta = prepare(p, body, edit)
    except ValueError as e:
        if do_log:
            store.log_row(provider, body.get("model"), f"/up/{provider}/v1/images", 400, None,
                          int((time.time() - t0) * 1000), str(e), body, None, None, kind="client",
                          token=tk_name, token_id=tk_id)
        return JSONResponse({"error": {"message": str(e), "type": "invalid_request_error"}}, status_code=400), {"ms": 0}

    # 按分组挑密钥：sub2api 系上游的 key 是绑分组的（gemini / gpt 常常不同组），
    # 所以这里先按模型算出该用哪一组，再在该组内轮换（挑不出来时逐级退让，见 protocols.pick_keys）。
    entries = protocols.pick_keys(p, body.get("model"), (meta or {}).get("up_model"))
    if not entries:
        return JSONResponse({"error": {"message": f"渠道 '{provider}' 未配置 API key",
                                       "type": "invalid_request_error"}}, status_code=503), {"ms": 0}

    tried: set[int] = set()
    last: tuple[int, str] | None = None
    for attempt in range(1, MAX_KEY_ATTEMPTS + 1):
        pick = _next_key(provider, entries, tried)
        if not pick:
            break
        idx, secret = pick["idx"], pick["key"]
        tried.add(idx)
        headers = protocols.auth_headers((meta or {}).get("auth_mode") or p.get("auth_mode") or "bearer", secret)
        try:
            status, up_json, up_text = protocols.call_upstream(url, headers, up_body)
        except Exception as e:
            _cooldown_key(provider, idx, None, attempt)
            last = (502, f"upstream request failed: {e!r}")
            continue

        if status >= 400:
            msg = up_text[:800]
            if isinstance(up_json, dict):
                msg = json.dumps(up_json.get("error") or up_json.get("detail") or up_json,
                                 ensure_ascii=False)[:800]
            _cooldown_key(provider, idx, status, attempt)
            last = (status, msg)
            if status in RETRYABLE and attempt < MAX_KEY_ATTEMPTS:
                continue
            if do_log:
                store.log_row(provider, body.get("model"), f"/up/{provider}/v1/images", status, status,
                              int((time.time() - t0) * 1000), msg, body, up_body, up_text,
                              kind=log_kind, attempts=attempt, key_index=idx,
                              token=tk_name, token_id=tk_id,
                              up_url=url, up_method="POST", up_headers=_redact_headers(headers, secret),
                              imagehost=(meta or {}).get("imagehost"))
            if _countable_failure(status):          # 渠道级失败计数（够数自动停用）
                store.bump_provider_fail(provider, f"上游 {status}: {msg[:160]}", disconnect=True)
            return JSONResponse({"error": {"message": f"upstream {status}: {msg}", "type": "upstream_error",
                                           "attempts": attempt, "provider": provider}}, status_code=status), \
                   {"ms": int((time.time() - t0) * 1000), "upstream_status": status}

        # ---- 成功 ----
        out = _shape_success(p, body, meta, up_json, headers, provider, t0)
        if isinstance(out, JSONResponse):
            return out, {"ms": int((time.time() - t0) * 1000), "upstream_status": status}
        images = len((out or {}).get("data") or []) or int(body.get("n") or 1)
        cost, currency = store.estimate_cost(provider, body.get("model") or "", images)
        store.clear_provider_fail(provider)          # 成功一次 → 失败计数清零
        store.bump_token_usage(tk_id, images=images, cost=cost)
        if do_log:
            store.log_row(provider, body.get("model"), f"/up/{provider}/v1/images", 200, status,
                          int((time.time() - t0) * 1000), None, body, up_body,
                          json.dumps(out, ensure_ascii=False)[:1200], kind=log_kind,
                          attempts=attempt, key_index=idx, images=images, cost=cost, cost_currency=currency,
                          token=tk_name, token_id=tk_id,
                          up_url=url, up_method="POST", up_headers=_redact_headers(headers, secret),
                          imagehost=(meta or {}).get("imagehost"))
        return out, {"ms": int((time.time() - t0) * 1000), "upstream_status": status,
                     "images": images, "cost": cost, "currency": currency, "attempts": attempt,
                     # 上游报文/地址：供路由汇总行落库，让客户端那一行也能看到「② 本网关 → 上游」
                     "up_url": url, "up_method": "POST", "up_body": up_body,
                     "up_headers": _redact_headers(headers, secret),
                     **_size_info(meta), **_imagehost_info(meta)}

    msg = f"渠道 '{provider}' 所有 key 均不可用或已进冷却"
    if last:
        msg = f"{msg}；最后一次错误 {last[0]}: {last[1][:300]}"
    if do_log:
        store.log_row(provider, body.get("model"), f"/up/{provider}/v1/images", 503,
                      last[0] if last else None, int((time.time() - t0) * 1000), msg, body, None, None,
                      kind=log_kind, attempts=len(tried), token=tk_name, token_id=tk_id,
                      up_url=url, up_method="POST")
    if tried:      # 真打过上游才算一次失败：全部 key 在冷却里的「短路」不该重复计数，
                   # 否则一串请求（或探针）就能把整条渠道熔断掉。
        store.bump_provider_fail(provider, msg, disconnect=True)
    return JSONResponse({"error": {"message": msg, "type": "upstream_error", "provider": provider,
                                   "attempts": len(tried)}}, status_code=503), {"ms": int((time.time() - t0) * 1000)}




# ------------------------------------------------------------------ 路由链

def resolve_chain(model: str, exclude: set[str] | None = None) -> list[dict]:
    """给一个模型算出「可用渠道实例」列表：显式路由规则优先，否则按优先级自动排。

    隐式自动路由的规则：所有启用了该模型（model_map 里有这个客户端模型名）的渠道实例，
    按 priority 从大到小；显式规则（routes 表）则完全按你写的顺序。
    """
    exclude = exclude or set()
    _auto_recover()                         # 冷却到期的「自动停用」渠道：探活通过才放回路由
    allowed = store.get_chain(model)
    out: list[dict] = []
    if allowed:
        for key in allowed:
            if key in exclude:
                continue
            p = store.get_provider(key)
            if p and p.get("enabled"):
                out.append(p)
        return out
    for p in store.list_providers(only_enabled=True):
        if p["key"] in exclude:
            continue
        # 通配符规则也算「这个渠道支持该模型」（gemini-3* 命中 gemini-3-pro-image）
        if model and protocols.match_model(p, model) in (p.get("model_map") or {}):
            full = store.get_provider(p["key"])
            if full:
                out.append(full)
    return _weighted_order(out)


_LAST_RECOVER = [0.0]


def _auto_recover() -> list[str]:
    """自动熔断恢复：冷却到期且零成本探活通过的渠道，自动放回路由。

    借鉴 New API 的「自动启用」+ 本服务的零成本探活语义（4xx=链路可达，5xx=上游异常）。
    每 60 秒最多跑一次，避免每个请求都去打上游。
    """
    now = time.time()
    if now - _LAST_RECOVER[0] < 60:
        return []
    _LAST_RECOVER[0] = now
    out: list[str] = []
    for key in store.auto_recover_candidates():
        p = store.get_provider(key)
        if not p:
            continue
        if p.get("site_id"):               # 余额熔断的：等余额恢复，不做探活
            continue
        try:
            res = selftest_provider(p)
        except Exception:
            res = {"ok": 0}
        if res.get("ok") == 1:
            store.enable_provider(key)
            store.set_health(key, res)
            out.append(key)
        else:
            store.extend_cooldown(key, store.AUTO_RECOVER_SEC)
    return out


def _weighted_order(providers: list[dict]) -> list[dict]:
    """同优先级的渠道按权重随机排序（借鉴 New API 的「渠道加权随机」）。

    优先级是硬门槛（高优先级永远在前），权重只决定同一优先级内的先后；
    排序结果仍然是「故障切换链」的完整顺序，只是同档内谁先试由权重决定。
    """
    buckets: dict[int, list[dict]] = {}
    for p in providers:
        buckets.setdefault(int(p.get("priority") or 0), []).append(p)
    out: list[dict] = []
    for prio in sorted(buckets.keys(), reverse=True):
        group = buckets[prio]
        if len(group) == 1:
            out += group
            continue
        pool = list(group)
        while pool:
            total = sum(max(1, int(x.get("weight") or 1)) for x in pool)
            r = random.uniform(0, total)
            acc = 0.0
            for i, x in enumerate(pool):
                acc += max(1, int(x.get("weight") or 1))
                if r <= acc:
                    out.append(pool.pop(i))
                    break
    return out


def _handle(provider: str, request: Request, edit: bool, dry: bool = False, probe: bool = False):
    """单渠道直连入口：/up/<渠道实例>/v1/images/..."""
    p = store.get_provider(provider)
    if not p:
        return JSONResponse({"error": {"message": f"unknown provider '{provider}'",
                                       "type": "invalid_request_error"}}, status_code=404)
    if not p.get("enabled"):
        # 停用的渠道不参与路由，直连面也不放行 —— 但要说清是「停用」而不是「不存在」，
        # 否则排障时会误以为渠道被删了（历史上有过这个困惑）。
        return JSONResponse({"error": {"message": f"渠道 '{provider}' 已停用（在控制台「渠道实例」里启用后再试）",
                                       "type": "invalid_request_error"}}, status_code=503)
    access, deny = _authorize(request)
    if deny:
        return deny

    ch = channels.get(p.get("protocol") or "")
    op = "edit" if edit else "generate"
    if ch and not ch.supports(op) and not probe:
        return JSONResponse({"error": {"message": f"渠道 '{provider}'（{ch.label}）不支持图片{'编辑' if edit else '生成'}",
                                       "type": "invalid_request_error",
                                       "operations": ch.info()["operations"]}}, status_code=400)

    body: dict = dict(getattr(request.state, "json_body", {}) or {})
    body = _merge_files(body)
    if not body.get("model"):
        body["model"] = protocols.default_model(p)
    body["model"] = protocols.match_model(p, body.get("model") or "")   # 大小写/别名归一
    scope = _guard_scope(access, body.get("model") or "", provider)
    if scope:
        return scope
    if probe:
        body["prompt"] = body.get("prompt") or "probe"
        body.setdefault("size", "1024x1024")

    if probe:
        return JSONResponse(selftest_provider(p, body.get("model")))

    url, up_body, meta = None, None, {}
    if dry:
        try:
            url, up_body, meta = prepare(p, body, edit)
        except ValueError as e:
            return JSONResponse({"error": {"message": str(e), "type": "invalid_request_error"}}, status_code=400)
        shown = dict(up_body)
        if "contents" in shown:
            shown["contents"] = f"<{len(json.dumps(up_body.get('contents'), ensure_ascii=False))} 字节 contents（含参考图 base64）>"
        return JSONResponse({"dry_run": True, "provider": provider, "plugin": p.get("protocol"),
                             "operations": ch.info()["operations"] if ch else [],
                             "url": url, "upstream_body": shown, "meta": meta})

    # 直连面同样过并发闸门：拿不到槽位就 503 + Retry-After，别把上游打爆
    limit = gate.limit_of(p)
    q0 = time.time()
    rej = gate.acquire(provider, limit)
    if rej:
        return JSONResponse({"error": {"message": f"渠道 '{provider}' 并发已满：{rej}",
                                       "type": "rate_limit_error", "provider": provider}},
                            status_code=503, headers={"Retry-After": "3"})
    queue_ms = int((time.time() - q0) * 1000)
    try:
        out, info = invoke_provider(p, body, edit, access=access, log_kind="probe" if probe else "relay")
    finally:
        gate.release(provider)
    if isinstance(out, JSONResponse):
        return out
    resp = JSONResponse(protocols.unify_model(p, body, out))
    resp.headers["X-QLike-Provider"] = provider
    resp.headers["X-QLike-Attempt"] = "1"
    resp.headers["X-QLike-Queue-Ms"] = str(queue_ms)
    if info.get("size_hdr"):
        resp.headers["X-QLike-Size"] = info["size_hdr"]      # 尺寸被换过 → 明确告诉客户端
    if info.get("imagehost"):
        resp.headers["X-QLike-Imagehost"] = info["imagehost"]  # 参考图走了图床 → 告诉客户端
    return resp


def _tiers(chain: list[dict]) -> list[list[dict]]:
    """把候选链按优先级分档（chain 已按「优先级降序 + 档内加权随机」排好）。"""
    out: list[list[dict]] = []
    for p in chain:
        pr = int(p.get("priority") or 0)
        if out and int(out[-1][0].get("priority") or 0) == pr:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def _tier_retry(tier: list[dict]) -> int:
    """同档重试次数：取该档渠道里最大的 options.retry（借鉴 New API priorities[retry] 的逐档降级）。

    默认 0 = 同档不重试，失败立刻降到下一档（图片生成重试有重复扣费风险，默认保守）。
    """
    n = 0
    for p in tier:
        try:
            n = max(n, int((p.get("options") or {}).get("retry") or 0))
        except Exception:
            continue
    return max(0, min(n, 2))


def _attempt_sequence(tiers: list[list[dict]]) -> list[tuple[dict, int]]:
    """展开成 (渠道, 档位) 的尝试序列：同一档先重试 retry 轮，再降到下一档。"""
    seq: list[tuple[dict, int]] = []
    for ti, tier in enumerate(tiers):
        for _ in range(1 + _tier_retry(tier)):
            for p in tier:
                seq.append((p, ti))
    return seq[:MAX_ROUTE_ATTEMPTS]


def _handle_router(request: Request, edit: bool):
    """统一入口 /v1/images/...：按模型路由到各上游渠道实例。

    阶段 1 语义：优先级分档 → 同档内按权重分流（档内 retry 次重试）→ 档失败则降级到下一档；
    每档进上游前过并发闸门（槽位/排队），某家排队超时先换下一家（降低单点依赖）；
    路由决策通过 X-QLike-* 响应头回给客户端，可观测、可对账。
    """
    access, deny = _authorize(request)
    if deny:
        return deny
    body: dict = dict(getattr(request.state, "json_body", {}) or {})
    body = _merge_files(body)
    model = (body.get("model") or "").strip()
    t0 = time.time()
    if not model:
        return JSONResponse({"error": {"message": "model is required（统一入口必须带模型名，路由层按它选上游）",
                                       "type": "invalid_request_error"}}, status_code=400)
    chain = resolve_chain(model)
    if not chain:
        store.log_row("-", model, "/v1/images", 404, None, 0,
                      f"没有渠道支持模型 '{model}'", body, None, None, kind="client",
                      token=(access or {}).get("name"), token_id=(access or {}).get("id"))
        return JSONResponse({"error": {"message": f"没有渠道实例支持模型 '{model}'（可在控制台「渠道实例」里配置模型映射）",
                                       "type": "invalid_request_error"}}, status_code=404)

    tiers = _tiers(chain)
    seq = _attempt_sequence(tiers)
    chain_names = [p["key"] for p in chain]
    tried: list[str] = []
    saturated: list[str] = []
    last: tuple[str, int, str] | None = None
    attempt = 0
    for p, tier_idx in seq:
        body["model"] = protocols.match_model(p, model)      # 大小写不敏感 + 别名归一
        scope = _guard_scope(access, body["model"], p["key"])
        if scope:
            return scope
        limit = gate.limit_of(p)
        q0 = time.time()
        rej = gate.acquire(p["key"], limit)
        if rej:
            # 这家排队等不到槽位 → 不硬等，直接换下一家（这就是「降低单点依赖」）
            saturated.append(f"{p['key']}（{rej}）")
            continue
        queue_ms = int((time.time() - q0) * 1000)
        try:
            attempt += 1
            out, info = invoke_provider(p, body, edit, access=access, log_kind="relay")
        finally:
            gate.release(p["key"])
        if not isinstance(out, JSONResponse):
            snippet = {"chain": chain_names, "attempt": attempt, "degrade": tier_idx,
                       "queue_ms": queue_ms, "ok": p["key"]}
            if tried:
                snippet["failed_over_from"] = tried
            if saturated:
                snippet["saturated"] = saturated
            if info.get("size_note"):
                snippet["size"] = info["size_note"]
            store.log_row("-", model, "/v1/images", 200, None, int((time.time() - t0) * 1000),
                          None, body, info.get("up_body"), json.dumps(snippet, ensure_ascii=False),
                          kind="router", attempts=attempt, key_index=None,
                          up_url=info.get("up_url"), up_method=info.get("up_method"),
                          up_headers=info.get("up_headers"),
                          imagehost=info.get("imagehost_raw"))
            resp = JSONResponse(protocols.unify_model(p, body, out, client_model=model))
            resp.headers["X-QLike-Provider"] = p["key"]        # 方便和 New API 日志对账
            resp.headers["X-QLike-Failover"] = str(len(tried))
            resp.headers["X-QLike-Chain"] = ",".join(chain_names)
            resp.headers["X-QLike-Attempt"] = str(attempt)
            resp.headers["X-QLike-Degrade"] = str(tier_idx)     # 0 = 首档即成功，没降级
            if info.get("imagehost"):
                resp.headers["X-QLike-Imagehost"] = info["imagehost"]
            resp.headers["X-QLike-Queue-Ms"] = str(queue_ms)
            if info.get("size_hdr"):
                resp.headers["X-QLike-Size"] = info["size_hdr"]
            return resp
        tried.append(p["key"])
        try:
            detail = json.loads(out.body.decode()).get("error", {})
        except Exception:
            detail = {}
        status = out.status_code
        last = (p["key"], status, str(detail.get("message") or ""))
        if not _retryable(p, status):    # 非渠道类错误（请求本身有问题）直接返回，避免无谓重试
            return out

    if not tried and saturated:
        # 所有候选都在排队/满队，一次上游都没打出去 —— 明确告诉客户端退避重试
        store.log_row("-", model, "/v1/images", 503, None, int((time.time() - t0) * 1000),
                      "所有候选渠道并发已满：" + "；".join(saturated)[:300], body, None,
                      json.dumps({"saturated": saturated}, ensure_ascii=False), kind="router")
        resp = JSONResponse({"error": {"message": "所有候选渠道并发已满，请稍后重试（" + "；".join(saturated)[:200] + "）",
                                       "type": "rate_limit_error", "saturated": saturated}},
                            status_code=503)
        resp.headers["Retry-After"] = "3"
        resp.headers["X-QLike-Chain"] = ",".join(chain_names)
        return resp

    store.log_row("-", model, "/v1/images", last[1] if last else 503, last[1] if last else None,
                  int((time.time() - t0) * 1000),
                  f"路由链全部失败：{last[2][:300] if last else ''}", body, None,
                  json.dumps({"tried": tried, "chain": chain_names, "saturated": saturated},
                             ensure_ascii=False), kind="router", attempts=attempt)
    resp = JSONResponse({"error": {"message": f"该模型的所有上游渠道都失败了（依次尝试：{', '.join(tried)}）；"
                                              f"最后一次：{last[2][:200] if last else ''}",
                                   "type": "upstream_error", "tried": tried}},
                        status_code=last[1] if last and last[1] >= 400 else 503)
    resp.headers["X-QLike-Chain"] = ",".join(chain_names)
    resp.headers["X-QLike-Attempt"] = str(attempt)
    return resp


# ------------------------------------------------------------------ 路由

@router.get("/{provider}/v1/models")
def up_models(provider: str, request: Request):
    p = store.get_provider(provider, with_keys=False)
    if not p or not p.get("enabled"):
        return JSONResponse({"error": {"message": f"unknown provider '{provider}'"}}, status_code=404)
    access, deny = _authorize(request)
    if deny:
        return deny
    return {"object": "list", "data": [{"id": m["id"], "object": "model", "owned_by": "qlikeapi-plugins",
                                        "upstream": m["upstream"]} for m in protocols.model_list(p)]}


@router.post("/{provider}/v1/images/generations")
def up_generations(provider: str, request: Request):
    return _handle(provider, request, edit=False)


@router.post("/{provider}/v1/images/edits")
def up_edits(provider: str, request: Request):
    return _handle(provider, request, edit=True)


@router.post("/{provider}/v1/images/preview")
def up_preview(provider: str, request: Request):
    return _handle(provider, request, edit=False, dry=True)


@router.post("/{provider}/v1/images/edits/preview")
def up_edits_preview(provider: str, request: Request):
    """改图面（带参考图）的 dry-run：只回「将要发给上游的请求」，不发出去。
    修「客户端把 n 发成字符串」那类问题时，就是靠它零成本看到真实报文。"""
    return _handle(provider, request, edit=True, dry=True)


@router.post("/{provider}/v1/images/selftest")
def up_selftest(provider: str, request: Request, model: str | None = None):
    return _handle(provider, request, edit=False, probe=True)


PROBE_TIMEOUT = float(os.getenv("QLIKEAPI_PROBE_TIMEOUT", "12"))
# 探活时必须清空的字段（只要这些字段里有内容，上游就可能真的开始出图 → 花钱）
_PROBE_BLANK = {"prompt", "prompt_text", "text", "content", "contents", "parts", "input", "inputs",
                "image", "images", "mask", "init_image", "reference_images"}


def blank_for_probe(v: Any) -> Any:
    """递归清空请求体里一切「会让上游真的出图」的内容，只留模型名/尺寸这类结构。

    ⚠ 这是零成本探活的安全底线：不能只按 protocol 判断（合并插件如 change2pro 会按模型名
      分流到 gemini/openai 两套协议，漏判一次就是一次真出图真扣费）。
    """
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for k, x in v.items():
            lk = str(k).lower()
            if lk in _PROBE_BLANK:
                out[k] = "" if isinstance(x, str) else ([] if isinstance(x, (list, tuple)) else x)
            else:
                out[k] = blank_for_probe(x)
        return out
    if isinstance(v, list):
        return [blank_for_probe(x) for x in v]
    return v


def selftest_provider(p: dict, model: str | None = None) -> dict:
    """零成本探活：把 prompt 内容全部清空后打上游 —— 上游必然拒绝（4xx），
    于是「能不能连上 / 鉴权对不对 / 模型名认不认」全都验到了，且绝不会真的出图。

    判定：4xx = 链路通（含鉴权、模型名）；5xx = 上游异常；超时 = 上游挂起（也算不通）。
    """
    m = model or protocols.default_model(p)
    body: dict[str, Any] = {"model": m, "prompt": "probe", "size": "1024x1024", "quality": "standard"}
    _ch = channels.get(p.get("protocol") or "")
    if _ch and _ch.route_mode("generate") == "native":
        body["response_format"] = "b64_json"
    try:
        url, up_body, meta = prepare(p, body, False)
    except Exception as e:
        res = {"ok": False, "model": m, "error": repr(e)}
        _log_probe(p, m, url="", res=res)
        return res
    up_body = blank_for_probe(up_body)          # ← 安全底线：任何插件都不可能漏
    entries = protocols.pick_keys(p, m, (meta or {}).get("up_model"))
    if not entries:
        res = {"ok": False, "model": m, "error": "no api key"}
        _log_probe(p, m, url, res, up_body=up_body)
        return res
    secret = entries[0]["key"]
    headers = protocols.auth_headers((meta or {}).get("auth_mode") or p.get("auth_mode") or "bearer", secret)
    t0 = time.time()
    try:
        status, up_json, up_text = protocols.call_upstream(url, headers, up_body, timeout=PROBE_TIMEOUT)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        timeout = "Timeout" in type(e).__name__
        res = {"ok": False, "reachable": False, "model": m, "url": url, "ms": ms,
               "timeout": timeout,
               "error": ("探活超时（%ds 内上游没回应，说明这条链路上该模型名/路径不被接受或上游挂起）" % int(PROBE_TIMEOUT))
                        if timeout else repr(e)}
        _log_probe(p, m, url, res, ms, up_body=up_body, secret=secret)
        return res
    msg = up_text[:600]
    if isinstance(up_json, dict):
        msg = json.dumps(up_json.get("error") or up_json.get("detail") or up_json, ensure_ascii=False)[:600]
    reachable = status < 500
    res = {"ok": reachable, "reachable": reachable, "model": m, "url": url, "upstream_status": status,
           "ms": int((time.time() - t0) * 1000), "upstream_message": msg,
           "note": "已清空 prompt 的探活：4xx = 链路/鉴权/模型名都通（上游只是拒绝了空请求）；5xx = 上游异常；超时 = 上游挂起。零成本"}
    _log_probe(p, m, url, res, up_body=up_body, secret=secret)
    return res


def _log_probe(p: dict, model: str, url: str, res: dict, ms: int | None = None,
               up_body: dict | None = None, secret: str | None = None) -> None:
    """探活也写进请求日志（类型=探活），便于和「真调用」对账。

    和真调用一样记完整请求（URL / 方法 / 请求头占位），这样日志详情里的 curl 对探活也可用。
    """
    red = None
    try:
        ent = ([{"key": secret}] if secret else protocols.pick_keys(p, model)) or []
        if ent:
            red = _redact_headers(protocols.auth_headers(p.get("auth_mode") or "bearer", ent[0]["key"]),
                                  ent[0]["key"])
    except Exception:
        red = None
    try:
        store.log_row(p.get("key") or "", model, "/probe", 200 if res.get("ok") else 502,
                      res.get("upstream_status"), ms if ms is not None else res.get("ms") or 0,
                      None if res.get("ok") else (res.get("error") or res.get("upstream_message")),
                      json.dumps({"probe": True, "model": model}, ensure_ascii=False),
                      json.dumps(up_body, ensure_ascii=False) if up_body else "", "",
                      kind="probe", up_url=url or None, up_method="POST" if url else None,
                      up_headers=red)
    except Exception:
        pass


# ------------------------------------------------------------------ 统一入口（New API 只需挂这一个渠道）

router_v1 = APIRouter()


@router_v1.post("/images/generations")
def v1_generations(request: Request):
    """客户端/New API 统一入口：按模型自动路由到各上游渠道实例，失败自动切换。"""
    return _handle_router(request, edit=False)


@router_v1.post("/images/edits")
def v1_edits(request: Request):
    return _handle_router(request, edit=True)


@router_v1.get("/models")
def v1_models(request: Request):
    """统一模型清单：各渠道实例对外暴露的模型并集（令牌只看到自己能用那些）。"""
    access, deny = _authorize(request)
    if deny:
        return deny
    row = (access or {}).get("row") or {}
    limit_models = store._json_or(row.get("allowed_models"), [])
    limit_provs = store._json_or(row.get("allowed_providers"), [])
    seen: dict[str, dict] = {}
    for p in store.list_providers(only_enabled=True):
        if limit_provs and p["key"] not in limit_provs:
            continue
        for m in protocols.model_list(p):
            if limit_models and m["id"] not in limit_models:
                continue
            cur = seen.setdefault(m["id"], {"id": m["id"], "object": "model", "owned_by": "qlikeapi-plugins",
                                            "providers": []})
            cur["providers"].append(p["key"])
    return {"object": "list", "data": list(seen.values())}


@router_v1.get("/route-preview")
def v1_route_preview(request: Request, model: str):
    """零成本干跑：这个模型会依次走哪些渠道（不发上游）。"""
    access, deny = _authorize(request)
    if deny:
        return deny
    scope = _guard_scope(access, model)
    if scope:
        return scope
    chain = resolve_chain(model)
    explicit = store.get_chain(model)
    return {"model": model,
            "rule": "explicit" if explicit else "auto(按优先级)",
            "chain": [{"provider": p["key"], "label": p["label"], "priority": p.get("priority") or 0,
                       "plugin": p.get("protocol"), "url": p.get("base_url"),
                       "keys": len(store.provider_keys(p)),
                       "key_groups": sorted({e.get("label") or "（未分组）" for e in store.key_entries(p)})} for p in chain],
            "note": "这是路由顺序；实际会从第一个开始，遇到上游不可用（429/5xx/超时/连不上）自动换下一个"}
