# 贡献指南

感谢愿意花时间改进这个项目 🙌
下面的规则不长，但**每条都是为了不把真金白银打给上游** —— 这是一个会被真实调用、
会真实计费的服务，不是玩具项目。

---

## 1. 环境准备

```bash
git clone https://github.com/xiaoqi0102/qlikeapi-plugins.git
cd qlikeapi-plugins
make install          # 建 .venv 并装 requirements-dev.txt
make test             # 198 个用例，全离线
make lint             # ruff
```

不需要任何上游 API key，也不需要 Docker 就能跑测试和 lint。
需要跑起服务看界面时：

```bash
cp .env.example .env   # 随便填个本地密钥即可
make run               # http://127.0.0.1:18673
```

---

## 2. 分支与提交

| 项目 | 约定 |
|---|---|
| 主分支 | `main` —— 必须常绿（CI 通过） |
| 分支命名 | `feat/xxx`、`fix/xxx`、`docs/xxx`、`refactor/xxx`、`chore/xxx` |
| 提交信息 | [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：`feat: 支持 xx` |

提交信息示例：

```
feat(channels): 新增「某中转站」渠道插件
fix(relay): 超时后不再切换渠道，避免重复扣费
docs(api): 补充 /v1/route-preview 的返回示例
test(probe): 增加合并插件探活不携带提示词的用例
```

- 一个提交只做一件事；**格式化改动不要和功能改动混在一个提交里**。
- 提交信息用中文或英文都行，但要能说清「干了什么、为什么」。

---

## 3. 代码风格

- Python 3.10+ 语法；模块/函数/变量 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE`。
- 4 空格缩进、单行 ≤ 120 字符（`.editorconfig` 已配好）。
- 注释写「为什么」，不写「是什么」；对外行为/边界条件必须有注释。
- 复杂处允许中文注释（本项目的主要读者是中文维护者）。
- 遵守 ruff 配置（`pyproject.toml`）；`make fmt` 可自动修可修的，`make lint` 必须全绿。
- **日志/异常里的密钥一律打码**：`store.mask()` 或干脆不打印。

---

## 4. 测试要求（硬性）

1. **任何新增/修改的行为都要有测试**；修 bug 先加一个能复现的失败用例。
2. **测试里禁止真实联网**：
   - 上游调用一律 monkeypatch `protocols.call_upstream`；
   - 永远不要为了测试去申请上游 key、更不要真发一次带提示词的请求；
   - conftest 提供了 `no_upstream`（一打上游就报错）和 `fake_upstream(...)` 两个夹具。
3. **零成本铁律必须有守门用例**（改这些地方必须同步更新 `tests/`）：
   - 缺/空/纯空白提示词 → 本地 400（`tests/test_channels.py`、`tests/test_probe.py`）；
   - 探活报文里不许出现任何提示词内容（`tests/test_probe.py`）；
   - 密钥只以掩码出现在任何 API 响应里（`tests/test_api.py`）。
4. 提交前跑 `make check`（= lint + test），CI 会做同样的事。

---

## 5. 新增一个渠道插件

推荐流程：

```bash
cp app/channels/_template.py app/channels/my_relay.py
```

1. 填 `CHANNEL` 元信息：`id / label / hint / auth_modes / default_auth /
   default_base_url / operations / models`。
2. 实现 `build(p, body, edit) -> (url, upstream_body, meta)`：
   - **缺提示词就抛 `ChannelError`**（→ 400），不要自己造默认提示词；
   - 只做翻译，不做网络请求（网络统一走 `protocols.call_upstream`）；
   - `meta` 里放点有用的信息（如 `face`、`refs`、`removed`），会进日志。
3. 需要新解析逻辑时实现 `parse(payload) -> [{"url": ...} | {"b64_json": ...}]`。
4. 加测试：`tests/test_channels.py` 里加一条「缺提示词必须报错」+ 一条「报文形状」用例。
5. 跑 `make test && make lint`；在控制台点「重载插件」即可选用（不必重启容器）。

详细字段说明见 [`docs/CHANNEL-PLUGINS.md`](docs/CHANNEL-PLUGINS.md)。

---

## 6. 不要提交的东西

`.gitignore` 已经把这些挡在外面，但请确认：

- `data/`（含 `qlikeapi.db` —— **里面有加密后的渠道密钥**）、`*.bak-*`
- `.env`、`docker-compose.yml`（真实部署文件）、`.up_token`
- 任何真实上游 key、New API 管理 token、站点密码、`QLIKEAPI_*` 真实值
- `__pycache__/`、`.venv/`、`.pytest_cache/`、`backups/`

不确定时：

```bash
git status --short          # 看有没有意外的文件
git diff --cached | grep -iE "sk-|token|password|secret"   # 提交前扫一眼
```

---

## 7. Pull Request 检查清单

- [ ] 目标分支是 `main`，分支名符合约定
- [ ] `make check` 本地通过（lint + test）
- [ ] 新增/修改的行为有测试；bug 修复带复现用例
- [ ] 没有真实密钥、没有 `data/`、没有部署文件
- [ ] 对外行为/接口有变化时，同步更新 `README` / `docs/` / `CHANGELOG.md`（`Unreleased` 段）
- [ ] 涉及渠道插件、路由、探活的改动：确认零成本铁律没有被破坏
- [ ] PR 描述里写清：**改了什么、为什么、怎么验证的**（贴命令和输出）

小改动可以走直推，但同样要满足上面这些；**破坏性变更**（数据结构、接口形状）必须先开
issue 讨论，并在 PR 里给出迁移方案。

---

## 8. 发布流程（维护者）

1. 确认 `main` 全绿，`CHANGELOG.md` 把 `Unreleased` 段整理成新版本段。
2. 同步版本号：`app/main.py` 的 `version`（`/healthz` 与页面页脚都读它）。
3. 提交 `chore(release): vX.Y.Z`，打 tag 并推送：

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin main --tags
   ```

4. 在 GitHub 上基于 tag 建 Release，正文直接取 `CHANGELOG` 对应段落。
5. 生产环境升级：`make backup` → `git pull` → `make up`（回滚就是切回上一个 tag +
   恢复备份的 `data/qlikeapi.db`）。

---

## 9. 报告问题

- 功能缺陷 / 需求 → GitHub Issue（模板会引导你填环境、复现步骤、期望行为）
- 安全问题 → **不要开公开 issue**，走 [`SECURITY.md`](SECURITY.md) 里的私有渠道
