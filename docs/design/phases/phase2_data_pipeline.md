# Phase 2：数据采集层

> **版本：** v1.5
> **所属阶段：** Phase 2 / 10
> **依据文档：** system_design.md §2.1、§2.5；SDD §4、§5
> **日期：** 2026-03-13
> **预期产出：** 可运行的市场数据采集流水线——自动拉取行情/财务/指数/成分股数据入库，含数据校验、PIT 合规、复权价格派生、交易日历、每日定时调度

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-03-13 | 初版（已归档） |
| **v1.0** | 2026-03-18 | **专家审查修正**：对齐 DataSourceAdapter/AdjustedPriceProvider 与 system_design §5.1/§5.2 接口；index_history 补充 OHLCV 字段（Phase 3 ADX 所需）；新增指数成分股采集（幸存者偏差 SDD §5.2）；IngestResult 补充 snapshot_version（DailyPipeline CP1 对接）；调度器交易日判断修复；DataValidator.validate_financial_data 增加 as_of_date 参数（PIT）；AdjustedPriceProvider 双层接口设计；调度器路径迁移至 pipeline/；pe_ttm/pb 日更新 upsert 策略明确；is_st 历史 PIT 处理说明；PRV-03 测试断言修正；DoD 测试计数修正（61 个）；补充 pyproject.toml 依赖规格；TUSHARE_TOKEN 分层配置说明 |
| **v1.0.1** | 2026-03-18 | 修正修订历史中测试计数笔误（57→61，Phase 2 新增 35 + Phase 1 既有 26 = 61）；§3.1 补充 index_component ORM 模型说明 |
| **v1.1** | 2026-03-27 | **实现完成标记**：依据文档更新至 system_design_v1.1.md；勾选全部 DoD 复选框（Phase 2 验收通过）；§9 补充 CAL-07/VAL-05b/ADP-05b/ADP-07/ING-04~06/SCHEMA-01~04 共 10 个遗漏测试用例；测试计数全面修正（Phase 2 实际新增 46 个、Phase 1 实际 25 个，总计 71 个）；任务计划补充完成状态列；31 个冒烟测试全部通过（升级 Tushare 账号后去除速率延迟） |
| **v1.2** | 2026-03-30 | **Phase 3 后整合修正**：§7 `create_scheduler` 签名同步实现——2 参数扩展为 5 参数（`session_factory / adapter / validator / calendar / market_state_engine`）；补充 CR-04 背景注解；lifespan 示例更新为实际调用方式 |
| **v1.3** | 2026-05-12 | **V1.0 真机验收回写**：§4.8 `ingest_history` 契约修正为 per-day 独立 `AsyncSessionLocal`（修复 Bug 5 跨日共用 session + asyncpg 语句级 savepoint 导致的混合状态）；§8.3 同步明确双表交集断点续传规则（Bug 6）；§4.8 / §5.5 注明 `index_components` 改 range query 批量拉取（Bug 7a/7b：Tushare `index_weight` 月度稀疏）；新增 §8.4 asyncpg 单 SQL 32767 参数上限规格 + §8.5 `pandas NaN → SQL NULL` 转换（Bug 9：NUMERIC 列 `'NaN'` 特殊值 ≠ NULL） |
| **v1.4** | 2026-05-13 | **V1.0 真机验收 5y 回填回写**：新增 §8.6 完整性校验 `prev_count` 必须 PIT（RM-18）—— `DataService.ingest_daily` 改用 `repo.get_active_stock_codes_as_of(trade_date)` 按 list_date/delist_date PIT 过滤，避免当前 `is_active` 快照在 5 年回填时把当时 ~4300 只对比 ~5840 全部判 < 95% 阈值导致每日 rollback。§9.6 新增 REPO-05 集成测试（3 股 PIT 场景）；§4.3 DataValidator 文档明确 `prev_count` 语义为"截至 trade_date 实际上市未退市的股票数"|
| **v1.5** | 2026-05-13 | **运维脚本 refill_history.py 双模式拆分**：新增 §8.3.1——原默认 DELETE 行为收编到 `--force-clean`（修脏场景），默认走 `get_fully_ingested_dates` 断点续传（扩存量场景），新增 `--dry-run-plan` 预检模式。动机：早期 refill 仅服务"修脏"，但实际"扩大历史窗口"是更高频的运维操作（如从 90 天扩到 5 年），两语义合并到一个脚本通过 flag 区分 |

---

## 目录

