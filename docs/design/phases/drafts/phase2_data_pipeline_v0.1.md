# Phase 2：数据采集层（归档）

> **版本：** v0.1（已归档）
> **所属阶段：** Phase 2 / 10
> **依据文档：** system_design_v1.0.md §2.1、§2.5；SDD §4、§5
> **日期：** 2026-03-13
> **归档说明：** 本文件为 v1.0 专家审查前的原始版本，已于 2026-03-18 归档。正式版本见 `phases/phase2_data_pipeline.md`

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-03-13 | 初版（专家审查前归档） |

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
- 严格 PIT 合规：财务数据以公告发布日为可用时点
- 数据质量保障：入库前执行完整性/有效性/连续性校验
- 复权价格按需派生：不持久化，支持后复权（回测）和前复权（展示）

### 1.2 主要交付物

| 交付物 | 说明 |
|--------|------|
| `data/adapters/base.py` | DataSourceAdapter ABC（适配器模式，输出 SDD 附录 D 标准格式） |
| `data/adapters/tushare.py` | Tushare Pro 适配器（主数据源） |
| `data/adapters/akshare.py` | AKShare 适配器（备用/补充数据源） |
| `data/calendar.py` | TradingCalendar（A 股交易日历，基于 Tushare trade_cal） |
| `data/validators.py` | DataValidator（SDD §5.5 全部校验规则） |
| `data/price_provider.py` | AdjustedPriceProvider（前/后复权按需派生） |
| `data/repository.py` | MarketDataRepository（4 张表的幂等 upsert CRUD） |
| `services/data_service.py` | DataService（采集流程编排） |
| `schemas/data.py` | 数据相关 Pydantic Schema |
| `api/v1/data.py` | 数据管理 API（状态查询 + 手动触发） |
| `core/scheduler.py` | APScheduler 配置（每日 17:30 采集任务） |
| 全量测试套件 | 31 个测试用例，覆盖 CAL/VAL/PRV/ADP/REPO/DATA/ING |

---

## 2. 前置条件

- Phase 1 全部完成（DB schema、JWT 认证、Docker 环境）
- `backend/.env` 中 `TUSHARE_TOKEN` 已填入有效 Token
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
├── services/                      # Phase 2 新增
│   ├── __init__.py
│   └── data_service.py            # DataService
├── schemas/
│   ├── auth.py                    # 已有
│   └── data.py                    # Phase 2 新增
├── api/v1/
│   ├── auth.py                    # 已有
│   └── data.py                    # Phase 2 新增
└── core/
    ├── scheduler.py               # Phase 2 新增
    └── ...                        # 已有

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
    └── test_data_ingestion.py     # Phase 2 新增
```

---

## 4. 模块规格

### 4.1 DataSourceAdapter ABC

**文件**：`data/adapters/base.py`

所有适配器继承此抽象类。输出 DataFrame 的列名必须严格符合 SDD 附录 D 标准格式（§5 详见字段映射表）。

```python
from abc import ABC, abstractmethod
from datetime import date
import pandas as pd


class DataSourceAdapter(ABC):
    """数据源适配器基类。
    所有输出 DataFrame 的列名遵循 SDD 附录 D 标准格式（snake_case，元，小数比率）。
    所有方法均为异步，内部用 asyncio.to_thread() 包装同步 SDK。
    """

    @abstractmethod
    async def fetch_stock_list(self) -> pd.DataFrame:
        """获取全市场股票基础信息（含已退市股）。
        输出列：ts_code, name, market, sw_industry_l1, sw_industry_l2,
                list_date, delist_date, is_active
        """

    @abstractmethod
    async def fetch_daily_quotes(
        self, trade_date: date
    ) -> pd.DataFrame:
        """获取指定交易日全市场日线数据。
        输出列：ts_code, trade_date, open, high, low, close, pre_close,
                pct_chg, vol, amount, turnover_rate, float_mkt_cap,
                adj_factor, is_suspended, is_st, limit_up, limit_down
        单位：价格（元）、vol（股）、amount（元）、rate（小数）、市值（元）
        """

    @abstractmethod
    async def fetch_financial_data(
        self, trade_date: date
    ) -> pd.DataFrame:
        """获取截至 trade_date 最新公告的财务数据（PIT：以 publish_date 为准）。
        输出列：ts_code, report_period, publish_date, pe_ttm, pb, roe,
                net_profit_yoy, revenue_yoy, dividend_yield,
                total_equity, debt_to_asset
        单位：比率（小数）、金额（元）
        """

    @abstractmethod
    async def fetch_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """获取指数历史日线数据。
        输出列：index_code, trade_date, close, pct_chg
        单位：价格（元）、pct_chg（小数）
        """

    @abstractmethod
    async def fetch_trade_calendar(
        self, start_date: date, end_date: date
    ) -> list[date]:
        """获取指定范围内的 A 股交易日列表（升序）。"""
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
from dataclasses import dataclass
from datetime import date
import pandas as pd


