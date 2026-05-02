# Phase 3：市场状态识别

> **版本：** v1.1
> **所属阶段：** Phase 3 / 10
> **依据文档：** system_design.md §2.6、§3、§4；SDD §6、附录 B
> **日期：** 2026-03-27
> **预期产出：** 基于 ADX + 均线的三态市场状态识别引擎，含防抖动机制、每日自动计算、持久化存储及 REST 查询 API

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-03-27 | 初版 |
| **v1.0** | 2026-03-27 | **设计审查修正**（版本号不变）：P1-01 修正 MarketStateService 生命周期（Engine 单例存 app.state，Service 按请求/任务创建，对齐 Phase 2 DataService 模式）；P2-01 补充 prev_confirmed 语义说明（窗口前状态查询 + burn-in 说明）；P2-02 新增 api/deps.py 到项目结构；P2-03 修正 MarketStateHistory ORM 归属（models/business.py）；P2-04 DoD 补充测试总数（90）；P3-02 补充 identify() description 生成逻辑说明；P3-03 移除冗余 open 字段；P3-04 修正 §11.1 权重来源表述 |
| **v1.1** | 2026-03-28 | **实现后同步（代码审查 CR-01/CR-04）**：CR-01 补充时间窗口说明（§2 新增 1.5× 日历天换算规则，CR-04 补充调度器显式传参方式（§3 更新 scheduler.py 说明）；其余 CR 均与设计文档一致，无需修订 |
| **v1.2** | 2026-03-30 | **整合修正**：头部依据文档引用 §5.3（策略基类）→ §4（数据模型）——Phase 3 不含策略实现，正确引用应为数据模型章节 |

---

## 目录

