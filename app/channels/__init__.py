"""channels/__init__.py —— 渠道插件注册表（自动发现 + 热重载）。

两处扫描：
  · 内置 `app/channels/*.py` —— 随镜像走，回滚兜底
  · 外部 `QLIKEAPI_PLUGIN_DIR`（默认 `/data/plugins`，在挂载卷上）—— 面板「渠道插件」页
    上传的插件落这里，重建容器不丢

两者写法完全一样（模块名都是 `app.channels.<文件名>`，相对导入照常工作）。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import sys

from .base import Channel, ChannelError  # noqa: F401  供插件 from .base import 使用

REGISTRY: dict[str, Channel] = {}
ERRORS: dict[str, str] = {}
ORIGINS: dict[str, str] = {}       # 插件 id → builtin | uploaded
ORIGIN_FILES: dict[str, str] = {}  # 插件 id → 文件绝对路径（校验时判 id 占用）
_SKIP = {"__init__", "base"}


def plugin_dir() -> pathlib.Path:
    """上传插件的目录（面板里安装的插件住这儿）。"""
    return pathlib.Path(os.environ.get("QLIKEAPI_PLUGIN_DIR") or "/data/plugins")


def load_path(path: pathlib.Path, mod_name: str, reload: bool = False):
    """按文件路径装载一个插件模块 —— 内置与上传走同一条路。

    模块名统一用 `app.channels.<stem>`，所以插件里的 `from .. import protocols` /
    `from .base import Channel` 都能正常解析。

    注意：这里**不用 `importlib.reload`**。reload 会拿模块的 `__spec__.parent` 去找文件，
    也就是去 `app/channels/` 里找 —— 上传的插件在 `/data/plugins/`，永远找不到，
    报 `spec not found for the module`。所以一律重新建 spec 再 exec，改完插件才能真正热重载。
    """
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"装载不了插件文件：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod          # 相对导入依赖它
    spec.loader.exec_module(mod)
    return mod


def _load_one(path: pathlib.Path, origin: str, seen: set[str]) -> None:
    stem = path.name[: -len(".py")]
    if stem in _SKIP or stem.startswith("_"):
        return
    if stem in seen:
        ERRORS[stem] = f"与内置插件同名，已跳过：改个文件名再装（{path.name}）"
        return
    seen.add(stem)
    try:
        mod = load_path(path, f"{__package__}.{stem}", reload=True)
        ch = getattr(mod, "CHANNEL", None)
        if ch is None:
            ERRORS[stem] = "模块里没有 CHANNEL 对象（末尾要写 CHANNEL = 你的类()）"
            return
        if not getattr(ch, "id", ""):
            ERRORS[stem] = "CHANNEL.id 为空"
            return
        if ch.id in REGISTRY:
            ERRORS[stem] = f"id「{ch.id}」已被其它插件占用，已跳过"
            return
        REGISTRY[ch.id] = ch
        ORIGINS[ch.id] = origin
        ORIGIN_FILES[ch.id] = str(path)
    except Exception as e:  # 单个插件写错不影响其它插件
        ERRORS[stem] = repr(e)


def discover(reload: bool = False) -> dict[str, Channel]:
    """扫描内置目录 + 上传目录，装载所有带 CHANNEL 对象的插件文件。"""
    REGISTRY.clear()
    ERRORS.clear()
    ORIGINS.clear()
    ORIGIN_FILES.clear()
    seen: set[str] = set()
    for f in sorted(pathlib.Path(__file__).parent.glob("*.py")):
        _load_one(f, "builtin", seen)
    pd = plugin_dir()
    if pd.is_dir():
        for f in sorted(pd.glob("*.py")):
            _load_one(f, "uploaded", seen)
    return REGISTRY


# 历史 id → 现 id（改名后老数据仍能用；别名不出现在面板列表里）
ALIASES = {"fal_queue": "qiniu_fal"}


def get(channel_id: str) -> Channel | None:
    cid = channel_id or ""
    return REGISTRY.get(cid) or REGISTRY.get(ALIASES.get(cid, ""))


def origin_of(channel_id: str) -> str:
    return ORIGINS.get(channel_id, "")


def list_channels() -> list[dict]:
    return [dict(c.info(), origin=origin_of(c.id)) for c in REGISTRY.values()]


def available_ids() -> list[str]:
    return sorted(REGISTRY.keys())


discover()
