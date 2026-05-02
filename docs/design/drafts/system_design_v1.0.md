# QuantPilot 系统设计文档

> **版本：** v1.0（已归档）
> **基线依据：** QuantPilot_SDD_v1.0（规范文档，专家审定版）
> **日期：** 2026-03-05
> **归档说明：** 本文件为 Phase 2 数据层接口同步前的快照（v1.0 正式版），已于 2026-03-18 归档。正式版本见 `design/system_design_v1.0.md`

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-03-05 | 初版草稿，基于 SDD v0.4（已归档） |
| v0.2 | 2026-03-05 | 正式版：对齐 SDD v1.0 专家审定版，新增回测引擎架构、财务数据模型、复权策略、流水线检查点、数据血缘最小实现 |
| v0.2-r1 | 2026-03-05 | 全面点检修正：统一评分尺度为 0-100；Signal 补充 signal_strength/liquidity_note/t1_warning；新增 user_watchlist 表；补回 plugin_runner.py；补充黑白名单 API；明确 PositionSizer 市场状态调节和信号过期归属；新增非功能性需求摘要（第 10 节） |
| **v1.0** | 2026-03-05 | **专家审查补全**：新增因子质量监控（V1.0 必需）、资金流水表、用户配置分层表及版本历史、报告生成模块、行为分析；修正 signal.score 字段类型 Bug；补充回测交易成本参数；修正 signal_strength 分类；明确市场状态仓位系数注释；定义 WebSocket 用途；新增持仓保护逻辑；补全 API 端点（认证/因子监控/资金流水/绩效归因/报告）；V1.5 低波动策略占位 |

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
9. [V1.0 开发阶段规划](#9-v1.0-开发阶段规划)
10. [非功能性需求摘要](#10-非功能性需求摘要)

---

## 1. 技术栈选型

### 1.1 基础技术栈

| 层次 | 技术选型 | 版本要求 | 选型理由 |
|------|----------|----------|----------|
| **语言** | Python | 3.12+ | 量化计算生态无可替代（pandas/numpy/scipy） |
| **Web 框架** | FastAPI | 0.110+ | 异步高性能、自动 OpenAPI 文档、原生 Pydantic 集成 |
| **ORM** | SQLAlchemy | 2.0+ | 成熟稳定，支持异步，声明式模型 |
| **数据库迁移** | Alembic | 1.13+ | SQLAlchemy 官方配套 |
| **数据库** | PostgreSQL | 15+ | 适合时序+关系混合场景，索引能力强；625 万行日线数据可通过分区+索引满足性能要求 |
| **缓存** | Redis | 7+ | 流水线状态、会话缓存、实时进度推送（WebSocket 广播） |
| **任务调度** | APScheduler | 3.10+ | 轻量级，支持日级批处理（每日 17:00）和月级任务（月末因子监控、报告生成） |
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
| **numpy** | 1.26+ | 数值计算基础；全市场 5000+ 标的向量化评分的计算基础 |
| **pandas-ta** | 0.3+ | 技术指标（MA/MACD/RSI/ADX/BBands 等） |
| **scipy** | 1.12+ | Spearman 秩相关（Rank IC）、统计检验 |
| **statsmodels** | 0.14+ | 多因子回归归因（V1.5） |

> **选 pandas-ta 而非 TA-Lib：** 纯 Python，无 C 编译依赖，跨平台零障碍。日线级别性能差异可忽略（<5000 只标的）。

> **技术选型约束（对应 SDD §15.7）：**
> - 全市场评分必须支持向量化批量计算（pandas/numpy），禁止逐行循环
> - 回测引擎与实时评分引擎**必须调用同一套** Engine 层函数，禁止分别实现
> - 策略评分通过 `asyncio.to_thread()` 在线程池中并行执行同步函数，无需为每个策略定义 `score_async` 方法
> - 数据血缘每日存储全市场因子快照，需评估数据量并制定保留策略

### 1.3 数据与通知

| 用途 | 技术 | 说明 |
|------|------|------|
| 数据源（主） | Tushare Pro | 日线行情、财务数据、指数成分 |
| 数据源（备） | AKShare | Tushare 缺失指标的补充 |
| 微信通知 | WxPusher | 收盘后推送信号摘要；降级策略见 SDD §13.1 |

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
│  仪表盘 / 信号列表 / 持仓管理 / 因子监控 / 回测 / 设置  │
└─────────────────────┬───────────────────────────────┘
                      │ HTTP / WebSocket（见 §2.6）
┌─────────────────────▼───────────────────────────────┐
│                   API 层 (FastAPI)                    │
│      路由分发 / 请求验证 / 响应序列化 / JWT 认证        │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│                  Service 层                           │
│  DataService / MarketStateService / StrategyService   │
│  SignalService / AccountService / PerformanceService  │
│  NotificationService / LineageService                 │
│  FactorMonitorService / ReportService                 │
└──────────┬──────────────────────────┬────────────────┘
           │                          │
┌──────────▼──────────┐   ┌───────────▼─────────────────────┐
│     Engine 层        │   │         Pipeline 层               │
│  纯函数 / 无 IO      │   │  ┌─────────────────────────────┐ │
│  market_state        │   │  │ DailyPipeline（每日 17:00）   │ │
│  universe_filter     │   │  │ 含 CP1/CP2/CP3 检查点        │ │
│  strategies (×4+1)   │   │  └─────────────────────────────┘ │
│  factor_monitor      │   │  ┌─────────────────────────────┐ │
│  scorer / pool       │   │  │ MonthlyScheduler（月末）      │ │
│  signal / position   │   │  │ 因子监控计算 + 报告生成       │ │
│  risk                │   │  └─────────────────────────────┘ │
└──────────┬──────────┘   │  ┌─────────────────────────────┐ │
           │               │  │ BacktestEngine               │ │
           │               │  │ 共用 Engine 层，按需触发      │ │
           │               │  └─────────────────────────────┘ │
           │               └─────────────────────────────────┘
           │                          │
┌──────────▼──────────────────────────▼──────────────┐
│                   Data 层                             │
│  DataSourceAdapter (Tushare/AKShare)                  │
│  AdjustedPriceProvider（复权价格按需派生）             │
│  Repository (SQLAlchemy async)                        │
│  DataValidator / TradingCalendar                      │
└──────────┬──────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────┐
│                   Model 层                            │
│    SQLAlchemy ORM 模型 / Pydantic Schema              │
│    市场数据 / 财务数据 / 业务实体 / 账户 / 系统         │
└─────────────────────────────────────────────────────┘
                      │
           ┌──────────▼──────────┐
           │  PostgreSQL + Redis  │
           └─────────────────────┘
```

**关键约束：BacktestEngine 与 DailyPipeline 共用同一组 Engine 层函数实例，严禁分别实现策略逻辑（SDD §7.7.1）。**

### 2.2 DailyPipeline 主流程（含检查点）

每日收盘后（默认 17:00）由 APScheduler 触发：

```python
class DailyPipeline:
    async def run(self, trade_date: date) -> PipelineResult:
        run = await self.pipeline_repo.get_or_create(trade_date)

        # ── CP1：数据就绪 ──────────────────────────────────
        if not run.cp1_data_ready:
            raw_data = await self.data_service.ingest(trade_date)
            await self.pipeline_repo.mark_cp1(run.id, snapshot_version=raw_data.version)
        else:
            raw_data = await self.data_service.load_snapshot(run.data_snapshot_version)

        # ── CP2：评分完成 ──────────────────────────────────
        if not run.cp2_scoring_done:
            market_state = self.market_state_engine.identify(raw_data.index_history)
            universe = self.universe_engine.filter(raw_data)   # 含基本面底线过滤
            # 使用 asyncio.to_thread 并行执行各策略同步评分函数
            strategy_scores = await asyncio.gather(*[
                asyncio.to_thread(s.score, universe, raw_data) for s in self.strategies
            ])
            composite = self.scorer.aggregate(strategy_scores, market_state)
            await self.pool_service.update(composite, trade_date, positions=await self.account_service.get_all_positions())
            await self.pipeline_repo.mark_cp2(run.id)
        else:
            composite, market_state = await self.pool_service.load(trade_date)

        # ── CP3：信号生成完成 ──────────────────────────────
        if not run.cp3_signals_done:
            positions = await self.account_service.get_all_positions()
            account = await self.account_service.get_default()
            signals = self.signal_engine.generate(composite, positions, market_state)
            signals = self.position_engine.suggest(signals, account, market_state)
            self.risk_engine.check(signals, positions, account)
            await self.signal_service.save(signals)
            await self.lineage_service.record(signals, composite, trade_date)
            await self.pipeline_repo.mark_cp3(run.id, signal_count=len(signals))

        await self.notifier.send_with_fallback(signals)
        return PipelineResult(trade_date=trade_date, signals=signals)
```

### 2.3 MonthlyScheduler 流程（月末触发）

```python
class MonthlyScheduler:
    """每月末（最后一个交易日收盘后）由 APScheduler 触发"""

    async def run(self, calc_month: date) -> None:
        # 因子质量监控（SDD §7.4，V1.0 必需）
        ic_results = await asyncio.to_thread(
            self.factor_monitor_engine.calc_ic_batch,
            calc_month=calc_month,
            return_window=self.config.ic_return_window,  # 默认 20 个交易日
        )
        await self.factor_monitor_service.save_and_alert(ic_results, calc_month)

        # 月报生成（SDD §12.5）
        await self.report_service.generate_monthly(calc_month)
```

### 2.4 BacktestEngine 主流程

```python
class BacktestEngine:
    """与 DailyPipeline 共用同一组 Engine 层实例（注入相同对象）"""

    async def run(self, config: BacktestConfig) -> BacktestResult:
        trade_dates = self.calendar.get_trade_dates(config.start_date, config.end_date)
        nav = {}
        positions: dict[str, Position] = {}

        for trade_date in trade_dates:
            # 使用后复权价格序列（SDD §7.7.3）
            raw_data = self.price_provider.backward_adjusted(trade_date)
            # 标的池基于历史时点可投资宇宙（含已退市股，PIT 原则）
            universe = self.universe_engine.filter_historical(raw_data, trade_date)

            market_state = self.market_state_engine.identify(raw_data.index_history)
            strategy_scores = await asyncio.gather(*[
                asyncio.to_thread(s.score, universe, raw_data) for s in self.strategies
            ])
            composite = self.scorer.aggregate(strategy_scores, market_state)
            signals = self.signal_engine.generate(composite, positions, market_state)
            positions = self._execute_signals(signals, positions, raw_data, config)
            nav[trade_date] = self._calc_nav(positions, raw_data)

        return BacktestResult(daily_nav=pd.Series(nav), ...)

    def _execute_signals(self, signals, positions, raw_data, config: BacktestConfig):
        """执行信号并扣除交易成本（佣金 + 印花税 + 滑点）"""
        for signal in signals:
            cost = self._calc_cost(signal, raw_data, config)
            # 从模拟账户现金中扣除交易成本
            ...
```

### 2.5 复权价格策略

> 对应 SDD §4.1 复权策略设计。

| 场景 | 复权方式 | 实现方式 |
|------|----------|----------|
| 回测引擎 | **后复权**（以上市首日为基准，向前累乘） | `AdjustedPriceProvider.backward_adjusted()` |
| 界面展示 | **前复权**（以最新价为基准，向历史调整） | `AdjustedPriceProvider.forward_adjusted()` |
| 数据库存储 | **原始不复权价格 + 每日复权因子** | `daily_quote.close` + `daily_quote.adj_factor` |

**禁止将动态计算的前复权价格持久化为唯一历史数据**，以保证幂等性（SDD §3.2）。

### 2.6 市场状态识别逻辑

基于沪深 300，防抖动机制：连续 3 个交易日满足新状态条件才确认切换（SDD §6.5）。

```
ADX > 25（趋势明确）
  ├─ MA20 > MA60 且 收盘价 > MA20  →  UPTREND（上涨趋势）
  ├─ MA20 < MA60 且 收盘价 < MA20  →  DOWNTREND（下跌趋势）
  └─ 其他                          →  OSCILLATION（震荡）
ADX ≤ 25                           →  OSCILLATION
```

### 2.7 WebSocket 端点定义

WebSocket 仅用于以下两类实时进度推送场景，其余接口均为 REST：

| WS 端点 | 用途 | 推送频率 |
|---------|------|----------|
| `WS /ws/pipeline/progress` | 日级批处理实时进度（CP1/CP2/CP3 完成事件） | 事件驱动 |
| `WS /ws/backtest/{id}/progress` | 回测任务实时进度（当前日期/总进度百分比） | 每 100 个交易日推送一次 |

后端使用 Redis Pub/Sub 广播进度事件，FastAPI WebSocket Handler 订阅并转发给前端。

---

## 3. 项目结构

```
QuantPilot/
├── backend/
│   ├── src/quantpilot/
│   │   ├── models/                    # SQLAlchemy ORM 模型
│   │   │   ├── market.py              # StockInfo, DailyQuote, IndexHistory, FinancialData
│   │   │   ├── business.py            # CandidatePool, Signal, SignalScoreSnapshot
│   │   │   │                          # FactorIcHistory, Report
│   │   │   ├── account.py             # Account, Position, TradeRecord, FundFlow
│   │   │   └── system.py              # PipelineRun, MarketStateHistory
│   │   │                              # SystemConfig, UserConfig, UserConfigHistory
│   │   ├── schemas/                   # Pydantic 请求/响应模型
│   │   ├── data/                      # Data 层
│   │   │   ├── adapters/
│   │   │   │   ├── base.py            # DataSourceAdapter ABC（输出标准格式，见 SDD 附录D）
│   │   │   │   ├── tushare.py
│   │   │   │   └── akshare.py
│   │   │   ├── price_provider.py      # AdjustedPriceProvider（前/后复权按需派生）
│   │   │   ├── validators.py          # PIT 校验、异常值检测、数据质量规则
│   │   │   ├── calendar.py            # 交易日历
│   │   │   └── repository.py          # 数据库 CRUD
│   │   ├── engine/                    # Engine 层（纯函数，无 IO）
│   │   │   ├── market_state.py        # 市场状态识别（ADX/MA，防抖动）
│   │   │   ├── universe.py            # 股票池筛选（含基本面底线过滤）
│   │   │   ├── strategies/
│   │   │   │   ├── base.py            # BaseStrategy ABC（score() 含 Rank IC 归一化）
│   │   │   │   ├── trend.py
│   │   │   │   ├── mean_reversion.py
│   │   │   │   ├── momentum.py        # 含风险调整动量选项（L2+ 可配置）
│   │   │   │   ├── value.py           # 含价值陷阱规避：ROE低于行业中位数时得分上限50分
│   │   │   │   ├── low_volatility.py  # V1.5 预留占位（接口与 BaseStrategy 一致）
│   │   │   │   └── plugin_runner.py   # 插件策略沙箱执行（SDD §15.2，禁止 IO/网络）
│   │   │   ├── factor_monitor.py      # IC/IR 计算（纯函数，scipy.stats.spearmanr）
│   │   │   ├── scorer.py              # 综合评分合成（市场状态权重动态切换）
│   │   │   ├── pool.py                # 候选股池管理（含持仓保护：持仓标的强制留池）
│   │   │   ├── signal.py              # 信号生成逻辑（含买入价区间、加仓条件）
│   │   │   ├── position.py            # 仓位建议（固定比例法；V1.5 凯利）
│   │   │   │                          # 市场状态调节系数：UPTREND×1.0 / OSCILLATION×0.75 / DOWNTREND×0.50
│   │   │   │                          # （乘以用户设定的 total_position_cap，如 80%×0.50=40%）
│   │   │   └── risk.py                # 风控检查（集中度、止损）
│   │   ├── backtest/                  # 回测引擎（共用 Engine 层）
│   │   │   ├── engine.py              # BacktestEngine（含交易成本扣除）
│   │   │   └── report.py              # 绩效报告生成（含 SDD §7.7.4 免责声明）
│   │   ├── services/                  # Service 层（编排，含 IO）
│   │   │   ├── data_service.py
│   │   │   ├── market_state_service.py
│   │   │   ├── strategy_service.py
│   │   │   ├── signal_service.py      # 含信号过期扫描（每日流水线完成后更新 EXPIRED）
│   │   │   ├── account_service.py     # 含资金流水 CRUD（FundFlow）
│   │   │   ├── performance_service.py # 绩效指标计算（含行为分析）
│   │   │   ├── factor_monitor_service.py  # IC/IR 存储、告警触发（SDD §7.4）
│   │   │   ├── report_service.py      # 周报/月报生成（APScheduler 月末挂载）
│   │   │   ├── notification_service.py
│   │   │   ├── watchlist_service.py   # 黑白名单 CRUD（user_watchlist 表）
│   │   │   └── lineage_service.py     # 数据血缘记录（V1.0 最小实现）
│   │   ├── notification/
│   │   │   ├── base.py                # NotificationChannel ABC
│   │   │   └── wxpusher.py            # 含重试（3次）和降级至系统内通知
│   │   ├── pipeline/
│   │   │   ├── daily_pipeline.py      # 含 CP1/CP2/CP3 检查点
│   │   │   ├── monthly_scheduler.py   # 月末：因子监控 + 月报生成
│   │   │   └── scheduler.py           # APScheduler 注册（日级 + 月级 + 周报）
│   │   ├── api/
│   │   │   ├── deps.py                # JWT 认证依赖（get_current_user）
│   │   │   └── v1/
│   │   │       ├── auth.py            # 认证端点（login/refresh）
│   │   │       ├── market.py
│   │   │       ├── signals.py
│   │   │       ├── positions.py
│   │   │       ├── account.py         # 含资金流水端点
│   │   │       ├── performance.py     # 含归因、行为分析端点
│   │   │       ├── factor_quality.py  # 因子 IC/IR 监控端点
│   │   │       ├── reports.py         # 周报/月报查询端点
│   │   │       ├── backtest.py        # 回测 API（含 WS 进度推送）
│   │   │       ├── watchlist.py       # 黑白名单管理 API
│   │   │       └── settings.py        # 系统配置 + 用户配置（含历史回溯）
│   │   ├── core/
│   │   │   ├── config.py              # 环境配置（pydantic-settings）
│   │   │   ├── database.py            # DB 连接池
│   │   │   ├── security.py            # JWT 签发/验证
│   │   │   └── exceptions.py
│   │   └── main.py
│   ├── tests/
│   │   ├── unit/                      # Engine 层单元测试
│   │   ├── integration/               # Service + DB 集成测试
│   │   └── e2e/                       # API 端到端测试
│   ├── alembic/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── views/
│   │   ├── components/
│   │   ├── stores/
│   │   ├── api/
│   │   └── router/
│   ├── package.json
│   └── Dockerfile
├── docs/
│   ├── spec/
│   │   ├── QuantPilot_SDD_v1.0.md
│   │   └── drafts/
│   └── design/
│       ├── system_design_v1.0.md      # 本文档
│       ├── drafts/                    # 历史草稿
│       └── phases/                   # 各阶段详细设计（按需创建）
├── docker-compose.yml
├── docker-compose.dev.yml
└── .env.example
```

---

## 4. 数据模型

### 4.1 市场数据表

```sql
-- 股票基础信息（含已退市股，用于幸存者偏差消除）
CREATE TABLE stock_info (
    ts_code        VARCHAR(10) PRIMARY KEY,
    name           VARCHAR(50) NOT NULL,
    industry       VARCHAR(50),
    sw_industry_l1 VARCHAR(20),                  -- 申万一级行业
    sw_industry_l2 VARCHAR(20),                  -- 申万二级行业
    market         VARCHAR(10),                  -- 'MAIN'/'SME'/'GEM'/'STAR'
    list_date      DATE,
    delist_date    DATE,                         -- NULL 表示仍上市
    is_active      BOOLEAN DEFAULT TRUE,
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- 日线行情（存储原始不复权价格 + 累乘复权因子，SDD §4.1）
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
    turnover_rate  NUMERIC(8,6),
    float_mkt_cap  NUMERIC(18,2),                -- 流通市值（元）
    adj_factor     NUMERIC(12,6),                -- 累乘复权因子（以上市首日为基准 1.0）
    is_suspended   BOOLEAN DEFAULT FALSE,
    is_st          BOOLEAN DEFAULT FALSE,
    limit_up       BOOLEAN DEFAULT FALSE,
    limit_down     BOOLEAN DEFAULT FALSE,
    UNIQUE (ts_code, trade_date)
);
CREATE INDEX idx_daily_quote_date ON daily_quote(trade_date);
CREATE INDEX idx_daily_quote_code ON daily_quote(ts_code, trade_date DESC);

-- 财务数据（PIT 存储，publish_date 为可用时点，SDD §5.1）
CREATE TABLE financial_data (
    id             BIGSERIAL PRIMARY KEY,
    ts_code        VARCHAR(10) NOT NULL,
    report_period  DATE NOT NULL,                -- 报告期末日（如 2025-09-30）
    publish_date   DATE NOT NULL,                -- 公告发布日（PIT 时点，非 report_period）
    pe_ttm         NUMERIC(10,4),               -- 市盈率 TTM（负值表示亏损）
    pb             NUMERIC(8,4),
    roe            NUMERIC(8,6),                -- 净资产收益率（小数形式）
    net_profit_yoy NUMERIC(8,4),                -- 净利润同比增长率
    revenue_yoy    NUMERIC(8,4),                -- 营收同比增长率
    dividend_yield NUMERIC(8,6),                -- 股息率（小数形式）
    total_equity   NUMERIC(18,2),               -- 净资产/股东权益（元，负值保留用于过滤）
    debt_to_asset  NUMERIC(8,6),                -- 资产负债率（小数形式）
    UNIQUE (ts_code, report_period, publish_date)
);
CREATE INDEX idx_financial_code_publish ON financial_data(ts_code, publish_date DESC);

-- 指数历史（市场状态识别用）
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
-- 市场状态历史（每日记录，用于回测和绩效归因）
CREATE TABLE market_state_history (
    id              BIGSERIAL PRIMARY KEY,
    trade_date      DATE NOT NULL UNIQUE,
    market_state    VARCHAR(20) NOT NULL,        -- 'UPTREND'/'DOWNTREND'/'OSCILLATION'
    trend_strength  NUMERIC(5,2),               -- 0-100，ADX 归一化
    adx_value       NUMERIC(6,3),
    ma20            NUMERIC(10,3),
    ma60            NUMERIC(10,3),
    state_changed   BOOLEAN DEFAULT FALSE,
    description     TEXT
);

-- 候选股池日快照（含各策略得分）
CREATE TABLE candidate_pool (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(10) NOT NULL,
    trade_date      DATE NOT NULL,
    composite_score NUMERIC(5,2),               -- 0-100 综合得分（横截面百分位）
    trend_score     NUMERIC(5,2),
    reversion_score NUMERIC(5,2),
    momentum_score  NUMERIC(5,2),
    value_score     NUMERIC(5,2),
    market_state    VARCHAR(20),
    in_pool         BOOLEAN DEFAULT TRUE,
    is_holding      BOOLEAN DEFAULT FALSE,       -- 当前是否为持仓标的（持仓标的强制留池，SDD §8.2）
    UNIQUE (ts_code, trade_date)
);
CREATE INDEX idx_pool_date_score ON candidate_pool(trade_date, composite_score DESC);
CREATE INDEX idx_pool_code_date  ON candidate_pool(ts_code, trade_date DESC);

-- 交易信号
CREATE TABLE signal (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(10) NOT NULL,
    signal_type     VARCHAR(10) NOT NULL,        -- 'BUY'/'SELL'/'HOLD'/'EXIT'
    trade_date      DATE NOT NULL,
    score           NUMERIC(5,2),               -- 0-100 综合评分（修正：原 NUMERIC(5,4) 有误）
    suggested_pct   NUMERIC(5,4),               -- 建议仓位占总资产比例（小数形式）
    suggested_price_low  NUMERIC(10,3),          -- 建议买入价区间下限（收盘价×0.99）
    suggested_price_high NUMERIC(10,3),          -- 建议买入价区间上限（收盘价×1.02）
    stop_loss_price NUMERIC(10,3),               -- 参考止损价
    signal_strength VARCHAR(10),                 -- 'STRONG'(≥90分)/'MODERATE'(80-89分)；仅买入信号使用
    liquidity_note  TEXT,                        -- 流动性提示（日均成交额不足时）
    t1_warning      TEXT,                        -- T+1 限制风险提示（买入信号必填）
    reason          TEXT,
    status          VARCHAR(15) DEFAULT 'NEW',   -- 'NEW'/'VIEWED'/'ACTED'/'EXPIRED'/'SUPERSEDED'
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ts_code, trade_date, signal_type)
);
CREATE INDEX idx_signal_code_date ON signal(ts_code, trade_date DESC);
-- 信号过期由 signal_service.py 每日流水线完成后扫描：将超过有效期（默认3日）的 NEW/VIEWED 信号标记为 EXPIRED

-- 信号-评分快照（数据血缘 V1.0 最小实现，SDD §15.6）
CREATE TABLE signal_score_snapshot (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       BIGINT REFERENCES signal(id),
    trade_date      DATE NOT NULL,
    ts_code         VARCHAR(10) NOT NULL,
    composite_score NUMERIC(5,2),               -- 0-100 分
    trend_score     NUMERIC(5,2),
    reversion_score NUMERIC(5,2),
    momentum_score  NUMERIC(5,2),
    value_score     NUMERIC(5,2),
    market_state    VARCHAR(20),
    score_breakdown JSONB,                       -- 各策略得分明细（含权重贡献）
    raw_factors     JSONB                        -- 原始因子值（V1.5 完整溯源使用）
);
CREATE INDEX idx_snapshot_signal ON signal_score_snapshot(signal_id);

-- 因子质量监控历史（SDD §7.4，V1.0 必需）
CREATE TABLE factor_ic_history (
    id              BIGSERIAL PRIMARY KEY,
    calc_month      DATE NOT NULL,               -- 计算月份（月末日期，如 2026-02-28）
    strategy_name   VARCHAR(30) NOT NULL,        -- 'trend'/'mean_reversion'/'momentum'/'value'
    factor_name     VARCHAR(50) NOT NULL,        -- 如 'ma_alignment'/'rsi_oversold'/'pe_percentile'
    ic_value        NUMERIC(8,6),               -- 当月 Rank IC（Spearman 秩相关系数）
    ic_mean_3m      NUMERIC(8,6),               -- 近 3 个月 IC 均值
    ic_std_3m       NUMERIC(8,6),               -- 近 3 个月 IC 标准差
    ir_3m           NUMERIC(8,6),               -- 近 3 个月 IR（ic_mean_3m / ic_std_3m）
    half_life_days  NUMERIC(6,1),               -- 因子半衰期（交易日数）
    return_window   INTEGER DEFAULT 20,          -- 下期收益计算窗口（交易日，SDD §7.4）
    alert_status    VARCHAR(20),                 -- NULL / 'DECAY'（IC均值连续3月负）
                                                --       / 'INEFFICIENT'（IR<0.3）
                                                --       / 'FAST_DECAY'（半衰期<5日）
    UNIQUE (calc_month, strategy_name, factor_name, return_window)
);
CREATE INDEX idx_ic_history_strategy ON factor_ic_history(strategy_name, calc_month DESC);

-- 报告存储（SDD §12.5）
CREATE TABLE report (
    id              BIGSERIAL PRIMARY KEY,
    report_type     VARCHAR(15) NOT NULL,        -- 'WEEKLY'/'MONTHLY'/'CUSTOM'
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    content         JSONB NOT NULL,              -- 结构化报告数据（绩效指标、归因、图表数据）
    summary         TEXT,                        -- 摘要文本（供推送/预览）
    generated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_report_type_period ON report(report_type, period_end DESC);

-- 用户自定义黑白名单（SDD §8.3/§14.5）
CREATE TABLE user_watchlist (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(10) NOT NULL,
    list_type       VARCHAR(10) NOT NULL,        -- 'WHITELIST'（优先关注）/'BLACKLIST'（强制屏蔽）
    reason          TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ts_code, list_type)
);
-- BLACKLIST：Universe 过滤阶段剔除，不进入评分
-- WHITELIST：持仓保护同等逻辑，降低候选池进入阈值（具体阈值通过 user_config 配置）
```

### 4.3 账户数据表

```sql
CREATE TABLE account (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) NOT NULL,
    account_type    VARCHAR(10) DEFAULT 'REAL',  -- 'REAL'/'PAPER'（模拟）
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
    cost_price      NUMERIC(10,3),               -- 加权平均成本价
    current_price   NUMERIC(10,3),
    market_value    NUMERIC(15,2),
    pnl_pct         NUMERIC(8,4),
    open_date       DATE,
    phase           VARCHAR(10),                 -- 'BUILD'/'HOLD'/'REDUCE'
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (account_id, ts_code)
);

