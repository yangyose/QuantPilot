# Phase 1：基础设施

> **版本：** v0.1（归档版）
> **所属阶段：** Phase 1 / 10
> **依据文档：** system_design_v1.0.md
> **日期：** 2026-03-05
> **说明：** 本文档为归档版本，已被 v1.0（专家审查优化版）取代，仅供历史参考。
> **预期产出：** 可运行的项目骨架 + 完整 DB schema + JWT 认证框架 + Docker 环境

---

## 目录

1. [阶段目标与交付物](#1-阶段目标与交付物)
2. [前置条件](#2-前置条件)
3. [完整 DB Schema](#3-完整-db-schema)
4. [Alembic 迁移设计](#4-alembic-迁移设计)
5. [项目骨架规格](#5-项目骨架规格)
6. [JWT 认证实现规格](#6-jwt-认证实现规格)
7. [Docker 配置规格](#7-docker-配置规格)
8. [CI 配置](#8-ci-配置)
9. [测试用例](#9-测试用例)
10. [验收标准（DoD）](#10-验收标准dod)

---

## 1. 阶段目标与交付物

### 1.1 目标

建立后续所有阶段的开发底座：

- 一条命令启动完整开发环境（`docker compose -f docker-compose.dev.yml up`）
- 完整 DB schema 一次性建立，避免后续频繁 schema 变更
- JWT 认证骨架就位，后续所有 API 基于此鉴权
- 项目目录结构与 `pyproject.toml` 固定，后续只添加不改动结构

### 1.2 主要交付物

| 交付物 | 说明 |
|--------|------|
| `docker-compose.yml` | 生产环境：PostgreSQL + Redis + Backend + Frontend |
| `docker-compose.dev.yml` | 开发环境：hot reload，挂载源码 |
| `backend/Dockerfile` | 后端镜像 |
| `backend/pyproject.toml` | 完整依赖声明 |
| `backend/alembic/` | 迁移配置 + 初始迁移（含全部 18 张表） |
| `backend/src/quantpilot/models/` | 全部 SQLAlchemy ORM 模型 |
| `backend/src/quantpilot/core/` | config / database / security / exceptions |
| `POST /api/v1/auth/login` | 登录端点，返回 JWT |
| `POST /api/v1/auth/refresh` | 刷新 access_token |
| `GET /health` | 健康检查（无需鉴权） |
| `.env.example` | 完整环境变量模板 |
| `.github/workflows/ci.yml` | 基础 CI：lint + unit test |

---

## 2. 前置条件

- Python 3.11+ 和 `uv` 已安装（本地开发）
- Docker Desktop 已安装
- 无代码存量（从零开始）

---

## 3. 完整 DB Schema

本阶段一次性建立全部 18 张表，不预留后续 schema 变更空间。

### 3.1 市场数据表

```sql
-- 股票基础信息（含已退市股，用于幸存者偏差消除）
CREATE TABLE stock_info (
    ts_code        VARCHAR(10) PRIMARY KEY,
    name           VARCHAR(50) NOT NULL,
    industry       VARCHAR(50),
    sw_industry_l1 VARCHAR(20),
    sw_industry_l2 VARCHAR(20),
    market         VARCHAR(10),       -- 'MAIN'/'SME'/'GEM'/'STAR'
    list_date      DATE,
    delist_date    DATE,              -- NULL 表示仍上市
    is_active      BOOLEAN DEFAULT TRUE,
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- 日线行情（原始不复权 + 累乘复权因子）
CREATE TABLE daily_quote (
    id             BIGSERIAL PRIMARY KEY,
    ts_code        VARCHAR(10) NOT NULL REFERENCES stock_info(ts_code),
    trade_date     DATE NOT NULL,
    open           NUMERIC(10,3),
    high           NUMERIC(10,3),
    low            NUMERIC(10,3),
    close          NUMERIC(10,3),
    pre_close      NUMERIC(10,3),
    pct_chg        NUMERIC(8,4),
    vol            BIGINT,
    amount         NUMERIC(15,3),
    turnover_rate  NUMERIC(8,6),
    float_mkt_cap  NUMERIC(18,2),
    adj_factor     NUMERIC(12,6),    -- 累乘复权因子（上市首日基准 = 1.0）
    is_suspended   BOOLEAN DEFAULT FALSE,
    is_st          BOOLEAN DEFAULT FALSE,
    limit_up       BOOLEAN DEFAULT FALSE,
    limit_down     BOOLEAN DEFAULT FALSE,
    UNIQUE (ts_code, trade_date)
);
CREATE INDEX idx_daily_quote_date ON daily_quote(trade_date);
CREATE INDEX idx_daily_quote_code ON daily_quote(ts_code, trade_date DESC);

-- 财务数据（PIT 存储，publish_date 为实际可用时点）
CREATE TABLE financial_data (
    id             BIGSERIAL PRIMARY KEY,
    ts_code        VARCHAR(10) NOT NULL REFERENCES stock_info(ts_code),
    report_period  DATE NOT NULL,    -- 报告期末日，如 2025-09-30
    publish_date   DATE NOT NULL,    -- 公告发布日（PIT 时点）
    pe_ttm         NUMERIC(10,4),
    pb             NUMERIC(8,4),
    roe            NUMERIC(8,6),     -- 净资产收益率（小数形式）
    net_profit_yoy NUMERIC(8,4),
    revenue_yoy    NUMERIC(8,4),
    dividend_yield NUMERIC(8,6),
    total_equity   NUMERIC(18,2),   -- 净资产（负值保留，用于过滤）
    debt_to_asset  NUMERIC(8,6),    -- 资产负债率（小数形式）
    UNIQUE (ts_code, report_period, publish_date)
);
CREATE INDEX idx_financial_code_publish ON financial_data(ts_code, publish_date DESC);

-- 指数历史（市场状态识别，主要使用 000300.SH 沪深300）
CREATE TABLE index_history (
    id             BIGSERIAL PRIMARY KEY,
    index_code     VARCHAR(10) NOT NULL,
    trade_date     DATE NOT NULL,
    close          NUMERIC(10,3),
    pct_chg        NUMERIC(8,4),
    UNIQUE (index_code, trade_date)
);
CREATE INDEX idx_index_history_code_date ON index_history(index_code, trade_date DESC);
```

### 3.2 业务数据表

```sql
-- 市场状态历史
CREATE TABLE market_state_history (
    id              BIGSERIAL PRIMARY KEY,
    trade_date      DATE NOT NULL UNIQUE,
    market_state    VARCHAR(20) NOT NULL, -- 'UPTREND'/'DOWNTREND'/'OSCILLATION'
    trend_strength  NUMERIC(5,2),         -- 0-100，ADX 归一化
    adx_value       NUMERIC(6,3),
    ma20            NUMERIC(10,3),
    ma60            NUMERIC(10,3),
    state_changed   BOOLEAN DEFAULT FALSE,
    description     TEXT
);

-- 候选股池日快照
CREATE TABLE candidate_pool (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(10) NOT NULL REFERENCES stock_info(ts_code),
    trade_date      DATE NOT NULL,
    composite_score NUMERIC(5,2),         -- 0-100 综合得分（横截面百分位）
    trend_score     NUMERIC(5,2),
    reversion_score NUMERIC(5,2),
    momentum_score  NUMERIC(5,2),
    value_score     NUMERIC(5,2),
    market_state    VARCHAR(20),
    in_pool         BOOLEAN DEFAULT TRUE,
    is_holding      BOOLEAN DEFAULT FALSE, -- 是否持仓标的（持仓标的强制留池）
    UNIQUE (ts_code, trade_date)
);
CREATE INDEX idx_pool_date_score ON candidate_pool(trade_date, composite_score DESC);
CREATE INDEX idx_pool_code_date  ON candidate_pool(ts_code, trade_date DESC);

-- 交易信号
CREATE TABLE signal (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(10) NOT NULL REFERENCES stock_info(ts_code),
    signal_type     VARCHAR(10) NOT NULL,  -- 'BUY'/'SELL'/'HOLD'/'EXIT'
    trade_date      DATE NOT NULL,
    score           NUMERIC(5,2),          -- 0-100 综合评分
    suggested_pct   NUMERIC(5,4),          -- 建议仓位占总资产比例（小数）
    suggested_price_low  NUMERIC(10,3),    -- 买入价区间下限（收盘价×0.99）
    suggested_price_high NUMERIC(10,3),    -- 买入价区间上限（收盘价×1.02）
    stop_loss_price NUMERIC(10,3),
    signal_strength VARCHAR(10),           -- 'STRONG'(≥90)/'MODERATE'(80-89)；仅买入信号
    liquidity_note  TEXT,                  -- 流动性提示（日均成交额不足时填写）
    t1_warning      TEXT,                  -- T+1 风险提示（买入信号必填）
    reason          TEXT,
    status          VARCHAR(15) DEFAULT 'NEW', -- 'NEW'/'VIEWED'/'ACTED'/'EXPIRED'/'SUPERSEDED'
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ts_code, trade_date, signal_type)
);
CREATE INDEX idx_signal_code_date ON signal(ts_code, trade_date DESC);
CREATE INDEX idx_signal_date_type ON signal(trade_date, signal_type);

-- 信号-评分快照（数据血缘 V1.0 最小实现）
CREATE TABLE signal_score_snapshot (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       BIGINT NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
    trade_date      DATE NOT NULL,
    ts_code         VARCHAR(10) NOT NULL,
    composite_score NUMERIC(5,2),
    trend_score     NUMERIC(5,2),
    reversion_score NUMERIC(5,2),
    momentum_score  NUMERIC(5,2),
    value_score     NUMERIC(5,2),
    market_state    VARCHAR(20),
    score_breakdown JSONB,               -- 各策略得分明细（含权重贡献）
    raw_factors     JSONB                -- 原始因子值（V1.5 溯源使用）
);
CREATE INDEX idx_snapshot_signal ON signal_score_snapshot(signal_id);

-- 因子质量监控历史（每月末计算）
CREATE TABLE factor_ic_history (
    id              BIGSERIAL PRIMARY KEY,
    calc_month      DATE NOT NULL,        -- 计算月份（月末日期，如 2026-02-28）
    strategy_name   VARCHAR(30) NOT NULL, -- 'trend'/'mean_reversion'/'momentum'/'value'
    factor_name     VARCHAR(50) NOT NULL,
    ic_value        NUMERIC(8,6),         -- 当月 Rank IC（Spearman 秩相关系数）
    ic_mean_3m      NUMERIC(8,6),
    ic_std_3m       NUMERIC(8,6),
    ir_3m           NUMERIC(8,6),         -- IC 均值 / IC 标准差
    half_life_days  NUMERIC(6,1),
    return_window   INTEGER DEFAULT 20,   -- 下期收益窗口（交易日）
    alert_status    VARCHAR(20),          -- NULL/'DECAY'/'INEFFICIENT'/'FAST_DECAY'
    UNIQUE (calc_month, strategy_name, factor_name, return_window)
);
CREATE INDEX idx_ic_history_strategy ON factor_ic_history(strategy_name, calc_month DESC);

-- 报告存储（周报/月报/自定义）
CREATE TABLE report (
    id              BIGSERIAL PRIMARY KEY,
    report_type     VARCHAR(15) NOT NULL, -- 'WEEKLY'/'MONTHLY'/'CUSTOM'
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    content         JSONB NOT NULL,       -- 结构化报告数据
    summary         TEXT,
    generated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_report_type_period ON report(report_type, period_end DESC);

-- 用户黑白名单
CREATE TABLE user_watchlist (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(10) NOT NULL REFERENCES stock_info(ts_code),
    list_type       VARCHAR(10) NOT NULL, -- 'WHITELIST'/'BLACKLIST'
    reason          TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ts_code, list_type)
);
```

### 3.3 账户数据表

```sql
CREATE TABLE account (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) NOT NULL,
    account_type    VARCHAR(10) DEFAULT 'REAL', -- 'REAL'/'PAPER'
    broker          VARCHAR(50),
    total_assets    NUMERIC(15,2),
    cash            NUMERIC(15,2),
    synced_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE position (
    id              BIGSERIAL PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES account(id),
    ts_code         VARCHAR(10) NOT NULL REFERENCES stock_info(ts_code),
    shares          INTEGER NOT NULL,
    cost_price      NUMERIC(10,3),
    current_price   NUMERIC(10,3),
    market_value    NUMERIC(15,2),
    pnl_pct         NUMERIC(8,4),
    open_date       DATE,
    phase           VARCHAR(10),          -- 'BUILD'/'HOLD'/'REDUCE'
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (account_id, ts_code)
);

CREATE TABLE trade_record (
    id              BIGSERIAL PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES account(id),
    ts_code         VARCHAR(10) NOT NULL REFERENCES stock_info(ts_code),
    trade_type      VARCHAR(10) NOT NULL, -- 'BUY'/'SELL'
    trade_date      DATE NOT NULL,
    price           NUMERIC(10,3),
    shares          INTEGER,
    amount          NUMERIC(15,2),
    commission      NUMERIC(10,2),
    stamp_tax       NUMERIC(10,2),
    signal_id       BIGINT REFERENCES signal(id),  -- 关联信号（策略归因用）
    note            TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_trade_record_account_date ON trade_record(account_id, trade_date DESC);

-- 资金流水（完整资金变动记录）
CREATE TABLE fund_flow (
    id              BIGSERIAL PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES account(id),
    flow_type       VARCHAR(15) NOT NULL,
        -- 'DEPOSIT'（入金）
        -- 'WITHDRAW'（出金）
        -- 'DIVIDEND'（分红）
        -- 'BUY_FEE'（买入扣款，含成本）
        -- 'SELL_PROCEEDS'（卖出回款，扣除税费后）
    amount          NUMERIC(15,2) NOT NULL, -- 正值=流入，负值=流出
    trade_date      DATE NOT NULL,
    ts_code         VARCHAR(10),            -- 分红时关联股票（可选）
    related_trade_id BIGINT REFERENCES trade_record(id),
    note            TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_fund_flow_account_date ON fund_flow(account_id, trade_date DESC);
```

### 3.4 系统表

```sql
-- 流水线运行记录（含 CP1/CP2/CP3 检查点）
CREATE TABLE pipeline_run (
    id                    BIGSERIAL PRIMARY KEY,
    trade_date            DATE NOT NULL UNIQUE,
    status                VARCHAR(10),   -- 'RUNNING'/'SUCCESS'/'FAILED'
    started_at            TIMESTAMPTZ,
    finished_at           TIMESTAMPTZ,
    signal_count          INTEGER,
    error_msg             TEXT,
    cp1_data_ready        BOOLEAN DEFAULT FALSE,
    cp1_at                TIMESTAMPTZ,
    data_snapshot_version VARCHAR(64),   -- 输入数据版本号（幂等性保障）
    cp2_scoring_done      BOOLEAN DEFAULT FALSE,
    cp2_at                TIMESTAMPTZ,
    cp3_signals_done      BOOLEAN DEFAULT FALSE,
    cp3_at                TIMESTAMPTZ
);

-- 系统运维配置（Token、调度时间等，管理员维护）
CREATE TABLE system_config (
    key             VARCHAR(100) PRIMARY KEY,
    value           TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 用户业务配置（分层，区别于 system_config）
CREATE TABLE user_config (
    id              BIGSERIAL PRIMARY KEY,
    config_key      VARCHAR(100) NOT NULL UNIQUE,
    config_value    JSONB NOT NULL,
    user_level      VARCHAR(5) NOT NULL DEFAULT 'L2', -- 该项所需最低层级
    description     TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 用户配置变更历史（支持 API 回退）
CREATE TABLE user_config_history (
    id              BIGSERIAL PRIMARY KEY,
    config_key      VARCHAR(100) NOT NULL,
    old_value       JSONB,
    new_value       JSONB NOT NULL,
    changed_at      TIMESTAMPTZ DEFAULT NOW(),
    change_note     TEXT
);
CREATE INDEX idx_config_history_key ON user_config_history(config_key, changed_at DESC);
```

### 3.5 表总览

| 分组 | 表名 | 说明 |
|------|------|------|
| 市场数据 | stock_info | 股票基础信息（含退市） |
| 市场数据 | daily_quote | 日线行情（原始 + 复权因子） |
| 市场数据 | financial_data | 财务数据（PIT） |
| 市场数据 | index_history | 指数历史 |
| 业务数据 | market_state_history | 市场状态历史 |
| 业务数据 | candidate_pool | 候选股池日快照 |
| 业务数据 | signal | 交易信号 |
| 业务数据 | signal_score_snapshot | 信号-评分快照（血缘） |
| 业务数据 | factor_ic_history | 因子 IC/IR 监控历史 |
| 业务数据 | report | 周报/月报存储 |
| 业务数据 | user_watchlist | 黑白名单 |
| 账户数据 | account | 账户 |
| 账户数据 | position | 持仓 |
| 账户数据 | trade_record | 成交记录 |
| 账户数据 | fund_flow | 资金流水 |
| 系统表 | pipeline_run | 流水线运行记录 |
| 系统表 | system_config | 系统运维配置 |
| 系统表 | user_config | 用户业务配置 |
| 系统表 | user_config_history | 用户配置变更历史 |

---

## 4. Alembic 迁移设计

### 4.1 目录结构

```
backend/
├── alembic/
│   ├── env.py              # 配置异步引擎，导入所有 ORM 模型
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py   # 单一初始迁移，含全部 18 张表
└── alembic.ini
```

### 4.2 env.py 关键配置

```python
# alembic/env.py
from quantpilot.core.config import settings
from quantpilot.models import Base  # 聚合所有 ORM 模型的 metadata

# 异步迁移配置
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata
```

### 4.3 初始迁移约定

- 迁移文件名：`0001_initial_schema.py`
- `upgrade()`：按依赖顺序建表（先无外键表，后有外键表）
  - 顺序：stock_info → daily_quote / financial_data / index_history → signal → signal_score_snapshot → account → position / trade_record → fund_flow → 其余表
- `downgrade()`：按反序删除所有表

---

## 5. 项目骨架规格

### 5.1 完整目录（Phase 1 需创建）

```
QuantPilot/
├── backend/
│   ├── src/
│   │   └── quantpilot/
│   │       ├── __init__.py
│   │       ├── main.py                 # FastAPI app 入口
│   │       ├── models/
│   │       │   ├── __init__.py         # 聚合 Base + 所有模型（Alembic 需要）
│   │       │   ├── market.py           # StockInfo, DailyQuote, FinancialData, IndexHistory
│   │       │   ├── business.py         # MarketStateHistory, CandidatePool, Signal,
│   │       │   │                       # SignalScoreSnapshot, FactorIcHistory, Report, UserWatchlist
│   │       │   ├── account.py          # Account, Position, TradeRecord, FundFlow
│   │       │   └── system.py           # PipelineRun, SystemConfig, UserConfig, UserConfigHistory
│   │       ├── schemas/
│   │       │   ├── __init__.py
│   │       │   └── auth.py             # LoginRequest, TokenResponse, RefreshRequest
│   │       ├── core/
│   │       │   ├── __init__.py
│   │       │   ├── config.py           # Settings（pydantic-settings）
│   │       │   ├── database.py         # 异步引擎 + Session 工厂
│   │       │   ├── security.py         # JWT 签发 / 验证 / bcrypt
│   │       │   └── exceptions.py       # 自定义异常类 + 全局异常处理器
│   │       ├── api/
│   │       │   ├── __init__.py
│   │       │   ├── deps.py             # get_current_user 依赖
│   │       │   └── v1/
│   │       │       ├── __init__.py
│   │       │       └── auth.py         # POST /auth/login, POST /auth/refresh
│   │       └── data/                   # 空目录，Phase 2 填充
│   │           └── __init__.py
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py                 # pytest fixtures（DB / client）
│   │   └── unit/
│   │       ├── __init__.py
│   │       └── test_auth.py            # JWT 单元测试
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       └── 0001_initial_schema.py
│   ├── alembic.ini
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   └── Dockerfile                      # 占位，Phase 9 填充
├── docs/
│   └── (已有文档)
├── docker-compose.yml
├── docker-compose.dev.yml
└── .env.example
```

### 5.2 main.py 规格

```python
# src/quantpilot/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from quantpilot.core.config import settings
from quantpilot.core.database import engine
from quantpilot.core.exceptions import register_exception_handlers
from quantpilot.api.v1 import auth

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # 未来在此处初始化连接池、调度器等

app = FastAPI(
    title="QuantPilot",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,   # 生产关闭 Swagger UI
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["认证"])

@app.get("/health", tags=["系统"])
async def health():
    return {"status": "ok", "version": "1.0.0"}
```

### 5.3 models/\_\_init\_\_.py 规格

```python
# 必须导入所有 ORM 模型，Alembic autogenerate 才能感知到
from quantpilot.models.base import Base
from quantpilot.models.market import StockInfo, DailyQuote, FinancialData, IndexHistory
from quantpilot.models.business import (
    MarketStateHistory, CandidatePool, Signal, SignalScoreSnapshot,
    FactorIcHistory, Report, UserWatchlist,
)
from quantpilot.models.account import Account, Position, TradeRecord, FundFlow
from quantpilot.models.system import PipelineRun, SystemConfig, UserConfig, UserConfigHistory

__all__ = [
    "Base",
    "StockInfo", "DailyQuote", "FinancialData", "IndexHistory",
    "MarketStateHistory", "CandidatePool", "Signal", "SignalScoreSnapshot",
    "FactorIcHistory", "Report", "UserWatchlist",
    "Account", "Position", "TradeRecord", "FundFlow",
    "PipelineRun", "SystemConfig", "UserConfig", "UserConfigHistory",
]
```

### 5.4 ORM 模型约定

- 所有模型继承自 `models/base.py` 中的 `Base = declarative_base()`
- 字段命名与 DDL 保持一致（snake_case）
- 外键字段额外声明 `ForeignKey`，relationship 在 Phase 1 中可选（按需添加）
- `JSONB` 类型使用 `sqlalchemy.dialects.postgresql.JSONB`
- `TIMESTAMPTZ` 使用 `sqlalchemy.TIMESTAMP(timezone=True)`

---

## 6. JWT 认证实现规格

### 6.1 认证策略

QuantPilot 为个人工具，单用户。无需用户表，凭证存储在环境变量中：

```
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<bcrypt hash>   # 使用 bcrypt.hashpw() 生成
JWT_SECRET_KEY=<随机 64 字节 hex>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60       # 1 小时
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
```

### 6.2 core/security.py 规格

```python
from datetime import datetime, timedelta, timezone
from typing import Literal
import bcrypt
from jose import jwt, JWTError
from quantpilot.core.config import settings

TokenType = Literal["access", "refresh"]

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

def create_token(token_type: TokenType) -> str:
    if token_type == "access":
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_access_token_expire_minutes
        )
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.jwt_refresh_token_expire_days
        )
    payload = {"sub": settings.admin_username, "type": token_type, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

def decode_token(token: str, expected_type: TokenType) -> str:
    """返回 username，验证失败抛出 AuthError"""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != expected_type:
            raise AuthError("token 类型不匹配")
        return payload["sub"]
    except JWTError as e:
        raise AuthError(str(e))
```

### 6.3 API 端点规格

**POST /api/v1/auth/login**

```
Request:  { "username": str, "password": str }
Response: { "access_token": str, "refresh_token": str, "token_type": "bearer" }
Error:    401 { "code": 401, "msg": "用户名或密码错误" }
```

**POST /api/v1/auth/refresh**

```
Request:  { "refresh_token": str }
Response: { "access_token": str, "token_type": "bearer" }
Error:    401 { "code": 401, "msg": "refresh_token 无效或已过期" }
```

### 6.4 api/deps.py 规格

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from quantpilot.core.security import decode_token
from quantpilot.core.exceptions import AuthError

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    try:
        return decode_token(credentials.credentials, expected_type="access")
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
```

### 6.5 统一响应格式

所有接口返回：

```json
{ "code": 0, "data": <payload>, "msg": "ok" }
```

错误时：

```json
{ "code": <http_status>, "data": null, "msg": "<error_message>" }
```

在 `exceptions.py` 中注册全局异常处理器覆盖 FastAPI 默认格式。

---

## 7. Docker 配置规格

### 7.1 .env.example

```bash
# === 数据库 ===
DB_USER=quantpilot
DB_PASSWORD=changeme
DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@db:5432/quantpilot

# === Redis ===
REDIS_PASSWORD=changeme
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0

# === JWT ===
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=                    # 运行 python -c "import bcrypt; print(bcrypt.hashpw(b'yourpwd', bcrypt.gensalt()).decode())" 生成
JWT_SECRET_KEY=                         # 运行 openssl rand -hex 64 生成
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# === 数据源（Phase 2 使用）===
TUSHARE_TOKEN=

# === 通知（Phase 10 使用）===
WXPUSHER_TOKEN=

# === 应用 ===
DEBUG=false
CORS_ORIGINS=["http://localhost:5173","http://localhost:80"]
```

### 7.2 backend/Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装 uv
RUN pip install uv

# 复制依赖声明
COPY pyproject.toml .
RUN uv sync --no-dev

# 复制源码
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

ENV PYTHONPATH=/app/src

CMD ["uv", "run", "uvicorn", "quantpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 7.3 docker-compose.dev.yml

```yaml
# 开发环境：源码挂载 + hot reload
version: "3.9"

services:
  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: quantpilot
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - postgres_dev_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER}"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    ports:
      - "6379:6379"
    volumes:
      - redis_dev_data:/data

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: >
      uv run uvicorn quantpilot.main:app
      --host 0.0.0.0 --port 8000
      --reload --reload-dir /app/src
    volumes:
      - ./backend/src:/app/src         # 热重载挂载
    environment:
      DATABASE_URL: postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@db:5432/quantpilot
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      ADMIN_USERNAME: ${ADMIN_USERNAME}
      ADMIN_PASSWORD_HASH: ${ADMIN_PASSWORD_HASH}
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
      DEBUG: "true"
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy

volumes:
  postgres_dev_data:
  redis_dev_data:
```

### 7.4 pyproject.toml（完整依赖）

```toml
[project]
name = "quantpilot"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = [
    # Web 框架
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    # 数据库
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    # 缓存
    "redis>=5.0",
    # 认证
    "python-jose[cryptography]>=3.3",
    "bcrypt>=4.1",
    "python-multipart>=0.0.9",
    # 量化计算（Phase 2+ 使用，提前锁定版本）
    "pandas>=2.2",
    "numpy>=1.26",
    "pandas-ta>=0.3.14b",
    "scipy>=1.12",
    "statsmodels>=0.14",
    # 调度
    "apscheduler>=3.10",
    # 数据源（Phase 2 使用）
    "tushare>=1.4",
    "akshare>=1.12",
    # 通知（Phase 10 使用）
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "hypothesis>=6.100",
    "factory-boy>=3.3",
    "testcontainers[postgres]>=4.4",
    "httpx>=0.27",   # TestClient
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.uv]
dev-dependencies = [
    "ruff>=0.4",
    "mypy>=1.9",
]
```

---

## 8. CI 配置

### 8.1 .github/workflows/ci.yml

```yaml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15-alpine
        env:
          POSTGRES_DB: quantpilot_test
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-retries 5
        ports:
          - 5432:5432

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Install dependencies
        working-directory: backend
        run: uv sync --group dev

      - name: Lint
        working-directory: backend
        run: uv run ruff check src/ tests/

      - name: Run tests
        working-directory: backend
        env:
          DATABASE_URL: postgresql+asyncpg://test:test@localhost:5432/quantpilot_test
          ADMIN_USERNAME: admin
          ADMIN_PASSWORD_HASH: ${{ secrets.TEST_PASSWORD_HASH }}
          JWT_SECRET_KEY: test-secret-key-for-ci-only
        run: uv run pytest tests/ --cov=quantpilot --cov-report=term-missing -v
```

---

## 9. 测试用例

### 9.1 数据库迁移测试（tests/integration/test_migrations.py）

| 用例 ID | 测试内容 | 预期结果 |
|---------|---------|---------|
| MIG-01 | 执行 `alembic upgrade head` | 成功，无报错 |
| MIG-02 | 执行 `alembic downgrade base` | 成功，所有表被删除 |
| MIG-03 | 重新执行 `upgrade head` | 幂等，成功 |
| MIG-04 | 检查 18 张表全部存在 | `information_schema.tables` 中均可找到 |
| MIG-05 | 检查关键索引存在 | `idx_daily_quote_code`、`idx_pool_date_score` 等 |

### 9.2 JWT 认证单元测试（tests/unit/test_auth.py）

| 用例 ID | 测试内容 | 预期结果 |
|---------|---------|---------|
| AUTH-01 | 正确用户名密码 → 登录成功 | 返回 access_token + refresh_token |
| AUTH-02 | 错误密码 → 登录失败 | HTTP 401 |
| AUTH-03 | 错误用户名 → 登录失败 | HTTP 401 |
| AUTH-04 | 有效 access_token → 访问受保护路由 | HTTP 200 |
| AUTH-05 | 无 token → 访问受保护路由 | HTTP 403 |
| AUTH-06 | 篡改 token → 访问受保护路由 | HTTP 401 |
| AUTH-07 | 过期 access_token（mock 时间） | HTTP 401 |
| AUTH-08 | 有效 refresh_token → 获得新 access_token | 返回新 access_token |
| AUTH-09 | 用 access_token 调 refresh 接口 | HTTP 401（类型不匹配） |
| AUTH-10 | 过期 refresh_token → 刷新失败 | HTTP 401 |

### 9.3 健康检查测试（tests/unit/test_health.py）

| 用例 ID | 测试内容 | 预期结果 |
|---------|---------|---------|
| HEALTH-01 | `GET /health` 无需鉴权 | HTTP 200，`{"status": "ok"}` |

### 9.4 响应格式测试

| 用例 ID | 测试内容 | 预期结果 |
|---------|---------|---------|
| FMT-01 | 成功响应格式 | `{"code": 0, "data": ..., "msg": "ok"}` |
| FMT-02 | 4xx 错误响应格式 | `{"code": 4xx, "data": null, "msg": "..."}` |

---

## 10. 验收标准（DoD）

以下全部通过方可进入 Phase 2：

- [ ] `docker compose -f docker-compose.dev.yml up` 一条命令启动，无报错
- [ ] `docker compose run --rm backend alembic upgrade head` 建表成功，18 张表全部存在
- [ ] `GET http://localhost:8000/health` 返回 `{"status": "ok"}`
- [ ] `POST /api/v1/auth/login` 正确凭证返回 JWT，错误凭证返回 401
- [ ] `POST /api/v1/auth/refresh` 有效 refresh_token 返回新 access_token
- [ ] 所有测试用例（AUTH-01 ~ AUTH-10, HEALTH-01, FMT-01 ~ FMT-02, MIG-01 ~ MIG-05）通过
- [ ] `pytest --cov` 覆盖率报告可生成（具体比例 Phase 1 不作强制要求）
- [ ] `.env.example` 完整，新成员 `cp .env.example .env` 后只需填写 token 即可运行
- [ ] `ruff check` 无错误

---

*Phase 2（数据采集层）设计文档在 Phase 1 DoD 全部通过后创建。*