@dataclass
class ValidationResult:
    is_valid: bool
    warnings: list[str]   # 告警但不阻断（如复权连续性异常）
    errors: list[str]     # 阻断性错误（如数据量不足 95%）
    invalid_rows: pd.Index  # 异常行的索引（价格/成交量异常）


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
        """

    def validate_financial_data(
        self, df: pd.DataFrame
    ) -> ValidationResult:
        """执行财务数据 PIT 校验：
        - publish_date 不能晚于今日
        - publish_date >= report_period（公告不能早于报告期末）
        - total_equity 允许负值（用于过滤，需保留）
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

```python
import pandas as pd
from datetime import date


class AdjustedPriceProvider:
    """复权价格按需派生器。
    输入：从 DB 查询到的原始 close 序列 + adj_factor 序列
    输出：复权后的价格序列
    禁止将结果持久化为唯一历史数据。
    """

    def backward_adjusted(
        self, close: pd.Series, adj_factor: pd.Series
    ) -> pd.Series:
        """后复权序列（以上市首日为基准=1.0，历史价格向前累乘）。
        公式：backward_adj_close[t] = close[t] × adj_factor[t]
        用于：回测引擎。序列稳定，不随新除权事件变化。
        入参：close 和 adj_factor 均以 trade_date 为 index，升序。
        """

    def forward_adjusted(
        self, close: pd.Series, adj_factor: pd.Series
    ) -> pd.Series:
        """前复权序列（以最新价为基准，历史价格向前调整）。
        公式：forward_adj_close[t] = close[t] × (adj_factor[-1] / adj_factor[t])
        用于：界面展示。动态计算，随新除权事件变化。
        """
```

**adj_factor 语义**：Tushare `adj_factor` 字段为累乘式，以上市首日为基准值 1.0，除权时乘以调整系数（< 1.0 表示除权）。

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

    async def fetch_daily_quotes(self, trade_date: date) -> pd.DataFrame:
        """合并调用：
        - pro.daily(trade_date=...) → OHLCV + pct_chg
        - pro.daily_basic(trade_date=...) → turnover_rate, circ_mv
        - pro.adj_factor(trade_date=...) → adj_factor
        - pro.suspend_d(suspend_date=...) → is_suspended
        - pro.limit_list_d(trade_date=...) → limit_up, limit_down
        映射并合并为单一 DataFrame"""

    async def fetch_financial_data(self, trade_date: date) -> pd.DataFrame:
        """调用 pro.fina_indicator(ann_date≤trade_date)
        + pro.daily_basic() 获取 pe_ttm, pb, dv_ttm
        取每只股票最新公告期（max publish_date）的数据"""

    async def fetch_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """调用 pro.index_daily(ts_code=index_code, ...)"""

    async def fetch_trade_calendar(
        self, start_date: date, end_date: date
    ) -> list[date]:
        """调用 pro.trade_cal(exchange='SSE', ...)
        过滤 is_open==1 的日期，返回升序 date 列表"""
