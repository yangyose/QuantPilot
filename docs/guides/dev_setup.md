# QuantPilot 开发环境配置指南

> 适用版本：V1.0（Phase 1~10 全量交付 + V1.0 整改批次完成）
> 目标：5 分钟跑起来，30 分钟跑通完整测试矩阵 + 启动前端做手工验证。

---

## 目录

1. [快速通道（一键脚本）](#1-快速通道一键脚本)
2. [前置依赖](#2-前置依赖)
3. [环境变量配置](#3-环境变量配置)
4. [启动方式](#4-启动方式)
   - 4.1 Docker 全栈（推荐）
   - 4.2 本地后端 + Docker 中间件
   - 4.3 前端独立开发服务器
5. [数据库迁移与演示数据](#5-数据库迁移与演示数据)
6. [测试体系](#6-测试体系)
7. [常用命令速查](#7-常用命令速查)
8. [常见问题](#8-常见问题)

---

## 1. 快速通道（一键脚本）

如果你只想跑起来看效果，执行下面三条命令即可——脚本会自动生成 `.env`、启容器、跑迁移、植入演示数据。

```bash
# Linux / macOS / Git Bash
scripts/bootstrap_dev.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_dev.ps1
```

脚本完成后访问：

| 入口 | 地址 |
|------|------|
| 后端 API | http://localhost:8000 |
| API 文档（Swagger） | http://localhost:8000/docs |
| 前端（需另启） | http://localhost:5173 |

默认管理员账号 `admin` / 脚本运行时提示输入的密码。如果走非交互模式（`SKIP_PROMPT=1`）默认密码是 `Quantpilot123!`。

跳过自动化，想理解原理？继续看下文。

---

## 2. 前置依赖

| 工具 | 最低版本 | 用途 | 必装？ |
|------|---------|------|-------|
| Docker Desktop | 最新 | 启 PostgreSQL / Redis / 后端容器 | 是 |
| Docker Compose v2 | 内置于 Docker Desktop | 服务编排 | 是 |
| Python | 3.12 | 仅本地运行后端/测试时需要 | 否（有 Docker 即可） |
| [uv](https://docs.astral.sh/uv/) | 最新 | Python 依赖管理 | 否（同上） |
| Node.js | 20 LTS | 前端 dev server 与 lint | 仅前端开发 |
| Git | 2.30+ | 版本控制 | 是 |

**安装 uv（仅本地后端开发需要）：**

```bash
# macOS / Linux：官方脚本（独立二进制，与 Python 解耦）
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 已有 Python 时也可：pip install uv
```

**安装 Node.js（仅前端开发需要）：**

推荐使用 [nvm](https://github.com/nvm-sh/nvm)（Linux/macOS）或 [nvm-windows](https://github.com/coreybutler/nvm-windows) 切换版本：

```bash
nvm install 20 && nvm use 20
```

---

## 3. 环境变量配置

项目根目录的 `.env` 是开发环境的唯一配置入口。`bootstrap_dev` 脚本会替你生成；如果要手工准备：

### 3.1 复制模板

```bash
cp .env.example .env
```

### 3.2 必填项与生成方法

| 变量 | 说明 | 生成命令 |
|------|------|---------|
| `ADMIN_USERNAME` | 管理员用户名 | 直接填，默认 `admin` |
| `ADMIN_PASSWORD_HASH` | 密码 bcrypt 哈希（**禁止填明文**） | 见 §3.3 |
| `JWT_SECRET_KEY` | JWT 签名密钥，≥ 64 字符随机 | `openssl rand -hex 64` |
| `DB_PASSWORD` | PostgreSQL 密码 | 自定义强密码，避免 `changeme` |
| `REDIS_PASSWORD` | Redis 密码 | 同上 |
| `TUSHARE_TOKEN` | Tushare Pro Token（积分 ≥ 2000） | 留空时数据 API 返回 503，不影响 UI 演示 |

### 3.3 生成 bcrypt 密码哈希

```bash
# 方式 A：使用 backend 容器（推荐，无需本地 Python）
docker compose -f docker-compose.dev.yml run --rm backend \
    python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"

# 方式 B：本地 uv（已装 Python + uv）
cd backend && uv run python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"
```

**注意**：哈希值含 `$` 时，必须用单引号包裹写入 `.env`：

```bash
ADMIN_PASSWORD_HASH='$2b$12$abcdefg...'   # ✅ 单引号
ADMIN_PASSWORD_HASH="$2b$12$abcdefg..."   # ❌ 双引号会被 shell 展开
```

### 3.4 生成 JWT 密钥

```bash
# Linux / macOS / Git Bash
openssl rand -hex 64

# Windows PowerShell（无 openssl 时）
$bytes = New-Object byte[] 64
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
[System.BitConverter]::ToString($bytes).Replace("-","").ToLower()
```

> `.env` 已加入 `.gitignore`，**不要提交到版本控制**。

---

## 4. 启动方式

按场景选一种。

### 4.1 方式 A：Docker 全栈（推荐日常开发）

源码挂载到容器，修改后端 `.py` 自动热重载。

```bash
# 启动 db / redis / backend
docker compose -f docker-compose.dev.yml up -d

# 跟踪日志
docker compose -f docker-compose.dev.yml logs -f backend
```

首次启动需构建镜像（约 2~5 分钟）。后续启动秒级。

| 服务 | 地址 |
|------|------|
| 后端 API | http://localhost:8000 |
| PostgreSQL | localhost:5432 (`quantpilot` / 你设置的 `DB_PASSWORD`) |
| Redis | localhost:6379 (密码 `REDIS_PASSWORD`) |

**首次启动后必跑迁移**（见 §5）。

### 4.2 方式 B：本地后端 + Docker 中间件

适合 IDE 调试断点、跑单元测试。

```bash
# 1) 只启数据库和 Redis
docker compose -f docker-compose.dev.yml up -d db redis

# 2) 调整 .env：DATABASE_URL/REDIS_URL 主机改 localhost
#    DATABASE_URL=postgresql+asyncpg://quantpilot:PASS@localhost:5432/quantpilot
#    REDIS_URL=redis://:PASS@localhost:6379/0

# 3) 启动后端（自动加载 backend/.env）
cd backend
uv sync --group dev
uv run uvicorn quantpilot.main:app --reload --reload-dir src --port 8000
```

如果 `backend/.env` 不存在，可从根目录链过来：

```bash
# Linux / macOS
ln -s ../.env backend/.env

# Windows PowerShell（管理员）
New-Item -ItemType SymbolicLink -Path backend\.env -Target ..\.env
```

### 4.3 方式 C：前端独立开发服务器

后端启动后，前端 vite dev server 通过 `vite.config.ts` 中的 proxy 转发 `/api/*` 到后端 8000：

```bash
cd frontend
npm install        # 首次或 package.json 更新后
npm run dev        # http://localhost:5173
```

修改前端代码后浏览器自动刷新（HMR）。

---

## 5. 数据库迁移与演示数据

### 5.1 迁移（必跑）

后端启动后第一件事：建表。共 7 个版本（0001~0007）覆盖 23 张表。

```bash
# Docker 模式
docker compose -f docker-compose.dev.yml exec backend uv run alembic upgrade head

# 或一次性容器
docker compose -f docker-compose.dev.yml run --rm backend uv run alembic upgrade head

# 本地模式
cd backend && uv run alembic upgrade head
```

预期最后一行：`Running upgrade 0006_... -> 0007_phase10_config_and_notifications`。

### 5.2 演示数据（可选，仅用于 UI 验收）

`backend/scripts/seed_demo_data.py` 会植入：账户 1 + 50 万初始资金、2 只持仓、HS300 30 日基准、最近 30 个交易日的快照与信号、市场状态历史等，让所有前端页面有数据可展示。

```bash
# Docker 模式
docker compose -f docker-compose.dev.yml exec backend uv run python scripts/seed_demo_data.py

# 本地模式
cd backend && uv run python scripts/seed_demo_data.py
```

**注意**：脚本会清空 `signal` / `trade_record` / `position` 等业务表后重建，仅用于开发演示。生产环境严禁执行。脚本内置 A 股法定节假日表（覆盖 2025~2026），自动跳过周末与节假日。

### 5.3 创建新迁移

修改 ORM 后：

```bash
cd backend
uv run alembic revision --autogenerate -m "添加 xxx 字段"
# 检查生成的 alembic/versions/NNNN_*.py
uv run alembic upgrade head
```

**禁止**手工修改已发布的迁移文件。需修正时新增一份递进迁移。

---

## 6. 测试体系

测试分四层，各层依赖与运行时间不同：

| 类型 | 目录 | 数量 | 依赖 | 平均耗时 |
|------|------|------|------|---------|
| 单元测试 | `backend/tests/unit/` | ~270 | 无 DB | < 5 s |
| E2E 测试 | `backend/tests/e2e/` | ~140 | ASGITransport，无 DB | ~10 s |
| 集成测试 | `backend/tests/integration/` | ~85 | PostgreSQL | ~30 s |
| 冒烟测试 | `backend/tests/smoke/` | ~125 | 服务运行中 + `API_PASSWORD` | ~20 s |

### 6.1 Claude Code 工程化资产（hooks / skills）

`.claude/` 下的 hooks 与 skills **已入库**，clone 到新机器即随仓库带过来。但它们有运行时依赖，换机后需按本节确认可用，否则会**静默失效**（比没有更危险——给人"有守卫"的错觉）。

**入库 vs 本机**：
- 入库（共享）：`.claude/settings.json`（hook 注册）、`.claude/hooks/*`、`.claude/skills/*`
- 本机（gitignore）：`.claude/settings.local.json`（个人权限/偏好，不随仓库）

**已配置的 hooks**：
- `auto_test.sh`（PostToolUse: Edit|Write）：编辑 `backend/**/*.py` 后自动跑 `unit/`+`e2e/`；编辑 `alembic/` 或 `tests/integration/` 且 PG 容器在线时加跑 `integration/`；失败回传 Claude 自动调试。
- `guard.sh` + `guard.py`（PreToolUse: Bash|Edit|Write）：强制宪法红线——生产破坏性动作(prod 信号 AND 破坏性)弹确认、`git add -A/./--all` 拒绝、测试文件写入 `@pytest.mark.anyio` 拒绝。

**已配置的 skills**（对话中按 description 自动触发，也可 `/<name>` 显式调）：
- `prod-healthcheck`：生产体检/补跑/回填 OOM 恢复运行手册（含「换机适配」段）。
- `phase-kickoff` / `phase-closeout`：Phase 启动/收尾核查（对应本文 §5 + 项目 CLAUDE.md §5）。

#### 换机后必做：依赖与重定向校验

1. **Python 解释器**（hooks 解析 JSON 依赖）：需 PATH 上有可用的 `python` / `py` / `python3` 之一。
   ⚠️ 本机 `python3` 是坏的 Windows Store 别名桩（`python3 --version` 输出 `Python` 后 exit 49），故两个 hook 都按 `python→py→python3` 顺序探测**真能跑**的解释器；新机器若三者全无，hook 会 fail-open 放行（不阻断，但红线守卫等于失效）——确保至少装一个真 Python 3。
2. **Shell**：hook 是 `.sh`，非 Windows 原生跑；Windows 上由 Claude Code 经 Git Bash 执行（须装 Git for Windows）。
3. **guard.py 生产信号重定向**（**安全关键**）：`guard.py` 靠正则识别"是否针对生产"——
   `docker-compose.prod.yml | .env.prod | quantpilot-(db|backend|redis|nginx)-1`。
   容器名 = compose 项目名（默认 = 仓库目录名小写）+ 服务名。**若 clone 到非 `QuantPilot` 目录、或设了 `COMPOSE_PROJECT_NAME`，容器前缀变化 → 正则漏判 → 生产破坏性动作不再弹确认**。换机后务必核对并改 `guard.py` 里的前缀正则（或统一项目名）。
4. **prod-healthcheck skill**：按其文内「换机适配」段重定向容器名（动态发现）+ 确认 `.env.prod` 凭据。

#### 验证 hooks 可用（人工在终端跑——hook 只拦 Claude 的工具调用，不拦你手敲的命令）

```bash
# guard.py：三条规则各验一例
echo '{"tool_name":"Bash","tool_input":{"command":"docker exec quantpilot-db-1 psql -c \"DROP TABLE x\""}}' | python .claude/hooks/guard.py   # 期望输出 permissionDecision=ask
echo '{"tool_name":"Bash","tool_input":{"command":"uv run pytest tests/integration/"}}'                    | python .claude/hooks/guard.py   # 期望无输出（放行：本地/测试库不拦）
echo '{"tool_name":"Bash","tool_input":{"command":"git add -A"}}'                                          | python .claude/hooks/guard.py   # 期望 permissionDecision=deny

# auto_test.sh：解析能力（输出 PARSED: 路径 即正常；输出空/报错说明 python 不可用）
echo '{"tool_input":{"file_path":"backend/src/x.py"}}' | python -c "import sys,json;print('PARSED:',json.load(sys.stdin).get('tool_input',{}).get('file_path',''))"
```

#### 修改 skills / hooks

- skill 改 `.claude/skills/<name>/SKILL.md`（frontmatter `name`+`description` 驱动自动触发；description 写满触发词命中率更高）。新建/改完通常**新开会话**才进可用列表。
- hook 改逻辑后用上面的命令离线验证；改 `guard.py` 正则后务必重跑三条用例。改 `settings.json` 的 matcher/命令后，hook 下次工具调用即生效。

### 6.2 手动运行

```bash
cd backend

# 全量（含覆盖率）
uv run pytest tests/ --cov=quantpilot --cov-report=term-missing -v

# 按层
uv run pytest tests/unit/ -v
uv run pytest tests/e2e/ -v
# ⚠️ 集成测试会 `alembic downgrade base` DROP 全部表——必须连独立测试库（:5433），
#    绝不能对含真实数据的 :5432 跑（conftest 有硬护栏：非 :5433 直接中止）。
docker run -d --name quantpilot-testdb-5433 -e POSTGRES_USER=quantpilot \
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=quantpilot -p 5433:5432 postgres:15
DATABASE_URL=postgresql+asyncpg://quantpilot:test@localhost:5433/quantpilot \
  uv run pytest tests/integration/ -v
docker rm -f quantpilot-testdb-5433   # 跑完清理

# 冒烟（需先启服务）
API_PASSWORD=YOUR_PASSWORD uv run pytest tests/smoke/ -v

# 关键字过滤
uv run pytest tests/ -k "test_login_success" -v

# 单文件
uv run pytest tests/unit/test_security.py -v
```

⚠️ **集成测试禁止并发**：多进程会导致 DB schema 竞态。CI 与本地都用单进程跑。

### 6.3 前端类型检查与 lint

```bash
cd frontend
npm run type-check    # vue-tsc
npm run lint          # eslint
npm run build         # 生产构建（顺带类型检查）
```

### 6.4 后端 lint（必须 0 error 才能合并）

```bash
cd backend
uv run ruff check src/ tests/          # 检查
uv run ruff check src/ tests/ --fix    # 自动修复
```

---

## 7. 常用命令速查

```bash
# === 启动/停止 ===
docker compose -f docker-compose.dev.yml up -d              # 启动全栈
docker compose -f docker-compose.dev.yml up -d db redis     # 仅启中间件
docker compose -f docker-compose.dev.yml down               # 停止保留数据
docker compose -f docker-compose.dev.yml down -v            # 停止并清空数据卷
docker compose -f docker-compose.dev.yml logs -f backend    # 跟踪后端日志
docker compose -f docker-compose.dev.yml ps                 # 查看状态

# === 后端 ===
cd backend
uv sync --group dev                                          # 装依赖
uv run uvicorn quantpilot.main:app --reload --port 8000     # 本地启动
uv run alembic upgrade head                                  # 迁移
uv run alembic revision --autogenerate -m "msg"             # 生成迁移
uv run alembic current                                       # 当前版本
uv run alembic history --verbose                             # 历史
uv run python scripts/seed_demo_data.py                     # 植入演示数据

# === 前端 ===
cd frontend
npm install
npm run dev                                                  # http://localhost:5173
npm run build
npm run type-check

# === 测试 ===
cd backend
uv run pytest tests/unit/ tests/e2e/ -q                     # 快速反馈
DATABASE_URL=...:5433/... uv run pytest tests/integration/ -v   # ⚠️ 必须连 :5433 测试库（会 DROP 全表）
uv run pytest tests/ --cov=quantpilot --cov-report=html     # HTML 覆盖率
API_PASSWORD=xxx uv run pytest tests/smoke/ -v              # 冒烟（需服务在线）

# === 代码检查 ===
cd backend && uv run ruff check src/ tests/ --fix
cd frontend && npm run lint && npm run type-check

# === 数据库直连（容器） ===
docker compose -f docker-compose.dev.yml exec db psql -U quantpilot -d quantpilot
# 进入 psql 后：\dt 查看表，\q 退出

# === 验证 API ===
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"YOUR_PASSWORD"}'
```

---

## 8. 常见问题

### Q1: `settings` 报错 `field required: ADMIN_PASSWORD_HASH`

**原因**：`.env` 缺少必填变量，或运行目录不对（`pydantic-settings` 优先读环境变量，再读 `.env`）。

**解决**：
- Docker 模式：确认根目录有 `.env` 且变量已填
- 本地模式：确认 `backend/.env` 存在（或用软链）

---

### Q2: `alembic upgrade head` 报 `connection refused`

**原因**：PostgreSQL 未启动 / `DATABASE_URL` 主机名不匹配。

**解决**：
- Docker 模式：`docker compose ps` 确认 `db` healthy；`DATABASE_URL` 主机用 `db`
- 本地模式：`DATABASE_URL` 主机改 `localhost`

---

### Q3: 前端访问 `/api/*` 报 502 / ECONNREFUSED

**原因**：vite 代理目标（默认 `http://localhost:8000`）后端没起来。

**解决**：先启后端（方式 A 或 B），再 `npm run dev`。

---

### Q4: 集成测试报 `relation "xxx" already exists`

**原因**：上次测试未完成 downgrade。

**解决**：
```bash
cd backend && uv run alembic downgrade base && uv run alembic upgrade head
```

---

### Q5: 集成测试随机 `event loop is closed`

**原因**：`db_engine` fixture 没用 `NullPool`，连接被跨 event loop 复用。

**解决**：参考已有集成测试的 fixture 写法（`poolclass=NullPool`）。

---

### Q6: `bcrypt` 在 Windows 上报 `Microsoft Visual C++ 14.0 is required`

**原因**：bcrypt 编译需 C++ 工具链。

**解决**：用 Docker 容器生成哈希（见 §3.3 方式 A），或装 [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)。

---

### Q7: Docker backend 容器启动后立即退出

**排查**：

```bash
docker compose -f docker-compose.dev.yml logs backend
```

最常见原因：
1. `.env` 缺必填项
2. `db` 还未 healthy（等 ~10 秒重启 backend）
3. ORM 与迁移版本不一致（先 `alembic upgrade head`）

---

### Q8: 数据 API 全部返回 503 `TUSHARE_TOKEN not configured`

**原因**：未配置 Tushare Token。

**说明**：UI 演示不依赖 Tushare（`seed_demo_data.py` 已造好数据）。需要真实数据采集时填入 Token；积分要求 ≥ 2000（`daily_basic` 等接口的最低门槛）。

---

### Q9: 改动 `.env` 后 backend 不生效

**原因**：环境变量在容器启动时注入，运行时不读 `.env`。

**解决**：
```bash
docker compose -f docker-compose.dev.yml up -d --force-recreate backend
```

---

### Q10: 想恢复成"干净状态"重来一次

```bash
docker compose -f docker-compose.dev.yml down -v   # 清数据
rm .env                                            # 删配置
scripts/bootstrap_dev.sh                           # 重新跑一键
```

---

**参考文档**：

- 规范：`docs/spec/QuantPilot_SDD.md`
- 系统设计：`docs/design/system_design.md`
- Phase N 详细设计：`docs/design/phases/phaseN_*.md`
- 生产部署：`docs/guides/deployment.md`
