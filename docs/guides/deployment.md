# QuantPilot 生产部署指南

> 目标：单机 Docker Compose 部署。**只读这一篇文档**就能从空机器开始把系统跑起来。
> 适用版本：V1.0（Phase 1~10 + V1.0 整改批次完成）

---

## 0. 谁该看这篇？

- ✅ 在自己的服务器（云主机 / 家用 NAS）上首次部署 QuantPilot
- ✅ 已部署 → 想升级或回滚
- ✅ 想搭建每日自动备份 / 配置 HTTPS

不在范围内：多机分布式部署、Kubernetes、大流量优化（V1.5 规划）。

---

## 1. 部署模式选择

| 场景 | 是否需要 HTTPS | 是否需公网访问 | 说明 |
|------|---------------|---------------|------|
| **本地试用 / 内网 / 家用 NAS** | 否（可跳） | 否 | 走 §3 一键脚本即可 |
| **公网部署** | **强制必须** | 是 | 走 §3 + §6 HTTPS 配置；未启用 HTTPS 不得暴露公网 |

> ⚠️ **公网部署 HTTPS 强制要求**（V1.0 整改 Batch 2 — B2-5）：
> JWT token 通过 HTTP Header 传输，未启用 HTTPS = 中间人可窃取 token = 账户被完全劫持（攻击者能看持仓、改阈值、提交订单）。**禁止在公网开放未加密的 80 端口**。

---

## 2. 前置要求

| 组件 | 版本要求 | 检查方法 |
|------|---------|---------|
| 操作系统 | Linux（推荐 Ubuntu 22.04+） | `uname -a` |
| Docker Engine | 20.10+ | `docker version` |
| Docker Compose | v2（`docker compose` 子命令） | `docker compose version` |
| CPU / 内存 | ≥ 2 核 / ≥ 2 GB（推荐 4 核 / 4 GB） | `nproc; free -h` |
| 磁盘可用空间 | ≥ 30 GB | `df -h` |
| 端口 | 80（HTTP）/ 可选 443（HTTPS） | `ss -ltn` |
| 时间同步 | NTP 已同步 | `timedatectl` |

**Docker 未装时**（Ubuntu 22.04）：

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER     # 让当前用户免 sudo 跑 docker（重新登录生效）
sudo systemctl enable --now docker
```

> Windows / macOS 服务器场景不在此文档覆盖范围。如果你用 Docker Desktop 试装，流程类似但建议读完后用 Linux 复跑。

---

## 3. 一键部署（推荐路径）

### 3.1 拉取代码

```bash
git clone <你的仓库地址> /opt/QuantPilot
cd /opt/QuantPilot
```

> 路径建议放 `/opt/QuantPilot`；后续所有命令默认从该目录执行。如放别处，把 `cron` / `systemd` 路径相应替换。

### 3.2 跑引导脚本

```bash
scripts/bootstrap_prod.sh
```

脚本会按步骤交互引导你：

1. **系统体检** —— 检测 Docker、内存、磁盘、80 端口冲突、时间同步
2. **生成 `.env.prod`** —— 提示输入域名、管理员密码、Tushare/WxPusher Token；自动生成 bcrypt 哈希、JWT 密钥（64 字节 hex）、强随机数据库 / Redis 密码
3. **占位值校验** —— 确保 `.env.prod` 中没有 `CHANGE_ME` 残留
4. **调用 `deploy.sh` 起服务** —— 拉镜像 / 起 db & redis / 跑 `alembic upgrade` / 起 backend & nginx / 健康检查
5. **后续步骤提示** —— 备份 cron、HTTPS、冒烟测试命令

约 5~10 分钟（首次镜像构建占大部分时间）。脚本结束时屏幕会打印访问地址与登录账号。

### 3.3 第一次登录

浏览器访问 `http://<服务器IP>` 或 `http://your-domain.com`：

