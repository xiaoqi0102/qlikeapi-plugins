"""channels/base.py —— 渠道插件基类。

一个「渠道插件」= 一个上游协议的实现。写一个新渠道只需要：
  1. 复制 _template.py 成一个新文件（比如 my_relay.py）
  2. 填 CHANNEL 元信息 + 实现 build()（把标准 OpenAI 图片请求翻译成上游要的样子）
  3. 在 Web 控制台点「重载插件」（或重启容器）即可用

约定：
  · build(p, body, edit) 返回 (url, upstream_body, meta)
  · parse(payload) 返回 OpenAI 形状的 data 列表（默认实现见协议模块）
  · operations 声明这个渠道支持哪些操作：generate / edit；不支持的操作直接 400，
    绝不会打到上游才发现
"""
from __future__ import annotations

from typing import Any


class ChannelError(ValueError):
    """请求翻译阶段的用户侧错误 → HTTP 400。"""


class Channel:
    # ---- 元信息（子类覆盖） ----
    id: str = ""                      # 插件唯一标识，等于 providers.protocol 的值
    label: str = ""                   # 中文名，显示在控制台
    hint: str = ""                    # 一句话说明
    vendor: str = ""                  # 协议归属方（谁家的协议就用谁的名字）
    docs: str = ""                    # 官方文档地址（写口径时以它为准）
    protocol_note: str = ""            # 该协议的关键约束 / 与形似协议的区别（面板上展示）
    auth_modes: tuple[str, ...] = ("bearer", "x-goog-api-key", "fal_key")
    default_auth: str = "bearer"
    default_base_url: str = ""
    operations: dict[str, str] = {"generate": "native", "edit": "native"}
    # native=原样透传 converted=本服务翻译 queue=异步提交+轮询
    models: dict[str, Any] = {}       # 预置模型名 → 上游真实名（可被渠道实例的 model_map 覆盖）
    # 参考图形态（按**该家官方文档**声明，别猜）：
    #   url    只认公网 URL（如七牛的 fal 异步面，它只拉 URL）→ base64 必须转图床直链，转不了就报错
    #   both   两种都支持 → 优先公网 URL，图床不可用时回落 base64 内联（不因此失败）
    #   base64 只认 base64（如 Gemini 的 inlineData）→ 原样透传，不做任何转换
    ref_input: str = "base64"

    # ---- 行为（子类实现） ----
    def build(self, p: dict, body: dict, edit: bool) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def parse(self, payload: Any) -> list[dict]:
        return []

    # ---- 参考图能力协商 ----
    def declared_ref_input(self, p: dict, body: dict, edit: bool) -> str:
        """插件声明的参考图形态；合并插件（按模型分流）可在这里按「面」细化。"""
        return self.ref_input if self.ref_input in ("url", "both", "base64") else "base64"

    def ref_policy(self, p: dict, body: dict, edit: bool) -> str:
        """本次请求实际采用的策略：插件声明 + 渠道实例级覆盖（options.ref_prefer）。

        实例级开关是「出问题时的应急阀门」：填 inline 一律内联 base64（不上传），
        填 url 则强制转公网直链 —— 不用改代码就能按渠道微调。
        """
        base = self.declared_ref_input(p, body, edit)
        pref = str((p.get("options") or {}).get("ref_prefer") or "").lower()
        if pref == "inline":
            return "base64"
        if pref == "url" and base == "base64":
            return "url"
        return base

    # ---- 工具 ----
    def supports(self, operation: str) -> bool:
        return operation in self.operations

    def route_mode(self, operation: str) -> str:
        return self.operations.get(operation, "unsupported")

    def info(self) -> dict:
        return {"id": self.id, "label": self.label, "hint": self.hint,
                "vendor": self.vendor, "docs": self.docs, "note": self.protocol_note,
                "auth_modes": list(self.auth_modes), "default_auth": self.default_auth,
                "default_base_url": self.default_base_url,
                "operations": [{"operation": op, "mode": mode} for op, mode in self.operations.items()],
                "ref_input": self.ref_input,
                "ref_input_note": {"url": "只认公网 URL（参考图会自动转图床直链）",
                                   "both": "URL / base64 都支持（优先公网 URL）",
                                   "base64": "只认 base64（参考图原样透传）"}.get(self.ref_input, ""),
                "models": sorted(self.models.keys()),
                # 预置映射（客户端名 → 上游真名）：面板「同步最新支持模型」与预设药丸用它
                "model_map": {k: (v if isinstance(v, str) else "upstream" in v and v.get("upstream") or k)
                              for k, v in self.models.items()}}
