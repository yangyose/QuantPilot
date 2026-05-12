# QuantPilot 量化领航

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue 3](https://img.shields.io/badge/Vue-3-42b883.svg)](https://vuejs.org/)

> 个人量化交易决策辅助系统——基于多因子模型，每日收盘后自动采集数据、评分选股、生成交易信号，并通过微信推送给用户。
>
> **当前状态：V1.0 全部 10 个 Phase + V1.0 整改 Batch 1/2/3 全部完成（2026-05-01）；V1.0 真机验收核心通路修复完成（2026-05-12，11 个 bug 修；4 项推迟 V1.5 见 `docs/design/v1_5_roadmap.md` §2.9）**
>
> ⚠️ **风险提示**：本工具仅用于个人学习与决策辅助，**不构成任何投资建议**。所有信号、回测、绩效输出均存在模型局限与数据延迟风险，使用者应自行承担投资决策与风险。

---

## 功能概览

| 模块 | 功能 |
|------|------|
| **市场状态识别** | 基于沪深 300 的 ADX/MA 指标，自动判断 上涨/震荡/下跌 三种市场状态，动态调整仓位系数 |
| **多因子评分** | 趋势、均值回归、动量、价值四大策略并行评分，加权合成 0-100 综合分 |
| **每日信号** | 综合评分 + 持仓状态 + 风险约束，生成 BUY/SELL/HOLD/EXIT 信号，含建议仓位与止损 |
| **风控三层** | 集中度（单股/行业）+ 账户回撤 WARN（B2-1 接入后实际触发），与回测共用同一 `RiskChecker` |
| **因子质量监控** | 月末自动计算各因子 IC 均值/IR，预警衰退或失效因子 |
| **持仓与资金管理** | 实盘账户 / 持仓 / 成交 / 资金流水（DIVIDEND 单独识别）/ DailyPortfolioValue 净值快照 |
| **报告生成** | 周报/月报自动生成，含市场状态、持仓汇总、绩效摘要、因子表现 |
| **WxPusher 通知** | 五类事件（信号/告警/状态/采集失败/Pipeline）统一站内信 + 可选微信渠道，缺 token 自动降级 |
| **回测引擎** | 与实盘共用同一组 Engine 函数；T+1 撮合（默认 OPEN_T1，B3-2 重构）+ 涨停/停牌/退市过滤 + RiskChecker + PIT PE/PB |
| **前端仪表盘** | Vue 3 + Ant Design Vue + ECharts，含仪表盘/信号/持仓/因子监控/报告/回测/设置/Onboarding 八视图 |
| **配置消费链** | ConfigService（12 类 config_key）+ 三级折叠 Settings（L1/L2/L3）+ 字段级 tier 描述 + YAML 导入导出 |
| **合规链条** | DISCLAIMER 反映 V1.0 真实能力（B1-1）+ BacktestLimitationsBanner（B1-2）+ 三视图免责（B1-3）+ LoginView 投顾边界（B2-4）|

---

## 系统架构

```
前端 Vue 3 (Dashboard / Signals / Positions / FactorQuality / Reports / Backtest / Settings / Login / Onboarding)
            │ HTTP REST + WebSocket（回测进度推送）
API 层 FastAPI (JWT 认证 + 统一响应格式 + api/deps.py 集中依赖注入)
            │
Service 层 (Data / MarketState / Strategy / Signal / Account / Settings /
            Config / FactorMonitor / Performance / Backtest / Lineage / Notification / Report)
      ┌─────┴──────┐
  Engine 层          Pipeline 层
  纯函数 / 无 IO      DailyPipeline（每日 17:30）：CP1 → CP2 → CP3 → 盯市 → 自动分红 → 信号过期
  market_state       MonthlyScheduler（月末）：因子监控 IC/IR + 月报
  strategies × 4     BacktestEngine（按需）：T+1 撮合 + RiskChecker + PIT 数据切片
  scorer / risk      Scheduler（APScheduler）：每日 + 月末 + 周报三 Job
  signal / position
  factor_monitor
  backtest / report
            │
Data 层 (TushareAdapter / AkshareAdapter / TradingCalendar / DataValidator /
         AdjustedPriceProvider / MarketDataRepository)
            │
     PostgreSQL 15 + Redis 7
```

**核心约束（SDD §7.7.1）**：BacktestEngine 与 DailyPipeline **共用同一组 Engine 层函数**，策略逻辑禁止分别实现。V1.0 整改 Batch 3 完成后，回测引擎 T+1 撮合 / RiskChecker / PIT PE/PB / delist 过滤 / 涨停停牌过滤等所有行为已与实盘对齐。

---

## 开发进度

### V1.0 Phase（全部完成）

| Phase | 内容 | 状态 |
|-------|------|------|
| **1** | 基础设施（FastAPI 骨架 / ORM 23 表 / Alembic / JWT / 测试框架） | ✅ 完成 |
| **2** | 数据采集层（Tushare 接入 / 行情 / 财务 / 指数 / 调度） | ✅ 完成 |
| **3** | 市场状态识别（ADX+MA 三态 + 防抖动 + REST API） | ✅ 完成 |
| **4** | 因子计算引擎（UniverseFilter / 4 大策略 / Scorer / CandidatePoolManager） | ✅ 完成 |
| **5** | 信号生成（SignalGenerator / PositionSizer / RiskChecker + signals API） | ✅ 完成 |
| **6** | 账户持仓管理（AccountService / SettingsService + 13 端点） | ✅ 完成 |
| **7** | Pipeline + 因子监控 + 报告（DailyPipeline / MonthlyScheduler / FactorMonitor / Report / Lineage） | ✅ 完成 |
| **8** | 绩效归因 + 回测引擎（BacktestEngine + PerformanceService + WS 进度推送） | ✅ 完成 |
| **9** | 前端（Vue 3 仪表盘 9 视图） | ✅ 完成 |
| **10** | 配置消费 + 通知 + 部署收尾（ConfigService / WxPusher / Settings 三级折叠 / YAML / 生产 Docker） | ✅ 完成 |

### V1.0 整改批次（2026-04-27 V1.0 整体评审，全部完成 2026-05-01）

| Batch | 主题 | 子任务 | 状态 |
|-------|------|--------|------|
| **1** | 合规链条 P0（4 项） | B1-1 DISCLAIMER 重写 + B1-2 BacktestLimitationsBanner + B1-3 三视图 DisclaimerBanner + B1-4 SDD §7.7.5 | ✅ |
| **2** | 实盘风控+UX P1（6 项） | B2-1 CP3 max_drawdown_pct + B2-2 record_dividend 排查 + B2-3 闰年 bug + B2-4 LoginView + B2-5 HTTPS 警示 + B2-6 INT-ACC-10/11 + INT-SIG-GEN-01d + LEAP-01~04 | ✅ |
| **3** | 回测引擎重构 P0+P1（10 项） | B3-1~10：DataBundle 全字段 + T+1 撮合 + PE/PB 真实切片 + RiskChecker 集成 + PIT is_st/is_suspended/delist + financials 切片 + DataValidator + 8 处异常合规化 + INT-BE-03~08 集成测试 | ✅ |

### V1.5 路线图

V1.5 完整 scope 共 **50 项**（SDD §16 14 项产品功能 + V1.0 评审 P2/P3 25 项 + SDD 外部专家评审 8 项 + Phase 10 评审 3 项），按 10 主题（V1.5-A..J）打包 ~91-115 pd（6-8 个月）。详见 [docs/design/v1_5_roadmap.md](docs/design/v1_5_roadmap.md)。

---

## 快速开始（开发环境）

### 前置要求

- Docker Desktop 24+（PostgreSQL + Redis + 后端容器）
- Node.js 20+（仅本地启前端 dev server 时需要；不跑前端可跳过）
- Python 3.12 + [uv](https://docs.astral.sh/uv/)（仅本地起后端 / 跑 pytest 时需要；走 Docker 模式可跳过）
- Tushare Pro Token（无 token 时数据采集 API 返回 503，但不影响登录与演示数据浏览）

### 一键引导（推荐）

```bash
# Linux / macOS / Git Bash
scripts/bootstrap_dev.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_dev.ps1
```

脚本会自动：体检 Docker → 生成 `.env`（含 bcrypt 哈希、JWT 密钥、随机数据库密码）→ 启 db/redis/backend → 跑迁移 → 植入演示数据 → 输出登录信息。完成后另开一个终端 `cd frontend && npm install && npm run dev` 即可看到完整界面。

> 详见 [`docs/guides/dev_setup.md`](docs/guides/dev_setup.md)。下面是手动逐步流程，便于排查或定制。

### 手动流程

#### 1. 克隆并配置环境变量

```bash
git clone <repo-url>
cd QuantPilot
cp backend/.env.example backend/.env
```

编辑 `backend/.env`：

```env
# 生成密码哈希
# uv run python -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH='$2b$12$...'   # 含 $ 时须用单引号包裹

# JWT 密钥（64 字符以上）
# openssl rand -hex 64
JWT_SECRET_KEY=...

# Tushare Pro Token（可选；未配置时数据采集端点返回 503）
TUSHARE_TOKEN=...

# Redis：本地开发 localhost；compose 内部部署改为 redis:6379
# 密码含特殊字符（如 # @ /）须按 RFC 3986 URL 编码，例：# → %23
REDIS_URL=redis://:<your_redis_password_url_encoded>@localhost:6379/0
DATABASE_URL=postgresql+asyncpg://quantpilot:<your_db_password_url_encoded>@localhost:5432/quantpilot
```

#### 2. 启动数据库容器 + 迁移

```bash
docker compose -f docker-compose.dev.yml up -d db redis

cd backend
uv sync --group dev
uv run alembic upgrade head
```

#### 3. 灌入演示数据（可选，但推荐首次体验）

```bash
# 在 backend/ 目录
uv run python scripts/seed_demo_data.py
```

植入：1 个账户（总资产 ~120 万）+ 3 只持仓（茅台/五粮液/宁德时代）+ 6 只演示股票 90 日 K 线 + 3 个今日信号 + 5 个历史信号 + 30 日 NAV + HS300 基准 + 5 日市场状态 + 3 因子 IC × 3 月 + 周报/月报。脚本已含 A 股法定节假日过滤。

#### 4. 启动后端 + 前端

```bash
# Terminal 1：后端（backend/ 目录）
uv run uvicorn quantpilot.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2：前端（frontend/ 目录）
npm install
npm run dev
```

### 访问入口

| 服务 | 地址 |
|------|------|
| **前端 UI** | http://localhost:5173/ |
| 后端 API | http://127.0.0.1:8000/ |
| 健康检查 | http://127.0.0.1:8000/health |
| OpenAPI Swagger（DEBUG=true 时） | http://127.0.0.1:8000/docs |

### 全栈 Docker（生产模拟）

```bash
docker compose -f docker-compose.dev.yml up --build
```

---

## 运行测试

所有命令在 `backend/` 目录执行：

```bash
# 全量（unit + e2e + integration，需 db+redis 容器）
uv run pytest tests/ --cov=quantpilot --cov-report=term-missing -v

# 按层级
uv run pytest tests/unit/           # 269 cases，无 DB，秒级
uv run pytest tests/e2e/            # 139 cases，ASGI 内存，无 DB
uv run pytest tests/integration/    # 86 cases，需要 PostgreSQL
API_PASSWORD=xxx uv run pytest tests/smoke/ -v   # 126 cases，需服务运行 + Tushare Token，不入 CI

# Lint
uv run ruff check src/ tests/
uv run ruff check --fix src/ tests/

# 前端
cd frontend && npm run build       # vue-tsc 类型检查 + Vite 构建
```

**当前基线**：unit + e2e + integration = **494 passed**，ruff 0 error，前端 vue-tsc 0 error。

**自动测试钩子**：`.claude/hooks/auto_test.sh` 在编辑 Python 文件后自动运行 unit + e2e；编辑 alembic / 集成测试且容器运行时再加 integration。

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 后端语言 | Python 3.12 |
| Web 框架 | FastAPI + Uvicorn |
| ORM / 迁移 | SQLAlchemy 2.0（asyncio）+ Alembic |
| 数据库 | PostgreSQL 15 + asyncpg |
| 缓存 / 限流 | Redis 7 |
| 认证 | PyJWT + bcrypt（单管理员） |
| 量化计算 | pandas 2.2 / numpy / pandas-ta / scipy / statsmodels |
| 数据源 | Tushare Pro（主）/ AKShare（备） |
| 调度 | APScheduler 3.10 |
| 前端 | Vue 3 + TypeScript + Ant Design Vue + ECharts + Pinia + axios |
| 容器化 | Docker + Compose（dev + prod 两套） |
| 包管理 | uv + hatchling（src layout） |
| 测试 | pytest + anyio + pytest-cov + httpx |
| Lint | ruff（line-length=100, py312） |
| 反代 | nginx（生产前端静态 + 反代 API + WS 升级） |

---

## 项目结构

```
QuantPilot/
├── .claude/                    # Claude Code 配置（auto_test 钩子）
├── docker-compose.dev.yml      # 开发编排（仅 db + redis 默认）
├── docker-compose.prod.yml     # 生产编排（含 backend + frontend + nginx）
├── nginx/nginx.prod.conf       # 生产反代（HTTPS 警示注释见 B2-5）
│
├── backend/
│   ├── src/quantpilot/
│   │   ├── main.py             # FastAPI 应用入口 + lifespan
│   │   ├── core/               # config / database / security / exceptions
│   │   ├── models/             # SQLAlchemy ORM（23 张表 across market.py / business.py / account.py / system.py）
│   │   ├── schemas/            # Pydantic 请求/响应模型
│   │   ├── api/v1/             # REST API 路由
│   │   │   ├── auth / setup / data / market / signals / pipeline
│   │   │   ├── account / positions / settings / settings_yaml
│   │   │   ├── factor_quality / reports / backtest / performance
│   │   │   └── notifications / config
│   │   ├── api/deps.py         # 依赖注入函数（统一存放）
│   │   ├── engine/             # Engine 层，纯函数无 IO
│   │   │   ├── market_state.py     # ADX+MA 三态识别 + 防抖动
│   │   │   ├── universe.py         # UniverseFilter（F-1~F-7 过滤）
│   │   │   ├── strategies/         # 4 大策略：trend / reversion / momentum / value
│   │   │   ├── scorer.py           # 横截面归一化 + 三态加权
│   │   │   ├── pool.py             # CandidatePoolManager（持仓保护 + 白名单）
│   │   │   ├── signal.py           # SignalGenerator（BUY/SELL/HOLD）
│   │   │   ├── position.py         # PositionSizer
│   │   │   ├── risk.py             # RiskChecker（集中度 + 行业 + 回撤）
│   │   │   ├── factor_monitor.py   # IC/IR/半衰期 + 告警
│   │   │   └── backtest/           # BacktestEngine + BacktestReport
│   │   ├── data/               # 数据采集层
│   │   │   ├── adapters/       # tushare / akshare（base 抽象）
│   │   │   ├── calendar.py     # TradingCalendar（A 股交易日历）
│   │   │   ├── price_provider.py   # 后/前复权
│   │   │   ├── repository.py   # MarketDataRepository
│   │   │   └── validators.py   # DataValidator（SDD §5.5 校验）
│   │   ├── services/           # Service 层（含 IO，编排 Engine）
│   │   │   ├── data_service / market_state_service / strategy_service
│   │   │   ├── signal_service / account_service / settings_service
│   │   │   ├── config_service / config_snapshot
│   │   │   ├── factor_monitor_service / report_service / lineage_service
│   │   │   ├── notification_service / wxpusher_adapter
│   │   │   ├── performance_service / backtest_service
│   │   │   └── status_service
│   │   └── pipeline/           # APScheduler 调度
│   │       ├── scheduler.py        # 每日 + 月末 + 周报 3 Job
│   │       ├── daily_pipeline.py   # CP1 → CP2 → CP3 + 盯市 + 自动分红
│   │       └── monthly_scheduler.py # 因子监控 + 月报
│   ├── alembic/versions/       # 8 个迁移：0001~0008（0008 幂等播种默认账户 id=1）
│   ├── scripts/seed_demo_data.py  # 演示数据植入（含 A 股节假日过滤）
│   ├── tests/
│   │   ├── unit/               # 269 cases（无 DB，钩子自动运行）
│   │   ├── e2e/                # 139 cases（ASGI 内存）
│   │   ├── integration/        # 86 cases（需 PostgreSQL）
│   │   └── smoke/              # 126 cases（需服务 + Token，不入 CI）
│   └── pyproject.toml
│
├── frontend/                   # Vue 3 前端
│   └── src/
│       ├── views/              # 9 视图：Login / Onboarding / Dashboard /
│       │                        # Signals / Positions / FactorQuality /
│       │                        # Reports / Backtest / Settings
│       ├── components/         # AppLayout / DisclaimerBanner /
│       │                        # BacktestLimitationsBanner（B1-2）/
│       │                        # NavChart / KlineChart / SignalCard /
│       │                        # NotificationBell / TermLabel / EmptyState
│       ├── stores/             # Pinia: auth / signals / positions / market / backtest
│       ├── api/                # axios 客户端 + REST 端点封装
│       ├── utils/glossary.ts   # 28 项术语 Tooltip 字典
│       └── types/api.ts        # 后端 schema TS 类型
│
└── docs/
    ├── spec/QuantPilot_SDD.md      # 系统规范文档（v1.0-r6）
    ├── design/system_design.md     # 技术架构 + Phase 9 范围
    ├── design/v1_5_roadmap.md      # V1.5 完整 scope（50 项 / 10 主题）
    ├── design/phases/              # phase1 ~ phase10 详细设计
    ├── reviews/                    # 4 份评审报告
    │   ├── v1_overall_review_2026-04-27.md
    │   ├── phase10_design_review_2026-04-20.md
    │   ├── sdd_system_design_review_2026-04-07.md
    │   └── SDD_review_outside_2026-04-22.md  # SDD 外部专家评审（V1.5 8 项）
    └── guides/                     # dev_setup + deployment（一键脚本 + HTTPS / 备份 / 故障排查）
```

---

## API 响应格式

所有接口统一返回：

```json
// 成功
{ "code": 0, "data": { ... }, "msg": "ok" }

// 错误
{ "code": 401, "data": null, "msg": "用户名或密码错误" }

// 参数校验失败（422）
{
  "code": 422, "data": null, "msg": "请求参数校验失败",
  "errors": [{ "field": "body.username", "reason": "Field required" }]
}
```

---

## CI/CD

GitHub Actions 在 `push` 到 `main`/`develop` 或 PR 时执行：

1. `ruff check` 代码检查
2. unit + e2e + integration 全量 pytest（PostgreSQL 15 服务容器；smoke 不入 CI，需 Token）
3. 前端 `npm run build`（vue-tsc 类型检查）

---

## 文档

| 文档 | 说明 |
|------|------|
| [SDD 规范](docs/spec/QuantPilot_SDD.md) | 系统需求与功能规范（权威来源，v1.0-r6） |
| [系统设计](docs/design/system_design.md) | 技术架构、数据模型、API 概览、Phase 划分 |
| [V1.5 路线图](docs/design/v1_5_roadmap.md) | V1.5 完整 scope（50 项 / 10 主题 / ~91-115 pd） |
| [Phase 1~10 详细设计](docs/design/phases/) | 各 Phase 实现细节（含修订历史） |
| [V1.0 整体评审](docs/reviews/v1_overall_review_2026-04-27.md) | 8 P0 + 12 P1 整改清单（已全部完成） |
| [SDD 外部专家评审](docs/reviews/SDD_review_outside_2026-04-22.md) | 机构级量化体系视角，9 项建议（8 入 V1.5） |
| [部署指南](docs/guides/deployment.md) | 生产部署（一键脚本 + HTTPS / 备份 / 升级回滚 / 故障树） |
| [开发环境指南](docs/guides/dev_setup.md) | 项目整体开发环境（一键引导 + 测试体系 + 常见问题） |

---

## 合规与免责

本系统为**个人量化交易决策辅助工具**，**不提供投资建议、不接受委托、不构成投顾服务**。所有市场状态判断、信号、绩效与回测结果仅作为决策辅助参考；回测引擎与实盘存在系统性差异（详见 SDD §7.7.5），任何投资决策与盈亏由用户自行承担。
