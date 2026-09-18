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

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from . import channels, store

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

# ------------------------------------------------------------------ 价格直读（按渠道）

def _client_name(p: dict, upstream: str) -> str:
    """上游真实名 → 该渠道对外的客户端模型名（不在映射里就原样用上游名）。"""
    for cli, up in (p.get("model_map") or {}).items():
        if str(up) == upstream:
            return cli
    return upstream


def _manual_groups(p: dict) -> list:
    """渠道选项里手工指定的令牌分组顺序（options.price_groups，数组或逗号串）。

    用途：/api/token/ 读不到（令牌过期/权限不足）时，也能按真实分组顺序算倍率。
    """
    o = p.get("options")
    if isinstance(o, str):
        try:
            o = json.loads(o or "{}")
        except Exception:
            o = {}
    if not isinstance(o, dict):
        return []
    g = o.get("price_groups")
    if isinstance(g, (list, tuple)):
        return [str(x).strip() for x in g if str(x).strip()]
    return [x.strip() for x in str(g or "").replace("，", ",").split(",") if x.strip()]


def _manual_ratio(p: dict):
    """渠道选项里的手工分组倍率（options.price_ratio）；没填返回 None。"""
    o = p.get("options")
    if isinstance(o, str):
        try:
            o = json.loads(o or "{}")
        except Exception:
            o = {}
    if not isinstance(o, dict):
        return None
    try:
        v = float(o.get("price_ratio"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _price_note(site_name: str, up: str, info: dict) -> str:
    """把「裸价 × 倍率 = 实付」的账写清楚（价格不编造，来源/口径都标出来）。"""
    if info.get("ratio") and abs(info["ratio"] - 1.0) > 1e-9:
        tag = {"token": "令牌分组", "cheapest": "最便宜可用分组"}.get(info.get("how") or "", "分组")
        return (f"{site_name} /api/pricing：{up} ${info.get('raw')} × {tag} {info.get('group')} "
                f"倍率 {info['ratio']} = ${info['price']}/次")
    return f"{site_name} /api/pricing：{up} = ${info['price']}/次（倍率 1）"


def _pricing_effective(models: list, ratios: dict, groups: list) -> dict:
    """把「平台裸价」折算成「这把 key 实际会被扣的价」。

    New API 的分组倍率语义（aicost 面板原话）：
      · 一个令牌可以绑多个分组；第一个是默认请求分组，后面的按选择顺序作为失败备用分组；
      · 每个分组有自己的倍率折扣（同一个模型在不同分组价钱不一样）；
      · 某个模型只在部分分组可售（enable_groups），默认分组不卖就顺位落到下一个能卖的分组。
    所以「真实单价 = 模型裸价 × 实际服务分组的倍率」，实际服务分组 = 令牌分组顺序 ∩ 该模型可售分组 的第一个。
    取不到令牌分组时退化为「该模型可售分组里最便宜的那个」，并在 how 里标明是估算。
    """
    order = [str(g).strip() for g in (groups or []) if str(g).strip()]
    out: dict = {}
    for m in models or []:
        if not isinstance(m, dict):
            continue
        name = str(m.get("model_name") or "").strip()
        raw = m.get("model_price")
        if not name or not isinstance(raw, (int, float)) or float(raw) <= 0:
            continue
        if int(m.get("quota_type") or 0) != 1:      # 0 = 按 token 倍率计费，没有「每张多少钱」
            continue
        eg = [str(g).strip() for g in (m.get("enable_groups") or []) if str(g).strip()]
        hit = [g for g in order if not eg or g in eg]
        if hit:
            group, how = hit[0], "token"          # 令牌自己的分组顺序 = 真实路由
        elif eg:
            cand = sorted((float(ratios[g]), g) for g in eg if g in ratios)
            group, how = (cand[0][1], "cheapest") if cand else ("", "none")
        else:
            group, how = (order[0] if order else ""), ("token" if order else "none")
        ratio = float(ratios.get(group) or 1) if group else 1.0
        out[name] = {"raw": round(float(raw), 6), "ratio": ratio, "group": group, "how": how,
                     "price": round(float(raw) * ratio, 6), "groups": eg}
    return out


def _newapi_token_groups(site: dict, api_key: str = "") -> list:
    """读「这把 key 绑定了哪些分组」（有序：第一个是默认请求分组）。

    New API 的 /api/token/ 列表里 key 是打码的（`bKcI****WreC`），所以用尾 4 位匹配。
    group 字段可能是逗号分隔的多分组（新版多选），也可能是单个。
    """
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        return []
    h = _headers(site)
    if site.get("uid"):
        h["New-Api-User"] = str(site["uid"])
    try:
        d = _json(HTTP.get(f"{base}/api/token/?p=0&size=100", headers=h))
    except Exception:
        return []
    if not isinstance(d, dict) or d.get("success") is False:
        return []
    data = d.get("data")
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    tail = (api_key or "").strip()[-4:]
    for t in items:
        if not isinstance(t, dict):
            continue
        if tail and tail not in str(t.get("key") or ""):
            continue
        # 新版 New API：group=默认分组（单个），groups=多分组数组（含失败备用，按选择顺序）
        for field in ("groups", "group"):
            g = t.get(field)
            if isinstance(g, (list, tuple)):
                out = [str(x).strip() for x in g if str(x).strip()]
            else:
                out = [x.strip() for x in str(g or "").replace("，", ",").split(",") if x.strip()]
            if out:
                return out
    return []


def fetch_newapi_pricing(site: dict, api_key: str = "", groups: list | None = None) -> dict:
    """newapi 系「平台报价」直读：GET {base}/api/pricing（Bearer + New-Api-User: <uid>）。

    返回的每条价 = 裸价 × 该 key 实际服务分组的倍率（见 _pricing_effective），
    并带上 raw/ratio/group 便于在界面上把账算给人看。
    """
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        return {"error": "缺少 base_url"}
    h = _headers(site)
    if site.get("uid"):
        h["New-Api-User"] = str(site["uid"])
    r = HTTP.get(f"{base}/api/pricing", headers=h)
    data = _json(r)
    if r.status_code != 200 or not isinstance(data, dict):
        return {"error": f"HTTP {r.status_code} {str(data or r.text)[:200]}", "raw": data}
    if data.get("success") is False:
        return {"error": str(data.get("message") or "接口返回 success=false"), "raw": data}
    ratios = data.get("group_ratio") or {}
    ratios = {str(k): float(v) for k, v in ratios.items() if isinstance(v, (int, float))}
    lst = data.get("data") if isinstance(data.get("data"), list) else []
    groups = [str(g).strip() for g in (groups or []) if str(g).strip()] or _newapi_token_groups(site, api_key)
    prices = _pricing_effective(lst, ratios, groups)
    return {"prices": prices, "currency": "USD", "total": len(lst),
            "groups": groups, "ratios": ratios}


def _site_of_provider(p: dict) -> dict | None:
    """渠道实例挂在哪个站点上：先看 site_id，再按 base_url 兜底匹配。"""
    sid = p.get("site_id")
    if sid:
        s = store.get_site(int(sid))
        if s:
            return s
    base = (p.get("base_url") or "").rstrip("/")
    if not base:
        return None
    for s in store.list_sites():
        if (s.get("base_url") or "").rstrip("/") == base:
            return s
    return None


def _providers_of_site(site: dict) -> list[dict]:
    base = (site.get("base_url") or "").rstrip("/")
    out = []
    for p in store.list_providers():
        sid = p.get("site_id")
        if (sid and int(sid) == int(site["id"])) or (base and (p.get("base_url") or "").rstrip("/") == base):
            out.append(p)
    return out


def _sync_site_prices(site: dict, only_provider: str | None = None) -> dict:
    """同步一个站点的真实单价，写到它下面各渠道实例上（按「客户端模型名」落库）。

    - sub2api 系：/v1/usage 的每模型累计消耗 ÷ 请求数 = 真实单价（来源 upstream）
    - newapi  系：/api/pricing 的按次报价 model_price（来源 platform）
    """
    name = site.get("name") or site.get("base_url") or "?"
    stype = (site.get("type") or "").lower()
    provs = _providers_of_site(site)
    if only_provider:
        provs = [p for p in provs if p["key"] == only_provider]
        if not provs:
            return {"site": name, "error": f"渠道 {only_provider} 不在站点「{name}」下，无法直读它的平台价"}
    if not provs:
        return {"site": name, "error": "该站点下还没有渠道实例，先建渠道再同步价格"}

    written: list[str] = []
    if stype == "sub2api":
        try:
            res = fetch_sub2api(site)
        except Exception as e:
            return {"site": name, "error": f"{type(e).__name__}: {e}"}
        if res.get("error"):
            return {"site": name, "error": res["error"]}
        raw = res.get("raw") or {}
        usage = raw.get("model_usage") or raw.get("model_stats") or []
        for u in usage:
            up = str(u.get("model") or "")
            req = int(u.get("requests") or 0)
            cost = float(u.get("cost") or u.get("actual_cost") or 0)
            if not up or req <= 0 or cost <= 0:
                continue
            unit = round(cost / req, 6)
            for p in provs:
                cli = _client_name(p, up)
                store.set_price_full(cli, p["key"], unit, currency="USD", source="upstream",
                                     note=f"{name} /v1/usage：{req} 次 ${cost:.2f}")
                written.append(f"{cli}@{p['key']}=${unit}")
        return {"site": name, "source": "usage", "balance": res.get("balance"), "prices": written}

    if stype == "newapi":
        # 一个站点上的不同渠道可能用不同的 key（不同 key 绑不同分组 → 倍率不同），
        # 所以按渠道逐个直读，绝不共用一份价。
        res_all = {}
        for p in provs:
            keys = [k.strip() for k in (p.get("api_key") or "").split("\n") if k.strip()]
            k0 = keys[0] if keys else ""
            if "payload" not in res_all:
                res_all["payload"] = fetch_newapi_pricing(site, api_key=k0,
                                                         groups=_manual_groups(p))
            res = res_all["payload"]
            if res.get("error"):
                return {"site": name, "error": res["error"], "group": None}
            prices = res.get("prices") or {}
            inv = {str(v): k for k, v in (p.get("model_map") or {}).items()}
            ov = _manual_ratio(p)                      # 手工倍率覆盖（渠道选项里可填）
            n = 0
            for up, info in prices.items():
                cli = inv.get(up)
                if not cli:
                    continue              # 该渠道不对外暴露这个模型，不写价
                price, note = info["price"], _price_note(name, up, info)
                if ov is not None:
                    price = round(float(info.get("raw") or info["price"]) * ov, 6)
                    note = (f"{name} /api/pricing：{up} ${info.get('raw') or info['price']}"
                            f" × 手工倍率 {ov} = ${price}")
                store.set_price_full(cli, p["key"], price,
                                     currency=res.get("currency") or "USD", source="platform", note=note)
                written.append(f"{cli}@{p['key']}=${price}")
                n += 1
            res_all[p["key"]] = {"group": res.get("groups"), "models": n}
        return {"site": name, "source": "pricing", "prices": written,
                "group": (res_all.get(provs[0]["key"]) or {}).get("group") if provs else None,
                "per_provider": {k: v for k, v in res_all.items() if k != "payload"}}

    return {"site": name, "error": f"站点类型 {stype or '未知'} 不支持价格直读（目前支持 sub2api / newapi）"}


def ensure_site_for_provider(p: dict, create: bool = True) -> dict:
    """渠道实例 ↔ 站点余额条目联动：加渠道时顺手把站点条目建出来。

    · 同一个 base_url 只建一个站点（已存在就复用，不覆盖你填过的令牌 / uid）；
    · 站点类型优先用插件声明的 site_type（如 change2pro→sub2api、aicost→newapi），
      没声明就记成「手工记账」，免得余额查询天天报错；
    · 令牌 / uid 这类凭据一律留空，由用户在「站点余额」页手动填（不猜、不兜底）。
    """
    sid = p.get("site_id")
    if str(sid or "").strip() not in ("", "None"):
        s = store.get_site(int(sid))
        if s:
            return {"site_id": int(sid), "created": False, "type": s.get("type"),
                    "name": s.get("name"), "linked": True}
    base = (p.get("base_url") or "").rstrip("/")
    if not base:
        return {"error": "渠道没填 base_url，无法建站点余额条目"}
    for s in store.list_sites():
        if (s.get("base_url") or "").rstrip("/") == base:
            return {"site_id": int(s["id"]), "created": False, "type": s.get("type"),
                    "name": s.get("name"), "linked": True}
    if not create:
        return {"error": "还没有对应的站点余额条目"}
    ch = channels.get(p.get("protocol") or "")
    stype = (getattr(ch, "site_type", "") or "manual") if ch else "manual"
    label = p.get("label") or p.get("key") or base
    new_id = store.save_site({"name": label, "type": stype, "base_url": base,
                              "token": "", "uid": "", "extra": {"auto_created_by": p.get("key") or ""}})
    return {"site_id": int(new_id), "created": True, "type": stype, "name": label,
            "need": (TYPE_META.get(stype) or {}).get("need", ""),
            "note": "站点条目已自动建好，令牌 / uid 请到「站点余额」页手动填"}


def sync_provider_prices(key: str) -> dict:
    """单个渠道实例的「同步价格」：只从它自己配置的平台直读，别的渠道不碰。"""
    p = store.get_provider(key, with_keys=False)
    if not p:
        return {"ok": False, "error": f"渠道实例 {key} 不存在"}
    site = _site_of_provider(p)
    if not site:
        return {"ok": False, "provider": key,
                "error": f"渠道 {key} 没关联站点，无法直读平台价（先到「渠道实例」里给它绑定站点）"}
    out = _sync_site_prices(site, only_provider=key)
    out["provider"] = key
    out["ok"] = not out.get("error")
    return out


def sync_prices_from_sites() -> dict:
    """全部站点一起同步（模型目录页的「全部渠道同步」用它）。"""
    return {"synced": [_sync_site_prices(s) for s in store.list_sites(only_enabled=True)]}
