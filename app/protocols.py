"""protocols.py —— 三种上游协议的可复用实现。

渠道插件可以直接调用这里的函数，也可以自己写一份（比如某家上游的怪癖太多）。
  · gemini_native : OpenAI 图片请求 ⇄ Gemini v1beta generateContent
  · openai_images : 标准 OpenAI 图片面（字段纠偏后透传）
  · fal_queue     : fal 队列协议（提交 → 轮询 → 取结果 URL）
"""
from __future__ import annotations

import os
import time
import urllib.parse
from typing import Any

import httpx

from . import store, utils

TIMEOUT = float(os.environ.get("QLIKEAPI_TIMEOUT", "900"))
POLL_INTERVAL = float(os.environ.get("QLIKEAPI_POLL_INTERVAL", "3"))
POLL_MAX = float(os.environ.get("QLIKEAPI_POLL_MAX", "600"))
HTTP = httpx.Client(timeout=httpx.Timeout(TIMEOUT, connect=20))


# ------------------------------------------------------------------ 通用

def auth_headers(auth_mode: str, secret: str) -> dict:
    if auth_mode == "x-goog-api-key":
        return {"x-goog-api-key": secret, "Content-Type": "application/json"}
    if auth_mode == "fal_key":
        return {"Authorization": f"Key {secret}", "Content-Type": "application/json"}
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


def mask_headers(headers: dict) -> dict:
    return {k: (str(v)[:8] + "…") if k.lower() in ("authorization", "x-goog-api-key") else v
            for k, v in headers.items()}


def call_upstream(url: str, headers: dict, body: dict, timeout: float | None = None) -> tuple[int, Any, str]:
    """打上游。timeout 只给探活用（正常转发走客户端长超时，图片生成可能几十秒）。"""
    kw = {"timeout": timeout} if timeout else {}
    r = HTTP.post(url, headers=headers, json=body, **kw)
    try:
        return r.status_code, r.json(), r.text
    except Exception:
        return r.status_code, None, r.text


def prompt_of(body: dict) -> str:
    """取提示词：空串、纯空白（只有空格/换行）一律算「没有提示词」。

    ⚠ 铁律：没有 prompt 必须本地报错，绝不回落默认提示词 ——
      有的上游把空 prompt 当默认提示词直接出图，回落一次就是一次真扣费。
    """
    return str(body.get("prompt") or body.get("text") or "").strip()


def match_model(p: dict, client_model: str) -> str:
    """把客户端传来的模型名归一到 model_map 里的「标准写法」（大小写不敏感、忽略首尾空格）。

    借鉴 done-hub：模型名大小写不敏感，避免客户端写 Gpt-Image-2 就找不到渠道。
    找不到就原样返回（让渠道插件自己去撞上游，报错更直观）。
    """
    mm = p.get("model_map") or {}
    m = (client_model or "").strip()
    if not mm or m in mm:
        return m
    low = m.lower()
    for name in mm:
        if str(name).lower() == low:
            return name
    return m


def resolve_model(p: dict, client_model: str) -> dict:
    """渠道实例的 model_map：客户端模型名 → 上游真实名（+ fal 端点）。"""
    entry = (p.get("model_map") or {}).get(match_model(p, client_model))
    if isinstance(entry, str):
        return {"upstream": entry}
    if isinstance(entry, dict):
        return dict(entry)
    return {"upstream": client_model}


def unify_model(p: dict, body: dict, payload: Any, client_model: str = "") -> Any:
    """统一模型名：客户端看到的 model 永远是它自己请求的那个名字（借鉴 done-hub）。

    渠道实例 options.unify_model=false 可关掉（保留上游真实模型名，便于排查）。
    """
    if not isinstance(payload, dict):
        return payload
    if (p.get("options") or {}).get("unify_model") is False:
        return payload
    want = client_model or match_model(p, body.get("model") or "") or body.get("model")
    if want and "model" in payload:
        payload["model"] = want
    elif want:
        payload["model"] = want
    return payload