1. 用脚本中设置的管理员账号登录
2. 系统检测到首次启动 → 自动跳转 **首次启动向导**（5 步）
3. 按引导确认 Tushare Token、初始资金、参数默认值
4. 跳转「总览」页 → 部署完成

---

## 4. 手工部署（理解 / 排查时用）

如果想知道 `bootstrap_prod.sh` 在做什么，或在受限环境无法跑交互脚本，按这套手工流程：

### 4.1 准备 `.env.prod`

```bash
cp .env.prod.example .env.prod
chmod 600 .env.prod
```

按下表填写关键变量：

| 变量 | 说明 | 生成方法 |
|------|------|---------|
| `POSTGRES_PASSWORD` | PostgreSQL 强密码 | `openssl rand -base64 24` |
| `DATABASE_URL` | 与上一行密码同步，**主机必须是 `db`** | `postgresql+asyncpg://quantpilot:<上面的密码>@db:5432/quantpilot` |
| `REDIS_PASSWORD` | Redis 密码 | `openssl rand -base64 24` |
| `REDIS_URL` | 同上 | `redis://:<密码>@redis:6379/0` |
| `ADMIN_USERNAME` | 管理员用户名 | `admin` |
| `ADMIN_PASSWORD_HASH` | bcrypt 哈希（**必须用单引号包裹**） | `docker run --rm python:3.12-slim sh -c "pip install -q bcrypt && python -c \"import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())\""` |
| `JWT_SECRET_KEY` | JWT 签名密钥 | `openssl rand -hex 64` |
| `TUSHARE_TOKEN` | Tushare Pro Token（积分 ≥ 2000） | Tushare 官网注册 |
| `WXPUSHER_APP_TOKEN` / `WXPUSHER_UID` | 微信推送（留空降级为站内信） | WxPusher 服务号控制台 |
| `CORS_ORIGINS` | 前端域名白名单（JSON 数组） | `["https://your-domain.com","http://localhost"]` |
| `HTTP_PORT` | Nginx 对外端口 | `80` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `TZ` | 时区 | `Asia/Shanghai` |

### 4.2 启动服务

```bash
scripts/deploy.sh
```

`deploy.sh` 内部依次：
1. `docker compose build --pull`（拉基础镜像并构建）
2. `docker compose up -d db redis`
3. 等 PostgreSQL `pg_isready`
4. `docker compose up -d frontend-builder backend`（backend 启动时自动 `alembic upgrade head`）
5. `docker compose up -d nginx`
6. `curl http://localhost:${HTTP_PORT}/health` 健康检查

### 4.3 验证部署

```bash
# 健康检查
curl http://localhost/health
# 预期：{"status":"ok","version":"1.0.0"}

# 完整冒烟（登录、关键 API、YAML 导出）
BASE_URL=http://localhost API_PASSWORD=YOUR_PASSWORD scripts/prod_smoke.sh
```

---

## 5. 服务架构与端口

```
                    ┌────────────────────────────────────────┐
        :80 / :443  │              nginx                      │  ← 唯一对外暴露
                    │  • SPA 路由                              │
                    │  • /api/* → backend:8000                │
                    │  • /ws/*  → backend:8000 (WebSocket)    │
                    └─────────────┬──────────────────────────┘
                                  │  internal docker network
                    ┌─────────────┴──────────────┐
                    │     backend:8000           │   FastAPI + APScheduler（单进程）
                    │     /app/logs (volume)     │   alembic upgrade 在容器启动时自动执行
                    └──┬──────────────────────┬──┘
                       │                      │
              ┌────────┴────────┐    ┌────────┴────────┐
              │   db:5432       │    │   redis:6379    │
              │   pg_data vol   │    │   redis_data    │
              └─────────────────┘    └─────────────────┘
```

**对外端口**：仅 nginx 的 80（与可选 443）。**禁止**直接暴露 PostgreSQL / Redis / backend 端口到公网。

