# AGENTS.md — qlikeapi-plugins

本文件是**接手本项目的 AI 的长期规则**。入口与「任务→文件」路由见 `PROJECT_INDEX.md`。

## 0. 三条铁律（最高优先级，任何改动都不得破坏）

1. **缺 prompt 直接 400**：绝不回落默认提示词 —— 否则会真出图、真扣费。
2. **图片不落盘、不转存**：b64 或上游 URL 原样交给客户端。
3. **探活一律零成本**：探活前**递归抹掉一切提示词字段**再打上游，用 `4xx` 判定链路可达；
   `QLIKEAPI_PROBE_TIMEOUT` 硬超时必须保留（防止挂起的上游卡死探活）。

> 涉及出图/扣费的功能，默认只做**只读或零成本探测**；要真打上游必须先跟用户确认。

## 1. 检索规则（省 token、保判断质量）

1. **按当前问题选资料**，不因为「文件存在 / 可能有用 / 被索引链接」就读取。
2. **不默认读取所有文件，也不默认全文读取**选中的文件。先定位文件 → 再定位章节 / 符号 / 函数 / 配置项。
3. 读取顺序：`PROJECT_INDEX.md 路由表` → 索引或限定范围搜索（如 `grep`/`search_files`）→ 命中片段及必要上下文。
4. 信息足够即停止。**不得连续分段读取同一文件变相遍历全文**（`admin.py`/`app.js` 这类大文件尤其注意）。
5. 全文读取仅限：短小且全部相关的文件、需要完整理解适用规则的场景。
6. 索引是条件路由，不是必读清单。`data/`、`docs/images/`、`CHANGELOG.md`（只在查历史时读）默认不读；
   **不读 `.env`、数据库里的密钥、任何机密**。
7. 已加载且仍有效的内容不重复读取；不为省上下文跳过本文件的规则。

## 2. 架构硬约束

- **依赖方向不许反向**：`main → relay → channels → protocols → utils`，`admin → store → crypto`。
- `protocols.py` / `utils.py` 是纯函数层：不碰数据库、不发网络请求。**所有出站请求只走
  `protocols.call_upstream()`**（唯一出口，统一超时/日志/打桩）。
- `channels/*` 只做报文翻译，不知道数据库结构；`store.py` 只做持久化，不含业务判断。
- 一个上游协议 = 一个渠道插件文件。**内置插件改动会改核心行为**，先在 `make test` 里补测试。
- 运维写码**不要**直接读容器内数据库来推断逻辑 —— 先读 `store.py` 的表结构定义。

## 3. 改动流程（照做）

```bash
# 1) 改代码（先看 PROJECT_INDEX.md 的路由表定位文件）
# 2) 提交前必跑
make check            # = ruff lint + ui_lint 设计规范 + pytest（零网络）
#    单跑某块：make lint / make ui-lint / make test
#    改前端样式另需：make ui-diff（需先注入上一版 style.css 当对照组，见 Makefile 注释）
# 3) 改完必须重建镜像才生效（⚠ app/ 未挂载进容器，--build 不能省）
docker compose up -d --build && curl -s localhost:18673/healthz | python3 -m json.tool
```

- `/healthz` 里 **`plugin_errors` 必须是空对象**；非空说明某个插件写坏了（控制台「设置」页也会提示）。
- **面板装的运行插件**（`data/plugins/*.py`）保存即热重载，**不需要**重建容器；但它们是可执行代码、
  不在 git 内，只装可信来源，静态校验不是沙箱。
- **内置插件**（`app/channels/*.py`）在面板里只读；要改就另存为新文件名 + 换 `id`，或改 `app/` 后重建。
- 数据层改动：`store.py` 启动时按 `MIGRATIONS` 自动跑列迁移；动表结构前先 `make backup`。

## 4. 记录与提交规则

- 只在**信息有实质变化**时更新文档；只读排查不改记录。
- 每类事实只有一个权威来源，其余位置用简短摘要 + 链接（如路由细节只写 `docs/ARCHITECTURE.md`）。
- 版本号 `app/main.py` 的 `version` 与 `CHANGELOG.md` 顶部保持一致，按 `## vX.Y.Z — YYYY-MM-DD` 追加。
- 提交信息用 Conventional Commits + 中文描述，例如：
  `feat(panel): …`、`fix(docs): …`、`chore: …`。
- 「命令已配置」≠「执行成功」，历史结果 ≠ 当前证据；文档里的步骤不构成执行授权。

## 5. 不要做的事

- 不要为了「省事」在探活/自检路径上带 prompt（会真出图扣费）。
- 不要自由发挥改写 `docs/DESIGN-SYSTEM.md` 之外的配色/圆角/字号 —— 前端样式以 `tokens.css` 为准。
- 不要提交密钥、`.env`、`data/*.db`、`backups/`。
- 不要用 `git push --force`、不要动 `main` 之外的发布流程（镜像由 GitHub Actions 打 `v*` 标签自动构建）。
- 不要一次性重写大文件；用定点编辑，改前读目标片段及必要上下文。

## 6. 两条容易想当然的（已核实）+ 一条风险

- **生图卖价规则不在本仓库**。本仓库只负责「按渠道读上游实付单价」：
  `app/balances.py` → `store.list_prices()`，价格来自各渠道自己的平台，随行标注来源与币种，不做汇率换算。
  「卖价 = 各渠道最高实付 USD + 0.05」是**下游 New API 侧的定价约定**——改价去 New API，不要在本仓库找落点。
- `docs/SMART-ROUTING-PLAN.md` 是**分阶段规划稿**：阶段 1（P0：失败降级语义 + 并发闸门 + 决策可见）已于
  **v3.5.0 落地**；阶段 2/3（健康画像、请求内容感知）**未做**。引用前先对照 `relay.py` / `store.py` 确认是否已实现。
- ⚠ 生产库 `data/qlikeapi.db` 存的是密文密钥，可解性取决于 `QLIKEAPI_SECRET`：**换 SECRET 会让已存渠道密钥全部作废**
  （启动时 `store.enc_health()` 报「解不开 N 条」，面板侧栏可见）。
