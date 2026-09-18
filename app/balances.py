"""
balances.py —— 站点余额查询（取数器插件式，和渠道插件一个思路）。

内置 5 种已实测可用的取数器：
  newapi          GET {base}/api/user/self       Bearer + New-Api-User: <uid>   → quota/500000 = USD
  sub2api         GET {base}/v1/usage            Bearer                          → remaining / balance
  deepseek        GET https://api.deepseek.com/user/balance                     → balance_infos[].total_balance
  siliconflow     GET https://api.siliconflow.cn/v1/user/info                   → data.balance
  openai_billing  GET {base}/dashboard/billing/subscription                     → hard_limit_usd
另外两种万能兜底：
  custom          自己填 URL + 请求头 + JSON 路径（用来接 non-standard 站点）
  manual          手工记账（没有公开余额 API 的站点，比如 change2pro / 七牛）

想加新类型：在 FETCHERS 里加一个函数即可，UI 的「重试/全部刷新」会直接用它。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from . import store

HTTP = httpx.Client(timeout=httpx.Timeout(20, connect=10), follow_redirects=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")   # 很多站的 CF 会 403 掉默认 UA
QUOTA_PER_USD = 500_000


# ------------------------------------------------------------------ 工具

def _dig(obj: Any, path: str) -> Any:
    """按 'a.b.0.c' 取值；找不到返回 None。"""
    cur = obj
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        elif isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    return cur


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


def _headers(site: dict) -> dict:
    extra = site.get("extra") or {}
    h = {"Accept": "application/json", "User-Agent": UA}
    h.update({k: v for k, v in (extra.get("headers") or {}).items() if isinstance(v, str)})
    token = site.get("token") or ""
    mode = extra.get("auth") or "bearer"
    if token:
        if mode == "cookie":
            h["Cookie"] = token
        elif mode == "raw":
            h["Authorization"] = token
        else:
            h.setdefault("Authorization", f"Bearer {token}")
    return h


def _try_paths(data: Any, paths: list[str]) -> Any:
    for p in paths:
        v = _dig(data, p)
        if v not in (None, "", []):
            return v
    return None


# ------------------------------------------------------------------ 取数器

def fetch_newapi(site: dict) -> dict:
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        return {"error": "缺少 base_url"}
    h = _headers(site)
    if site.get("uid"):
        h["New-Api-User"] = str(site["uid"])
    r = HTTP.get(f"{base}/api/user/self", headers=h)
    data = _json(r)
    if r.status_code != 200 or not isinstance(data, dict):
        return {"error": f"HTTP {r.status_code} {str(data or r.text)[:200]}", "raw": data}
    if data.get("success") is False:
        return {"error": str(data.get("message") or "接口返回 success=false"), "raw": data}
    d = data.get("data") or {}
    quota = float(d.get("quota") or 0)
    used = float(d.get("used_quota") or 0)
    return {"balance": round(quota / QUOTA_PER_USD, 4), "unit": "USD",
            "used": round(used / QUOTA_PER_USD, 4),
            "plan": d.get("group") or d.get("display_name") or d.get("username") or "",
            "extra_info": {"请求次数": d.get("request_count"), "额度quota": int(quota)},
            "raw": d}


def fetch_sub2api(site: dict) -> dict:
    """sub2api 系余额：GET {base}/v1/usage（Bearer）。

    字段口径对齐飞书《站点余额查询代码接入》：
      remaining = data.remaining ?? quota.remaining ?? data.balance
      used      = quota.used ?? usage.total.cost
    """
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        return {"error": "缺少 base_url"}
    r = HTTP.get(f"{base}/v1/usage", headers=_headers(site))
    data = _json(r)
    if r.status_code != 200 or not isinstance(data, dict):
        return {"error": f"HTTP {r.status_code} {str(data or r.text)[:200]}", "raw": data}
    quota = data.get("quota") if isinstance(data.get("quota"), dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    total = usage.get("total") if isinstance(usage.get("total"), dict) else {}
    remaining = data.get("remaining", quota.get("remaining", data.get("balance")))
    if remaining is None:
        return {"error": "响应里没有 remaining/balance 字段", "raw": data}
    unit = data.get("unit") or quota.get("unit") or "USD"
    if data.get("isValid") is False or data.get("is_active") is False:
        return {"error": "账户已失效", "balance": float(remaining), "unit": unit, "raw": data}
    used = quota.get("used", total.get("cost"))
    extra = {}
    if total.get("requests") is not None:
        extra["请求数"] = total.get("requests")
    if total.get("total_tokens") is not None:
        extra["累计 tokens"] = total.get("total_tokens")
    if quota.get("limit") is not None:
        extra["额度上限"] = quota.get("limit")
    return {"balance": float(remaining), "unit": unit,
            "used": float(used) if isinstance(used, (int, float)) else None,
            "plan": data.get("planName") or data.get("mode") or data.get("status") or "",
            "extra_info": extra or None, "raw": data}


def fetch_deepseek(site: dict) -> dict:
    base = (site.get("base_url") or "https://api.deepseek.com").rstrip("/")
    r = HTTP.get(f"{base}/user/balance", headers=_headers(site))
    data = _json(r)
    if r.status_code != 200 or not isinstance(data, dict):
        return {"error": f"HTTP {r.status_code} {str(data or r.text)[:200]}", "raw": data}
    infos = data.get("balance_infos") or []
    if not infos:
        return {"error": "没有 balance_infos", "raw": data}
    total = sum(float(i.get("total_balance") or 0) for i in infos)
    cur = infos[0].get("currency") or "CNY"
    return {"balance": round(total, 4), "unit": cur,
            "extra_info": {"赠金": infos[0].get("granted_balance"), "充值": infos[0].get("topped_up_balance")},
            "raw": data}


def fetch_siliconflow(site: dict) -> dict:
    base = (site.get("base_url") or "https://api.siliconflow.cn").rstrip("/")
    r = HTTP.get(f"{base}/v1/user/info", headers=_headers(site))
    data = _json(r)
    if r.status_code != 200 or not isinstance(data, dict):
        return {"error": f"HTTP {r.status_code} {str(data or r.text)[:200]}", "raw": data}
    d = data.get("data") or {}
    bal = _try_paths(d, ["balance", "totalBalance", "chargeBalance", "total_balance"])
    if bal is None:
        return {"error": "响应里没有余额字段", "raw": data}
    return {"balance": round(float(bal), 4), "unit": "CNY",
            "plan": d.get("name") or d.get("profile_name") or "",
            "extra_info": {"状态": d.get("status")}, "raw": d}


def fetch_openai_billing(site: dict) -> dict:
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        return {"error": "缺少 base_url"}
    r = HTTP.get(f"{base}/dashboard/billing/subscription", headers=_headers(site))
    data = _json(r)
    if r.status_code != 200 or not isinstance(data, dict):
        return {"error": f"HTTP {r.status_code} {str(data or r.text)[:200]}", "raw": data}
    bal = _try_paths(data, ["hard_limit_usd", "total_available", "system_hard_limit_usd"])
    if bal is None:
        return {"error": "响应里没有额度字段", "raw": data}
    return {"balance": round(float(bal), 4), "unit": "USD",
            "plan": (data.get("plan") or {}).get("title") if isinstance(data.get("plan"), dict) else "", "raw": data}


def fetch_custom(site: dict) -> dict:
    """自定义：extra 里给 url / method / headers / jsonpath（balance/used/plan/unit）。"""
    extra = site.get("extra") or {}
    url = extra.get("url") or site.get("base_url") or ""
    if not url:
        return {"error": "缺少 url（写在 extra.url 或 base_url）"}
    method = (extra.get("method") or "GET").upper()
    body = extra.get("body")
    r = HTTP.request(method, url, headers=_headers(site),
                     json=body if isinstance(body, (dict, list)) else None)
    data = _json(r)
    if r.status_code >= 400:
        return {"error": f"HTTP {r.status_code} {str(data or r.text)[:200]}", "raw": data}
    paths = extra.get("jsonpath") or {}
    bal = _dig(data, paths.get("balance") or "balance")
    if bal is None:
        bal = _try_paths(data, ["balance", "data.balance", "remaining", "data.remaining", "quota"])
    if bal is None:
        return {"error": "按 jsonpath 没取到余额", "raw": data}
    try:
        bal = float(bal)
    except Exception:
        pass
    return {"balance": bal, "unit": paths.get("unit_value") or "USD",
            "used": _dig(data, paths["used"]) if paths.get("used") else None,
            "plan": _dig(data, paths["plan"]) if paths.get("plan") else "",
            "raw": data}


def fetch_manual(site: dict) -> dict:
    """手工记账：余额填在 extra.manual_balance，可备注最后充值时间。"""
    extra = site.get("extra") or {}
    if extra.get("manual_balance") in (None, ""):
        return {"error": "手工站点还没填余额（编辑站点 → extra.manual_balance）"}
    return {"balance": float(extra["manual_balance"]), "unit": extra.get("unit") or "CNY",
            "plan": extra.get("note") or "手工记账",
            "raw": {"manual_balance": extra.get("manual_balance"), "note": extra.get("note")}}


FETCHERS: dict[str, Callable[[dict], dict]] = {
    "newapi": fetch_newapi,
    "sub2api": fetch_sub2api,
    "deepseek": fetch_deepseek,
    "siliconflow": fetch_siliconflow,
    "openai_billing": fetch_openai_billing,
    "custom": fetch_custom,
    "manual": fetch_manual,
}

TYPE_META = {
    "newapi": {"label": "New API 系（/api/user/self）", "need": "base_url + token(+uid)"},
    "sub2api": {"label": "sub2api（/v1/usage）", "need": "base_url + token"},
    "deepseek": {"label": "DeepSeek 官方", "need": "token"},
    "siliconflow": {"label": "硅基流动", "need": "token"},
    "openai_billing": {"label": "OpenAI 兼容计费接口", "need": "base_url + token"},
    "custom": {"label": "自定义（URL+JSON路径）", "need": "extra.url / headers / jsonpath"},
    "manual": {"label": "手工记账（无公开 API）", "need": "extra.manual_balance"},
}


def check_site(site: dict, persist: bool = True) -> dict:
    """查一个站点的余额；结果落库（可选）。"""
    fn = FETCHERS.get(site.get("type") or "newapi")
    t0 = time.time()
    if not fn:
        res = {"error": f"未知站点类型 '{site.get('type')}'"}
    else:
        try:
            res = fn(site) or {}
        except Exception as e:
            res = {"error": f"{type(e).__name__}: {e}"}
    res["ms"] = int((time.time() - t0) * 1000)
    res["checked_at"] = int(time.time())
    if persist and site.get("id"):
        store.update_site_result(int(site["id"]), res)
    return res


def check_all() -> list[dict]:
    out = []
    for site in store.list_sites(only_enabled=True):
        res = check_site(site)
        out.append({"id": site["id"], "name": site["name"], "type": site.get("type"), **res})
    return out


def threshold_of(site: dict) -> float:
    extra = site.get("extra") or {}
    try:
        return float(extra.get("threshold") or 0)
    except Exception:
        return 0.0


# ------------------------------------------------------------------ 从 .env 一键导入

ENV_CANDIDATES = [
    # (站点名, 类型, base 键, token 键, uid 键)
    ("code-plan", "newapi", "CODEPLAN_BASE_URL", "CODEPLAN_ACCESS_TOKEN", "CODEPLAN_USER_ID"),
    ("aicost.me", "newapi", "AICOST_BASE_URL", "AICOST_ACCESS_TOKEN", "AICOST_USER_ID"),
    ("42API", "newapi", "API42_BASE_URL", "API42_ACCESS_TOKEN", "API42_USER_ID"),
    ("随时跑路", "newapi", "RUNANYTIME_BASE_URL", "RUNANYTIME_ACCESS_TOKEN", "RUNANYTIME_USER_ID"),
    ("烁API", "newapi", "SHUO_BASE_URL", "SHUO_ACCESS_TOKEN", "SHUO_USER_ID"),
    ("七牛/多吉 API", "newapi", "API67_BASE_URL", "API67_API_KEY", ""),
]


def load_env_file(path: str) -> dict:
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def import_from_env(path: str = "/env/hermes.env") -> dict:
    env = load_env_file(path)
    if not env:
        return {"error": f"读不到 {path}（需要把宿主机的 .env 只读挂载进容器）"}
    existing = {(s.get("name") or "").strip() for s in store.list_sites()}
    added, skipped = [], []
    for name, typ, bk, tk, uk in ENV_CANDIDATES:
        base, token = env.get(bk, ""), env.get(tk, "")
        if not base or not token:
            continue
        if name in existing:
            skipped.append(name)
            continue
        store.save_site({"name": name, "type": typ, "base_url": base, "token": token,
                         "uid": env.get(uk, "") if uk else "", "extra": {}, "enabled": True})
        added.append(name)
    # sub2api 多账号：每个账号是各自的域名（实测各站独立），别用一个 base 套所有 key
    for label, url, key_name in (
            ("sub2api-sixoner", "https://sub.sixoner.com", "SUB2API_KEY"),
            ("sub2api-maok", "https://ai.maok.shop", "SUB2API_MAOK_KEY"),
            ("sub2api-kaola", "https://www.appkaola.com", "SUB2API_KAOLA_KEY"),
            ("sub2api-cch", "https://origin.chhlink.xyz", "SUB2API_CCH_KEY")):
        if not env.get(key_name):
            continue
        if label in existing:
            skipped.append(label)
            continue
        store.save_site({"name": label, "type": "sub2api", "base_url": url,
                         "token": env[key_name], "extra": {}, "enabled": True})
        added.append(label)
    # DeepSeek / 硅基流动
    for name, typ, token_key in (("DeepSeek 官方", "deepseek", "DEEPSEEK_API_KEY"),
                                 ("硅基流动", "siliconflow", "SILICONFLOW_API_KEY")):
        if not env.get(token_key):
            continue
        if name in existing:
            skipped.append(name)
            continue
        store.save_site({"name": name, "type": typ, "base_url": "", "token": env[token_key],
                         "extra": {}, "enabled": True})
        added.append(name)
    return {"added": added, "skipped": skipped, "env_keys": len(env)}

# ------------------------------------------------------------------ 上游价格同步

def sync_prices_from_sites() -> dict:
    """从上游站点「实测」价格：sub2api 系的 /v1/usage 会给出每个模型的累计消耗与请求数，
    相除就是真实单价。写进价格表（渠道专属价，币种 USD，来源标记 upstream）。

    还会把该站点 base_url 下的所有渠道实例都配上同价（change2pro 一个站点两套协议都吃同一价）。
    """
    out = []
    for site in store.list_sites(only_enabled=True):
        if site.get("type") != "sub2api":
            continue
        try:
            res = fetch_sub2api(site)
        except Exception as e:
            out.append({"site": site["name"], "error": f"{type(e).__name__}: {e}"})
            continue
        raw = res.get("raw") or {}
        usage = raw.get("model_usage") or raw.get("model_stats") or []
        base = (site.get("base_url") or "").rstrip("/")
        targets = [p["key"] for p in store.rows("SELECT key, base_url FROM providers")
                   if (p["base_url"] or "").rstrip("/") == base]
        written = []
        for u in usage:
            model = u.get("model") or ""
            req = int(u.get("requests") or 0)
            cost = float(u.get("cost") or u.get("actual_cost") or 0)
            if not model or req <= 0 or cost <= 0:
                continue
            unit = round(cost / req, 6)
            for t in targets:
                store.set_price_full(model, t, unit, currency="USD", source="upstream",
                                     note=f"上游实测（{site['name']} /v1/usage：{req} 次 ${cost:.2f}）")
                written.append(f"{model}@{t}=${unit}")
            if not targets:      # 站点还没对应渠道实例时，先记成全局价
                store.set_price_full(model, "*", unit, currency="USD", source="upstream",
                                     note=f"上游实测（{site['name']}）")
                written.append(f"{model}@*=${unit}")
        out.append({"site": site["name"], "balance": res.get("balance"), "prices": written})
    return {"synced": out}
