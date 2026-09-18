"""channels/__init__.py —— 渠道插件注册表（自动发现 + 热重载）。

把 .py 文件丢进这个目录就是「加了一个渠道插件」；Web 控制台点「重载插件」即时生效，
不用重启容器、不用改任何核心代码。
"""
from __future__ import annotations

import importlib
import pathlib
import sys

from .base import Channel, ChannelError  # noqa: F401  供插件 from .base import 使用

REGISTRY: dict[str, Channel] = {}
ERRORS: dict[str, str] = {}
_SKIP = {"__init__", "base"}


def discover(reload: bool = False) -> dict[str, Channel]:
    """扫描本目录，装载所有带 CHANNEL 对象的插件文件。"""
    REGISTRY.clear()
    ERRORS.clear()
    here = pathlib.Path(__file__).parent
    for f in sorted(here.glob("*.py")):
        stem = f.stem
        if stem in _SKIP or stem.startswith("_"):
            continue
        mod_name = f"{__package__}.{stem}"
        try:
            if reload and mod_name in sys.modules:
                mod = importlib.reload(sys.modules[mod_name])
            else:
                mod = importlib.import_module(mod_name)
            ch = getattr(mod, "CHANNEL", None)
            if ch is None:
                continue
            if not getattr(ch, "id", ""):
                ERRORS[stem] = "CHANNEL.id 为空"
                continue
            REGISTRY[ch.id] = ch
        except Exception as e:  # 单个插件写错不影响其它插件
            ERRORS[stem] = repr(e)
    return REGISTRY


# 历史 id → 现 id（改名后老数据仍能用；别名不出现在面板列表里）
ALIASES = {"fal_queue": "qiniu_fal"}


def get(channel_id: str) -> Channel | None:
    cid = channel_id or ""
    return REGISTRY.get(cid) or REGISTRY.get(ALIASES.get(cid, ""))


def list_channels() -> list[dict]:
    return [c.info() for c in REGISTRY.values()]


def available_ids() -> list[str]:
    return sorted(REGISTRY.keys())


discover()