**Volumes**（数据卷，`docker compose down` 不会删除，`down -v` 会删除）：

| Volume | 用途 |
|--------|------|
| `pg_data` | PostgreSQL 数据 |
| `redis_data` | Redis AOF 持久化 |
| `frontend_dist` | 前端构建产物（builder → nginx 共享） |
| `backend_logs` | 后端 RotatingFileHandler 滚动日志 |
| `nginx_logs` | nginx 访问日志 |

---

## 6. HTTPS 配置（公网部署强制）

### 6.1 域名 A 记录

将域名 A 记录指向服务器公网 IP，等 DNS 生效（`ping your-domain.com` 看返回 IP）。

### 6.2 申请 Let's Encrypt 证书

确保 80 端口暂时未被占用（先 `docker compose -f docker-compose.prod.yml stop nginx`）：

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d your-domain.com --agree-tos -m you@example.com -n
```

证书生成在 `/etc/letsencrypt/live/your-domain.com/`。

### 6.3 拷贝到项目目录

```bash
sudo mkdir -p ./nginx/ssl
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem ./nginx/ssl/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem ./nginx/ssl/
sudo chmod 644 ./nginx/ssl/fullchain.pem
sudo chmod 600 ./nginx/ssl/privkey.pem
```

### 6.4 修改 nginx 配置

编辑 `nginx/nginx.prod.conf`：

1. **取消注释 HTTP→HTTPS 重定向 server 块**（文件顶部）：
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;
       return 301 https://$server_name$request_uri;
   }
   ```
2. **将主 server 块** `listen 80 default_server;` **注释掉**，并取消下面三行的注释：
   ```nginx
   listen 443 ssl http2;
   ssl_certificate /etc/nginx/ssl/fullchain.pem;
   ssl_certificate_key /etc/nginx/ssl/privkey.pem;
   ssl_protocols TLSv1.2 TLSv1.3;
   ```
3. 把 `server_name _;` 改成 `server_name your-domain.com;`

### 6.5 修改 docker-compose.prod.yml

取消两处注释：

```yaml
ports:
  - "${HTTP_PORT:-80}:80"
  - "443:443"           # ← 取消注释
volumes:
  ...
  - ./nginx/ssl:/etc/nginx/ssl:ro    # ← 取消注释
```

### 6.6 修改 `.env.prod`

```bash
CORS_ORIGINS=["https://your-domain.com"]
```

### 6.7 重启 nginx

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d nginx
```

访问 `https://your-domain.com` 验证。

### 6.8 证书自动续期

```bash
crontab -e
# 每周日凌晨 3 点续期 + 重启 nginx
0 3 * * 0 certbot renew --quiet --deploy-hook "cd /opt/QuantPilot && cp /etc/letsencrypt/live/your-domain.com/*.pem ./nginx/ssl/ && docker compose -f docker-compose.prod.yml restart nginx"
```

---

## 7. 数据备份

### 7.1 每日自动备份（强烈推荐）

```bash
crontab -e
# 每日凌晨 2 点备份；日志写到 logs/backup.log
0 2 * * * cd /opt/QuantPilot && scripts/backup_db.sh >> logs/backup.log 2>&1
```

备份文件：`backups/qp_YYYYMMDD_HHMMSS.sql.gz`，默认保留 30 天（`RETENTION_DAYS=30`，可在脚本顶部改）。

### 7.2 异地备份（可选）

```bash
# 每日 3 点同步备份到对象存储 / NAS（示例：rclone）
0 3 * * * rclone sync /opt/QuantPilot/backups remote:quantpilot-backups
```

### 7.3 手动恢复

```bash
scripts/restore_db.sh backups/qp_20260424_020000.sql.gz
# 按提示输入 yes 确认
```

⚠️ 恢复**会覆盖**当前数据。建议先 `scripts/backup_db.sh` 保存最新状态再恢复历史。

---

## 8. 日志查看

