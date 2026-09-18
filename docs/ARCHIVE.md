# 首次归档记录（v3.15.2 · 2026-09-18）

> 本文是 qlikeapi-plugins **首次归档**的存档说明：记下这个时间点的状态、产物位置和恢复方式。
> 归档 ≠ 停止开发 —— 项目继续在 `main` 上迭代，本文只作为这个时间点的快照。

## 一、归档时的状态

| 项 | 值 |
|---|---|
| 版本 | **v3.15.2**（`app/main.py` 里的 `version`；`/healthz` 也会回这个号） |
| 源码 | tag **`v3.15.2`**（`git checkout v3.15.2` 可回到归档点） |
| 容器镜像 | `ghcr.io/xiaoqi0102/qlikeapi-plugins:v3.15.2`（另有 `3.15.2` / `3.15` / `latest` / `sha-xxxx`） |
| 上游 | change2pro（sub2api 系）、七牛 ModelInk + fal（qnaigc）、aicost.me（newapi 系） |
| 线上形态 | 容器 `qlikeapi-plugins`，只听 `127.0.0.1:18673`；New API 侧只留**一个**渠道指向本服务（优先级最高），客户端零改动 |
| 规模 | 21 个 Python 模块 / 约 6,100 行应用代码 + 2,400 行前端；416 个测试用例；11 篇文档 |
| 许可 | MIT（第三方组件见 `THIRD-PARTY-NOTICES.md`） |

## 二、归档产物在哪

| 产物 | 位置 | 说明 |
|---|---|---|
| 源码快照 | 随包 `qlikeapi-plugins-v3.15.2-src.tar.gz` | 由 `git archive v3.15.2` 生成，**只含版本控制里的文件**（不含 `data/`、`.env`、`docker-compose.yml` 等本地私密文件） |
| 容器镜像 | GitHub Packages → `ghcr.io/xiaoqi0102/qlikeapi-plugins` | Actions 构建 + 回拉冒烟后才推；当前为私有包 |
| 发布说明 | GitHub → Releases → `v3.15.2` | 变更明细 |
| 变更历史 | 仓库 `CHANGELOG.md` | 从 v3.9.x 到 v3.15.2 全程记录 |
| 上一代源码 | 服务器 `/opt/qlikeapi-plugins/data/archive/legacy-imggw-src-20260918.tar.gz` | 旧 `/opt/imggw` + 设计稿 `/opt/mockup` 的源码存档（原目录已从 `/opt` 移除） |
| 数据库快照 | 服务器 `/opt/qlikeapi-plugins/data/qlikeapi.db.bak-20260918-archive` | 归档当刻的库（渠道密钥是加密存的，仍请注意保密） |

## 三、怎么恢复 / 部署

**A. 从镜像起（最快）**

```bash
# 私有包：先 docker login ghcr.io（密码用带 read:packages 的 PAT），或把包改成 Public
docker pull ghcr.io/xiaoqi0102/qlikeapi-plugins:v3.15.2

docker run -d --name qlikeapi-plugins -p 127.0.0.1:18673:18673 \
  -e QLIKEAPI_SECRET=$(openssl rand -hex 24) \
  -e QLIKEAPI_UP_TOKEN=$(openssl rand -hex 24) \
  -e QLIKEAPI_ADMIN_USER=admin \
  -e QLIKEAPI_ADMIN_PASS=换成强密码 \
  -v /opt/qlikeapi-plugins/data:/data \
  ghcr.io/xiaoqi0102/qlikeapi-plugins:v3.15.2
```

**B. 从源码起（要改代码）**

```bash
git clone https://github.com/xiaoqi0102/qlikeapi-plugins.git && cd qlikeapi-plugins
cp .env.example .env && cp docker-compose.example.yml docker-compose.yml   # 填两个随机密钥 + 控制台密码
docker compose up -d --build
curl -s localhost:18673/healthz      # {"ok":true,...}
```

**C. 回到本归档点**

```bash
git checkout v3.15.2 && docker compose up -d --build      # 源码回滚
# 或直接把容器换成 v3.15.2 的镜像标签
```

## 四、归档时的验收证据

- **测试**：`pytest -q` 416 项全过；`ruff check app tests scripts` clean；`scripts/ui_lint.py`（设计规范守卫）通过。
- **CI**：lint / test（Python 3.10 + 3.12）/ docker build + 冒烟，全绿。
- **真实出图**：`/up/aicost/v1/images/generations` 打 `gpt-image-2` / `1024x1024` → HTTP 200、23.1 秒、1 张图。
- **价格对账**：aicost 余额 $1.0817 → $1.0715（实付 $0.0102），与「$0.445 裸价 × 0.023 分组倍率」的推算一致。
- **链路**：`aicost(prio 11) → change2pro(prio 10) → qnaigc(prio 5)`，`/v1/route-preview` 实测首选为 aicost。

## 五、不随归档走的（重要）

- `data/`（运行时库、上传的插件、库备份）—— 状态数据，不入库、不进源码包。
- `docker-compose.yml`、`.env` —— 真实凭据所在，已在 `.gitignore` / `.dockerignore` 里排除。
- 所有上游 API key、站点令牌、控制台口令、内部主密钥 —— 只存在服务器本地，任何产物里都没有。
