# 渠道插件示例（不进 Docker 镜像，仅作版本化留档）

这里放**面板安装型**渠道插件的源码备份。它们运行时的落盘位置是容器里的
`QLIKEAPI_PLUGIN_DIR`（默认 `/data/plugins`，宿主机 `data/plugins/`），而 `data/` 是
**git 忽略**的（运行时数据/密钥不入库），所以把源码同时在这里留一份，避免只存在那台机器上。

> `app/channels/*.py` 是**内置**插件（随镜像发布）；这里的是**外挂**插件（走面板「渠道插件」装）。
> 两者都是同一个 `Channel` 契约，区别只是加载路径与「面板只读」与否。

## tudou.py —— AI-Tudou（土豆API）

- 站点：https://api.ai-tudou.net （Bearer）
- 一个实例覆盖两面，按模型名自动分流：
  - `gemini-*` → Gemini 原生 `POST /v1beta/models/{model}:generateContent`
    （`generationConfig.responseModalities` 固定 `["TEXT","IMAGE"]`；`imageConfig.{aspectRatio,imageSize}` 控比例/分辨率，
    imageSize 必须大写 `1K/2K/4K`；参考图**只吃 base64** 的 `inlineData`，且**图片 parts 放在文本前**）
  - `gpt-image-2-all` → 异步 `POST /v1/images/generations/async` → 轮询 `GET /v1/tasks/{task_id}`
    （结果在 `data.result.images[0].url[0]`，`url` 本身是数组；由插件 `poll()` 钩子兜成同步结果）
- 落地方式：面板「渠道插件 → 添加插件」贴源码 → 校验 → 安装；或直接写进 `data/plugins/tudou.py`（保存即热重载）。
- 实例参数：`protocol=tudou`、`base_url=https://api.ai-tudou.net`、`auth_mode=bearer`、key 填站点 API Key。
  建议 `priority` 给低值当**兜底**，避免把既有渠道的流量抢走。