CREATE TABLE trade_record (
    id              BIGSERIAL PRIMARY KEY,
    account_id      INTEGER REFERENCES account(id),
    ts_code         VARCHAR(10) NOT NULL,
    trade_type      VARCHAR(10) NOT NULL,        -- 'BUY'/'SELL'
    trade_date      DATE NOT NULL,
    price           NUMERIC(10,3),
    shares          INTEGER,
    amount          NUMERIC(15,2),
    commission      NUMERIC(10,2),               -- 佣金
    stamp_tax       NUMERIC(10,2),               -- 印花税（卖出时）
    signal_id       BIGINT REFERENCES signal(id),  -- 关联信号（用于策略维度归因）
    note            TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 资金流水（SDD §11.4）：记录所有资金变动，包含 trade_record 未覆盖的入金/出金/分红
CREATE TABLE fund_flow (
    id              BIGSERIAL PRIMARY KEY,
    account_id      INTEGER REFERENCES account(id),
    flow_type       VARCHAR(15) NOT NULL,        -- 'DEPOSIT'（入金）/'WITHDRAW'（出金）
                                                --  /'DIVIDEND'（分红）/'BUY_FEE'（买入扣款）
                                                --  /'SELL_PROCEEDS'（卖出回款）
    amount          NUMERIC(15,2) NOT NULL,      -- 正值=资金流入账户，负值=资金流出账户
    trade_date      DATE NOT NULL,
    ts_code         VARCHAR(10),                 -- 分红时关联股票（可选）
    related_trade_id BIGINT REFERENCES trade_record(id),  -- 关联买卖记录（可选）
    note            TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_fund_flow_account_date ON fund_flow(account_id, trade_date DESC);
```

### 4.4 系统表

```sql
-- 流水线运行记录（含检查点，SDD §15.3）
CREATE TABLE pipeline_run (
    id                   BIGSERIAL PRIMARY KEY,
    trade_date           DATE NOT NULL UNIQUE,
    status               VARCHAR(10),            -- 'RUNNING'/'SUCCESS'/'FAILED'
    started_at           TIMESTAMPTZ,
    finished_at          TIMESTAMPTZ,
    signal_count         INTEGER,
    error_msg            TEXT,
    -- 检查点
    cp1_data_ready       BOOLEAN DEFAULT FALSE,
    cp1_at               TIMESTAMPTZ,
    data_snapshot_version VARCHAR(64),           -- 输入数据版本号（幂等性保障）
    cp2_scoring_done     BOOLEAN DEFAULT FALSE,
    cp2_at               TIMESTAMPTZ,
    cp3_signals_done     BOOLEAN DEFAULT FALSE,
    cp3_at               TIMESTAMPTZ
);

-- 系统级配置（数据源 Token、调度时间等运维参数，由管理员维护）
CREATE TABLE system_config (
    key             VARCHAR(100) PRIMARY KEY,
    value           TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 用户配置（按层级分离；区别于 system_config，SDD §14.1-14.3）
CREATE TABLE user_config (
    id              BIGSERIAL PRIMARY KEY,
    config_key      VARCHAR(100) NOT NULL UNIQUE,
    config_value    JSONB NOT NULL,              -- 支持复杂结构（如策略参数对象）
    user_level      VARCHAR(5) NOT NULL DEFAULT 'L2',  -- 该配置项所需最低用户层级
    description     TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 用户配置变更历史（SDD §14.6，支持回退到历史配置）
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

---

## 5. 核心模块接口

### 5.1 数据源适配器

输出必须符合 SDD 附录 D 定义的内部标准格式（snake_case，价格单位元，比率小数形式）。

```python
class DataSourceAdapter(ABC):
    """适配器输出映射为 SDD 附录 D 标准格式，计算引擎只消费标准格式"""

    @abstractmethod
    async def fetch_daily_quotes(
        self, ts_codes: list[str], trade_date: date
    ) -> pd.DataFrame:
        """返回标准列：ts_code, open, high, low, close, vol, amount,
           turnover_rate, adj_factor, is_suspended, is_st, limit_up, limit_down,
           sw_industry_l1, sw_industry_l2, float_mkt_cap"""

    @abstractmethod
    async def fetch_financial_data(
        self, ts_codes: list[str], as_of_date: date
    ) -> pd.DataFrame:
        """返回 PIT 财务数据（publish_date <= as_of_date 的最新一期）
           列：ts_code, pe_ttm, pb, roe, net_profit_yoy, revenue_yoy,
               dividend_yield, total_equity, debt_to_asset"""

    @abstractmethod
    async def fetch_stock_list(self) -> pd.DataFrame:
        """含已退市股票，列：ts_code, name, market, list_date, delist_date"""

    @abstractmethod
    async def fetch_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """列：trade_date, close, pct_chg"""
```

### 5.2 复权价格提供器

```python
class AdjustedPriceProvider:
    """按需派生复权价格序列，不持久化计算结果（SDD §4.1）"""

    def backward_adjusted(
        self, ts_code: str, start_date: date, end_date: date
    ) -> pd.Series:
        """后复权序列（以上市首日为基准向前累乘）
        用于：回测引擎。历史序列稳定，不随新除权事件变化。"""

    def forward_adjusted(
        self, ts_code: str, start_date: date, end_date: date
    ) -> pd.Series:
        """前复权序列（以最新价为基准向历史调整）
        用于：界面展示。动态计算，禁止持久化为唯一历史数据。"""
```

### 5.3 策略基类

评分使用 **Spearman 秩相关（Rank IC）** 归一化，与因子质量监控保持一致（SDD §7.4）。
策略为同步纯函数，由 Pipeline 层通过 `asyncio.to_thread()` 并发调用。

```python
@dataclass
class StrategyScore:
    ts_code: str
    raw_factors: dict[str, float]   # 原始因子值（用于数据血缘/归因）
    score: float                    # 0-100 评分（横截面百分位，Rank IC 归一化）
    reason: str                     # 可读解释（面向 L1 用户）

class BaseStrategy(ABC):
    name: str           # 策略标识符，如 'trend'
    display_name: str   # 中文名，如 '趋势跟踪'
    weights: dict[str, float]   # 策略内因子权重

    @abstractmethod
    def compute_raw_factors(
        self, universe: pd.DataFrame, market_data: dict
    ) -> pd.DataFrame:
        """计算原始因子值，index=ts_code，列=各因子。纯函数，无 IO。"""

    def score(
        self, universe: pd.DataFrame, market_data: dict
    ) -> list[StrategyScore]:
        """完整评分：原始因子 → 横截面 Rank 百分位归一化 → 策略内加权 → reason
        调用方使用 asyncio.to_thread(strategy.score, ...) 实现并发，无需定义 score_async。
        注意：market_data 为只读引用，compute_raw_factors 内禁止修改传入数据。"""
        raw = self.compute_raw_factors(universe, market_data)
        normalized = raw.rank(pct=True) * 100               # Spearman 秩百分位 × 100，∈[0,100]
        scores = (normalized * pd.Series(self.weights)).sum(axis=1)
        return [
            StrategyScore(
                ts_code=ts,
                raw_factors=raw.loc[ts].to_dict(),
                score=float(scores[ts]),
                reason=self._build_reason(ts, raw.loc[ts], scores[ts])
            )
            for ts in universe.index
        ]
```

> **价值陷阱规避（value.py 专属约束，SDD §7.2.4）：**
> `value.py` 的 `compute_raw_factors()` 在合并得分前须检查：若该标的 ROE 低于当日申万行业中位数，则将价值策略最终得分截断至 50 分上限。此逻辑在归一化后、`score()` 返回前施加。

### 5.4 股票池管理（含持仓保护）

```python
class CandidatePoolManager:
    """SDD §8.2：已持仓标的无论评分高低，始终在候选池中显示"""

    async def update(
        self,
        composite: pd.DataFrame,          # 全市场综合评分
        trade_date: date,
        positions: list[Position],         # 当前持仓列表
    ) -> None:
        holding_codes = {p.ts_code for p in positions}
        top_n = self.config.pool_capacity  # 默认 20，可配置

        # 评分排名前 N 入池
        pool_codes = set(composite.nlargest(top_n, 'composite_score').index)

        # 持仓保护：持仓标的强制留池（SDD §8.2）
        pool_codes |= holding_codes

        # 白名单：用户自选股额外展示
        whitelist_codes = await self.watchlist_repo.get_whitelist_codes()
        pool_codes |= whitelist_codes

        # 写入 candidate_pool，is_holding 字段标记持仓标的
        for ts_code in pool_codes:
            await self.repo.upsert(
                ts_code=ts_code,
                trade_date=trade_date,
                composite_score=composite.loc[ts_code, 'composite_score'] if ts_code in composite.index else None,
                is_holding=(ts_code in holding_codes),
                in_pool=True,
            )
```

### 5.5 因子质量监控（SDD §7.4，V1.0 必需）

```python
class FactorMonitorEngine:
    """纯函数，无 IO。每月末由 MonthlyScheduler 调用。"""

    def calc_ic(
        self,
        factor_values: pd.Series,        # index=ts_code，因子值
        forward_returns: pd.Series,       # index=ts_code，下期 return_window 日收益率
    ) -> float:
        """计算单因子单月 Rank IC（Spearman 秩相关系数）"""
        from scipy.stats import spearmanr
        corr, _ = spearmanr(factor_values, forward_returns, nan_policy='omit')
        return float(corr)

    def calc_ic_batch(
        self,
        calc_month: date,
        return_window: int = 20,          # 下期收益窗口（交易日，默认 20，SDD §7.4）
    ) -> list[FactorIcRecord]:
        """批量计算当月所有策略、所有因子的 IC/IR/半衰期"""
        ...

    def detect_alert(self, record: FactorIcRecord) -> str | None:
        """返回告警类型或 None：
        - 'DECAY'：IC 均值连续 3 个月为负
        - 'INEFFICIENT'：IR < 0.3
        - 'FAST_DECAY'：半衰期 < 5 个交易日
        """
        ...
```

### 5.6 绩效分析服务（SDD §12）

```python
class PerformanceService:
    """
    负责计算 SDD §12 的全部指标。
    策略维度归因依赖链：trade_record.signal_id → signal → signal_score_snapshot.score_breakdown
    """

    async def get_summary(self, account_id: int) -> PerformanceSummary:
        """7 项基础绩效指标（SDD §12.1）：
        - cumulative_return, annualized_return, max_drawdown
        - sharpe_ratio（无风险利率取 user_config 中配置值，默认 3%）
        - win_rate, profit_loss_ratio, benchmark_return（沪深300同期）
        """

    async def get_attribution(self, account_id: int, period: DateRange) -> Attribution:
        """三维归因（SDD §12.2）：
        - by_stock：每只已平仓标的盈亏、持有天数
        - by_industry：各申万一级行业合计盈亏贡献
        - by_strategy：按 signal.score_breakdown 中主要贡献策略分组，
                       统计该策略驱动的交易的累计收益、胜率、盈亏比
        """

    async def get_behavioral_analysis(self, account_id: int) -> BehavioralAnalysis:
        """行为分析（SDD §12.4）：
        - avg_holding_days：平均持仓天数
        - monthly_trade_count：月均交易笔数
        - signal_compliance_rate：实际交易中跟随信号的比例（trade_record.signal_id 非空比例）
        - stop_loss_execution_rate：止损信号触发后实际卖出的比例
        - chase_up_rate：信号 EXPIRED 后才买入的比例
        - pnl_distribution：单笔盈亏分布数据（用于直方图）
        """
```

### 5.7 报告生成服务（SDD §12.5）

```python
class ReportService:
    """周报由 APScheduler 每周末触发，月报由 MonthlyScheduler 触发"""

    async def generate_weekly(self, week_end: date) -> Report:
        """生成周报：本周交易汇总、周盈亏、新信号统计"""

    async def generate_monthly(self, month_end: date) -> Report:
        """生成月报：月度绩效、策略贡献、行为分析、因子监控告警摘要"""

    async def generate_custom(self, start: date, end: date) -> Report:
        """用户触发的自定义时间段报告"""
```

### 5.8 回测引擎接口

```python
@dataclass
class BacktestConfig:
    start_date: date
    end_date: date
    initial_capital: float
    strategy_config: dict       # 与实盘配置结构完全相同（SDD §7.7.2）
    account_config: dict        # 仓位控制参数，与实盘完全相同
    # 交易成本（SDD §10.5）
    commission_rate: float = 0.00025   # 双向佣金（默认 0.025%）
    stamp_tax_rate: float = 0.0005     # 印花税（默认 0.05%，仅卖出）
    slippage_rate: float = 0.001       # 滑点估算（默认 0.1%）

@dataclass
class BacktestResult:
    daily_nav: pd.Series            # 每日净值序列（含基准对比）
    daily_positions: pd.DataFrame   # 每日持仓快照
    signal_history: list            # 完整信号历史
    performance: dict               # 标准绩效报告（SDD 附录 C 全部指标）
    disclaimer: str                 # SDD §7.7.4 局限性声明（必须附带）

class BacktestEngine:
    """
    核心约束：必须注入与 DailyPipeline 相同的 strategies 实例。
    数据：使用 AdjustedPriceProvider.backward_adjusted()。
    标的池：universe_engine.filter_historical()，基于历史时点可投资宇宙（PIT）。
    """
    def __init__(
        self,
        strategies: list[BaseStrategy],   # 与 DailyPipeline 共用同一组实例
        market_state_engine,
        universe_engine,
        scorer,
        signal_engine,
        position_engine,
        price_provider: AdjustedPriceProvider,
        calendar: TradingCalendar,
    ): ...

    async def run(self, config: BacktestConfig) -> BacktestResult: ...
```

### 5.9 信号生成接口

```python
class SignalGenerator:
    def generate(
        self,
        composite_scores: pd.DataFrame,
        current_positions: list[Position],
        market_state: MarketState,
        risk_params: RiskParams
    ) -> list[Signal]:
        """
        买入：composite_score > buy_threshold（默认80），未持仓或符合加仓规则
        加仓条件（SDD §10.1）：评分>阈值 且（持仓盈利 OR 当前价偏离成本≤±10%）且市场非下跌趋势
        卖出：composite_score < sell_threshold（默认40），或触发硬止损/策略失效止损
        建议买入价区间：[close × 0.99, close × 1.02]（以信号生成日收盘价为基准）
        signal_strength：score≥90 → 'STRONG'；80-89 → 'MODERATE'（仅买入信号使用）
        """
```

### 5.10 通知渠道接口

```python
class NotificationChannel(ABC):
    @abstractmethod
    async def send(self, notification: Notification) -> bool: ...

class NotificationService:
    """含降级策略：WxPusher 失败重试 3 次（间隔 30s），仍失败存入系统内通知（SDD §13.1）"""
    async def send_with_fallback(self, signals: list[Signal]) -> None: ...
```

---

## 6. API 端点概览

所有接口前缀：`/api/v1`，响应格式：`{"code": 0, "data": ..., "msg": "ok"}`
WebSocket 端点见 §2.7（流水线进度、回测进度）。

| 分组 | 方法 | 路径 | 说明 |
|------|------|------|------|
| **认证** | POST | `/auth/login` | 用户登录，返回 JWT access_token + refresh_token |
| **认证** | POST | `/auth/refresh` | 刷新 access_token |
| **市场** | GET | `/market/state` | 当前市场状态及历史 |
| **市场** | GET | `/market/pool` | 候选股池（分页、排序、过滤；is_holding 标识持仓标的） |
| **市场** | GET | `/market/stock/{ts_code}/score` | 单股历史评分走势 |
| **信号** | GET | `/signals` | 今日信号列表 |
| **信号** | GET | `/signals/history` | 历史信号记录 |
| **信号** | PATCH | `/signals/{id}/status` | 更新信号状态（接受/忽略） |
| **信号** | GET | `/signals/{id}/lineage` | 信号数据血缘（评分快照） |
| **持仓** | GET | `/positions` | 当前持仓列表 |
| **持仓** | POST | `/positions` | 新增持仓记录 |
| **持仓** | PATCH | `/positions/{id}` | 更新持仓价格/阶段 |
| **账户** | GET | `/account` | 账户概览（总资产/现金/当日盈亏） |
| **账户** | POST | `/account/sync` | 手动同步账户 |
| **账户** | POST | `/account/trades` | 录入成交记录（同时写入 fund_flow） |
| **账户** | POST | `/account/deposit` | 录入入金（写入 fund_flow，flow_type=DEPOSIT） |
| **账户** | POST | `/account/withdraw` | 录入出金（写入 fund_flow，flow_type=WITHDRAW） |
| **账户** | GET | `/account/cashflow` | 资金流水查询（支持按类型/时间范围过滤，SDD §11.4） |
| **绩效** | GET | `/performance/summary` | 7 项基础绩效指标（含夏普比率，SDD §12.1） |
| **绩效** | GET | `/performance/history` | 净值曲线历史（含基准对比） |
| **绩效** | GET | `/performance/attribution` | 三维归因（个股/行业/策略，SDD §12.2） |
| **绩效** | GET | `/performance/behavior` | 行为分析（信号遵守率/止损执行率等，SDD §12.4） |
| **因子监控** | GET | `/factor-quality` | 最新月度 IC/IR 监控面板（所有策略/因子，SDD §7.4） |
| **因子监控** | GET | `/factor-quality/history` | 历史 IC 趋势（按策略/因子过滤） |
| **报告** | GET | `/reports` | 历史报告列表（按类型/时间范围过滤，SDD §12.5） |
| **报告** | GET | `/reports/{id}` | 获取报告详情 |
| **报告** | POST | `/reports/generate` | 触发自定义时间段报告生成 |
| **回测** | POST | `/backtest/run` | 启动回测任务（异步，WS 推送进度） |
| **回测** | GET | `/backtest/{id}/status` | 查询回测进度 |
| **回测** | GET | `/backtest/{id}/result` | 获取回测结果及免责声明 |
| **系统** | GET | `/pipeline/status` | 流水线运行状态（含检查点） |
| **系统** | POST | `/pipeline/trigger` | 手动触发日级流水线 |
| **设置** | GET | `/settings` | 获取用户配置（按 user_level 过滤可见项） |
| **设置** | PUT | `/settings` | 更新用户配置（自动写入 user_config_history） |
| **设置** | GET | `/settings/config-history` | 配置变更历史（支持回退，SDD §14.6） |
| **设置** | POST | `/settings/config-history/{id}/revert` | 回退到指定历史配置 |
| **黑白名单** | GET | `/watchlist` | 获取黑白名单列表（可按 list_type 过滤） |
| **黑白名单** | POST | `/watchlist` | 添加标的到黑名单或白名单 |
| **黑白名单** | DELETE | `/watchlist/{ts_code}?list_type=BLACKLIST` | 从指定名单移除标的 |

---

## 7. 部署方案

### 7.1 Docker Compose（生产）

```yaml
# docker-compose.yml
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
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
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
# 填入 TUSHARE_TOKEN、DB 密码、WXPUSHER_TOKEN、JWT_SECRET_KEY 等

docker compose up -d db redis
docker compose run --rm backend alembic upgrade head
docker compose up -d
curl http://localhost:8000/health
```

---

## 8. TDD 开发策略

### 8.1 核心原则

- **Red → Green → Refactor**：先写失败测试，再实现，再重构
- **Engine 层 100% 单元测试覆盖**：纯函数，无 IO，最易测试，是回测/实盘一致性的保障
- **Service 层集成测试**：testcontainers 启动真实 PostgreSQL
- **API 层 E2E 测试**：httpx TestClient，覆盖主要 happy path + error path
- **回测引擎对比测试**：用相同策略配置分别跑回测和单日评分，验证结果一致

### 8.2 测试分层

| 层次 | 类型 | 工具 | 原则 |
|------|------|------|------|
| Engine | 单元测试 | pytest + hypothesis | 纯函数，无 mock，参数化边界值 |
| BacktestEngine | 单元测试 | pytest + mock 数据 | 验证与实盘 Engine 调用路径一致 |
| Service | 集成测试 | testcontainers + factory-boy | 真实 DB，事务隔离 |
| API | E2E 测试 | httpx + pytest | 覆盖主要路径 |
| Pipeline | 集成测试 | mock 数据源 + 真实 DB | 验证检查点恢复逻辑 |

### 8.3 必测场景

**Engine 层：**
- `market_state`: ADX/MA 临界值组合，防抖动 3 日确认机制
- `universe_filter`: 停牌、涨停、低流动性、净资产为负、连续亏损、高杠杆（非金融豁免）
- `BaseStrategy.score()`: 横截面 Rank 百分位边界（全相同值、极端离群值、全 NaN）；market_data 只读性（不被修改）
- `value.score()`: 价值陷阱截断——ROE 低于行业中位数时最终得分 ≤ 50 分
- `scorer.aggregate()`: 三种市场状态权重切换（含下跌趋势 10%/5%/15%/70%）
- `pool.update()`: 持仓保护——持仓标的评分跌出前 N 时仍留池，`is_holding=True`
- `signal.generate()`: 同一标的不重复信号；买入价区间 [close×0.99, close×1.02]；加仓条件验证（盈利 OR 偏离成本≤±10%）；signal_strength 正确分类（STRONG/MODERATE 仅 ≥80 分时出现）
- `position.suggest()`: 市场状态调节系数验证（DOWNTREND 时总仓位上限×0.50）
- `risk.check()`: 单股集中度超限阻断，行业集中度超限阻断
- `AdjustedPriceProvider`: 后复权序列稳定性（新除权事件不影响历史），前复权连续性
- `factor_monitor.calc_ic()`: IC 计算正确性（已知分布的期望值）；alert 阈值边界
- `backtest._execute_signals()`: 交易成本正确扣除（佣金/印花税/滑点叠加）

**Pipeline：**
- 中断后从 CP1/CP2/CP3 各断点恢复，结果与完整运行一致（幂等性）
- 月末调度正确触发因子监控和月报生成

**Service 层：**
- `account_service.record_trade()`: 同时写入 trade_record + fund_flow（BUY_FEE/SELL_PROCEEDS）
- `account_service.deposit()`: 写入 fund_flow（DEPOSIT）并更新 account.cash
- `performance_service.get_attribution()`: 策略维度归因正确聚合（trade→signal→score_breakdown）
- `performance_service.get_behavioral_analysis()`: 信号遵守率计算（signal_id 非空比例）
- `report_service`: 周报/月报触发并正确存储至 report 表
- `factor_monitor_service.save_and_alert()`: 告警触发逻辑（IC连续3月负 → DECAY）

**认证：**
- JWT 签发/验证/过期/刷新全流程

### 8.4 测试数据原则

- **PIT 原则**：测试数据严格按 `publish_date` 切片，不使用未来财务数据
- **确定性**：factory-boy 生成，固定 seed
- **隔离**：每个集成测试在独立事务中执行，测试后回滚

---

## 9. V1.0 开发阶段规划

采用**分阶段文档模式**：每阶段开始前创建 `docs/design/phases/phaseN_name.md`，包含该阶段的接口定义、数据结构、测试用例细节。

| 阶段 | 名称 | 主要交付物 | 详细设计文档 |
|------|------|-----------|------------|
| **Phase 1** | 基础设施 | Docker 环境、完整 DB schema（含所有新增表）、JWT 认证框架、CI 基础、项目骨架 | phase1_infrastructure.md |
| **Phase 2** | 数据采集层 | DataSourceAdapter（Tushare/AKShare）、AdjustedPriceProvider、DataValidator（含 PIT 校验）、TradingCalendar | phase2_data_pipeline.md |
| **Phase 3** | Engine 核心 | MarketState、UniverseFilter（含基本面底线过滤）、四大策略（BaseStrategy + 四实现）、**FactorMonitorEngine**（IC/IR 计算） | phase3_engine.md |
| **Phase 4** | 评分与回测 | Scorer（三状态权重）、**CandidatePoolManager**（含持仓保护）、**BacktestEngine**（共用 Engine 层 + 交易成本，单策略回测验证） | phase4_scoring_backtest.md |
| **Phase 5** | 信号与持仓 | SignalGenerator（含买入价区间、加仓条件、signal_strength 分类）、PositionSizer（固定比例法）、RiskChecker | phase5_signals.md |
| **Phase 6** | 账户管理 | Account/Position/Trade CRUD、**FundFlow CRUD**（入金/出金/分红）、手动录入、一键从信号录入 | phase6_account.md |
| **Phase 7** | DailyPipeline | 串联 Phase 2-6、APScheduler 调度（日级 + 周报 + 月末）、CP1/CP2/CP3 检查点、LineageService（信号-快照绑定）、**MonthlyScheduler**（因子监控 + 月报） | phase7_pipeline.md |
| **Phase 8** | API 层 | 全部 REST 端点（含认证/因子监控/资金流水/绩效归因/行为分析/报告/配置历史）、WS 端点（流水线进度/回测进度）、E2E 测试 | phase8_api.md |
| **Phase 9** | 前端 | Vue 3 仪表盘、信号列表、持仓管理、因子监控面板、报告中心、回测入口、设置页（含配置历史） | phase9_frontend.md |
| **Phase 10** | 通知与收尾 | WxPusher（含降级重试）、**绩效归因全链路**（个股/行业/策略）、**行为分析**、全链路集成测试、部署文档 | phase10_notification.md |

> **注：**
> - Phase 1 同时建立完整 DB schema（避免后续 schema 变更积累）和 JWT 认证骨架（后续所有 API 基于此鉴权）。
> - Phase 3 将因子监控引擎与策略评分合并实现，因为 IC 计算依赖原始因子值，两者复用相同数据结构。
> - Phase 4 将回测引擎与评分合并，回测作为 Engine 层集成正确性的天然验证手段。

---

## 10. 非功能性需求摘要

> 对应 SDD §15，开发过程中须在各 Phase 设计文档中明确覆盖以下约束。

### 10.1 性能指标（SLA）

| 指标 | 目标 | 备注 |
|------|------|------|
| 日级批处理（全市场 5000+ 标的） | ≤ 30 分钟 | 含数据采集、评分、信号生成 |
| API 响应（P95） | ≤ 500 ms | 正常负载下 |
| 前端首屏加载 | ≤ 3 秒 | 4G 网络 |
| WxPusher 推送延迟 | ≤ 5 分钟 | 相对批处理完成时刻 |
| 数据库单次查询 | ≤ 100 ms | 含复杂多表联查 |

### 10.2 可靠性

| 要求 | 说明 |
|------|------|
| 批处理幂等性 | 同一 `trade_date` 重跑结果一致（CP1/CP2/CP3 检查点 + 数据快照版本保障） |
| 数据 PIT 合规 | 财务数据以 `publish_date` 为切片依据，禁止前视偏差 |
| 通知降级 | WxPusher 失败重试 3 次（间隔 30 秒），仍失败存入系统内通知 |
| 历史数据幂等 | 禁止持久化前复权价格；所有计算从 `raw_close × adj_factor` 派生 |
| 资金流水完整性 | 每笔买卖交易自动同步写入 fund_flow，确保资金流水不遗漏 |

### 10.3 安全性

| 要求 | 说明 |
|------|------|
| 认证 | JWT 认证（access_token 有效期 1 小时，refresh_token 7 天）；Header: `Authorization: Bearer <token>` |
| 插件沙箱 | `plugin_runner.py` 禁止插件访问 IO、网络、系统资源（SDD §15.2） |
| 敏感配置 | Token/密码/JWT_SECRET 通过环境变量注入，禁止硬编码，禁止记录到日志 |
| 免责声明 | 回测报告必须附带 SDD §7.7.4 局限性声明（非历史预测） |

### 10.4 可维护性

| 要求 | 说明 |
|------|------|
| Engine 层覆盖率 | 单元测试覆盖率 ≥ 90%（纯函数，强制要求） |
| 数据格式契约 | 适配器输出严格符合 SDD 附录 D 标准列定义 |
| 回测一致性 | BacktestEngine 与 DailyPipeline 禁止各自实现策略逻辑 |
| 日志 | 结构化日志（JSON），关键业务事件（信号生成、检查点、因子告警、异常）必须记录 |
| 配置可追溯 | 所有 user_config 变更自动写入 user_config_history，支持 API 回退 |

---

*文档维护：架构层面的修改更新本文档；实现细节记录在对应的 phase 文档中。*