1. [阶段目标与交付物](#1-阶段目标与交付物)
2. [前置条件](#2-前置条件)
3. [新增项目结构](#3-新增项目结构)
4. [算法规格](#4-算法规格)
5. [模块规格](#5-模块规格)
6. [数据库](#6-数据库)
7. [API 端点规格](#7-api-端点规格)
8. [测试用例](#8-测试用例)
9. [任务计划](#9-任务计划)
10. [验收标准（DoD）](#10-验收标准dod)
11. [Phase 4 前置说明](#11-phase-4-前置说明)

---

## 1. 阶段目标与交付物

### 1.1 目标

建立市场环境感知底座，实现每日自动识别大盘所处状态，为 Phase 4 策略评分的权重动态切换提供输入：

- 基于沪深 300 指数 OHLCV 数据，使用 ADX（趋势强度）+ MA20/MA60（均线方向）进行三态识别
- 防抖动机制：连续 3 个交易日满足新状态条件才确认切换，避免因单日波动频繁翻转（SDD §6.5）
- 每日 17:30 数据采集完成后自动触发计算并持久化
- 对外暴露 REST API，支持查询当前状态和历史状态

### 1.2 主要交付物

| 交付物 | 说明 |
|--------|------|
| `engine/market_state.py` | MarketStateEngine（纯函数，无 IO；ADX/MA 计算 + 防抖动） |
| `services/market_state_service.py` | MarketStateService（编排：DB 查询 → Engine → 持久化） |
| `api/v1/market.py` | GET /api/v1/market/state + GET /api/v1/market/state/history |
| `schemas/market.py` | MarketStateResponse、MarketStateHistoryResponse Pydantic Schema |
| `data/repository.py` 扩展 | 新增 `upsert_market_state`、`get_latest_market_state`、`get_market_state_history` |
| 调度器更新 | `pipeline/scheduler.py`：数据采集完成后触发市场状态识别 |
| 测试套件 | 19 个 Phase 3 新增测试（MSE-01~10 / MSTS-01~05 / MAPI-01~04） |

---

## 2. 前置条件

- **Phase 2 全部完成**：`index_history` 表含 `open/high/low/close/vol` 字段（Phase 2 补充），000300.SH 至少有 100 个交易日的历史数据（历史回填完成）
- **无新数据库迁移**：`market_state_history` 表已在 `0001_initial_schema.py` 中创建，字段完整，无需新增迁移

> **历史数据最低要求：** `identify()` 需要 `min(MA60周期, ADX暖启动) ≈ 88` 个交易日的数据。
> 服务层固定取最近 **100 个交易日**以留足余量。
>
> **⚠️ 实现注意（CR-01）：** `history_days=100` 是**交易日**数，不可直接用 `timedelta(days=100)` 查询（仅约 70 交易日）。
> 实现中使用 `calendar_days = int(history_days * 1.5)` 换算为日历天（150 天 ≈ 107 交易日），
> 避免注入 `TradingCalendar` 依赖到 `MarketStateService`。

---

## 3. 新增项目结构

```
backend/src/quantpilot/
├── engine/                          # Phase 3 新增目录
│   ├── __init__.py                  # 导出 MarketStateEngine, MarketStateEnum, MarketStateRecord
│   └── market_state.py             # 核心算法（纯函数）
├── schemas/
│   └── market.py                   # Phase 3 新增：MarketStateResponse/HistoryResponse
├── services/
│   ├── data_service.py             # Phase 2 已有
│   └── market_state_service.py     # Phase 3 新增
└── api/v1/
    ├── auth.py                     # Phase 1 已有
    ├── data.py                     # Phase 2 已有
    └── market.py                   # Phase 3 新增
```

**修改已有文件：**

| 文件 | 修改内容 |
|------|----------|
| `data/repository.py` | 新增 3 个 `market_state_history` CRUD 方法 |
| `api/v1/__init__.py` | 注册 market router（`prefix="/market"`） |
| `api/deps.py`（**新建**） | `get_market_state_service(request, session)` 依赖注入函数（参照 `get_data_service` 模式，按请求创建 `MarketStateService`） |
| `main.py` | lifespan 中初始化 `MarketStateEngine` 单例并存入 `app.state.market_state_engine`；`MarketStateService` 不存入 `app.state`（按请求/任务创建） |
| `pipeline/scheduler.py` | `create_scheduler()` 新增 `market_state_engine: MarketStateEngine` 参数，通过 `args=[...]` 传入 job 函数；job 中使用 session_factory 创建独立 session，实例化 `MarketStateService` 并调用 `identify_and_save()`（**CR-04**：job 函数无法访问 `request.app.state`，Engine 单例必须显式传参） |

---

## 4. 算法规格

### 4.1 判定指标

以**沪深 300 指数**（`000300.SH`）日线数据为唯一输入（Phase 3 固定，Phase N 可通过 `system_config` 配置化）：

| 指标 | 计算方法 | 参数 | 库 |
|------|---------|------|----|
| MA20 | 收盘价简单移动平均 | 周期 = 20 | pandas `.rolling(20).mean()` |
| MA60 | 收盘价简单移动平均 | 周期 = 60 | pandas `.rolling(60).mean()` |
| ADX | 平均方向指数（Wilder 平滑） | 周期 = 14 | `pandas_ta.adx()`，取 `ADX_14` 列 |

> **ADX 暖启动：** `pandas_ta.adx(length=14)` 在前 ~27 行输出 NaN（Wilder DM 平滑需 2 × 14 行数据），`compute_indicators()` 不丢弃这些行，交由调用方处理 NaN。

### 4.2 状态判定逻辑（SDD §6.3）

```
if ADX > 25:                        # 趋势明确
    if MA20 > MA60 and close > MA20:
        → UPTREND（上涨趋势）
    elif MA20 < MA60 and close < MA20:
        → DOWNTREND（下跌趋势）
    else:                           # ADX 强但均线信号混乱
        → OSCILLATION（震荡市）
else:                               # ADX ≤ 25，趋势强度不足
    → OSCILLATION
```

**参数默认值（SDD 附录 B）：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ma_short` | 20 | 短期均线周期 |
| `ma_long` | 60 | 长期均线周期 |
| `adx_period` | 14 | ADX 计算周期 |
| `adx_threshold` | 25.0 | 趋势/震荡分界阈值 |
| `debounce_days` | 3 | 防抖动确认天数 |

### 4.3 防抖动机制（SDD §6.5）

```
confirmed_state(t) =
    new_raw_state    if raw_state(t-2) == raw_state(t-1) == raw_state(t) == new_raw_state
                        AND new_raw_state != confirmed_state(t-1)
    confirmed_state(t-1)    otherwise
```

实现方式：`apply_debounce()` 接收完整的 raw state 序列（`pd.Series`，index=date）和 `prev_confirmed`（初始已确认状态），从前向后遍历，每次仅查看最近 `debounce_days` 个 raw state 是否一致且与当前 confirmed 不同。

> **初始状态：** 如 DB 中无历史记录（首次运行），`prev_confirmed = MarketStateEnum.OSCILLATION`。

### 4.4 `trend_strength` 与 `description`

| 字段 | 规则 |
|------|------|
| `trend_strength` | `min(adx_value, 100.0)`（ADX 已在 0-100 量纲，clip 防极端值） |
| `description` | 见下表 |

| 状态 | `description` 模板 |
|------|--------------------|
| UPTREND | `f"上涨趋势：ADX={adx:.1f}，均线多头排列（MA20={ma20:.2f} > MA60={ma60:.2f}）"` |
| DOWNTREND | `f"下跌趋势：ADX={adx:.1f}，均线空头排列（MA20={ma20:.2f} < MA60={ma60:.2f}）"` |
| OSCILLATION (ADX ≤ 25) | `f"震荡市：趋势强度不足（ADX={adx:.1f} ≤ 25），无明确方向"` |
| OSCILLATION (ADX > 25, 混乱) | `f"震荡市：ADX={adx:.1f} 偏强但均线方向不明确"` |

---

## 5. 模块规格

### 5.1 MarketStateEngine（`engine/market_state.py`）

#### 数据结构

```python
from enum import StrEnum
from dataclasses import dataclass
from datetime import date

class MarketStateEnum(StrEnum):
    UPTREND     = "UPTREND"
    DOWNTREND   = "DOWNTREND"
    OSCILLATION = "OSCILLATION"

@dataclass(frozen=True)
class MarketStateRecord:
    trade_date:     date
    market_state:   MarketStateEnum
    trend_strength: float           # 0-100，ADX 值（已 clip）
    adx_value:      float
    ma20:           float
    ma60:           float
    state_changed:  bool            # 与前一已确认状态相比是否发生切换
    description:    str
```

#### MarketStateEngine 接口

```python
class MarketStateEngine:
    def __init__(
        self,
        ma_short: int = 20,
        ma_long: int = 60,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        debounce_days: int = 3,
    ) -> None: ...

    def compute_indicators(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """
        输入：ohlcv — columns=[high, low, close]，index=date（升序）
        输出：在输入 DataFrame 上追加 [ma20, ma60, adx] 列，返回新 DataFrame。
        前 (ma_long-1) 行 ma60 为 NaN；前 ~27 行 adx 为 NaN（暖启动）。
        使用 pandas_ta.adx(high, low, close, length=adx_period)，取 ADX_<period> 列。
        """

    def determine_raw_state(
        self, adx: float, ma20: float, ma60: float, close: float
    ) -> MarketStateEnum:
        """单行判定，无防抖动。纯函数。"""

    def apply_debounce(
        self,
        raw_states: pd.Series,          # index=date（升序），values=MarketStateEnum
        prev_confirmed: MarketStateEnum = MarketStateEnum.OSCILLATION,
    ) -> pd.Series:
        """
        对 raw_states 序列逐日应用防抖动规则。
        返回同 index 的 confirmed_state 序列。纯函数。
        """

    def identify(
        self,
        ohlcv: pd.DataFrame,
        prev_confirmed: MarketStateEnum = MarketStateEnum.OSCILLATION,
    ) -> list[MarketStateRecord]:
        """
        完整流水线：compute_indicators → determine_raw_state → apply_debounce → records。
        仅返回所有指标均非 NaN 的日期的记录（即丢弃暖启动期）。
        prev_confirmed：OHLCV 窗口第一天之前的已确认状态（首次运行传 OSCILLATION）。
        description 由本方法按 §4.4 模板生成：对每行记录，根据 confirmed_state 和
        adx_value 选择对应模板格式化输出（OSCILLATION 需区分 ADX≤25 与 ADX>25 两种子情况）。
        """

    def identify_latest(
        self,
        ohlcv: pd.DataFrame,
        prev_confirmed: MarketStateEnum = MarketStateEnum.OSCILLATION,
    ) -> MarketStateRecord | None:
        """
        便捷方法：只返回 ohlcv 最后一行的 MarketStateRecord。
        历史数据不足时返回 None。日常生产调用入口。
        """
```

> **纯函数约束：** `engine/market_state.py` 内禁止任何 IO 操作（数据库、文件、网络）。
> 所有外部依赖（`pandas_ta`）通过函数参数传入数据而非调用 Adapter。

### 5.2 Repository 扩展（`data/repository.py`）

在 `MarketDataRepository` 中新增以下方法：

```python
async def upsert_market_state(self, record: MarketStateRecord) -> None:
    """
    INSERT ... ON CONFLICT (trade_date) DO UPDATE SET
        market_state=..., trend_strength=..., adx_value=...,
        ma20=..., ma60=..., state_changed=..., description=...
    """

async def get_latest_market_state(
    self, before_date: date | None = None
) -> MarketStateHistory | None:
    """
    SELECT * FROM market_state_history
    [WHERE trade_date < before_date]  -- before_date 为 None 时不加约束
    ORDER BY trade_date DESC LIMIT 1
    用于两种场景：
      1. API 查询当前状态（before_date=None）→ 返回全局最新行
      2. identify_and_save 获取 prev_confirmed（before_date=OHLCV 窗口第一天）
    """

async def get_market_state_history(
    self, start_date: date, end_date: date
) -> list[MarketStateHistory]:
    """
    SELECT * FROM market_state_history
    WHERE trade_date BETWEEN :start AND :end
    ORDER BY trade_date ASC
    """
```

> **ORM 类名：** `MarketStateHistory`（`models/business.py` 中已定义）。

### 5.3 MarketStateService（`services/market_state_service.py`）

```python
class MarketStateService:
    def __init__(
        self,
        engine: MarketStateEngine,
        repo: MarketDataRepository,
        index_code: str = "000300.SH",
        history_days: int = 100,
    ) -> None: ...

    async def identify_and_save(self, trade_date: date) -> MarketStateRecord | None:
        """
        1. 从 index_history 取最近 history_days 天的 OHLCV（到 trade_date 为止）
        2. 调用 repo.get_latest_market_state(before_date=first_ohlcv_date) 获取
           OHLCV 窗口第一天之前的最近已确认状态作为 prev_confirmed；
           无记录则 prev_confirmed = OSCILLATION。
           【注意】不得传入 before_date=None（最新行），否则在历史批量回填场景下
           窗口前段记录存在偏差。每日增量运行时 burn-in 收敛效应（约 3 日）
           使最终当日结果不受影响，但批量回填必须严格传入 before_date。
        3. engine.identify_latest(ohlcv, prev_confirmed)
        4. 若结果不为 None：upsert_market_state(record)
        5. 返回 record（或 None 表示数据不足）
        """

    async def get_current_state(self) -> MarketStateRecord | None:
        """从 DB 取最新状态行，转换为 MarketStateRecord 返回。无记录返回 None。"""

    async def get_state_history(
        self, start_date: date, end_date: date
    ) -> list[MarketStateRecord]:
        """从 DB 取指定范围历史，返回 list[MarketStateRecord]（升序）。"""
```

**从 `index_history` 读取的字段：** `high, low, close`（ADX 需要 high/low/close，MA 只需 close；`open` 不使用），以 `trade_date` 为 index 升序排列。

### 5.4 Pydantic Schema（`schemas/market.py`）

```python
class MarketStateItem(BaseModel):
    trade_date:     date
    market_state:   str             # "UPTREND" / "DOWNTREND" / "OSCILLATION"
    trend_strength: float
    adx_value:      float
    ma20:           float
    ma60:           float
    state_changed:  bool
    description:    str

class MarketStateResponse(BaseModel):
    """GET /market/state 响应体 data 字段"""
    current: MarketStateItem | None  # None 表示尚未计算（数据库为空）

class MarketStateHistoryResponse(BaseModel):
    """GET /market/state/history 响应体 data 字段"""
    items: list[MarketStateItem]
    total: int
```

统一包装在标准响应格式 `{"code": 0, "data": ..., "msg": "ok"}` 中。

### 5.5 API 端点（`api/v1/market.py`）

```
GET  /api/v1/market/state
     需要 Bearer token（与其他 API 一致）
     返回 MarketStateResponse

GET  /api/v1/market/state/history
     需要 Bearer token
     查询参数：start: date（必填），end: date（必填）
     返回 MarketStateHistoryResponse
```

依赖注入：`MarketStateService` 通过 `Depends(get_market_state_service)` 注入。`get_market_state_service` 定义于 `api/deps.py`，**按请求创建新实例**：从 `request.app.state.market_state_engine` 取引擎单例，结合当前请求的 `session`（由 `Depends(get_db)` 注入）构造 `MarketStateService`——与 Phase 2 `get_data_service` 模式完全一致。

### 5.6 调度器更新（`pipeline/scheduler.py`）

在 `run_daily_job()` 中，`DataService.ingest_daily()` 成功完成后追加：

```python
# 市场状态识别（Phase 3）
market_state_record = await market_state_service.identify_and_save(trade_date)
if market_state_record:
    logger.info(
        "market_state_identified",
        extra={
            "trade_date": str(trade_date),
            "state": market_state_record.market_state,
            "adx": market_state_record.adx_value,
            "state_changed": market_state_record.state_changed,
        },
    )
```

若 `identify_and_save` 返回 `None`（历史数据不足），仅记录 WARNING，不阻断调度器其他流程。

> **session 生命周期**：调度器中的 `market_state_service` 通过 `session_factory` 在 `_daily_ingest_job` 内创建独立 session 并注入，与 `DataService` 共用同一 job 函数作用域内的 session 即可——禁止将 `MarketStateService` 作为 lifespan 单例持有（session 不可跨任务复用）。

---

## 6. 数据库

### 6.1 无新迁移

`market_state_history` 表已在 `0001_initial_schema.py` 中完整创建。

### 6.2 表字段确认

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | BIGSERIAL PK | — |
| `trade_date` | DATE UNIQUE NOT NULL | 每日一行 |
| `market_state` | VARCHAR(20) NOT NULL | UPTREND / DOWNTREND / OSCILLATION |
| `trend_strength` | NUMERIC(5,2) | ADX 值（clip 至 0-100） |
| `adx_value` | NUMERIC(6,3) | ADX 原始值 |
| `ma20` | NUMERIC(10,3) | 20 日移动平均 |
| `ma60` | NUMERIC(10,3) | 60 日移动平均 |
| `state_changed` | BOOLEAN DEFAULT FALSE | 是否发生状态切换 |
| `description` | TEXT | 状态说明（面向用户） |

> `trade_date UNIQUE` 隐式创建 B-Tree 索引，`ORDER BY trade_date DESC LIMIT 1` 查询无需额外索引。

---

## 7. API 端点规格

### 7.1 GET /api/v1/market/state

**描述：** 查询最新市场状态。

| 项 | 值 |
|----|-----|
| 鉴权 | Bearer JWT（access token） |
| 响应（有数据） | `{"code": 0, "data": {"current": {...MarketStateItem}}, "msg": "ok"}` |
| 响应（无数据） | `{"code": 0, "data": {"current": null}, "msg": "ok"}` |
| 响应（未鉴权） | `{"code": 401, "data": null, "msg": "..."}` |

**响应示例（有数据）：**

```json
{
  "code": 0,
  "data": {
    "current": {
      "trade_date": "2026-03-26",
      "market_state": "UPTREND",
      "trend_strength": 32.5,
      "adx_value": 32.5,
      "ma20": 3850.20,
      "ma60": 3720.40,
      "state_changed": false,
      "description": "上涨趋势：ADX=32.5，均线多头排列（MA20=3850.20 > MA60=3720.40）"
    }
  },
  "msg": "ok"
}
```

### 7.2 GET /api/v1/market/state/history

**描述：** 查询历史市场状态序列。

| 项 | 值 |
|----|-----|
| 鉴权 | Bearer JWT |
| 查询参数 | `start: date`（必填），`end: date`（必填） |
| 响应 | `{"code": 0, "data": {"items": [...], "total": N}, "msg": "ok"}` |
| 参数校验失败 | `{"code": 422, "data": null, "msg": "请求参数校验失败", "errors": [...]}` |

---

## 8. 测试用例

### 8.1 单元测试：MarketStateEngine（`tests/unit/test_market_state_engine.py`）

使用合成 DataFrame（不依赖真实指数数据），通过控制 ADX/MA20/MA60/close 的具体值验证算法正确性。

**测试数据辅助函数：**

```python
def _make_ohlcv(n: int, close_values: list[float]) -> pd.DataFrame:
    """生成 n 行合成 OHLCV，high=close*1.01, low=close*0.99, open=close*0.995"""
```

| ID | 描述 | 验证点 |
|----|------|--------|
| MSE-01 | `determine_raw_state()` → UPTREND | ADX=30, MA20=3100, MA60=3000, close=3050 → UPTREND |
| MSE-02 | `determine_raw_state()` → DOWNTREND | ADX=30, MA20=2900, MA60=3000, close=2880 → DOWNTREND |
| MSE-03 | `determine_raw_state()` → OSCILLATION（低 ADX） | ADX=20, MA20>MA60 → OSCILLATION（ADX≤25 优先） |
| MSE-04 | `determine_raw_state()` → OSCILLATION（均线混乱） | ADX=30, MA20>MA60 but close<MA20 → OSCILLATION |
| MSE-05 | `apply_debounce()` — 连续 1 天新状态：不切换 | prev=OSCILLATION, raw=[OSC, OSC, UP] → confirmed 最后 1 日仍为 OSCILLATION |
| MSE-06 | `apply_debounce()` — 连续 2 天新状态：不切换 | prev=OSCILLATION, raw=[UP, UP] → confirmed 仍为 OSCILLATION |
| MSE-07 | `apply_debounce()` — 连续 3 天新状态：切换 | prev=OSCILLATION, raw=[UP, UP, UP] → confirmed 第 3 日变为 UPTREND |
| MSE-08 | `apply_debounce()` — 中断后重计 | raw=[UP, DOWN, UP, UP, UP] → 最终确认 UPTREND（需从第 3 个 UP 起数满 3 天） |
| MSE-09 | `compute_indicators()` 返回值合法 | 70 行合成数据，最后 1 行 ma20/ma60/adx 均非 NaN；ma20 值与手算一致 |
| MSE-10 | `identify_latest()` — `state_changed` 标志正确 | 前日 confirmed=OSCILLATION，今日 identify 后首次变 UPTREND → state_changed=True |

### 8.2 集成测试：MarketStateService（`tests/integration/test_market_state_service.py`）

需要真实 PostgreSQL（含 `index_history` + `market_state_history` 表），使用 `db_session` fixture。
测试前向 `index_history` 插入 000300.SH 的 100 天合成 OHLCV 数据（平稳上涨趋势，保证能触发 UPTREND）。

| ID | 描述 | 验证点 |
|----|------|--------|
| MSTS-01 | `identify_and_save()` 写入 DB | 调用后 `market_state_history` 中存在对应 `trade_date` 行，字段值与计算结果一致 |
| MSTS-02 | `get_current_state()` 返回最新行 | `trade_date` 与最后插入的日期一致 |
| MSTS-03 | 状态切换时 `state_changed=True` | 插入 62 行强趋势合成数据（仅交易日）；Engine 内部 debounce 恰好在第 3 个有效行触发切换，`state_changed=True` 完全由 Engine 产生，DB 无须预置历史记录（**CR-02**：删除 Service 层覆写逻辑后同步重构） |
| MSTS-04 | `identify_and_save()` 幂等 | 同一 `trade_date` 调用两次不报错，DB 中仍只有一行，值取最新计算结果 |
| MSTS-05 | 历史数据不足时返回 `None` | `index_history` 中只有 50 行数据 → 返回 None，DB 无新增行 |

### 8.3 E2E 测试：Market API（`tests/e2e/test_market_api.py`）

使用 ASGI + Mock `MarketStateService`（同 Phase 2 的 `mock_data_service` 模式）。

| ID | 描述 | 验证点 |
|----|------|--------|
| MAPI-01 | `GET /api/v1/market/state`（有 token）→ 200 | `code=0`，`data.current` 含 `market_state` 字段 |
| MAPI-02 | `GET /api/v1/market/state`（无 token）→ 401 | `code=401` |
| MAPI-03 | `GET /api/v1/market/state` 当无历史记录时 | `code=0`，`data.current == null` |
| MAPI-04 | `GET /api/v1/market/state/history?start=2026-01-01&end=2026-01-31` → 200 | `code=0`，`data.items` 为列表，`data.total >= 0` |

---

## 9. 任务计划

共 **10 个任务**，按 TDD 流程执行。

| 任务 | 文件 | 说明 | 前置 |
|------|------|------|------|
| T-01 | `engine/__init__.py`、`engine/market_state.py`（仅 MarketStateEnum + MarketStateRecord dataclass + MarketStateEngine 签名，方法体 `raise NotImplementedError`） | 目录骨架与接口定义 | — |
| T-02 | `tests/unit/test_market_state_engine.py` | MSE-01~10（RED） | T-01 |
| T-03 | `engine/market_state.py`（完整实现） | GREEN → MSE-01~10；使用 `pandas_ta.adx()` 计算 ADX | T-02 |
| T-04 | `data/repository.py`（新增 3 个市场状态方法） | `upsert_market_state`、`get_latest_market_state`、`get_market_state_history` | T-01 |
| T-05 | `schemas/market.py` | `MarketStateItem`、`MarketStateResponse`、`MarketStateHistoryResponse` Pydantic Schema | T-01 |
| T-06 | `tests/integration/test_market_state_service.py` | MSTS-01~05（RED；含 index_history 合成数据 fixture） | T-03, T-04 |
| T-07 | `services/market_state_service.py` | MarketStateService 完整实现（GREEN → MSTS-01~05） | T-06 |
| T-08 | `tests/e2e/test_market_api.py` | MAPI-01~04（RED；Mock MarketStateService） | T-05 |
| T-09 | `api/v1/market.py` + 更新 `api/v1/__init__.py` + `main.py` + `api/deps.py` | 注册路由；lifespan 中将 `MarketStateEngine` 单例存入 `app.state.market_state_engine`；`api/deps.py` 定义 `get_market_state_service`（按请求创建，GREEN → MAPI-01~04） | T-05, T-07, T-08 |
| T-10 | `pipeline/scheduler.py`（在 ingest 后追加 `identify_and_save(trade_date)` 调用） | 调度器集成；在 `_daily_ingest_job` 内通过 `session_factory` 创建独立 session，实例化 `MarketStateService` 并调用（与 Phase 2 `_daily_ingest_job` session 创建模式一致） | T-07 |

---

## 10. 验收标准（DoD）

全部满足后 Phase 3 方可视为完成：

### 10.1 功能验收

- [ ] `uv run pytest tests/ -v` 全部 **90** 个测试通过（Phase 1+2 的 71 个 + Phase 3 新增 19 个，无退化）
- [ ] `GET /api/v1/market/state` 返回正确的当前市场状态（需提前运行历史回填 + `identify_and_save`）
- [ ] `GET /api/v1/market/state/history` 返回有序历史列表

### 10.2 数据质量验收

- [ ] `market_state_history` 表中有 000300.SH 最近若干交易日的记录
- [ ] ADX 计算结果与 `pandas_ta.adx()` 参考值一致（MSE-09 通过）
- [ ] 防抖动机制正确：状态在单日触发时不切换，连续 3 日后切换（MSE-05~08 通过）
- [ ] `state_changed` 标志在首次切换时为 True，维持相同状态时为 False

### 10.3 代码规范验收

- [ ] `uv run ruff check src/ tests/` 无 error
- [ ] `engine/market_state.py` 内无任何 IO 操作（纯函数）
- [ ] 所有 DB 操作通过 `MarketDataRepository` 方法进行，Service/Route 层不直接操作 ORM
- [ ] `MarketStateEngine` 可被 `asyncio.to_thread()` 安全调用（无 async 依赖）

### 10.4 不在本 Phase 范围内（排除项）

- 四大策略评分引擎（Phase 4）
- 综合评分与候选股池更新（Phase 4）
- `DailyPipeline` CP2/CP3 实现（Phase 4/5）
- 布林带宽度计算（SDD §6.3 标注为"辅助"，按需在 Phase 4 补充）
- 多指数配置化（Phase N，通过 `system_config` 覆盖）
- 市场状态驱动的仓位调节系数（Phase 5/6）
- 市场状态变化微信通知（SDD §6.5, §13.2）→ Phase 10

---

## 11. Phase 4 前置说明

### 11.1 策略权重冲突（必须在 Phase 4 实现 Scorer 前确认）

**SDD §7.5** 是策略权重的唯一权威来源。system_design 不含独立的权重数字表，仅在 §8.3 必测场景中以"下跌趋势 10%/5%/15%/70%"的形式引用下表，两者一致。Phase 4 Scorer 实现时直接使用以下权重：

| 市场状态 | 趋势策略 | 动量策略 | 均值回归 | 价值策略 |
|----------|----------|----------|----------|----------|
| **UPTREND** | **40%** | **25%** | **15%** | **20%** |
| **DOWNTREND** | **10%** | **5%** | **15%** | **70%** |
| **OSCILLATION** | **15%** | **15%** | **40%** | **30%** |

> **Phase 4 Scorer 实现时统一使用上表（SDD §7.5）。** 如对任何权重存疑，以 SDD §7.5 为准，无需参考 system_design。

### 11.2 技术债 TD-3 依赖

Phase 4 `ValueStrategy` 的"价值陷阱规避"（ROE 与申万一级行业中位数比较）依赖 TD-3 解决（sw_industry_l1 当前为 Tushare 自有分类占位值）。Phase 4 启动前须先解决 TD-3。