def set_path(obj: Any, path: str, value: Any) -> None:
    """按点号路径写值，支持列表下标：generationConfig.imageConfig.imageSize / data.0.url"""
    keys = str(path).split(".")
    cur = obj
    for k in keys[:-1]:
        if isinstance(cur, list):
            cur = cur[int(k)]
        elif isinstance(cur, dict):
            cur = cur.setdefault(k, {})
        else:
            return
    last = keys[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    elif isinstance(cur, dict):
        cur[last] = value


def remove_path(obj: Any, path: str) -> bool:
    """按点号路径删字段，支持列表（用 * 或数字下标）：generationConfig.thinkingConfig / data.*.revised_prompt

    借鉴 done-hub 的 remove_params：New API 只支持顶层字段，这里支持嵌套。
    """
    keys = str(path).split(".")

    def rec(cur: Any, i: int) -> bool:
        if i >= len(keys):
            return False
        k, last = keys[i], i == len(keys) - 1
        if isinstance(cur, list):
            if k == "*":
                if last:
                    cur.clear()
                    return True
                ok = False
                for it in list(cur):
                    ok = rec(it, i + 1) or ok
                return ok
            try:
                idx = int(k)
            except Exception:
                return False
            if last:
                try:
                    cur.pop(idx)
                    return True
                except Exception:
                    return False
            try:
                return rec(cur[idx], i + 1)
            except Exception:
                return False
        if isinstance(cur, dict):
            if last:
                if k in cur:
                    cur.pop(k, None)
                    return True
                return False
            if k not in cur:
                return False
            return rec(cur[k], i + 1)
        return False

    try:
        return rec(obj, 0)
    except Exception:
        return False


def apply_removals(obj: Any, paths: list[str] | None) -> list[str]:
    """批量删除字段，返回真正删掉的路径清单（写进日志方便对账）。"""
    hit = []
    for path in paths or []:
        try:
            if remove_path(obj, path):
                hit.append(path)
        except Exception:
            pass
    return hit


def upstream_model(p: dict, client_model: str) -> str:
    return resolve_model(p, client_model).get("upstream") or client_model


def default_model(p: dict) -> str:
    return next(iter(p.get("model_map") or {}), "")


def model_list(p: dict) -> list[dict]:
    """渠道对外暴露的模型清单。同一平台里重复命名的模型只留一条（借鉴 New API 的去重口径）。"""
    out = []
    seen: set[str] = set()
    for name, entry in (p.get("model_map") or {}).items():
        name = str(name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        up = entry.get("upstream") if isinstance(entry, dict) else entry
        out.append({"id": name, "upstream": up or name, "aliased": bool(up and up != name)})
    return out


# ------------------------------------------------------------------ 上游模型列表

MODELS_PATHS = ("/v1/models", "/models")


def model_ids_from(payload: Any) -> list[str]:
    """从各种上游返回形态里抽模型名，并去重（OpenAI 的 {data:[{id}]}、裸数组、{models:[...]} 都认）。"""
    items: list[Any] = []
    if isinstance(payload, dict):
        for k in ("data", "models", "result", "items"):
            if isinstance(payload.get(k), list):
                items = payload[k]
                break
        if not items and isinstance(payload.get("model"), str):
            items = [payload["model"]]
    elif isinstance(payload, list):
        items = payload
    out: list[str] = []
    for it in items:
        if isinstance(it, str):
            mid = it
        elif isinstance(it, dict):
            mid = it.get("id") or it.get("name") or it.get("model") or it.get("slug") or ""
        else:
            mid = ""
        mid = str(mid).strip()
        if mid and mid not in out:                 # 去重：同一平台重复命名只留一条
            out.append(mid)
    return out


def fetch_upstream_models(p: dict, key: str | None = None, path: str | None = None,
                          timeout: float = 15.0) -> dict:
    """拉取上游模型列表 —— 零成本：只发一个 GET /v1/models，绝不触发任何出图。

    路径优先用渠道 options.models_path，其次依次试 /v1/models、/models；
    返回统一形态 {"ok":bool, "url":str, "http_status":int, "models":[str], "count":int} 或 {"ok":False,"error":str}。
    """
    base = str(p.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "这个渠道还没配 base_url"}
    if not key:
        return {"ok": False, "error": "这个渠道还没配 API key"}
    cand: list[str] = []
    if path:
        cand.append(path)
    else:
        opt = str((p.get("options") or {}).get("models_path") or "").strip()
        if opt:
            cand.append(opt)
    cand += [x for x in MODELS_PATHS if x not in cand]
    headers = auth_headers(p.get("auth_mode") or "bearer", key)
    headers.setdefault("Accept", "application/json")
    errs: list[str] = []
    for pa in cand:
        url = base + ("" if pa.startswith("/") else "/") + pa
        try:
            r = HTTP.get(url, headers=headers, timeout=timeout)
        except Exception as e:
            errs.append(f"{pa}: {e!r}")
            continue
        try:
            payload = r.json()
        except Exception:
            payload = None
        ids = model_ids_from(payload)
        if r.status_code < 400 and ids:
            return {"ok": True, "url": url, "http_status": r.status_code,
                    "models": sorted(ids), "count": len(ids)}
        errs.append(f"{pa}: HTTP {r.status_code} {(r.text or '')[:120]}")
    return {"ok": False, "error": "拉取失败 —— " + "；".join(errs)[:400]}


# ------------------------------------------------------------------ gemini native

def gemini_policy(p: dict) -> str:
    """Gemini 档位策略（渠道实例 options.gemini_size_policy 可覆盖）。

    class（默认，按尺寸档位分类）/ floor（向下取档，最省）/ ceil（向上取档，不降级）/ nearest（取最接近档）。
    """
    v = str((p.get("options") or {}).get("gemini_size_policy") or "class").lower()
    return v if v in ("class", "ceil", "floor", "nearest") else "class"


def discover_key_groups(p: dict, timeout: float = 20.0) -> dict:
    """零成本探测每把密钥的分组：逐把 GET 上游 /v1/models，拿到「这把 key 能用哪些模型」。

    只读接口，不发任何生成请求（sub2api 的 /v1/models 就是按 key 的分组返回可用模型的）。
    返回 {"groups": {标签: [模型…]}, "errors": {标签: 错误}, "plain": [无标签 key 的模型…]}
    """
    groups: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    plain: list[str] = []
    path_hint = str((p.get("options") or {}).get("models_path") or "").strip()
    paths = [path_hint] if path_hint else ["/v1/models", "/models"]
    for e in store.key_entries(p):
        label = e.get("label") or f"#{e['idx'] + 1}"
        ids: list[str] = []
        errs: list[str] = []
        for path in paths:
            url = p["base_url"].rstrip("/") + path
            try:
                headers = auth_headers(p.get("auth_mode") or "bearer", e["key"])
                r = httpx.get(url, headers=headers, timeout=timeout)
            except Exception as ex:                     # 连不上/超时
                errs.append(f"{path}: {type(ex).__name__}")
                continue
            if r.status_code >= 400:
                errs.append(f"{path}: HTTP {r.status_code} {(r.text or '')[:80]}")
                continue
            try:
                j = r.json()
            except Exception:
                errs.append(f"{path}: 非 JSON")
                continue
            items = j.get("data") if isinstance(j, dict) else None
            if not isinstance(items, list):
                items = (j.get("models") if isinstance(j, dict) else None) or []
            for it in items:
                mid = it.get("id") or it.get("name") if isinstance(it, dict) else str(it)
                if mid and str(mid) not in ids:
                    ids.append(str(mid))
            if ids:
                break
        if ids:
            groups[label] = ids
            if not e.get("label"):
                plain = ids
        elif errs:
            errors[label] = "；".join(errs)[:200]
    return {"groups": groups, "errors": errors, "plain": plain}


def key_group(p: dict, model: str | None, up_model: str | None = None) -> str | None:
    """这个模型该用哪个分组标签的密钥（None = 不限分组）。

    解析顺序：
      ① options.key_groups —— 手写规则，`{"gemini-*": "gemini", "gpt-image-2": "gpt"}`（支持 * ? 通配）；
      ② options.key_models —— 「探测各密钥分组」自动发现的结果，`{"gemini": ["gemini-3.1-flash-image", ...]}`；
      ③ 都没命中 → None（用没写标签的 key；再没有就退化成所有 key，与旧行为一致）。
    """
    opts = p.get("options") or {}
    names = [str(x).lower() for x in (model, up_model) if x]
    for pat, label in (opts.get("key_groups") or {}).items():
        if any(utils.glob_match(pat, n) for n in names):
            return str(label)
    for label, models in (opts.get("key_models") or {}).items():
        ids = [str(m).lower() for m in (models or [])]
        for n in names:
            if n in ids or any(utils.glob_match(i, n) for i in ids):
                return str(label)
    return None


def pick_keys(p: dict, model: str | None = None, up_model: str | None = None) -> list[dict]:
    """按分组挑出该模型可用的密钥条目（保持顺序）。挑不出来时逐级退让，绝不因配置不全而断路。"""
    entries = store.key_entries(p)
    if not entries:
        return []
    label = key_group(p, model, up_model)
    if label:
        hit = [e for e in entries if e.get("label") == label]
        if hit:
            return hit
    plain = [e for e in entries if not e.get("label")]
    return plain or entries


def size_mode(p: dict) -> str:
    """尺寸处理方式（渠道实例 options.size_mode 可覆盖）。

    snap（默认）：按官方约束最小改动吸附，非法尺寸只修不合法的那一边；
    passthrough：一个像素都不改，原样发给上游（上游实际接受更大尺寸时用这个）。
    """
    v = str((p.get("options") or {}).get("size_mode") or "snap").lower()
    return v if v in ("snap", "passthrough") else "snap"


def _size_meta(from_s: str, to_s: str, note: str, extra: str = "") -> dict:
    """尺寸换算的对外呈现：中文说明给面板/日志，纯 ASCII 给响应头（HTTP 头不能放中文）。"""
    if from_s == to_s and not extra:
        return {}
    hdr = f"{from_s}->{to_s}" + (f" ({extra})" if extra else "")
    return {"size_from": from_s, "size_to": to_s, "size_note": note, "size_hdr": hdr}


def build_gemini_native(p: dict, body: dict, edit: bool = False) -> tuple[str, dict, dict]:
    model = body.get("model") or ""
    up_model = upstream_model(p, model)
    parts: list[dict] = []
    prompt = prompt_of(body)
    if prompt:
        parts.append({"text": prompt})
    refs = 0
    for ref in utils.collect_refs(body):
        got = utils.fetch_as_b64(ref, HTTP)
        if got:
            parts.append({"inlineData": {"mimeType": got[0], "data": got[1]}})
            refs += 1
    if not parts:
        # 铁律：缺 prompt 直接报错，绝不回落默认提示词（那会真出图、真扣费）
        raise ValueError("prompt is required（缺 prompt 时本服务不会回落默认提示词）")
    gen_cfg: dict[str, Any] = {}
    wh = utils.parse_size(body.get("size"))
    plan = None
    if wh:
        plan = utils.gemini_plan(*wh, model=up_model, policy=gemini_policy(p))
        gen_cfg["imageConfig"] = {"aspectRatio": plan["ratio"], "imageSize": plan["tier"]}
    if (p.get("options") or {}).get("image_size_override"):
        gen_cfg.setdefault("imageConfig", {})["imageSize"] = p["options"]["image_size_override"]
    if body.get("safety_tolerance"):
        gen_cfg["safetyTolerance"] = str(body["safety_tolerance"])
    up: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}]}
    if gen_cfg:
        up["generationConfig"] = gen_cfg
    removed = apply_removals(up, (p.get("options") or {}).get("remove_params") or [])
    url = f"{p['base_url'].rstrip('/')}/v1beta/models/{up_model}:generateContent"
    meta = {"up_model": up_model, "refs": refs, "removed": removed}
    if plan:
        meta.update(_size_meta(f"{wh[0]}x{wh[1]}", f"{plan['pixels'][0]}x{plan['pixels'][1]}",
                               plan["note"], f"{plan['ratio']}@{plan['tier']}"))
    return url, up, meta


