# 部署与运维

## 1. 最短路径（Docker Compose）

```bash
git clone https://github.com/xiaoqi0102/qlikeapi-plugins.git
cd qlikeapi-plugins
cp .env.example .env && vi .env          # 填 QLIKEAPI_UP_TOKEN / QLIKEAPI_SECRET / QLIKEAPI_ADMIN_PASS
cp docker-compose.example.yml docker-compose.yml
docker compose up -d --build
docker compose logs -f --tail=50
```

自检：

```bash
curl -s localhost:18673/healthz | python3 -m json.tool
# {"ok":true,"app":"qlikeapi-plugins","version":"3.3.0","plugins":[…],"plugin_errors":{},…}
```

`plugin_errors` 必须是空对象 —— 非空说明某个插件文件写坏了，控制台「设置」页也会提示。

## 2. 让它能被 New API 访问到

New API 与本服务通常都是容器。两种接法：

**A. 同一个 docker 网络（推荐）**

```bash
docker network ls | grep -i panel        # 找到 New API 所在的网络名
```

```yaml
# docker-compose.yml 里给本服务加上：
services:
  qlikeapi-plugins:
    networks: [1panel-network]
networks:
  1panel-network:
    external: true
```

然后 New API 里 Base URL 直接写 `http://qlikeapi-plugins:18673`（容器名，走内网，不经公网）。

**B. 走宿主端口**：New API 用 `http://<宿主内网IP>:18673`（compose 默认只监听 `127.0.0.1`，
需要把端口映射改成 `18673:18673`，并确保防火墙只放内网）。

### 在 New API 里挂渠道

| 字段 | 值 |
|---|---|
| 类型 | OpenAI 兼容（或按 New API 版本选「自定义渠道」） |
| Base URL | `http://qlikeapi-plugins:18673` |
| 密钥 | `.env` 里的 `QLIKEAPI_UP_TOKEN` |
| 优先级 | 给**最高**（New API 是数字大者优先） |
| 模型 | 填本服务支持的模型名（`GET /v1/models` 可以看） |

> 把老渠道保留为低优先级兜底：需要回滚时，只要停用本服务这个渠道即可。

## 3. 反向代理与 HTTPS

Nginx 片段（只暴露必要接口，控制台建议再加一层访问控制）：

```nginx
server {
    listen 443 ssl http2;
    server_name img.example.com;

    ssl_certificate     /path/fullchain.pem;
    ssl_certificate_key /path/privkey.pem;

    client_max_body_size 32m;          # 图片编辑上传

    location / {
        proxy_pass http://127.0.0.1:18673;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 900s;       # 生成图片可能几十秒，别用默认 60s
        proxy_buffering off;           # 流式返回更顺
    }
}
```

要点：

- `client_max_body_size` 给足（图生图上传）；
- `proxy_read_timeout` ≥ `QLIKEAPI_TIMEOUT`；
- 反代后设 `QLIKEAPI_SECURE_COOKIE=1`；
- 强烈建议在反代上再加 Basic Auth / IP 白名单（控制台是管理员面）。

## 4. 备份 / 升级 / 回滚

**备份**（唯一需要备份的就是那个 SQLite 文件）：

```bash
make backup        # → backups/qlikeapi.db.YYYYmmdd-HHMMSS
```

热备份建议用 SQLite 自己的机制，避免拷到写一半的库：

```bash
docker exec qlikeapi-plugins python -c "
import sqlite3; s=sqlite3.connect('/data/qlikeapi.db'); d=sqlite3.connect('/data/backup.db')
s.backup(d); d.close(); s.close()"
docker cp qlikeapi-plugins:/data/backup.db ./backups/
```

> ⚠️ 备份文件里含**加密后的**渠道密钥。它的安全性取决于 `QLIKEAPI_SECRET`，
> 请当密钥文件对待：限权限、别丢公共网盘。

**升级**：