```

**特别约束**：
- Tushare 日期参数格式为 `YYYYMMDD` 字符串，需在调用前转换
- 单次调用最多返回 10000 行；全市场 5000 只需分批（按日期或股票代码分批）
- 历史回填时使用 `trade_date` 循环，每次取一个交易日全市场数据

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
    async def fetch_daily_quotes(self, trade_date) -> pd.DataFrame:
        raise NotImplementedError("AKShare daily quotes not implemented in Phase 2")
    async def fetch_financial_data(self, trade_date) -> pd.DataFrame:
        raise NotImplementedError
    async def fetch_index_history(self, index_code, start_date, end_date) -> pd.DataFrame:
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
        批量大小：500 行/批，避免单次 SQL 过大。
        返回：upsert 行数"""

    async def get_latest_quote_date(self) -> date | None:
        """返回 daily_quote 中最新的 trade_date，用于增量判断"""

    async def get_daily_quotes(
        self, ts_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """查询单只股票日线序列（含 close, adj_factor），用于复权计算"""

    # ---- financial_data ----
    async def upsert_financial_data(self, df: pd.DataFrame) -> int:
        """批量 upsert financial_data。ON CONFLICT (ts_code, report_period, publish_date) DO UPDATE"""

    async def get_latest_financial(
        self, ts_codes: list[str], as_of_date: date
    ) -> pd.DataFrame:
        """PIT 查询：取每只股票在 as_of_date 时点可用的最新财务数据
        （即 publish_date <= as_of_date 的最新一期）"""

    # ---- index_history ----
    async def upsert_index_history(self, df: pd.DataFrame) -> int:
        """批量 upsert index_history。ON CONFLICT (index_code, trade_date) DO UPDATE"""

    async def get_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """查询指数历史"""

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
from datetime import date
from quantpilot.data.adapters.base import DataSourceAdapter
from quantpilot.data.validators import DataValidator
from quantpilot.data.repository import MarketDataRepository
from quantpilot.data.calendar import TradingCalendar


class DataService:

    def __init__(
        self,
        adapter: DataSourceAdapter,
        validator: DataValidator,
        repo: MarketDataRepository,
        calendar: TradingCalendar,
    ):
        ...

    async def ingest_daily(self, trade_date: date) -> dict:
        """单日全量采集流程：
        1. 校验 trade_date 是否为交易日
        2. fetch_daily_quotes() → 校验（完整性、价格有效性）→ upsert
        3. fetch_financial_data() → 校验（PIT 合规）→ upsert
        4. fetch_index_history() for 4 indexes → upsert
        5. 返回采集摘要 {trade_date, quote_count, financial_count, errors}
        """

    async def ingest_history(
        self, start_date: date, end_date: date,
        progress_callback=None,
    ) -> dict:
        """历史数据回填：
        按交易日循环调用 ingest_daily()，
        progress_callback(current, total) 用于 WebSocket 进度推送（Phase 9）。
        遇到单日失败：记录日志，继续下一日（不中断整批）。
        返回 {success_count, fail_count, failed_dates}
        """

    async def refresh_stock_list(self) -> dict:
        """刷新全市场股票基础信息（含退市股）"""

    async def get_status(self) -> dict:
        """返回数据新鲜度状态，委托 repo.get_data_status()"""
```

---

## 5. Tushare 字段映射表

### 5.1 日线行情（`daily` + `daily_basic` + `adj_factor`）

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
| `is_st` | stock_basic | name 含 "ST" 或 "\*ST" | 布尔派生 |
| `limit_up` | limit_list_d | `limit_type == 'U'` | 布尔派生 |
| `limit_down` | limit_list_d | `limit_type == 'D'` | 布尔派生 |

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
| `total_equity` | fina_indicator | `total_hldr_eqy_exc_min_int` | `万元 → 元（× 10000）` |
| `debt_to_asset` | fina_indicator | `debt_to_assets` | `% → 小数（/ 100）` |

### 5.3 指数历史（`index_daily`）

| 内部字段 | Tushare 字段 | 单位转换 |
|----------|-------------|---------|
| `index_code` | `ts_code` | 直接使用 |
| `trade_date` | `trade_date` | YYYYMMDD → date |
| `close` | `close` | 元，直接使用 |
| `pct_chg` | `pct_chg` | `% → 小数（/ 100）` |

### 5.4 目标指数列表

| 指数代码 | 名称 |
|----------|------|
| `000001.SH` | 上证指数 |
| `000300.SH` | 沪深 300（市场状态识别主参考） |
| `000905.SH` | 中证 500 |
| `399006.SZ` | 创业板指 |

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

**文件**：`core/scheduler.py`

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


def create_scheduler(data_service) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 每日 17:30 触发增量采集（A 股 15:00 收盘，留 2.5h 数据就绪缓冲）
    scheduler.add_job(
        _daily_ingest_job,
        trigger=CronTrigger(hour=17, minute=30, timezone="Asia/Shanghai"),
        args=[data_service],
        id="daily_ingest",
        replace_existing=True,
        misfire_grace_time=3600,  # 错过触发时 1h 内补跑
    )
    return scheduler


async def _daily_ingest_job(data_service) -> None:
    from datetime import date
    from quantpilot.data.calendar import TradingCalendar
    # 取最近交易日（而非当日，确保数据已完整）
    today = date.today()
    # 若今日是交易日则采集今日，否则跳过
    result = await data_service.ingest_daily(today)
    # 记录日志（结构化日志，Phase 9 接入监控）
    import logging
    logger = logging.getLogger(__name__)
    logger.info("daily_ingest_completed", extra=result)