def parse_gemini_native(payload: Any) -> list[dict]:
    out: list[dict] = []
    for cand in (payload or {}).get("candidates", []) or []:
        for part in (cand.get("content") or {}).get("parts", []) or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                out.append({"b64_json": inline["data"], "mime_type": inline.get("mimeType") or "image/png"})
    return out


# ------------------------------------------------------------------ openai images

def build_openai_images(p: dict, body: dict, edit: bool = False) -> tuple[str, dict, dict]:
    model = body.get("model") or ""
    up_model = upstream_model(p, model)
    if not prompt_of(body):
        # 铁律：缺 prompt 直接报错，绝不把空请求打到上游（有的上游会当成默认提示词照出图）
        raise ValueError("prompt is required（缺 prompt 时本服务不会回落默认提示词）")
    opts = p.get("options") or {}
    nested = list(opts.get("remove_params") or [])          # 支持嵌套：a.b.c、data.*.x
    flat = list(opts.get("drop_fields") or [])              # 兼容旧配置（只支持顶层）
    drop = {"__files", "user", "extra_body", "image_config"} | {f for f in flat if "." not in f and "*" not in f}
    up = {k: v for k, v in body.items() if k not in drop and v is not None}
    up["model"] = up_model
    removed = apply_removals(up, [f for f in flat if "." in f or "*" in f] + nested)
    size_meta: dict = {}
    if up.get("size"):
        dec = utils.snap_size(up["size"], up_model, size_mode(p))
        up["size"] = dec["size"]
        size_meta = _size_meta(dec["original"] or "", dec["size"], dec["note"])
    if "quality" in up:
        up["quality"] = utils.normalize_quality(up["quality"])
    if opts.get("drop_quality"):
        up.pop("quality", None)
    refs = utils.collect_refs(body)
    if refs and "image" not in up and "images" not in up:
        up["image"] = refs
    for k, v in (opts.get("force_fields") or {}).items():   # 渠道实例级的强制字段
        up[k] = v
    path = (opts.get("edits_path") if edit else opts.get("generations_path")) or \
           ("/v1/images/edits" if edit else "/v1/images/generations")
    return p["base_url"].rstrip("/") + path, up, {"up_model": up_model, "removed": removed, **size_meta}