```bash
make backup
git pull
docker compose up -d --build      # 启动时自动跑列迁移（MIGRATIONS）
curl -s localhost:18673/healthz
```

**回滚**：

```bash
git checkout v3.3.0                # 或上一个可用 tag
docker compose up -d --build
# 数据层如需回退（新版写了新列/新表）：
docker compose down
cp backups/qlikeapi.db.<旧时间戳> data/qlikeapi.db
docker compose up -d
```

## 5. 日常运维

| 事 | 怎么做 |
|---|---|
| 看日志 | `make logs`（容器日志）；请求细节看控制台「请求日志」页 |
| 加渠道 | 控制台 → 渠道实例 → 新建（即时生效，不用重启） |
| 加插件 | 把 `.py` 放进 `app/channels/` → 控制台「重载插件」（热加载） |
| 改优先级/启停 | 渠道列表里直接改（内联编辑），或走 `POST /api/providers/{key}/patch` |
| 排查某模型走哪家 | `GET /v1/route-preview?model=xxx` |
| 排查某渠道报文 | `POST /up/<key>/v1/images/preview`（干跑，不发请求） |
| 验证某渠道是否可达 | 控制台点「探活」（零成本，逐模型出结果） |
| 查上游余额 | 控制台「站点余额」→ 全部刷新（可设预警线自动停用） |
| 对账 | 「用量统计」按天/按令牌看张数与花费，导出 CSV |

## 6. 常见故障

| 现象 | 排查 |
|---|---|
| `/healthz` 通，但 New API 报 `no available channels` | New API 侧的渠道没启用/模型名不匹配/优先级被别的渠道压过 |
| 客户端拿到 400 `prompt is required` | 客户端没传提示词（这是**保护**：本地拦下不花钱），补齐 prompt 即可 |
| 直连报 `渠道 'x' 已停用` | 该渠道在控制台被停用（或被自动熔断）；确认后再启用 |
| 全部渠道 503 | 看响应里的 `依次尝试：a(429), b(500)`，对着状态码处理（限流/余额/密钥） |
| 上游 401/403 | 渠道密钥失效或分组被删；控制台编辑实例换 key |
| 探活全失败但真实请求正常 | 有的上游对空请求返回 5xx 而非 4xx；看探活详情里的状态码与原文 |
| 超时但上游实际出图了 | 属预期：**超时默认不切换**，避免重复扣费；可加大 `QLIKEAPI_TIMEOUT` |
| 容器起来就退出 | `docker compose logs`；多半是 `.env` 没填或 `data/` 权限问题 |
| 忘记控制台密码 | 见下 |

**忘记控制台密码**：改 `.env` 里的 `QLIKEAPI_ADMIN_PASS` 不会生效（账号只在建库时创建）。
两种办法：

```bash
# ① 直接改库（hash_password 就是控制台登录用的那个哈希函数）
docker exec qlikeapi-plugins python -c "
import sys; sys.path.insert(0,'/')
from app import admin, store
store.execute('UPDATE users SET pass_hash=? WHERE username=?', (admin.hash_password('<新密码>'), 'admin'))
store.execute('DELETE FROM sessions')
print('已改密，会话已全部失效')"

# ② 或者删掉账号，重启容器让 .env 里的账号密码重建
docker exec qlikeapi-plugins python -c "
import sys; sys.path.insert(0,'/')
from app import store
store.execute('DELETE FROM users'); store.execute('DELETE FROM sessions'); print('ok')"
```

## 7. 性能与容量

- 单进程 uvicorn + SQLite（WAL）：自用/小团队场景（每秒个位数请求）完全够；
- 图片**不落盘**，所以磁盘占用几乎不增长（日志表会增长，定期清理）；
- 想要更高并发：`uvicorn --workers N`（注意 SQLite 写会串行，建议先换 Postgres 再谈多进程）；
- 单张图的上游耗时通常是主要延迟来源，本服务自身的开销在毫秒级。
