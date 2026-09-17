# 安全策略

## 支持范围

安全修复只针对最新发布版本（当前 `3.3.x`）与 `main` 分支。
旧版本如受影响，请先升级；本项目不做长期维护分支。

## 报告漏洞

**请不要开公开 issue**（公开的复现步骤会立刻变成别人的攻击脚本）。

请用以下任一私有渠道：

1. 首选：GitHub 仓库 → **Security** → **Report a vulnerability**（私有安全公告）
2. 或邮件：仓库 profile 里的公开邮箱（主题注明 `[SECURITY] qlikeapi-plugins`）

请尽量包含：

- 影响版本 / 提交号，部署方式（Docker？反代？）
- 复现步骤或 PoC（**不要带真实 key**，用占位符）
- 影响评估：能读到什么、能改什么、是否需要已登录
- 如果方便，附上修复建议

**响应承诺**：3 个工作日内确认收到；确认成立后在 30 天内给出修复版本，
并在修复发布后公开致谢（除非你要求匿名）。

## 本项目的安全设计

| 机制 | 实现位置 | 说明 |
|---|---|---|
| 落库密钥加密 | `app/crypto.py` | 标准库实现的 Encrypt-then-MAC（HMAC-SHA256 派生密钥流 + 完整性标签），密钥从 `QLIKEAPI_ENC_KEY` / `QLIKEAPI_SECRET` 派生；数据库文件被拷走也解不开密钥 |
| 历史明文迁移 | `app/store.py` 的 `init_db()` | 老库里未加密的密钥在启动时自动加密 |
| 密钥脱敏 | `store.mask()` | 控制台、日志、接口响应一律只给 `sk-xxx…abcd` |
| 常时间比较 | `hmac.compare_digest` | 主密钥与访问令牌校验不使用 `==` |
| 控制台口令 | `hashlib.scrypt` + 随机盐 | 存哈希不存明文；登录态是签名 Cookie（HttpOnly / SameSite），`QLIKEAPI_SECURE_COOKIE=1` 时加 `Secure` |
| 越权面收敛 | `app/admin.py` | 除登录/健康检查外，所有控制台接口都要登录；写操作要已登录会话 |
| 令牌约束 | `tokens` 表 | 额度、过期、允许模型、允许渠道、IP 白名单、启停，逐条校验 |
| 不落盘 | `app/relay.py` | 图片不写磁盘、不转存；只回上游 URL / base64 |
| 零成本探活 | `app/relay.py` / `app/protocols.py` | 探活不携带提示词，只以 4xx 判链路；硬超时防挂死 |

## 部署加固建议

1. **不要直接暴露公网**：默认只监听 `127.0.0.1:18673`，对外走 Nginx/Traefik 反代并强制 HTTPS。
2. 反代上再套一层访问控制（Basic Auth / IP 白名单 / 仅内网可达）。
3. New API 与本服务放同一 docker 内网，用服务名互相访问，不经过公网。
4. `QLIKEAPI_UP_TOKEN`、`QLIKEAPI_SECRET`、`QLIKEAPI_ADMIN_PASS` 用 `openssl rand -hex 24` 生成，
   不要复用其它系统的口令；定期轮换（换 `QLIKEAPI_SECRET` 会让已存密钥失效，需重填渠道密钥，
   所以更推荐单独用 `QLIKEAPI_ENC_KEY`，并注意轮换后要重存密钥）。
5. 备份 `data/qlikeapi.db` 时按「等同密钥文件」对待：限权限、别丢进公共网盘/仓库。
6. 别把 `QLIKEAPI_ADMIN_PASS` 留空上线：留空会在首次启动随机生成并打印到容器日志。
7. 定期看「请求日志」和「用量统计」页，异常量或异常模型第一时间能看出来。

## 已知取舍（不是漏洞，但你需要知道）

- 上游 key 在内存中是明文（要发给上游），只保证「静态落库加密 + 传输用 HTTPS」。
- 主密钥泄露 = 库里所有密钥可解 —— 这是对称加密的固有前提，请保护 `.env`。
- 控制台会话 Cookie 一旦泄露等于管理员权限；请务必在 HTTPS 下使用。
- 本项目不做用户体系、不做多租户隔离：它是「自用网关」，不要拿它当公网 SaaS 用。