```

**在 `main.py` lifespan 中注册**：

```python
from contextlib import asynccontextmanager
from quantpilot.core.scheduler import create_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动
    scheduler = create_scheduler(data_service)
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
| 完整性错误（< 95%） | 记录 ERROR 日志，中止当日入库，返回 errors 字段 |
| 价格异常行 | 标记 `invalid_rows`，其余正常行继续入库，记录 WARNING |
| 复权连续性告警 | 记录 WARNING，不阻断 |
| PIT 违规（publish_date 异常） | 记录 ERROR，跳过该行 |

### 8.3 历史回填容错

- 单日失败不中断整批（记录 `failed_dates` 列表）
- 支持断点续传：检查 `daily_quote` 已有数据，跳过已入库的交易日
- 完成后输出 `{success_count, fail_count, failed_dates}` 摘要

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

### 9.2 单元测试：DataValidator（`tests/unit/test_data_validator.py`）

| ID | 描述 | 验证点 |
|----|------|--------|
| VAL-01 | 正常日线数据 → `is_valid=True`，无 errors | 基准用例 |
| VAL-02 | `low > close` 的行 → `invalid_rows` 包含该行，不产生 error | 不阻断但标记 |
| VAL-03 | 股票数 < prev_count × 0.95 → `errors` 非空 | 完整性阻断 |
| VAL-04 | `publish_date > today` → PIT 违规，`errors` 非空 | 财务数据时效 |
| VAL-05 | `adj_factor` 相邻日变化 30% → `warnings` 非空 | 复权连续性告警 |

### 9.3 单元测试：AdjustedPriceProvider（`tests/unit/test_price_provider.py`）

构造 5 日简单序列：`close=[10,10,9,9,9]`，`adj_factor=[1.0,1.0,0.9,0.9,0.9]`（第 3 日除权）。

| ID | 描述 | 验证点 |
|----|------|--------|
| PRV-01 | `backward_adjusted()` 结果 = close × adj_factor | [10,10,8.1,8.1,8.1] |
| PRV-02 | `forward_adjusted()` 最新日等于当前 close | 最后一日 = close[-1] |
| PRV-03 | `forward_adjusted()` 与 `backward_adjusted()` 价格比率一致 | 相对涨跌幅相同 |
| PRV-04 | 上市首日 adj_factor=1.0 时，后复权 = 原始价格 | 无除权时两者相等 |

### 9.4 单元测试：TushareAdapter（`tests/unit/test_tushare_adapter.py`，Mock 数据）

使用 `unittest.mock.AsyncMock` 模拟 `asyncio.to_thread`，注入预制的 Tushare 响应 DataFrame。

| ID | 描述 | 验证点 |
|----|------|--------|
| ADP-01 | `fetch_stock_list()` 映射正确 | ts_code, name, list_date 字段存在且类型正确 |
| ADP-02 | `fetch_daily_quotes()` vol 单位转换 | vol 列值 = Tushare 手数 × 100 |
| ADP-03 | `fetch_daily_quotes()` amount 单位转换 | amount 列值 = Tushare 千元 × 1000 |
| ADP-04 | `fetch_daily_quotes()` pct_chg 转为小数 | 5.0% → 0.05 |
| ADP-05 | `fetch_financial_data()` roe 转为小数 | 15.0% → 0.15 |

### 9.5 E2E 测试：数据管理 API（`tests/e2e/test_data_api.py`，ASGI，Mock DataService）

在 `conftest.py` 中添加 `mock_data_service` fixture，覆盖 `DataService` 依赖。

| ID | 描述 | 验证点 |
|----|------|--------|
| DATA-01 | `GET /api/v1/data/status`（有 token）→ 200 | code=0, data 含 latest_quote_date |
| DATA-02 | `GET /api/v1/data/status`（无 token）→ 401 | 未鉴权拒绝 |
| DATA-03 | `POST /api/v1/data/ingest/daily`（有 token）→ 200 | code=0, data 含 quote_count |
| DATA-04 | `POST /api/v1/data/ingest/daily`（非交易日）→ 400 | 正确错误码 |

### 9.6 集成测试：MarketDataRepository（`tests/integration/test_data_repository.py`）

需要真实 PostgreSQL，使用 `db_session` fixture。

| ID | 描述 | 验证点 |
|----|------|--------|
| REPO-01 | `upsert_stock_list()` 批量插入 → 查询确认行数 | count 匹配 |
| REPO-02 | `upsert_daily_quotes()` 重复 upsert 同一天 → 不报错，数据被更新 | 幂等性 |
| REPO-03 | `get_latest_financial(as_of_date)` PIT 查询 → 不返回未来公告 | publish_date 约束 |
| REPO-04 | `upsert_index_history()` + `get_index_history()` 范围查询 | 日期区间正确 |

### 9.7 集成测试：DataService（`tests/integration/test_data_ingestion.py`）