| 类型 | 位置 | 命令 |
|------|------|------|
| 后端实时日志 | Docker stdout | `docker compose -f docker-compose.prod.yml logs -f backend` |
| 后端滚动日志（JSON） | volume `backend_logs` | `docker compose -f docker-compose.prod.yml exec backend tail -f /app/logs/quantpilot.log` |
| Nginx 访问 | volume `nginx_logs` | `docker compose -f docker-compose.prod.yml exec nginx tail -f /var/log/nginx/access.log` |
| Nginx 错误 | 同上 | `tail -f /var/log/nginx/error.log` |
| PostgreSQL | Docker stdout | `docker compose -f docker-compose.prod.yml logs -f db` |
| 备份脚本 | `logs/backup.log` | `tail -f logs/backup.log` |

后端滚动日志单文件 50 MB，保留 7 份归档（SDD §15.5 / Phase 10 §8.4）。

---

## 9. 常用运维命令

```bash
# 查看状态
docker compose -f docker-compose.prod.yml ps

# 重启单个服务
docker compose -f docker-compose.prod.yml restart backend

# 停止 / 启动整站
docker compose -f docker-compose.prod.yml stop
docker compose -f docker-compose.prod.yml start

# 升级（拉新代码后）
git pull
scripts/deploy.sh        # 自动重建镜像、滚动重启

# 回滚
git checkout <previous-commit>
scripts/deploy.sh

# 冒烟自测
BASE_URL=http://localhost API_PASSWORD=YOUR_PASSWORD scripts/prod_smoke.sh

# 进 backend 容器排查
docker compose -f docker-compose.prod.yml exec backend bash

# 直连数据库
docker compose -f docker-compose.prod.yml exec db psql -U quantpilot -d quantpilot

# 查看资源占用
docker stats --no-stream
```

---

## 10. 故障排查

### 10.1 部署阶段

| 症状 | 排查 | 处置 |
|------|------|------|
| `bootstrap_prod.sh` 报 `docker daemon not running` | `systemctl status docker` | `sudo systemctl start docker` |
| `docker compose build` OOM | `free -h` 内存 < 2 GB | 增内存或加 swap：`sudo fallocate -l 2G /swap && sudo mkswap /swap && sudo swapon /swap` |
| `alembic upgrade` 失败：connection refused | db 未起来 | 看 `logs db`；多等 10 秒；检查 `POSTGRES_PASSWORD` 与 `DATABASE_URL` 中密码是否一致 |
| Nginx 启动报 80 端口占用 | `ss -ltn \| grep :80` | 关闭其他占用进程，或改 `HTTP_PORT=8080` 后重 deploy |
| `frontend-builder` 一直 restarting | `logs frontend-builder` | 通常是 `npm install` 网络问题；首次构建耗时 1~3 分钟 |
| 健康检查失败 | `logs backend` | 95% 是 `.env.prod` 缺必填变量或哈希格式错（含 `$` 必须单引号） |

### 10.2 运行期

| 症状 | 可能原因 | 处置 |
|------|---------|------|
| 登录 401 | 密码哈希错误 | 重新生成哈希写入 `.env.prod`，`restart backend` |
| 数据 API 503 `tushare not configured` | `TUSHARE_TOKEN` 缺失 / 积分不足 | 填入有效 Token；账号需 ≥ 2000 积分 |
| 推送收不到微信 | WxPusher Token 缺失 / 服务号未审核 | 检查站内信是否收到（自动降级）；审核通过后填环境变量 |
| 前端白屏 | `frontend-builder` 还没构建完 | `logs frontend-builder`；首次启动等 1~2 分钟 |
| 浏览器报 CORS 错误 | `CORS_ORIGINS` 不含当前域名 | 编辑 `.env.prod` 加上域名，`restart backend` |
| WebSocket 回测进度断线 | nginx `proxy_read_timeout` 超时 | `nginx.prod.conf` 已设 3600s；超出仍断需调大 |
| 磁盘占用高 | PostgreSQL + 滚动日志 + 备份 | `df -h`；`docker system prune -f`；调整 `RETENTION_DAYS` |
| 容器 OOM-killed | 单容器内存超限 | `docker stats`；考虑加 swap 或换大内存机器 |
| 时间不准（信号触发延迟） | NTP 未同步 | `sudo timedatectl set-ntp true` |

