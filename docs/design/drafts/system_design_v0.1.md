# QuantPilot 系统设计文档

> **版本：** v0.1
> **基线依据：** spec_v0.4（规范文档草稿）
> **日期：** 2026-03-05
> **说明：** 初版系统设计草稿，基于 SDD v0.4 编写，已被 v1.0 取代归档。

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-03-05 | 初版草稿（基于 SDD v0.4，未经 SDD 专家评审对齐，已归档） |

---

## 目录

1. [技术栈选型](#1-技术栈选型)
2. [系统架构](#2-系统架构)
3. [项目结构](#3-项目结构)
4. [数据模型](#4-数据模型)
5. [核心模块接口](#5-核心模块接口)
6. [API 端点概览](#6-api-端点概览)
7. [部署方案](#7-部署方案)
8. [TDD 开发策略](#8-tdd-开发策略)
9. [V1.0 开发阶段规划](#9-v10-开发阶段规划)

---

## 1. 技术栈选型

### 1.1 基础技术栈

| 层次 | 技术选型 | 版本要求 | 选型理由 |
|------|----------|----------|----------|
| **语言** | Python | 3.11+ | 量化计算生态无可替代（pandas/numpy/scipy） |
| **Web 框架** | FastAPI | 0.110+ | 异步高性能、自动 OpenAPI 文档、原生 Pydantic 集成 |
| **ORM** | SQLAlchemy | 2.0+ | 成熟稳定，支持异步，声明式模型 |
| **数据库迁移** | Alembic | 1.13+ | SQLAlchemy 官方配套 |
| **数据库** | PostgreSQL | 15+ | 适合时序+关系混合场景，索引能力强 |
| **缓存** | Redis | 7+ | 流水线状态、会话缓存 |
| **任务调度** | APScheduler | 3.10+ | 轻量级，满足日级批处理需求 |
| **前端框架** | Vue 3 + TypeScript | 3.4+ | 轻量、中文社区活跃、适合数据仪表盘 |
| **构建工具** | Vite | 5+ | 快速 HMR，Vue 官方推荐 |
| **UI 组件库** | Ant Design Vue | 4+ | 企业级组件、表格/表单能力强 |
| **图表库** | ECharts | 5+ | 金融图表（K线、指标线）支持最佳，免费 |
| **前端状态管理** | Pinia | 2+ | Vue 3 官方推荐 |
| **容器化** | Docker + Compose | 24+ | 一键部署，环境一致性 |
| **包管理** | uv | latest | 比 pip 快 10-100x，现代 Python 包管理 |

### 1.2 量化计算依赖

| 库 | 版本 | 用途 |
|----|------|------|
| **pandas** | 2.2+ | 时间序列处理、数据对齐、分组计算 |
| **numpy** | 1.26+ | 数值计算基础 |
| **pandas-ta** | 0.3+ | 技术指标（MA/MACD/RSI/ADX/BBands 等） |
| **scipy** | 1.12+ | 统计计算（相关系数、分布检验） |
| **statsmodels** | 0.14+ | 因子回归分析（V1.5 归因） |

> **选 pandas-ta 而非 TA-Lib：** 纯 Python，无 C 编译依赖，跨平台零障碍。性能差异在日线级别可忽略（<5000 只标的，总耗时 <1s）。

### 1.3 数据与通知

| 用途 | 技术 | 说明 |
|------|------|------|
| 数据源（主） | Tushare Pro | 日线行情、基本面、指数成分 |
| 数据源（备） | AKShare | Tushare 缺失指标的补充 |
| 微信通知 | WxPusher | 收盘后推送评分/信号摘要 |

### 1.4 开发与测试

| 用途 | 技术 |
|------|------|
| 测试框架 | pytest 8+ |
| 属性测试 | hypothesis |
| 测试数据工厂 | factory-boy |
| 集成测试 DB | testcontainers-python |
| HTTP 测试客户端 | httpx |
| 覆盖率 | pytest-cov |

---

## 2. 系统架构

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────┐
│                   前端 (Vue 3)                        │
│         仪表盘 / 信号列表 / 持仓管理 / 设置            │
└─────────────────────┬───────────────────────────────┘
                      │ HTTP / WebSocket
┌─────────────────────▼───────────────────────────────┐
│                   API 层 (FastAPI)                    │
│      路由分发 / 请求验证 / 响应序列化 / 认证            │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│                  Service 层                           │
│  DataService / MarketStateService / StrategyService   │
│  SignalService / AccountService / PerformanceService  │
│  NotificationService                                  │
└──────────┬──────────────────────────┬────────────────┘
           │                          │
┌──────────▼──────────┐   ┌───────────▼────────────────┐
│     Engine 层        │   │      Pipeline 层             │
│  纯函数/无 IO        │   │  DailyPipeline (APScheduler) │
│  market_state        │   │  每日收盘后串联各 Service     │
│  universe_filter     │   └────────────────────────────┘
│  strategies (×4)     │
│  scorer / pool       │
│  signal / position   │
│  risk                │
└──────────┬──────────┘
           │
┌──────────▼──────────────────────────────────────────┐
│                   Data 层                             │
│    DataSourceAdapter (Tushare/AKShare)                │
│    Repository (SQLAlchemy async)                      │
│    DataValidator / TradingCalendar                    │
└──────────┬──────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────┐
│                   Model 层                            │
│    SQLAlchemy ORM 模型 / Pydantic Schema              │
│    市场数据 / 业务实体 / 账户 / 系统                   │
└─────────────────────────────────────────────────────┘
                      │
           ┌──────────▼──────────┐
           │  PostgreSQL + Redis  │
           └─────────────────────┘
```

### 2.2 DailyPipeline 主流程

每日收盘后（默认 17:00）由 APScheduler 触发，顺序执行：

```python
class DailyPipeline:
    async def run(self, trade_date: date) -> PipelineResult:
        # 1. 数据采集（网络 IO）
        raw_data = await self.data_service.ingest(trade_date)

        # 2. 市场状态识别（纯计算）
        market_state = self.market_state_service.identify(raw_data.index_history)

        # 3. 股票池筛选（纯计算）
        universe = self.universe_service.filter(raw_data.daily_quotes)

        # 4. 四策略并行评分（CPU 密集，asyncio.gather）
        strategy_scores = await asyncio.gather(*[
            s.score_async(universe, raw_data) for s in self.strategies
        ])

        # 5. 综合评分合成
        composite = self.scorer.aggregate(strategy_scores, market_state)

        # 6. 更新候选股池
        await self.pool_service.update(composite, trade_date)

        # 7. 生成交易信号
        positions = await self.account_service.get_all_positions()
        signals = self.signal_service.generate(composite, positions)
        signals = self.position_sizer.suggest(signals, account, market_state)

        # 8. 持久化 + 通知
        await self.signal_service.save(signals)
        await self.notifier.send(signals)

        return PipelineResult(trade_date=trade_date, signals=signals, ...)
```

### 2.3 市场状态机

```
           牛市信号              熊市信号
  震荡期 ──────────► 趋势牛市 ──────────► 趋势熊市
    ▲                              │
    └──────────────────────────────┘
              震荡信号
```

识别逻辑（基于沪深300）：
- **趋势牛市**：MA20 > MA60，ADX > 25，近 20 日收益 > 5%
- **趋势熊市**：MA20 < MA60，ADX > 25，近 20 日收益 < -5%
- **震荡期**：ADX < 25 或其他情况

---

## 3. 项目结构

```
QuantPilot/
├── backend/
│   ├── src/quantpilot/
│   │   ├── models/                # SQLAlchemy ORM 模型
│   │   │   ├── market.py          # StockInfo, DailyQuote, IndexHistory
│   │   │   ├── business.py        # CandidatePool, Signal, SignalHistory
│   │   │   ├── account.py         # Account, Position, Trade
│   │   │   └── system.py          # PipelineRun, SystemConfig
│   │   ├── schemas/               # Pydantic 请求/响应模型
│   │   ├── data/                  # Data 层
│   │   │   ├── adapters/          # DataSourceAdapter 实现
│   │   │   │   ├── base.py        # DataSourceAdapter ABC
│   │   │   │   ├── tushare.py
│   │   │   │   └── akshare.py
│   │   │   ├── validators.py      # PIT 原则校验、异常值检测
│   │   │   ├── calendar.py        # 交易日历
│   │   │   └── repository.py      # 数据库 CRUD
│   │   ├── engine/                # Engine 层（纯函数，无 IO）
│   │   │   ├── market_state.py    # 市场状态识别
│   │   │   ├── universe.py        # 股票池筛选
│   │   │   ├── strategies/        # 四大策略
│   │   │   │   ├── base.py        # BaseStrategy ABC
│   │   │   │   ├── trend.py       # 趋势跟踪策略
│   │   │   │   ├── mean_reversion.py
│   │   │   │   ├── momentum.py
│   │   │   │   └── value.py       # 价值低估策略
│   │   │   ├── scorer.py          # 综合评分合成
│   │   │   ├── pool.py            # 候选股池管理
│   │   │   ├── signal.py          # 信号生成逻辑
│   │   │   ├── position.py        # 仓位建议（Kelly）
│   │   │   └── risk.py            # 风控检查
│   │   ├── services/              # Service 层（编排，含 IO）
│   │   │   ├── data_service.py
│   │   │   ├── market_state_service.py
│   │   │   ├── strategy_service.py
│   │   │   ├── signal_service.py
│   │   │   ├── account_service.py
│   │   │   ├── performance_service.py
│   │   │   └── notification_service.py
│   │   ├── notification/          # 通知渠道
│   │   │   ├── base.py            # NotificationChannel ABC
│   │   │   └── wxpusher.py
│   │   ├── pipeline/              # 批处理流水线
│   │   │   ├── daily_pipeline.py
│   │   │   └── scheduler.py
│   │   ├── api/                   # FastAPI 路由
│   │   │   ├── deps.py            # 依赖注入
│   │   │   └── v1/
│   │   │       ├── market.py
│   │   │       ├── signals.py
│   │   │       ├── positions.py
│   │   │       ├── account.py
│   │   │       ├── performance.py
│   │   │       └── settings.py
│   │   ├── core/
│   │   │   ├── config.py          # 环境配置（pydantic-settings）
│   │   │   ├── database.py        # DB 连接池
│   │   │   └── exceptions.py
│   │   └── main.py                # FastAPI app 入口
│   ├── tests/
│   │   ├── unit/                  # Engine 层单元测试
│   │   ├── integration/           # Service + DB 集成测试
│   │   └── e2e/                   # API 端到端测试
│   ├── alembic/                   # 数据库迁移脚本
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── views/                 # 页面
│   │   ├── components/            # 组件
│   │   ├── stores/                # Pinia 状态
│   │   ├── api/                   # API 调用封装
│   │   └── router/
│   ├── package.json
│   └── Dockerfile
├── docs/
│   ├── spec/
│   │   ├── QuantPilot_SDD_v1.0.md # 功能规范基线
│   │   └── drafts/                # 历史草稿
│   └── design/
│       ├── system_design_v1.0.md  # 正式架构基线
│       └── drafts/                # 历史草稿
│           └── system_design_v0.1.md  # 本文档
├── docker-compose.yml
├── docker-compose.dev.yml
└── .env.example
```

---

## 4. 数据模型

### 4.1 市场数据表

```sql
-- 股票基础信息
CREATE TABLE stock_info (
    ts_code        VARCHAR(10) PRIMARY KEY,
    name           VARCHAR(50) NOT NULL,
    industry       VARCHAR(50),
    market         VARCHAR(10),
    list_date      DATE,
    delist_date    DATE,
    is_active      BOOLEAN DEFAULT TRUE,
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- 日线行情（存储原始不复权价格 + 复权因子）
CREATE TABLE daily_quote (
    id             BIGSERIAL PRIMARY KEY,
    ts_code        VARCHAR(10) NOT NULL,
    trade_date     DATE NOT NULL,
    open           NUMERIC(10,3),
    high           NUMERIC(10,3),
    low            NUMERIC(10,3),
    close          NUMERIC(10,3),
    pre_close      NUMERIC(10,3),
    pct_chg        NUMERIC(8,4),
    vol            BIGINT,
    amount         NUMERIC(15,3),
    adj_factor     NUMERIC(12,6),
    UNIQUE (ts_code, trade_date)
);
CREATE INDEX idx_daily_quote_date ON daily_quote(trade_date);

-- 指数历史
CREATE TABLE index_history (
    id             BIGSERIAL PRIMARY KEY,
    index_code     VARCHAR(10) NOT NULL,
    trade_date     DATE NOT NULL,
    close          NUMERIC(10,3),
    pct_chg        NUMERIC(8,4),
    UNIQUE (index_code, trade_date)
);
```

### 4.2 业务数据表

```sql
-- 候选股池日快照
CREATE TABLE candidate_pool (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(10) NOT NULL,
    trade_date      DATE NOT NULL,
    composite_score NUMERIC(5,4),
    trend_score     NUMERIC(5,4),
    reversion_score NUMERIC(5,4),
    momentum_score  NUMERIC(5,4),
    value_score     NUMERIC(5,4),
    market_state    VARCHAR(20),
    in_pool         BOOLEAN DEFAULT TRUE,
    UNIQUE (ts_code, trade_date)
);
CREATE INDEX idx_pool_date_score ON candidate_pool(trade_date, composite_score DESC);

-- 交易信号
CREATE TABLE signal (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(10) NOT NULL,
    signal_type     VARCHAR(10) NOT NULL,
    trade_date      DATE NOT NULL,
    score           NUMERIC(5,4),
    suggested_pct   NUMERIC(5,4),
    reason          TEXT,
    status          VARCHAR(10) DEFAULT 'PENDING',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ts_code, trade_date, signal_type)
);
```

### 4.3 账户数据表

```sql
CREATE TABLE account (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) NOT NULL,
    broker          VARCHAR(50),
    total_assets    NUMERIC(15,2),
    cash            NUMERIC(15,2),
    synced_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE position (
    id              BIGSERIAL PRIMARY KEY,
    account_id      INTEGER REFERENCES account(id),
    ts_code         VARCHAR(10) NOT NULL,
    shares          INTEGER NOT NULL,
    cost_price      NUMERIC(10,3),
    current_price   NUMERIC(10,3),
    market_value    NUMERIC(15,2),
    pnl_pct         NUMERIC(8,4),
    open_date       DATE,
    phase           VARCHAR(10),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (account_id, ts_code)
);

CREATE TABLE trade_record (
    id              BIGSERIAL PRIMARY KEY,
    account_id      INTEGER REFERENCES account(id),
    ts_code         VARCHAR(10) NOT NULL,
    trade_type      VARCHAR(10) NOT NULL,
    trade_date      DATE NOT NULL,
    price           NUMERIC(10,3),
    shares          INTEGER,
    amount          NUMERIC(15,2),
    signal_id       BIGINT REFERENCES signal(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### 4.4 系统表

```sql
CREATE TABLE pipeline_run (
    id              BIGSERIAL PRIMARY KEY,
    trade_date      DATE NOT NULL UNIQUE,
    status          VARCHAR(10),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    signal_count    INTEGER,
    error_msg       TEXT
);

CREATE TABLE system_config (
    key             VARCHAR(100) PRIMARY KEY,
    value           TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 5. 核心模块接口

### 5.1 数据源适配器

```python
class DataSourceAdapter(ABC):
    @abstractmethod
    async def fetch_daily_quotes(self, ts_codes: list[str], trade_date: date) -> pd.DataFrame: ...
    @abstractmethod
    async def fetch_stock_list(self) -> pd.DataFrame: ...
    @abstractmethod
    async def fetch_index_history(self, index_code: str, start_date: date, end_date: date) -> pd.DataFrame: ...
    @abstractmethod
    async def fetch_financial_indicators(self, ts_codes: list[str], period: str) -> pd.DataFrame: ...
```

### 5.2 策略基类

```python
class BaseStrategy(ABC):
    name: str
    display_name: str

    @abstractmethod
    def compute_raw_factors(self, universe: pd.DataFrame, market_data: dict) -> pd.DataFrame: ...

    def score(self, universe: pd.DataFrame, market_data: dict) -> list[StrategyScore]:
        raw = self.compute_raw_factors(universe, market_data)
        normalized = self._percentile_normalize(raw)   # 横截面 Rank 百分位
        scores = self._weighted_sum(normalized)
        return [StrategyScore(ts_code=ts, raw_factors=..., score=..., reason=...) for ts in universe.index]
```

### 5.3 信号生成接口

```python
class SignalGenerator:
    def generate(self, composite_scores: pd.DataFrame, current_positions: list[Position],
                 market_state: MarketState, risk_params: RiskParams) -> list[Signal]: ...
```

### 5.4 通知渠道接口

```python
class NotificationChannel(ABC):
    @abstractmethod
    async def send(self, notification: Notification) -> bool: ...
```

---

## 6. API 端点概览

所有接口前缀：`/api/v1`，响应格式：`{"code": 0, "data": ..., "msg": "ok"}`

| 分组 | 方法 | 路径 | 说明 |
|------|------|------|------|
| **市场** | GET | `/market/state` | 当前市场状态 |
| **市场** | GET | `/market/pool` | 候选股池列表 |
| **市场** | GET | `/market/stock/{ts_code}/score` | 单股历史评分走势 |
| **信号** | GET | `/signals` | 今日信号列表 |
| **信号** | GET | `/signals/history` | 历史信号记录 |
| **信号** | PATCH | `/signals/{id}/status` | 更新信号状态 |
| **持仓** | GET | `/positions` | 当前持仓列表 |
| **持仓** | POST | `/positions` | 新增持仓记录 |
| **持仓** | PATCH | `/positions/{id}` | 更新持仓价格/阶段 |
| **账户** | GET | `/account` | 账户概览 |
| **账户** | POST | `/account/sync` | 手动触发账户同步 |
| **账户** | POST | `/account/trades` | 录入成交记录 |
| **绩效** | GET | `/performance/summary` | 绩效摘要 |
| **绩效** | GET | `/performance/history` | 净值曲线历史数据 |
| **系统** | GET | `/pipeline/status` | 流水线运行状态 |
| **系统** | POST | `/pipeline/trigger` | 手动触发日级流水线 |
| **系统** | GET | `/settings` | 获取系统配置 |
| **系统** | PUT | `/settings` | 更新系统配置 |

---

## 7. 部署方案

### 7.1 Docker Compose（生产）

```yaml
version: "3.9"
services:
  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: quantpilot
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER}"]
      interval: 10s

  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data

  backend:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@db/quantpilot
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      TUSHARE_TOKEN: ${TUSHARE_TOKEN}
      WXPUSHER_TOKEN: ${WXPUSHER_TOKEN}
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8000:8000"

  frontend:
    build: ./frontend
    ports:
      - "80:80"
    depends_on:
      - backend

volumes:
  postgres_data:
  redis_data:
```

### 7.2 初始化步骤

```bash
cp .env.example .env
docker compose up -d db redis
docker compose run --rm backend alembic upgrade head
docker compose up -d
curl http://localhost:8000/health
```

---

## 8. TDD 开发策略

| 层次 | 类型 | 工具 | 原则 |
|------|------|------|------|
| Engine | 单元测试 | pytest + hypothesis | 纯函数，无 mock，参数化边界值 |
| Service | 集成测试 | testcontainers + factory-boy | 真实 DB，测试数据隔离 |
| API | E2E 测试 | httpx + pytest | 覆盖主要 happy path + error path |
| Pipeline | 集成测试 | mock 数据源 + 真实 DB | 验证完整日级流程 |

必测场景：market_state 临界值、universe_filter 过滤规则、BaseStrategy 百分位边界、scorer 权重切换、signal 重复信号、position Kelly 截断、risk 集中度阻断。

---

## 9. V1.0 开发阶段规划

| 阶段 | 名称 | 主要交付物 |
|------|------|-----------|
| **Phase 1** | 基础设施 | Docker 环境、DB schema、CI、项目骨架 |
| **Phase 2** | 数据采集层 | DataSourceAdapter、DataValidator、交易日历 |
| **Phase 3** | Engine 核心 | MarketState、UniverseFilter、四大策略 |
| **Phase 4** | 评分合成 | Scorer、CandidatePool 更新逻辑 |
| **Phase 5** | 信号与持仓 | SignalGenerator、PositionSizer、RiskChecker |
| **Phase 6** | 账户管理 | Account/Position/Trade CRUD |
| **Phase 7** | DailyPipeline | 串联各模块、APScheduler 调度 |
| **Phase 8** | API 层 | 全部 REST 端点实现、E2E 测试 |
| **Phase 9** | 前端 | Vue 3 仪表盘、信号列表、持仓管理 |
| **Phase 10** | 通知与收尾 | WxPusher 集成、全链路测试、部署文档 |

---

*本文档为归档草稿，已被 system_design_v1.0.md 取代。*