使用 Mock 适配器 + 真实 DB，验证完整采集流程。

| ID | 描述 | 验证点 |
|----|------|--------|
| ING-01 | `ingest_daily()` 正常流程 → 数据入库，返回摘要 | quote_count > 0 |
| ING-02 | 校验失败（完整性不足）→ 不入库，errors 非空 | 事务回滚 |
| ING-03 | `ingest_history()` 3 日范围 → 3 日数据入库 | get_latest_quote_date 正确 |

---

## 10. 任务计划

共 **19 个任务**，按 TDD 流程执行（标注 RED/GREEN）。

| 任务 | 文件 | 说明 | 前置 |
|------|------|------|------|
| T-01 | `data/__init__.py`, `data/adapters/__init__.py`, `services/__init__.py` | 目录骨架 | — |
| T-02 | `schemas/data.py` | DataStatus, IngestRequest, IngestResponse Pydantic Schema | T-01 |
| T-03 | `tests/unit/test_trading_calendar.py` | CAL-01~06（RED） | T-01 |
| T-04 | `data/calendar.py` | TradingCalendar 实现（GREEN → CAL-01~06） | T-03 |
| T-05 | `tests/unit/test_data_validator.py` | VAL-01~05（RED） | T-01 |
| T-06 | `data/validators.py` | DataValidator 实现（GREEN → VAL-01~05） | T-05 |
| T-07 | `tests/unit/test_price_provider.py` | PRV-01~04（RED） | T-01 |
| T-08 | `data/adapters/base.py` | DataSourceAdapter ABC | T-01 |
| T-09 | `data/price_provider.py` | AdjustedPriceProvider 实现（GREEN → PRV-01~04） | T-07, T-08 |
| T-10 | `tests/unit/test_tushare_adapter.py` | ADP-01~05（RED，mock） | T-08 |
| T-11 | `data/adapters/tushare.py` | TushareAdapter 实现（GREEN → ADP-01~05） | T-10 |
| T-12 | `data/adapters/akshare.py` | AKShareAdapter 最小实现 | T-08 |
| T-13 | `tests/integration/test_data_repository.py` | REPO-01~04（RED） | T-01 |
| T-14 | `data/repository.py` | MarketDataRepository 实现（GREEN → REPO-01~04） | T-13 |
| T-15 | `services/data_service.py` | DataService 实现 | T-04, T-06, T-11, T-14 |
| T-16 | `tests/integration/test_data_ingestion.py` | ING-01~03（RED → GREEN） | T-15 |
| T-17 | `tests/e2e/test_data_api.py` | DATA-01~04（RED） | T-02 |
| T-18 | `api/v1/data.py` + 更新 `api/v1/__init__.py` + `main.py` | data 路由 + 依赖注入（GREEN → DATA-01~04） | T-02, T-15, T-17 |
| T-19 | `core/scheduler.py` + 更新 `main.py` lifespan | APScheduler 每日任务 | T-15, T-18 |

---

## 11. 验收标准（DoD）

全部满足后 Phase 2 方可视为完成：

### 11.1 功能验收

- [ ] `uv run pytest tests/ -v` 全部 31 个测试通过（含 Phase 1 的 26 个）
- [ ] 覆盖率 `quantpilot/data/` 和 `quantpilot/services/data_service.py` ≥ 85%
- [ ] `POST /api/v1/data/ingest/daily` 成功采集并入库一个真实交易日数据（需 `TUSHARE_TOKEN`）
- [ ] `GET /api/v1/data/status` 返回正确的 `latest_quote_date` 和 `stock_count`

### 11.2 数据质量验收

- [ ] `daily_quote` 表中某一交易日的股票数量 ≥ 4500（全市场覆盖）
- [ ] `financial_data` PIT 查询：`publish_date <= as_of_date` 约束生效（单元测试 REPO-03 通过）
- [ ] upsert 幂等性：同一交易日调用两次 `ingest_daily()`，第二次无报错，数据不重复

### 11.3 代码规范验收

- [ ] `uv run ruff check src/ tests/` 无 error
- [ ] 所有 DataSourceAdapter 方法的输出 DataFrame 包含规定的全部列
- [ ] `AdjustedPriceProvider` 无持久化操作（仅内存计算）
- [ ] Tushare SDK 调用全部通过 `asyncio.to_thread()` 异步化

### 11.4 不在本 Phase 范围内（排除项）

- 市场状态识别和因子评分（Phase 3/4）
- WebSocket 进度推送（Phase 9）
- 历史回填 UI（Phase 9）
- AKShare 日线/财务数据（Phase 3+ 按需补充）