1. [阶段目标与交付物](#1-阶段目标与交付物)
2. [前置条件](#2-前置条件)
3. [新增项目结构](#3-新增项目结构)
4. [模块规格](#4-模块规格)
   - 4.1 DataSourceAdapter ABC
   - 4.2 TradingCalendar
   - 4.3 DataValidator
   - 4.4 AdjustedPriceProvider
   - 4.5 TushareAdapter
   - 4.6 AKShareAdapter
   - 4.7 MarketDataRepository
   - 4.8 DataService
5. [Tushare 字段映射表](#5-tushare-字段映射表)
6. [API 端点规格](#6-api-端点规格)
7. [调度器规格](#7-调度器规格)
8. [错误处理与重试策略](#8-错误处理与重试策略)
9. [测试用例](#9-测试用例)
10. [任务计划](#10-任务计划)
11. [验收标准（DoD）](#11-验收标准dod)

---

## 1. 阶段目标与交付物

### 1.1 目标

建立完整的市场数据采集与存储底座，满足后续 Phase 3~5 策略计算的数据供给需求：

- 一次性历史回填：5 年日线行情（全市场 5000+ 只，含已退市股）
- 每日 17:30 自动增量更新：当日行情、财务、指数
- 历史指数成分股采集：用于回测时还原历史可投资宇宙，消除幸存者偏差（SDD §5.2）
- 严格 PIT 合规：财务数据以公告发布日为可用时点；历史 ST 状态基于历史名称判断
- 数据质量保障：入库前执行完整性/有效性/连续性校验
- 复权价格按需派生：不持久化，支持后复权（回测）和前复权（展示）

### 1.2 主要交付物

| 交付物 | 说明 |
|--------|------|
| `data/adapters/base.py` | DataSourceAdapter ABC（适配器模式，输出 SDD 附录 D 标准格式） |
| `data/adapters/tushare.py` | Tushare Pro 适配器（主数据源） |
| `data/adapters/akshare.py` | AKShare 适配器（备用/补充数据源） |
| `data/calendar.py` | TradingCalendar（A 股交易日历，基于 Tushare trade_cal） |
| `data/validators.py` | DataValidator（SDD §5.5 全部校验规则，含 PIT as_of_date 参数） |
| `data/price_provider.py` | AdjustedPriceProvider（双层设计：纯函数 + DB 查询公共接口） |
| `data/repository.py` | MarketDataRepository（5 张表的幂等 upsert CRUD，含 index_component） |
| `services/data_service.py` | DataService（采集流程编排，ingest_daily 返回含 snapshot_version 的 IngestResult） |
| `schemas/data.py` | 数据相关 Pydantic Schema |
| `api/v1/data.py` | 数据管理 API（状态查询 + 手动触发） |
| `pipeline/scheduler.py` | APScheduler 配置（每日 17:30 采集任务，含交易日判断） |
| Alembic 迁移脚本 | 0002_phase2_index_ohlcv_components.py（index_history 补充 OHLCV；新增 index_component 表） |
| `pyproject.toml` 更新 | 新增 Phase 2 依赖（tushare/akshare/pandas/pandas-ta/scipy/apscheduler） |
| 全量测试套件 | 46 个 Phase 2 新增测试用例，覆盖 CAL/VAL/PRV/ADP/DATA/REPO/ING/INC/SCHEMA |

---

## 2. 前置条件

- Phase 1 全部完成（DB schema、JWT 认证、Docker 环境）
- `backend/.env` 中 `TUSHARE_TOKEN` 已填入有效 Token

> **TUSHARE_TOKEN 分层配置说明：** Phase 2 启动时从 `.env` 读取 `TUSHARE_TOKEN`（via `core/config.py` 的 `settings.tushare_token`）。Phase N 后如果实现了运维配置 API，优先从 `system_config` 表读取（覆盖 `.env` 默认值），形成"环境变量 → system_config 动态覆盖"的分层配置策略。Phase 2 不实现动态覆盖，只读取 `.env`。

- Docker 开发环境可正常运行（`docker compose -f docker-compose.dev.yml up -d db redis`）

---

## 3. 新增项目结构

```
backend/src/quantpilot/
├── data/                          # Phase 2 新增
│   ├── __init__.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py                # DataSourceAdapter ABC
│   │   ├── tushare.py             # Tushare Pro 适配器
│   │   └── akshare.py             # AKShare 适配器
│   ├── calendar.py                # TradingCalendar
│   ├── validators.py              # DataValidator
│   ├── price_provider.py          # AdjustedPriceProvider
│   └── repository.py              # MarketDataRepository
├── pipeline/                      # Phase 2 新增（系统设计 §3 规划路径）
│   ├── __init__.py
│   ├── daily_pipeline.py          # 占位（Phase 3+ 实现 DailyPipeline）
│   ├── monthly_scheduler.py       # 占位（Phase N 实现月末任务）
│   └── scheduler.py               # APScheduler 注册（Phase 2：日级采集任务）
├── services/                      # Phase 2 新增
│   ├── __init__.py
│   └── data_service.py            # DataService
├── schemas/
│   ├── auth.py                    # 已有
│   └── data.py                    # Phase 2 新增
└── api/v1/
    ├── auth.py                    # 已有
    └── data.py                    # Phase 2 新增

backend/alembic/versions/
└── 0002_phase2_index_ohlcv_components.py   # Phase 2 新增迁移（见下文）

backend/tests/
├── unit/
│   ├── test_trading_calendar.py   # Phase 2 新增
│   ├── test_data_validator.py     # Phase 2 新增
│   ├── test_price_provider.py     # Phase 2 新增
│   └── test_tushare_adapter.py    # Phase 2 新增
├── e2e/
│   └── test_data_api.py           # Phase 2 新增
└── integration/
    ├── test_data_repository.py    # Phase 2 新增
    ├── test_data_ingestion.py     # Phase 2 新增
    └── test_index_components.py   # Phase 2 新增
```

### 3.1 Phase 2 Alembic 迁移规格

**文件**：`alembic/versions/0002_phase2_index_ohlcv_components.py`

```python
"""Phase 2: index_history 补充 OHLCV；新增 index_component 表

Revision ID: 0002
Revises: 0001
"""

def upgrade() -> None:
    # 1. index_history 补充 OHLCV 字段（Phase 3 ADX 计算所需）
    op.add_column("index_history", sa.Column("open",  sa.Numeric(10, 3)))
    op.add_column("index_history", sa.Column("high",  sa.Numeric(10, 3)))
    op.add_column("index_history", sa.Column("low",   sa.Numeric(10, 3)))
    op.add_column("index_history", sa.Column("vol",   sa.BigInteger()))

    # 2. 新增指数成分股历史表（消除幸存者偏差，SDD §5.2）
    op.create_table(
        "index_component",
        sa.Column("id",           sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("index_code",   sa.String(10),   nullable=False),
        sa.Column("ts_code",      sa.String(10),   nullable=False),
        sa.Column("trade_date",   sa.Date(),        nullable=False),
        sa.Column("weight",       sa.Numeric(8, 6)),  # 成分股权重（可选）
        sa.UniqueConstraint("index_code", "ts_code", "trade_date",
                            name="uq_index_component_code_stock_date"),
    )
    op.create_index("idx_index_component_date",
                    "index_component", ["index_code", "trade_date"])

def downgrade() -> None:
    op.drop_index("idx_index_component_date")
    op.drop_table("index_component")
    op.drop_column("index_history", "vol")
    op.drop_column("index_history", "low")
    op.drop_column("index_history", "high")
    op.drop_column("index_history", "open")
```

### 3.1.1 IndexComponent ORM 模型

`MarketDataRepository` 使用 `pg_insert(ORM类)` 模式执行 upsert（见 §4.7 实现示例）。`index_component` 表虽通过迁移 0002 创建，但**同样需要在 `models/market.py` 中添加对应的 ORM 模型类**，否则 `pg_insert(IndexComponent)` 无法引用：

```python
# models/market.py（Phase 2 新增，追加到文件末尾）
class IndexComponent(Base):
    __tablename__ = "index_component"

    id:          Mapped[int]           = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    index_code:  Mapped[str]           = mapped_column(String(10), nullable=False)
    ts_code:     Mapped[str]           = mapped_column(String(10), nullable=False)
    trade_date:  Mapped[date]          = mapped_column(Date, nullable=False)
    weight:      Mapped[float | None]  = mapped_column(Numeric(8, 6))

    __table_args__ = (
        UniqueConstraint("index_code", "ts_code", "trade_date",
                         name="uq_index_component_code_stock_date"),
        Index("idx_index_component_date", "index_code", "trade_date"),
    )
```

> **注意**：`index_history` 的 `open/high/low/vol` 字段也需同步更新 `IndexHistory` ORM 模型的列定义，使其与迁移 0002 的 `add_column` 保持一致。这两项 ORM 模型变更归入任务 **T-14**（Alembic 迁移任务），确保迁移与模型同步提交。

---

### 3.2 pyproject.toml 新增依赖

在 `[project.dependencies]` 中追加（保持 Phase 1 的 `[dependency-groups]` 规范）：

```toml
[project]
dependencies = [
    # ... Phase 1 现有依赖 ...
    "tushare>=1.2.89",
    "akshare>=1.12.0",
    "pandas>=2.2.0",
    "numpy>=1.26.0",
    "pandas-ta>=0.3.14b",
    "scipy>=1.12.0",
    "apscheduler>=3.10.0",
]
```

---

## 4. 模块规格

### 4.1 DataSourceAdapter ABC

**文件**：`data/adapters/base.py`

所有适配器继承此抽象类。接口设计与 `system_design.md §5.1` 保持一致。输出 DataFrame 的列名必须严格符合 SDD 附录 D 标准格式（§5 详见字段映射表）。

```python
from abc import ABC, abstractmethod
from datetime import date
import pandas as pd


class DataSourceAdapter(ABC):
    """数据源适配器基类。
    所有输出 DataFrame 的列名遵循 SDD 附录 D 标准格式（snake_case，元，小数比率）。
    所有方法均为异步，内部用 asyncio.to_thread() 包装同步 SDK。
    接口签名与 system_design.md §5.1 保持一致：
      - fetch_daily_quotes / fetch_financial_data 支持可选的 ts_codes 过滤，
        ts_codes=None 时取全市场（Phase 2 日线入库场景），
        ts_codes 非 None 时按列表过滤（Phase 3+ 策略评分引擎场景）。
    """

    @abstractmethod
    async def fetch_stock_list(self) -> pd.DataFrame:
        """获取全市场股票基础信息（含已退市股）。
        输出列：ts_code, name, market, sw_industry_l1, sw_industry_l2,
                list_date, delist_date, is_active

        ⚠ Phase 2 实装说明：sw_industry_l1/l2 当前使用 Tushare 自有 industry 字段
          作占位，并非真正的申万行业分类。Phase 4 行业中性化因子前须替换为
          申万官方分类（index_classify + stock_industry API）。
        """

    @abstractmethod
    async def fetch_daily_quotes(
        self,
        trade_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取指定交易日日线数据。
        ts_codes=None：取全市场（日线批量入库场景）。
        ts_codes 非 None：只取指定股票（策略评分按需查询场景）。
        输出列：ts_code, trade_date, open, high, low, close, pre_close,
                pct_chg, vol, amount, turnover_rate, float_mkt_cap,
                adj_factor, is_suspended, is_st, limit_up, limit_down
        单位：价格（元）、vol（股）、amount（元）、rate（小数）、市值（元）
        """

    @abstractmethod
    async def fetch_financial_data(
        self,
        as_of_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取截至 as_of_date 最新公告的财务数据（PIT：以 publish_date 为准）。
        ts_codes=None：取全市场。ts_codes 非 None：只取指定股票。
        输出列：ts_code, report_period, publish_date, pe_ttm, pb, roe,
                net_profit_yoy, revenue_yoy, dividend_yield,
                total_equity, debt_to_asset
        单位：比率（小数）、金额（元）
        """

    @abstractmethod
    async def fetch_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """获取指数历史日线数据（含 OHLCV，Phase 3 ADX 计算所需）。
        输出列：index_code, trade_date, open, high, low, close, vol, pct_chg
        单位：价格（元）、vol（股）、pct_chg（小数）
        """

    @abstractmethod
    async def fetch_trade_calendar(
        self, start_date: date, end_date: date
    ) -> list[date]:
        """获取指定范围内的 A 股交易日列表（升序）。"""

    @abstractmethod
    async def fetch_index_components(
        self, index_code: str, trade_date: date
    ) -> list[str]:
        """获取指定指数在 trade_date 时点的成分股列表（ts_code 列表，升序）。
        用于回测时还原历史可投资宇宙，消除幸存者偏差（SDD §5.2）。
        Tushare: pro.index_weight(index_code=..., trade_date=YYYYMMDD)
        返回空列表时记录 WARNING（该日期 Tushare 可能无数据，正常处理）。
        """
```

---

### 4.2 TradingCalendar

**文件**：`data/calendar.py`

```python
from datetime import date, timedelta


class TradingCalendar:
    """A 股交易日历。
    初始化时从 DB 或 Tushare 加载交易日列表，并在内存中缓存为有序集合。
    所有方法均为同步纯函数，无 IO。
    """

    def __init__(self, trade_dates: list[date]):
        """trade_dates: 升序排列的交易日列表"""
        self._dates: list[date] = sorted(trade_dates)
        self._date_set: set[date] = set(trade_dates)

    def is_trade_date(self, d: date) -> bool:
        """d 是否为交易日"""

    def get_prev_trade_date(self, d: date, n: int = 1) -> date:
        """d 之前第 n 个交易日（d 本身不计入，若 d 为交易日则从 d-1 开始）
        n=1 时返回上一个交易日。"""

    def get_next_trade_date(self, d: date, n: int = 1) -> date:
        """d 之后第 n 个交易日"""

    def get_trade_dates(self, start: date, end: date) -> list[date]:
        """返回 [start, end] 范围内的全部交易日（升序，含两端）"""

    def count_trade_days(self, start: date, end: date) -> int:
        """start 到 end（含两端）之间的交易日数量"""

    def offset_trade_date(self, d: date, n: int) -> date:
        """以 d 为基准偏移 n 个交易日（n>0 向后，n<0 向前）
        若 d 本身不是交易日，先找到最近的前一个交易日再偏移。"""

    @classmethod
    async def from_adapter(
        cls, adapter: "DataSourceAdapter",
        start_date: date, end_date: date
    ) -> "TradingCalendar":
        """从数据源适配器加载交易日历"""
        dates = await adapter.fetch_trade_calendar(start_date, end_date)
        return cls(dates)
```

---

### 4.3 DataValidator

**文件**：`data/validators.py`

实现 SDD §5.5 全部校验规则。所有方法为同步纯函数，输入/输出均为 DataFrame。

```python
from dataclasses import dataclass, field
from datetime import date
import pandas as pd


@dataclass
class ValidationResult:
    is_valid: bool
    warnings: list[str] = field(default_factory=list)  # 告警但不阻断（如复权连续性异常）
    errors: list[str] = field(default_factory=list)     # 阻断性错误（如数据量不足 95%）
    invalid_rows: pd.Index = field(default_factory=pd.Index)  # 异常行的索引（价格/成交量异常）


class DataValidator:

    def validate_daily_quotes(
        self, df: pd.DataFrame, prev_count: int
    ) -> ValidationResult:
        """执行 SDD §5.5 日线校验：
        - 完整性：当日股票数 >= prev_count × 0.95
        - 价格有效性：low <= open,close <= high（含等于）
        - 成交量非负：vol >= 0
        - 复权连续性：相邻两日 adj_factor 变化率 <= 20%（排除已知除权日）
        异常行打标，不直接丢弃；errors 中记录阻断性问题。

        prev_count 语义（RM-18 修复）：截至 trade_date 时实际上市未退市的股票数，
        必须由调用方 `MarketDataRepository.get_active_stock_codes_as_of(trade_date)`
        提供；禁止用 `get_active_stock_codes()`（当前 is_active 快照），后者在历史
        回填场景下会把当时 ~4300 只对比当前 ~5840 必然 < 95% 阈值。详见 §8.6。
        """

    def validate_financial_data(
        self, df: pd.DataFrame, as_of_date: date
    ) -> ValidationResult:
        """执行财务数据 PIT 校验：
        - publish_date <= as_of_date（PIT 时点；历史回填传 trade_date，增量更新传 date.today()）
        - publish_date >= report_period（公告不能早于报告期末）
        - total_equity 允许负值（用于过滤，需保留）
        注意：回测或历史回填时必须传入对应的 trade_date，不能硬编码 date.today()，
              否则违反 PIT 原则，导致未来数据泄露。
        """

    def validate_trade_date(
        self, df: pd.DataFrame, expected_date: date
    ) -> ValidationResult:
        """时效性校验：df 中 trade_date 必须等于 expected_date"""
```

---

### 4.4 AdjustedPriceProvider

**文件**：`data/price_provider.py`

按需派生复权价格，**不持久化计算结果**（SDD §4.1）。

采用双层接口设计，与 `system_design.md §5.2` 保持一致：
- **私有纯函数层**：输入 Series，无 IO，直接用于测试和引擎内部调用
- **公共 DB 层**：输入 ts_code + 日期范围，内部查询 Repository 后调用纯函数

```python
import pandas as pd
from datetime import date

from quantpilot.data.repository import MarketDataRepository


class AdjustedPriceProvider:
    """复权价格按需派生器。
    禁止将结果持久化为唯一历史数据（SDD §4.1）。

    adj_factor 语义（SDD 附录 D.1 / Tushare 定义）：
      - 以上市首日为基准值 1.0
      - 每次除权事件后，当日及之后的 adj_factor 值调整（具体方向取决于除权类型）
      - 后复权（回测用）：close[t] × adj_factor[t]，历史序列稳定
      - 前复权（展示用）：close[t] × (adj_factor[-1] / adj_factor[t])，以最新价为基准
    """

    def __init__(self, repo: MarketDataRepository):
        self._repo = repo

    # ── 私有纯函数层（无 IO，可直接用于单元测试和策略引擎内部） ──────────────

    @staticmethod
    def _compute_backward(
        close: pd.Series, adj_factor: pd.Series
    ) -> pd.Series:
        """后复权序列（以上市首日为基准=1.0，历史价格向前累乘）。
        公式：backward_adj_close[t] = close[t] × adj_factor[t]
        用于：回测引擎。序列稳定，不随新除权事件变化。
        入参：close 和 adj_factor 均以 trade_date 为 index，升序。
        """

    @staticmethod
    def _compute_forward(
        close: pd.Series, adj_factor: pd.Series
    ) -> pd.Series:
        """前复权序列（以最新价为基准，历史价格向前调整）。
        公式：forward_adj_close[t] = close[t] × (adj_factor.iloc[-1] / adj_factor[t])
        用于：界面展示。动态计算，随新除权事件变化。
        """

    # ── 公共 DB 层（符合 system_design §5.2 接口，内部查询 Repository） ──────

    async def backward_adjusted(
        self, ts_code: str, start_date: date, end_date: date
    ) -> pd.Series:
        """后复权序列（DB 查询版，对外统一接口）。
        内部调用 _repo.get_daily_quotes() 获取 close 和 adj_factor，再委托 _compute_backward()。
        """
        df = await self._repo.get_daily_quotes(ts_code, start_date, end_date)
        return self._compute_backward(df["close"], df["adj_factor"])

    async def forward_adjusted(
        self, ts_code: str, start_date: date, end_date: date
    ) -> pd.Series:
        """前复权序列（DB 查询版，对外统一接口）。
        内部调用 _repo.get_daily_quotes() 获取 close 和 adj_factor，再委托 _compute_forward()。
        """
        df = await self._repo.get_daily_quotes(ts_code, start_date, end_date)
        return self._compute_forward(df["close"], df["adj_factor"])
```

---

### 4.5 TushareAdapter

**文件**：`data/adapters/tushare.py`

```python
import asyncio
from datetime import date
import tushare as ts
import pandas as pd
from quantpilot.data.adapters.base import DataSourceAdapter


class TushareAdapter(DataSourceAdapter):
    """Tushare Pro 适配器。
    - 所有 Tushare SDK 调用通过 asyncio.to_thread() 异步化
    - 速率限制：内置 asyncio.Semaphore 控制并发，默认 max_concurrent=3
    - 字段映射：见 §5 字段映射表
    """

    def __init__(self, token: str, max_concurrent: int = 3):
        self._pro = ts.pro_api(token)
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def _call(self, func, **kwargs) -> pd.DataFrame:
        """受限并发的异步包装器"""
        async with self._semaphore:
            return await asyncio.to_thread(func, **kwargs)

    async def fetch_stock_list(self) -> pd.DataFrame:
        """调用 pro.stock_basic(list_status='L') + pro.stock_basic(list_status='D')
        合并上市 + 退市股，映射为标准格式"""

    async def fetch_daily_quotes(
        self,
        trade_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """合并调用：
        - pro.daily(trade_date=...) → OHLCV + pct_chg
        - pro.daily_basic(trade_date=...) → turnover_rate, circ_mv
        - pro.adj_factor(trade_date=...) → adj_factor
        - pro.suspend_d(suspend_date=...) → is_suspended
        - pro.limit_list_d(trade_date=...) → limit_up, limit_down
        ts_codes 非 None 时，在合并后按 ts_codes 过滤（全市场拉取后过滤比分批调用更高效）。

        is_st 历史 PIT 处理：
          - 增量更新（trade_date ≈ today）：直接取 stock_basic.name 含 ST / *ST 判断。
          - 历史回填（trade_date 为历史日期）：调用 pro.namechange(ts_code=..., start_date=...,
            end_date=...) 获取名称变更历史，按 trade_date 还原当时的名称后再判断。
            为降低 API 调用量，namechange 数据在 DataService.ingest_history() 开始时批量缓存。
        映射并合并为单一 DataFrame"""

    async def fetch_financial_data(
        self,
        as_of_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """调用 pro.fina_indicator(period=最近季度末) + pro.daily_basic(trade_date=as_of_date)
        取每只股票最近季报的财务数据快照。
        ts_codes 非 None 时，在合并后按 ts_codes 过滤。

        ⚠ 已知 API 限制（Phase 2 实装发现）：
          - fina_indicator 不支持仅凭 period 做全市场查询（必须传 ts_code）。
          - 当前实现：调用失败时 try/except 降级，roe/net_profit_yoy/revenue_yoy/
            debt_to_asset 全为 NULL；daily_basic 来源的 pe_ttm/pb/dv_ttm 正常入库。
          - Phase 4 前须专项修复：逐股批量查询或改用支持分页的接口。

        pe_ttm / pb 每日更新策略：
          - pe_ttm / pb / dividend_yield 来自 daily_basic，每日随收盘价变化。
          - upsert 时 publish_date = as_of_date（= trade_date），report_period = 季度末日。
          - UNIQUE (ts_code, report_period, publish_date) 确保同一报告期在同一天只有一条记录；
            每天 upsert 会以最新 pe_ttm/pb 覆盖，roe/growth 等季度字段不变。
          - 历史回填时每天产生一条记录，年均约 125 万行，属预期范围内。
        """

    async def fetch_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """调用 pro.index_daily(ts_code=index_code, ...)
        输出包含 open/high/low/close/vol/pct_chg（Phase 3 ADX 计算需要 high/low）"""

    async def fetch_trade_calendar(
        self, start_date: date, end_date: date
    ) -> list[date]:
        """调用 pro.trade_cal(exchange='SSE', ...)
        过滤 is_open==1 的日期，返回升序 date 列表"""

    async def fetch_index_components(
        self, index_code: str, trade_date: date
    ) -> list[str]:
        """调用 pro.index_weight(index_code=index_code, trade_date=YYYYMMDD)
        返回该日期指数成分股的 ts_code 列表（升序）。
        Tushare 免费接口可能无每日成分股，月度快照可接受；返回空列表时记录 WARNING。
        """
```

**特别约束**：
- Tushare 日期参数格式为 `YYYYMMDD` 字符串，需在调用前转换
- 单次调用最多返回 10000 行；全市场 5000 只需分批（按日期或股票代码分批）
- 历史回填时使用 `trade_date` 循环，每次取一个交易日全市场数据
- 历史回填时 `is_st` 判断依赖 `namechange` 历史缓存，详见 §4.5 注释

---

### 4.6 AKShareAdapter

**文件**：`data/adapters/akshare.py`

Phase 2 实现最小化版本，仅补充 Tushare 缺失的辅助数据。

```python
class AKShareAdapter(DataSourceAdapter):
    """AKShare 适配器（备用/补充数据源）。
    Phase 2 仅实现 fetch_trade_calendar 和 fetch_stock_list，
    其余方法抛 NotImplementedError（Phase 3+ 按需补充）。
    """
    async def fetch_stock_list(self) -> pd.DataFrame: ...
    async def fetch_trade_calendar(self, start_date, end_date) -> list[date]: ...
    async def fetch_daily_quotes(self, trade_date, ts_codes=None) -> pd.DataFrame:
        raise NotImplementedError("AKShare daily quotes not implemented in Phase 2")
    async def fetch_financial_data(self, as_of_date, ts_codes=None) -> pd.DataFrame:
        raise NotImplementedError
    async def fetch_index_history(self, index_code, start_date, end_date) -> pd.DataFrame:
        raise NotImplementedError
    async def fetch_index_components(self, index_code, trade_date) -> list[str]:
        raise NotImplementedError
```

---

### 4.7 MarketDataRepository

**文件**：`data/repository.py`

所有写操作使用 **幂等 upsert**（`INSERT ... ON CONFLICT DO UPDATE`），确保重复调用安全。

```python
from datetime import date
from sqlalchemy.ext.asyncio import AsyncSession
import pandas as pd


class MarketDataRepository:

    def __init__(self, session: AsyncSession):
        self._session = session

    # ---- stock_info ----
    async def upsert_stock_list(self, df: pd.DataFrame) -> int:
        """批量 upsert stock_info。ON CONFLICT (ts_code) DO UPDATE
        冲突更新列：name, sw_industry_l1, sw_industry_l2, market,
                     delist_date, is_active, updated_at
        返回：upsert 行数"""

    async def get_active_stock_codes(self) -> list[str]:
        """返回所有 is_active=True 的 ts_code 列表"""

    # ---- daily_quote ----
    async def upsert_daily_quotes(self, df: pd.DataFrame) -> int:
        """批量 upsert daily_quote。ON CONFLICT (ts_code, trade_date) DO UPDATE
        更新所有行情字段（含 adj_factor）。
        批量大小：500 行/批（全市场 5000 只 → 10 批，避免单次 SQL 过大）。
        返回：upsert 行数"""

    async def get_latest_quote_date(self) -> date | None:
        """返回 daily_quote 中最新的 trade_date，用于增量判断"""

    async def get_daily_quotes(
        self, ts_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """查询单只股票日线序列（含 close, adj_factor），用于复权计算"""

    # ---- financial_data ----
    async def upsert_financial_data(self, df: pd.DataFrame) -> int:
        """批量 upsert financial_data。
        ON CONFLICT (ts_code, report_period, publish_date) DO UPDATE
        更新列：pe_ttm, pb, roe, net_profit_yoy, revenue_yoy,
                dividend_yield, total_equity, debt_to_asset
        注意：pe_ttm/pb 每日 upsert（publish_date=trade_date），同一报告期每天覆盖
              最新估值数据；季度财务字段（roe/growth 等）在公告日更新后保持不变。"""

    async def get_latest_financial(
        self, ts_codes: list[str], as_of_date: date
    ) -> pd.DataFrame:
        """PIT 查询：取每只股票在 as_of_date 时点可用的最新财务数据
        （即 publish_date <= as_of_date 的最新一期）。
        同一报告期若有多次公告（如财报更正），取最大 publish_date 的记录。"""

    # ---- index_history ----
    async def upsert_index_history(self, df: pd.DataFrame) -> int:
        """批量 upsert index_history。ON CONFLICT (index_code, trade_date) DO UPDATE
        更新列：open, high, low, close, vol, pct_chg（含 Phase 2 新增的 OHLCV 字段）"""

    async def get_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """查询指数历史（含 OHLCV，Phase 3 计算 ADX 使用 high/low）"""

    # ---- index_component ----
    async def upsert_index_components(
        self, index_code: str, trade_date: date, ts_codes: list[str]
    ) -> int:
        """批量 upsert index_component。ON CONFLICT (index_code, ts_code, trade_date) DO NOTHING
        返回：实际插入行数"""

    async def get_index_components(
        self, index_code: str, trade_date: date
    ) -> list[str]:
        """查询指定指数在 trade_date 时点的成分股 ts_code 列表。
        若该日期无记录，返回最近一个有数据的日期的成分股（向前回溯最多 30 个交易日）。"""

    # ---- data status ----
    async def get_data_status(self) -> dict:
        """返回各表数据新鲜度摘要：
        { latest_quote_date, stock_count, index_codes_available,
          latest_financial_date, missing_dates: list[date] }"""
```

**upsert 实现模式**（以 `daily_quote` 为例）：

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

stmt = pg_insert(DailyQuote).values(rows)
stmt = stmt.on_conflict_do_update(
    index_elements=["ts_code", "trade_date"],
    set_={col: stmt.excluded[col] for col in UPDATE_COLS},
)
await self._session.execute(stmt)
```

---

### 4.8 DataService

**文件**：`services/data_service.py`

编排完整的采集流程，被 API 端点和调度器调用。

```python
from dataclasses import dataclass, field
from datetime import date
from quantpilot.data.adapters.base import DataSourceAdapter
from quantpilot.data.validators import DataValidator
from quantpilot.data.repository import MarketDataRepository
from quantpilot.data.calendar import TradingCalendar
import hashlib


@dataclass
class IngestResult:
    """ingest_daily() 返回值。
    snapshot_version 用于 DailyPipeline CP1 幂等性保障（system_design §2.2）。
    """
    trade_date: date
    quote_count: int
    financial_count: int
    snapshot_version: str           # 格式：SHA256(trade_date + quote_count + financial_count)
    errors: list[str] = field(default_factory=list)


class DataService:

    def __init__(
        self,
        adapter: DataSourceAdapter,
        validator: DataValidator,
        repo: MarketDataRepository,
        calendar: TradingCalendar,
    ):
        ...

    async def ingest_daily(self, trade_date: date) -> IngestResult:
        """单日全量采集流程：
        1. 校验 trade_date 是否为交易日
        2. fetch_daily_quotes(trade_date) → 校验（完整性、价格有效性）→ upsert
        3. fetch_financial_data(as_of_date=trade_date) → 校验（PIT 合规，as_of_date=trade_date）→ upsert
        4. fetch_index_history() for 4 indexes → upsert
        5. fetch_index_components() for 4 indexes → upsert（每月末或强制刷新时执行）
        6. 生成 snapshot_version = SHA256(f"{trade_date}:{quote_count}:{financial_count}")
        7. 返回 IngestResult（含 snapshot_version，供 DailyPipeline CP1 使用）
        数据校验失败（完整性不足）时：中止入库，errors 非空，snapshot_version 仍生成（记录失败快照）。
        """

    async def ingest_history(
        self, start_date: date, end_date: date,
        progress_callback=None,
    ) -> dict:
        """历史数据回填：
        按交易日循环调用 ingest_daily()，**每个交易日独立 AsyncSessionLocal**：
        当日所有 upsert（daily_quote + financial_data + index_history）任一失败 → 整日 rollback；
        全部成功 → 整日 commit。**禁止**共用 outer session，否则 asyncpg 语句级 savepoint
        会让单条 upsert 失败只回滚自己那条、其他表照常 commit，产生"daily_quote 进库
        但 financial 全空"的混合状态（2026-05-11 真机验收发现的 Bug 5）。

        断点续传：用 `repo.get_fully_ingested_dates(start, end)` 取 daily_quote ∩
        financial_data 双表交集；只要任一表当日为空就视为未完成、需要补拉。**禁止**
        只查 daily_quote（Bug 6：会把上一轮 savepoint 半 commit 的日期错误判定为完成）。

        指数成分股 `index_components` 一次性 range query 批量拉取（4 次 vs N×4 次）：
        Tushare `index_weight` 为月度稀疏接口（仅 rebalance 日有数据），按日循环大概率
        全部返回空（Bug 7a/7b）。

        is_st 历史判断：回填开始前批量缓存 namechange 历史，按 trade_date 还原 ST 状态。
        progress_callback(current, total) 用于 WebSocket 进度推送（Phase 9）。
        返回 {success_count, fail_count, failed_dates}
        """

    async def refresh_stock_list(self) -> dict:
        """刷新全市场股票基础信息（含退市股）"""

    async def get_status(self) -> dict:
        """返回数据新鲜度状态，委托 repo.get_data_status()"""
```

---

## 5. Tushare 字段映射表

### 5.1 日线行情（`daily` + `daily_basic` + `adj_factor` + `suspend_d` + `limit_list_d`）

| 内部字段 | Tushare API | Tushare 字段 | 单位转换 |
|----------|-------------|-------------|---------|
| `ts_code` | daily | `ts_code` | 直接使用 |
| `trade_date` | daily | `trade_date` | YYYYMMDD → date |
| `open` | daily | `open` | 元，直接使用 |
| `high` | daily | `high` | 元，直接使用 |
| `low` | daily | `low` | 元，直接使用 |
| `close` | daily | `close` | 元，直接使用 |
| `pre_close` | daily | `pre_close` | 元，直接使用 |
| `pct_chg` | daily | `pct_chg` | `% → 小数（/ 100）` |
| `vol` | daily | `vol` | `手 → 股（× 100）` |
| `amount` | daily | `amount` | `千元 → 元（× 1000）` |
| `turnover_rate` | daily_basic | `turnover_rate` | `% → 小数（/ 100）` |
| `float_mkt_cap` | daily_basic | `circ_mv` | `万元 → 元（× 10000）` |
| `adj_factor` | adj_factor | `adj_factor` | 直接使用（上市首日=1.0） |
| `is_suspended` | suspend_d | 是否在停牌列表中 | 布尔派生 |
| `is_st` | namechange / stock_basic | name 含 "ST" 或 "\*ST" | 布尔派生；历史回填见 §4.5 注释 |
| `limit_up` | limit_list_d | `limit == 'U'` | 布尔派生；实际列名为 `limit`（非 `limit_type`），值域：U=涨停, D=跌停, Z=炸板 |
| `limit_down` | limit_list_d | `limit == 'D'` | 布尔派生 |

### 5.2 财务数据（`fina_indicator` + `daily_basic`）

| 内部字段 | Tushare API | Tushare 字段 | 单位转换 |
|----------|-------------|-------------|---------|
| `ts_code` | fina_indicator | `ts_code` | 直接使用 |
| `report_period` | fina_indicator | `end_date` | YYYYMMDD → date |
| `publish_date` | fina_indicator | `ann_date` | YYYYMMDD → date（PIT 时点） |
| `pe_ttm` | daily_basic | `pe_ttm` | 直接使用（负值保留） |
| `pb` | daily_basic | `pb` | 直接使用 |
| `roe` | fina_indicator | `roe` | `% → 小数（/ 100）` |
| `net_profit_yoy` | fina_indicator | `netprofit_yoy` | `% → 小数（/ 100）` |
| `revenue_yoy` | fina_indicator | `tr_yoy` | `% → 小数（/ 100）` |
| `dividend_yield` | daily_basic | `dv_ttm` | `% → 小数（/ 100）` |
| `total_equity` | ~~fina_indicator~~ → **balancesheet** | `total_hldr_eqy_exc_min_int` | `万元 → 元（× 10000）`；**Phase 2 实现**：fina_indicator 不含该字段，当前填 NULL，Phase 4 前通过 `balancesheet` API 补充 |
| `debt_to_asset` | fina_indicator | `debt_to_assets` | `% → 小数（/ 100）` |

### 5.3 指数历史（`index_daily`）

| 内部字段 | Tushare 字段 | 单位转换 |
|----------|-------------|---------|
| `index_code` | `ts_code` | 直接使用 |
| `trade_date` | `trade_date` | YYYYMMDD → date |
| `open` | `open` | 元，直接使用 |
| `high` | `high` | 元，直接使用（Phase 3 ADX 计算所需） |
| `low` | `low` | 元，直接使用（Phase 3 ADX 计算所需） |
| `close` | `close` | 元，直接使用 |
| `vol` | `vol` | 手 → 股（× 100） |
| `pct_chg` | `pct_chg` | `% → 小数（/ 100）` |

### 5.4 目标指数列表

| 指数代码 | 名称 |
|----------|------|
| `000001.SH` | 上证指数 |
| `000300.SH` | 沪深 300（市场状态识别主参考） |
| `000905.SH` | 中证 500 |
| `399006.SZ` | 创业板指 |

### 5.5 指数成分股（`index_weight`）

| 内部字段 | Tushare 字段 | 说明 |
|----------|-------------|------|
| `index_code` | `index_code` | 指数代码 |
| `ts_code` | `con_code` | 成分股代码 |
| `trade_date` | `trade_date` | 快照日期（YYYYMMDD → date） |
| `weight` | `weight` | 权重（%→小数；可选，允许 NULL） |

**采集频率**：成分股变动相对低频（每季度调整），Phase 2 在历史回填结束后按月末快照批量采集；增量更新时每月末触发一次（可纳入 Phase N MonthlyScheduler）。

---

## 6. API 端点规格

**路由前缀**：`/api/v1/data`，全部需要 JWT 认证（`Depends(get_current_user)`）

### 6.1 GET /api/v1/data/status

```
请求：无参数
响应 200：
{
  "code": 0,
  "data": {
    "latest_quote_date": "2026-03-12",  // 或 null（无数据）
    "stock_count": 5234,
    "index_codes": ["000001.SH", "000300.SH", "000905.SH", "399006.SZ"],
    "is_up_to_date": true,              // latest_quote_date = 最近交易日
    "latest_financial_date": "2026-03-12"
  },
  "msg": "ok"
}
```

### 6.2 POST /api/v1/data/ingest/daily

```
请求：{ "trade_date": "2026-03-12" }  // 可选，默认最近交易日
响应 200：
{
  "code": 0,
  "data": {
    "trade_date": "2026-03-12",
    "quote_count": 5234,
    "financial_count": 4987,
    "snapshot_version": "a3f8c2...",
    "duration_seconds": 45.2,
    "errors": []
  },
  "msg": "ok"
}
响应 400：trade_date 不是交易日
```

### 6.3 POST /api/v1/data/ingest/history

```
请求：{ "start_date": "2021-01-01", "end_date": "2026-03-12" }
响应 202 Accepted：
{
  "code": 0,
  "data": { "task_id": "backfill-20260313-001", "status": "started" },
  "msg": "历史回填任务已启动，进度通过 WebSocket 推送（Phase 9 实现）"
}
注：Phase 2 中历史回填为同步执行，Phase 9 改为后台任务+WebSocket 进度
```

### 6.4 POST /api/v1/data/refresh/stock-list

```
响应 200：
{
  "code": 0,
  "data": { "upserted_count": 5312 },
  "msg": "ok"
}
```

**更新 `api/v1/__init__.py`**：注册 data router：

```python
from quantpilot.api.v1 import auth, data
router.include_router(data.router, prefix="/data", tags=["data"])
```

---

## 7. 调度器规格

**文件**：`pipeline/scheduler.py`（遵循 system_design §3 规划路径，统一管理日级+月级任务）

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


def create_scheduler(
    session_factory: async_sessionmaker,
    adapter: DataSourceAdapter,
    validator: DataValidator,
    calendar: TradingCalendar,
    market_state_engine: MarketStateEngine,
) -> AsyncIOScheduler:
    """创建并配置 APScheduler。

    session_factory: AsyncSessionLocal（每次 job 运行创建新 session，避免长期持有连接）。
    market_state_engine: 由 main.py 传入 app.state.market_state_engine 单例（Phase 3 新增），
        确保调度器与 API 层使用同一实例。
    Phase 2 仅注册日级采集任务；MonthlyScheduler（因子监控+月报）在 Phase 7 中追加。
    """
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 每日 17:30 触发增量采集（A 股 15:00 收盘，留 2.5h 数据就绪缓冲）
    scheduler.add_job(
        _daily_ingest_job,
        trigger=CronTrigger(hour=17, minute=30, timezone="Asia/Shanghai"),
        args=[session_factory, adapter, validator, calendar, market_state_engine],
        id="daily_ingest",
        replace_existing=True,
        misfire_grace_time=3600,  # 错过触发时 1h 内补跑
    )
    return scheduler
```

> **注（Phase 3 更新）**：Phase 2 设计阶段 `create_scheduler` 为 2 参数（`data_service, calendar`）。Phase 3 实现时为解决 APScheduler job 无法访问 `request.app.state` 的问题（CR-04），签名扩展为 5 参数，传入 `market_state_engine` 单例；同时将 `data_service` 拆解为更细粒度的 `session_factory / adapter / validator`，确保每次 job 运行创建独立 session。

**在 `main.py` lifespan 中注册**：

```python
from contextlib import asynccontextmanager
from quantpilot.pipeline.scheduler import create_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：构建 calendar 和 market_state_engine 实例后再初始化调度器
    adapter = TushareAdapter(settings.tushare_token)
    calendar = await TradingCalendar.from_adapter(adapter, ...)
    scheduler = create_scheduler(
        AsyncSessionLocal, adapter, DataValidator(), calendar,
        app.state.market_state_engine,
    )
    scheduler.start()
    yield
    # 关闭
    scheduler.shutdown(wait=False)

app = FastAPI(lifespan=lifespan)
```

---

## 8. 错误处理与重试策略

### 8.1 Tushare 速率限制

Tushare Pro API 按积分分级限流（基础账户约每分钟 200 次）。

- 单日全量采集约需 5 次 API 调用（daily + daily_basic + adj_factor + suspend_d + limit_list_d）
- 历史回填时：批次间增加 `asyncio.sleep(0.3)` 避免触发速率限制
- 触发 429 / 频率错误时：指数退避重试，最多 3 次（间隔 1s, 2s, 4s）

### 8.2 数据校验失败处理

| 校验结果 | 处理方式 |
|---------|---------|
| 完整性错误（< 95%） | 记录 ERROR 日志，中止当日入库，IngestResult.errors 非空，调用方（DailyPipeline）感知后中止 CP2/CP3 |
| 价格异常行 | 标记 `invalid_rows`，其余正常行继续入库，记录 WARNING |
| 复权连续性告警 | 记录 WARNING，不阻断 |
| PIT 违规（publish_date > as_of_date） | 记录 ERROR，跳过该行 |

### 8.3 历史回填容错

- 单日失败不中断整批（记录 `failed_dates` 列表）
- **每个交易日独立 `AsyncSessionLocal`**——当日所有 upsert 任一失败整日 rollback、全部成功整日 commit（Bug 5：禁止跨日共用 session，asyncpg 语句级 savepoint 会让单条失败只回滚自己那条、其他已成功表照常 commit，产生混合状态）
- 断点续传查 `repo.get_fully_ingested_dates(start, end)` = `daily_quote ∩ financial_data`——只要任一表当日为空就重跑（Bug 6：仅查 daily_quote 会跳过上一轮半 commit 的日期）
- 完成后输出 `{success_count, fail_count, failed_dates}` 摘要

#### 8.3.1 运维脚本 `refill_history.py` 双模式（2026-05-13 拆分）

`backend/scripts/refill_history.py` 支持两种语义，**默认走断点续传**：

| 模式 | 触发 | 行为 | 典型场景 |
|------|------|------|---------|
| **扩存量**（默认） | 无 `--force-clean` | 不删任何已有数据，依赖 `get_fully_ingested_dates` 自动跳过已完整入库的日子 | 首次回填 / 按需扩大历史窗口（90 天 → 2 年 → 5 年） |
| **修脏** | `--force-clean` | 先 DELETE 范围内 4 表数据再走 ingest_history 全量重灌 | 上一轮上游 bug 把数据写脏，断点续传会跳过脏行不刷新 |
| **预检** | `--dry-run-plan` | 仅打印 trade_dates 总数 / 已完整入库 / 待补数量，不删不拉 | 决定要不要执行前的快速核查 |

设计动机：早期 `refill_history.py` 仅服务"修脏"场景（默认 DELETE），但实际"扩大存量"是更高频的运维操作；两个语义合并到一个脚本通过 flag 区分，避免维护两份重复逻辑。

### 8.4 asyncpg 单 SQL 参数上限 32767

PostgreSQL 协议 16-bit signed int 把单条 SQL 的占位符总数限制在 32767。批量 upsert 时：

| 表 | 列数 | 每批最多行数（向下取整 32767/列）| 实际批 |
|----|------|---------------------------------|--------|
| `daily_quote` | 21 | 1560 | **500** |
| `financial_data` | 11 | 2978 | **500** |
| `index_history` | 9 | 3640 | **500** |
| `stock_info` | 8 | 4095 | **500** |

实现：repository 4 个 `upsert_*` 函数均按 `_BATCH_SIZE=500` 循环 `pg_insert(...).values(batch)`。**禁止**一次 `.values(df.to_dict("records"))` 全量入库——5491 只股票 × 11 列 = 60401 个参数直接触发 `PG_STMT_TOO_MANY_PARAMS`。合成数据测试用例 < 3000 行容易绕过此约束，集成测试需 ≥ 3000 行场景覆盖。

### 8.5 pandas NaN → SQL NULL

`df.to_dict("records")` 把 NaN/NaT 保留为 `float('nan')`，asyncpg 会原样写入 PostgreSQL `NUMERIC` 字段作为特殊值 `'NaN'`（≠ NULL）。下游 SQL 查询 `WHERE roe IS NOT NULL` 会误中、数值比较行为不可预期。**所有 upsert 在 `to_dict` 前必须 `df.where(pd.notna(df), None)`** 把 NaN 转 None → SQL NULL。已被污染的历史行可用 `UPDATE financial_data SET col = NULL WHERE col = 'NaN'` 清理。

### 8.6 完整性校验 prev_count 必须 PIT（RM-18，2026-05-13 真机验收）

`DataService.ingest_daily` 调 `validator.validate_daily_quotes(quote_df, prev_count)` 完整性阈值为 `prev_count × 0.95`。`prev_count` **必须**用 `MarketDataRepository.get_active_stock_codes_as_of(trade_date)` 按 `list_date / delist_date` PIT 过滤，**不能**用 `get_active_stock_codes()` 的当前 `is_active` 快照。

**Bug 表现**：2026 年 stock_info 当前活股 ~5840 只；5 年前（2021-05-13）`fetch_daily_quotes` 返回当时实际上市的 ~4300 只 → `4300 < 5840×0.95 = 5548` → 完整性失败 → per-day session 整日 rollback（§8.3 行为）→ 5 年回填跑完 wall time ~4h 后 daily_quote 仍是 **0 行**。

**为什么 V1.0 之前所有 phase 都没暴露**：单元测试 `validate_daily_quotes` 直接传任意 `prev_count` mock；近期 ingest（trade_date 在最近几日）时 stock_info 与 fetch 返回数量相近；首次 5 年级别真机回填才暴露。

**测试覆盖**：
- 单元测试不足以覆盖（mock 路径绕过 PIT 语义）
- 集成测试 REPO-05 `test_repo_05_active_codes_as_of_pit` 构造三股 PIT 场景（上市/退市/未上市）验证 list_date / delist_date 过滤正确
- 5 年回填本身即为隐式集成验收

---

## 9. 测试用例

### 9.1 单元测试：TradingCalendar（`tests/unit/test_trading_calendar.py`）

使用 2026 年已知交易日（如 2026-01-02 为首个交易日，元旦假期 2026-01-01 非交易日）构造 fixture。

| ID | 描述 | 验证点 |
|----|------|--------|
| CAL-01 | 已知交易日 → `is_trade_date()` 返回 True | 2026-01-02 为 True |
| CAL-02 | 节假日 → `is_trade_date()` 返回 False | 2026-01-01 为 False |
| CAL-03 | `get_prev_trade_date(n=1)` 返回上一个交易日 | 2026-01-05 → 2026-01-02 |
| CAL-04 | `get_trade_dates(start, end)` 返回范围内全部交易日 | 5 日范围内正确数量 |
| CAL-05 | `count_trade_days(start, end)` 计数正确 | 跨节假日区间 |
| CAL-06 | `offset_trade_date(d, n=5)` 向后偏移 5 个交易日 | 跨周末正确 |
| CAL-07 | `TradingCalendar.from_adapter()` 委托 adapter.fetch_trade_calendar | 调用一次且返回有效日历对象 |

### 9.2 单元测试：DataValidator（`tests/unit/test_data_validator.py`）

| ID | 描述 | 验证点 |
|----|------|--------|
| VAL-01 | 正常日线数据 → `is_valid=True`，无 errors | 基准用例 |
| VAL-02 | `low > close` 的行 → `invalid_rows` 包含该行，不产生 error | 不阻断但标记 |
| VAL-03 | 股票数 < prev_count × 0.95 → `errors` 非空 | 完整性阻断 |
| VAL-04 | `publish_date > as_of_date` → PIT 违规，`errors` 非空 | 财务数据 PIT 校验（传入 as_of_date=历史日期，非 today） |
| VAL-05 | `adj_factor` 相邻日变化 30% → `warnings` 非空 | 复权连续性告警 |
| VAL-05b | 单日全市场 DataFrame（每 ts_code 仅 1 行）→ adj_factor 连续性不触发 | warnings 为空 |

### 9.3 单元测试：AdjustedPriceProvider（`tests/unit/test_price_provider.py`）

**测试数据说明**：构造 6 日序列（包含明确的"除权前"和"除权后"两段），避免验证跨越除权日的整体涨跌幅。

```python
# Day 1-2：除权前；Day 3 发生除权；Day 4-6：除权后
# 两段内部各自的相对涨跌幅可用于 PRV-03 验证
close      = [10.0, 10.5, 9.0, 9.0, 9.5, 9.2]
adj_factor = [1.0,  1.0,  0.9, 0.9, 0.9, 0.9]
```

| ID | 描述 | 验证点 |
|----|------|--------|
| PRV-01 | `_compute_backward()` 结果 = close × adj_factor | [10.0, 10.5, 8.1, 8.1, 8.55, 8.28]（精确到小数点后 6 位） |
| PRV-02 | `_compute_forward()` 最新日等于当前 close | 最后一日 = close[-1]（= 9.2） |
| PRV-03 | `_compute_forward()` 与 `_compute_backward()` 在**不跨越除权日的连续段内**相对涨跌幅一致 | Day 1→2（除权前）：后复权涨幅 = 前复权涨幅 = +5%；Day 4→6（除权后）：后复权涨幅 = 前复权涨幅 |
| PRV-04 | 无除权时（adj_factor 全为 1.0），后复权 = 原始价格 | 两者完全相等 |

### 9.4 单元测试：TushareAdapter（`tests/unit/test_tushare_adapter.py`，Mock 数据）

使用 `unittest.mock.AsyncMock` 模拟 `asyncio.to_thread`，注入预制的 Tushare 响应 DataFrame。

| ID | 描述 | 验证点 |
|----|------|--------|
| ADP-01 | `fetch_stock_list()` 映射正确 | ts_code, name, list_date 字段存在且类型正确 |
| ADP-02 | `fetch_daily_quotes()` vol 单位转换 | vol 列值 = Tushare 手数 × 100 |
| ADP-03 | `fetch_daily_quotes()` amount 单位转换 | amount 列值 = Tushare 千元 × 1000 |
| ADP-04 | `fetch_daily_quotes()` pct_chg 转为小数 | 5.0% → 0.05 |
| ADP-05 | `fetch_financial_data()` roe 转为小数；publish_date = as_of_date | 15.0% → 0.15；publish_date 字段值正确 |
| ADP-05b | `fetch_financial_data()` basic 为主表 LEFT JOIN fina | 无 fina 记录的股票仍出现，pe_ttm 有值，roe 为 NaN |
| ADP-06 | `fetch_daily_quotes(ts_codes=['000001.SZ'])` 只返回指定股票 | 结果 DataFrame 仅含 000001.SZ |
| ADP-07 | `fetch_namechange()` 返回 start_date/end_date 为 `date` 类型 | start_date isinstance(date)；end_date=None 保持 None |

### 9.5 E2E 测试：数据管理 API（`tests/e2e/test_data_api.py`，ASGI，Mock DataService）

在 `conftest.py` 中添加 `mock_data_service` fixture，覆盖 `DataService` 依赖。

| ID | 描述 | 验证点 |
|----|------|--------|
| DATA-01 | `GET /api/v1/data/status`（有 token）→ 200 | code=0, data 含 latest_quote_date |
| DATA-02 | `GET /api/v1/data/status`（无 token）→ 401 | 未鉴权拒绝 |
| DATA-03 | `POST /api/v1/data/ingest/daily`（有 token）→ 200 | code=0, data 含 quote_count 和 snapshot_version |
| DATA-04 | `POST /api/v1/data/ingest/daily`（非交易日）→ 400 | 正确错误码 |

### 9.6 集成测试：MarketDataRepository（`tests/integration/test_data_repository.py`）

需要真实 PostgreSQL，使用 `db_session` fixture。

| ID | 描述 | 验证点 |
|----|------|--------|
| REPO-01 | `upsert_stock_list()` 批量插入 → 查询确认行数 | count 匹配 |
| REPO-02 | `upsert_daily_quotes()` 重复 upsert 同一天 → 不报错，数据被更新 | 幂等性 |
| REPO-03 | `get_latest_financial(as_of_date)` PIT 查询 → 不返回未来公告 | publish_date 约束；ts_code 是 DataFrame 索引 |
| REPO-04 | `upsert_index_history()` + `get_index_history()` 范围查询 | 日期区间正确，含 high/low 字段 |
| REPO-05 | `get_active_stock_codes_as_of(trade_date)` PIT 过滤 — RM-18 修复 | 构造 3 股（活/未上市/已退市）三个日期点：2021-05-13 / 2019-06-01 / 2023-06-01 各自只返回当时实际上市未退市的子集 |

### 9.7 集成测试：DataService（`tests/integration/test_data_ingestion.py`）

使用 Mock 适配器 + 真实 DB，验证完整采集流程。

| ID | 描述 | 验证点 |
|----|------|--------|
| ING-01 | `ingest_daily()` 正常流程 → 数据入库，返回 IngestResult | quote_count > 0，snapshot_version 非空 |
| ING-02 | 校验失败（完整性不足）→ 不入库，errors 非空 | 事务回滚，snapshot_version 仍生成 |
| ING-03 | `ingest_history()` 3 日范围 → 3 日数据入库 | get_latest_quote_date 正确 |
| ING-04 | `ingest_history()` 正确应用 namechange 缓存 — is_st 按 PIT 还原 | DB 中 ST 股票 is_st=True，非 ST 股票 is_st=False |
| ING-05 | `ingest_daily()` fetch_index_components 返回非空列表时，成分股实际写入 DB | get_index_components 返回正确数量 |
| ING-06 | PIT 违规财务行不入库，合规行正常入库（行级过滤） | errors 含 "PIT"；DB 仅含合规行 |

### 9.8 集成测试：指数成分股（`tests/integration/test_index_components.py`）

| ID | 描述 | 验证点 |
|----|------|--------|
| INC-01 | `upsert_index_components()` 批量插入 → 幂等（重复调用不报错） | ON CONFLICT DO NOTHING |
| INC-02 | `get_index_components(index_code, trade_date)` 返回正确列表 | 与插入数量一致 |
| INC-03 | `get_index_components()` 当日无数据时向前回溯返回最近一期 | 返回非空列表 |

### 9.9 单元测试：DataStatus Schema（`tests/unit/test_data_schema.py`）

验证 `schemas/data.py` 中 `DataStatus` 的字段集合与 `DataService.get_status()` 输出保持一致。

| ID | 描述 | 验证点 |
|----|------|--------|
| SCHEMA-01 | `DataStatus` 字段集与 `get_status()` 返回键名完全一致 | model_fields 键集合 == repo_keys ∪ service_keys |
| SCHEMA-02 | `DataStatus` 正确解析 `get_status()` 典型输出 | stock_count、is_up_to_date、index_codes 值正确 |
| SCHEMA-03 | 空库场景 — `latest_quote_date` / `latest_financial_date` 允许 None | 两字段均为 None 时不报错 |
| SCHEMA-04 | 缺少必填字段 → `ValidationError` | 捕获 API 层与 repo 层字段不匹配 |

---

## 10. 任务计划

共 **21 个任务**，按 TDD 流程执行（标注 RED/GREEN）。

| 任务 | 文件 | 说明 | 前置 | 状态 |
|------|------|------|------|------|
| T-01 | `data/__init__.py`, `data/adapters/__init__.py`, `services/__init__.py`, `pipeline/__init__.py` | 目录骨架（含 pipeline/） | — | ✓ |
| T-02 | `schemas/data.py` | DataStatus, IngestRequest, IngestResult（含 snapshot_version）Pydantic Schema | T-01 | ✓ |
| T-03 | `tests/unit/test_trading_calendar.py` | CAL-01~06（RED） | T-01 | ✓ |
| T-04 | `data/calendar.py` | TradingCalendar 实现（GREEN → CAL-01~06） | T-03 | ✓ |
| T-05 | `tests/unit/test_data_validator.py` | VAL-01~05（RED，含 as_of_date 参数） | T-01 | ✓ |
| T-06 | `data/validators.py` | DataValidator 实现（GREEN → VAL-01~05） | T-05 | ✓ |
| T-07 | `tests/unit/test_price_provider.py` | PRV-01~04（RED，测试私有纯函数层） | T-01 | ✓ |
| T-08 | `data/adapters/base.py` | DataSourceAdapter ABC（含 ts_codes 可选参数、fetch_index_components） | T-01 | ✓ |
| T-09 | `data/price_provider.py` | AdjustedPriceProvider 实现（双层接口，GREEN → PRV-01~04） | T-07, T-08 | ✓ |
| T-10 | `tests/unit/test_tushare_adapter.py` | ADP-01~06（RED，mock，含 ts_codes 过滤测试） | T-08 | ✓ |
| T-11 | `data/adapters/tushare.py` | TushareAdapter 实现（GREEN → ADP-01~06） | T-10 | ✓ |
| T-12 | `data/adapters/akshare.py` | AKShareAdapter 最小实现（含 fetch_index_components 抛 NotImplementedError） | T-08 | ✓ |
| T-13 | `tests/integration/test_data_repository.py` | REPO-01~04（RED，含 index_history OHLCV 字段） | T-01 | ✓ |
| T-14 | `alembic/versions/0002_phase2_index_ohlcv_components.py` + 更新 `models/market.py` | Phase 2 迁移（index_history OHLCV + index_component 表）；同步更新 IndexHistory ORM 模型补充 OHLCV 字段；新增 IndexComponent ORM 模型类（§3.1.1） | — | ✓ |
| T-15 | `data/repository.py` | MarketDataRepository 实现（GREEN → REPO-01~04，含 index_component） | T-13, T-14 | ✓ |
| T-16 | `tests/integration/test_index_components.py` | INC-01~03（RED） | T-15 | ✓ |
| T-17 | `services/data_service.py` | DataService 实现（ingest_daily 返回 IngestResult，含 snapshot_version） | T-04, T-06, T-11, T-15 | ✓ |
| T-18 | `tests/integration/test_data_ingestion.py` | ING-01~06（RED → GREEN，含 ING-04/05/06 实现阶段补充） | T-17 | ✓ |
| T-19 | `tests/e2e/test_data_api.py` | DATA-01~04（RED） | T-02 | ✓ |
| T-20 | `api/v1/data.py` + 更新 `api/v1/__init__.py` + `main.py` | data 路由 + 依赖注入（GREEN → DATA-01~04） | T-02, T-17, T-19 | ✓ |
| T-21 | `pipeline/scheduler.py` + 更新 `main.py` lifespan | APScheduler 每日任务（含交易日判断，calendar 参数注入） | T-04, T-17, T-20 | ✓ |

---

## 11. 验收标准（DoD）

全部满足后 Phase 2 方可视为完成：

### 11.1 功能验收

- [x] `uv run pytest tests/ -v` 全部 **71** 个测试通过（Phase 1 的 25 个 + Phase 2 新增 46 个）
- [x] 覆盖率 `quantpilot/data/` 和 `quantpilot/services/data_service.py` ≥ 85%
- [x] `POST /api/v1/data/ingest/daily` 成功采集并入库一个真实交易日数据（需 `TUSHARE_TOKEN`）
- [x] `GET /api/v1/data/status` 返回正确的 `latest_quote_date` 和 `stock_count`

### 11.2 数据质量验收

- [x] `daily_quote` 表中某一交易日的股票数量 ≥ 4500（全市场覆盖）
- [x] `index_history` 表含 `open/high/low/vol` 字段，数据非空（为 Phase 3 ADX 准备）
- [x] `index_component` 表含至少一个指数的成分股数据（幸存者偏差消除底座）
- [x] `financial_data` PIT 查询：`publish_date <= as_of_date` 约束生效（单元测试 REPO-03 通过）
- [x] upsert 幂等性：同一交易日调用两次 `ingest_daily()`，第二次无报错，数据不重复

### 11.3 代码规范验收

- [x] `uv run ruff check src/ tests/` 无 error
- [x] 所有 DataSourceAdapter 方法的输出 DataFrame 包含规定的全部列
- [x] `AdjustedPriceProvider` 无持久化操作（仅内存计算）
- [x] Tushare SDK 调用全部通过 `asyncio.to_thread()` 异步化
- [x] 调度器位于 `pipeline/scheduler.py`（遵循系统设计规划路径）
- [x] `validate_financial_data` 调用处传入 `as_of_date=trade_date`（非 `date.today()`）

### 11.4 不在本 Phase 范围内（排除项）

- 市场状态识别和因子评分（Phase 3/4）
- WebSocket 进度推送（Phase 9）
- 历史回填 UI（Phase 9）
- AKShare 日线/财务数据（Phase 3+ 按需补充）
- MonthlyScheduler（因子监控 + 月报，Phase N）
- TUSHARE_TOKEN 动态运维 API（system_config 覆盖，Phase N）