### 10.3 数据问题

| 症状 | 处置 |
|------|------|
| 误删数据 / 错配置 | `scripts/restore_db.sh backups/qp_<已知良好时间>.sql.gz` |
| 想清空重来 | `docker compose -f docker-compose.prod.yml down -v` ⚠️ 会删所有数据卷 |
| 表结构不一致（升级失败） | `docker compose exec backend uv run alembic current`；对照 `alembic history`；必要时 `downgrade <prev_rev>` |

---

## 11. 升级与回滚

### 升级（保留数据）

```bash
cd /opt/QuantPilot
git fetch && git log HEAD..origin/main --oneline    # 看新版改了什么
git pull
scripts/deploy.sh
```

`deploy.sh` 会自动：拉新镜像 → 重建受影响容器 → backend 启动时跑 `alembic upgrade head`。**保留 db / redis / 日志 volume**。

### 回滚（保留数据）

```bash
git checkout <previous-commit-or-tag>
scripts/deploy.sh
```

> **注意**：如果新版有破坏性 schema 变更（删字段、改类型），需要同时回滚迁移：`docker compose exec backend uv run alembic downgrade <prev_revision>`。日常增量迁移通常无需手工 downgrade。

### 回滚（同时还原数据）

```bash
git checkout <previous-commit>
scripts/deploy.sh
scripts/restore_db.sh backups/qp_<good_timestamp>.sql.gz
```

---

## 12. 卸载

```bash
# 保留数据卷（可日后恢复）
docker compose -f docker-compose.prod.yml down

# ⚠️ 完全删除（含数据库 + 日志 + 前端构建产物）
docker compose -f docker-compose.prod.yml down -v
```

源代码与 `backups/` 目录脚本不会清理，需手动 `rm -rf`。

---

## 13. 监控（V1.5 规划）

V1.0 依赖：
- Docker 健康检查（`HEALTHCHECK` 在 `Dockerfile.prod` 与 `docker-compose.prod.yml`）
- `/health` 端点
- 滚动日志 + WxPusher 错误推送（`notify_system_error` 模板）

V1.5 计划接入 Prometheus + Grafana（参考 `docs/design/v1_5_roadmap.md`）。

---

## 附录 A：脚本一览

| 脚本 | 用途 |
|------|------|
| `scripts/bootstrap_prod.sh` | **首次部署**：体检 + 生成 `.env.prod` + 调 `deploy.sh` + 后续提示 |
| `scripts/deploy.sh` | 部署/升级：拉镜像 + 起服务 + 健康检查 |
| `scripts/backup_db.sh` | 数据库备份（cron 用） |
| `scripts/restore_db.sh <file>` | 数据库恢复 |
| `scripts/prod_smoke.sh` | 端到端冒烟测试 |

## 附录 B：环境变量速查

完整列表见 `.env.prod.example`。最关键的 7 项：

```bash
POSTGRES_PASSWORD          # 数据库密码
DATABASE_URL               # 必须与上一行一致，主机=db
REDIS_PASSWORD / REDIS_URL # 同上模式
ADMIN_PASSWORD_HASH        # bcrypt 哈希，含 $ 必须单引号
JWT_SECRET_KEY             # ≥ 64 字符随机
TUSHARE_TOKEN              # 积分 ≥ 2000
CORS_ORIGINS               # JSON 数组形式
HTTP_PORT                  # 默认 80
```

---

**至此 QuantPilot V1.0 部署收尾完成，进入运维期。**