# ------------------------------------------------------------------ fal queue

def fal_endpoints(p: dict, model: str, edit: bool) -> tuple[str, str, str]:
    entry = resolve_model(p, model)
    up_model = entry.get("upstream") or model
    submit = entry.get("submit_edit" if edit else "submit") or entry.get("submit") or f"/queue/{up_model}"
    poll = entry.get("poll_base") or entry.get("submit") or f"/queue/{up_model}"
    return up_model, submit, poll


def build_fal_queue(p: dict, body: dict, edit: bool = False) -> tuple[str, dict, dict]:
    model = body.get("model") or ""
    if not prompt_of(body):
        raise ValueError("prompt is required（缺 prompt 时本服务不会回落默认提示词）")
    up_model, submit_path, poll_base = fal_endpoints(p, model, edit)
    wh = utils.parse_size(body.get("size"))
    is_gpt = "gpt" in up_model.lower()
    up: dict[str, Any] = {"prompt": prompt_of(body)}
    urls = [r for r in utils.collect_refs(body) if r.startswith("http")]
    if edit or urls:
        if not urls:
            raise ValueError("fal 异步面的参考图必须是公网 URL（fal 只拉 URL，不接受 base64/本地文件）")
        up["image_urls"] = urls
    size_meta: dict = {}
    if wh:
        if is_gpt:
            dec = utils.snap_size(body.get("size"), up_model, size_mode(p))
            up["image_size"] = dec["size"]
            size_meta = _size_meta(dec["original"] or "", dec["size"], dec["note"])
        else:
            plan = utils.gemini_plan(*wh, model=up_model, policy=gemini_policy(p))
            up["aspect_ratio"] = plan["ratio"]
            up["resolution"] = plan["tier"]
            size_meta = _size_meta(f"{wh[0]}x{wh[1]}", f"{plan['pixels'][0]}x{plan['pixels'][1]}",
                                   plan["note"], f"{plan['ratio']}@{plan['tier']}")
    if is_gpt:
        q = utils.normalize_quality(body.get("quality"))
        if q:
            up["quality"] = q
        if body.get("n"):
            up["num_images"] = max(1, min(int(body["n"]), 4))
        for f in ("background", "output_compression", "output_format"):
            if body.get(f) not in (None, ""):
                up[f] = body[f]
    else:
        for f in ("output_format", "safety_tolerance"):
            if body.get(f) not in (None, ""):
                up[f] = str(body[f]) if f == "safety_tolerance" else body[f]
    base = p["base_url"].rstrip("/")
    return base + submit_path, up, {"up_model": up_model, "poll_base": base + poll_base, **size_meta}


