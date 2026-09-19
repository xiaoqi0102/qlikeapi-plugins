"""
qlikeapi-plugins v3 —— 图片协议转换网关（挂在 New API 后面当上游）

对外：
  客户端 / New API 只打标准 OpenAI 图片接口
  {base}/v1/images/generations | /v1/images/edits
  每家上游一个虚拟入口：/up/<渠道实例>/v1/...  → New API 里建普通渠道(type 1)，base_url 填这个

模块划分（都是小文件，改哪块看哪块）：
  store.py         SQLite：渠道实例、日志、用户、会话、健康度、异步任务
  protocols.py     协议实现（gemini_native / openai_images / qiniu_fal 队列）
  channels/        ★ 渠道插件目录：放一个 .py 就是一个新渠道类型，Web 上点「重载插件」即时生效
  relay.py         /up/<渠道>/v1/images/{generations,edits} 转发（含 key 轮换）
  admin.py         控制台 API：账号密码登录、渠道管理、插件重载、日志、探活
  static/          控制台页面

铁律：
  · 缺 prompt 直接 400，绝不回落默认提示词（否则真出图、真扣费）
  · 图片不落盘、不转存：b64 或上游 URL 原样交给客户端
  · 探活一律零成本：不带 prompt 打上游，上游拒绝即证明链路可达
"""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from . import admin, channels, relay, store

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")

app = FastAPI(title="qlikeapi-plugins", version = "3.16.1")
app.include_router(relay.router, prefix="/up", tags=["upstream"])
app.include_router(relay.router_v1, prefix="/v1", tags=["router"])   # 统一入口：New API 只挂这一个渠道
app.include_router(admin.router, tags=["admin"])


@app.middleware("http")
async def parse_upstream_body(request: Request, call_next):
    """只对 /up/*（单渠道直连）与 /v1/*（统一入口）的 POST 预解析请求体（JSON 或 multipart）。"""
    request.state.json_body = {}
    path = str(request.url.path)
    if request.method == "POST" and (path.startswith("/up/") or path.startswith("/v1/")):
        ct = request.headers.get("content-type", "")
        if "application/json" in ct:
            try:
                request.state.json_body = json.loads((await request.body()) or b"{}")
            except Exception:
                request.state.json_body = {}
        elif "multipart/form-data" in ct or "x-www-form-urlencoded" in ct:
            form = await request.form()
            data = {k: v for k, v in form.items() if not hasattr(v, "filename")}
            files = [(k, v) for k, v in form.multi_items() if hasattr(v, "filename")]
            if files:
                data["__files"] = files
            request.state.json_body = data
    return await call_next(request)


# ------------------------------------------------------------------ 页面

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    page = "index.html" if admin.current_user(request) else "login.html"
    with open(os.path.join(STATIC_DIR, page), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if admin.current_user(request):
        return RedirectResponse("/", status_code=302)
    with open(os.path.join(STATIC_DIR, "login.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/ui-kit", response_class=HTMLResponse)
def ui_kit_page(request: Request):
    """组件展示页（样式指南）：通用组件的实时预览 + 用法片段。

    只做预览，不读数据库、不调上游；未登录跳登录页（与首页一致）。
    """
    if not admin.current_user(request):
        return RedirectResponse("/login", status_code=302)
    with open(os.path.join(STATIC_DIR, "ui-kit.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/static/{path:path}")
def static_file(path: str):
    """静态文件（支持 vendor/ 子目录：Bootstrap / Bootstrap Icons / Chart.js 都是本地自托管）。"""
    full = os.path.normpath(os.path.join(STATIC_DIR, path))
    if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
        return JSONResponse({"error": "not found"}, status_code=404)
    ext = full.rsplit(".", 1)[-1].lower()
    ctype = {"css": "text/css", "js": "application/javascript", "svg": "image/svg+xml",
             "png": "image/png", "ico": "image/x-icon", "woff": "font/woff",
             "woff2": "font/woff2", "ttf": "font/ttf"}.get(ext, "text/plain")
    return FileResponse(full, media_type=ctype)


@app.get("/healthz")
def healthz():
    return {"ok": True, "app": "qlikeapi-plugins", "version": app.version,
            "plugins": channels.available_ids(), "plugin_errors": channels.ERRORS,
            "providers": [r["key"] for r in store.rows("SELECT key FROM providers")]}


store.init_db()