def poll_fal(meta: dict, headers: dict, timeout: float = POLL_MAX) -> tuple[str, Any]:
    """轮询 fal 任务到出结果；返回 (状态, 结果 JSON)。"""
    rid = meta["request_id"]
    h = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    t0 = time.time()
    state = "RUNNING"
    while time.time() - t0 < timeout:
        try:
            sj = HTTP.get(f"{meta['poll_base']}/requests/{urllib.parse.quote(rid)}/status", headers=h).json()
        except Exception:
            sj = {}
        state = str(sj.get("status", "")).upper()
        if state in ("COMPLETED", "SUCCESS", "OK"):
            break
        if state in ("FAILED", "ERROR", "CANCELLED"):
            return state, sj
        time.sleep(POLL_INTERVAL)
    else:
        return "TIMEOUT", {}
    try:
        return "OK", HTTP.get(f"{meta['poll_base']}/requests/{urllib.parse.quote(rid)}", headers=h).json()
    except Exception as e:
        return "OK", {"raw": f"result fetch failed: {e!r}"}


def extract_urls(payload: Any) -> tuple[list[str], str]:
    """从任意形状的响应里挖出图片 URL 和错误信息。"""
    import re
    urls: list[str] = []
    err = ""

    def walk(node: Any) -> None:
        nonlocal err
        if isinstance(node, dict):
            for k, v in node.items():
                kl = k.lower()
                if isinstance(v, str) and v.startswith("http"):
                    if kl in ("url", "image_url", "download_url", "image"):
                        urls.append(v)
                    elif re.search(r"\.(png|jpe?g|webp|gif|avif)(\?|$)", v, re.I):
                        urls.append(v)
                elif kl in ("error", "detail", "message", "msg", "fail_reason", "error_message"):
                    if isinstance(v, str) and v.strip():
                        err = err or v.strip()
                    else:
                        walk(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(payload)
    return list(dict.fromkeys(urls)), err
