# Phase 4：因子计算引擎

> **版本：** v1.0
> **所属阶段：** Phase 4 / 10
> **依据文档：** system_design.md §2.1、§2.4、§5.3–5.4、§6；SDD §7、§8.3、附录 B/D
> **日期：** 2026-04-01
> **预期产出：** 基于四大策略的股票评分引擎（趋势/均值回归/动量/价值），含跨截面百分位归一化、市场状态权重动态切换、候选池管理，以及 Phase 2 遗留技术债（TD-1/2/3）修复

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| **v1.0** | 2026-04-01 | 重写：基于 Phase 1–3 整合检查结果，完全对齐 SDD §7/§8.3 和 system_design §5.3–5.4；修正文件命名（universe.py / pool.py / strategy_service.py）；修正 API 路由（扩展 market.py + 新增 watchlist.py）；补全 UniverseFilter 六类过滤条件；明确 candidate_pool schema 无新迁移；完整 WatchlistService 规格 |
| **v1.1** | 2026-04-02 | 专家评审 C-01~C-12 全部修复：CandidatePoolManager 改为纯函数（C-01）；修复 frozen dataclass 循环赋值 bug（C-02/C-03）；修复 asyncio.gather 返回 tuple 类型不匹配（C-04）；补充 SDD §5.4 流动性/涨停封死两条过滤（C-05）；Phase 4 不写 signal_score_snapshot（C-06）；ROE 降级权重改为比例归一化（C-07）；淡出标记逻辑移入 ScoringService（C-08）；pe_pb_history 按 universe 过滤加载（C-09）；补充 TD 回填任务 T-01b/T-02b（C-10）；引入 MarketSnapshot TypedDict（C-11）；holding_codes 类型注解统一（C-12） |

---

## 目录

1. [阶段目标与交付物](#1-阶段目标与交付物)
2. [前置条件](#2-前置条件)
3. [新增项目结构](#3-新增项目结构)
4. [TD-1/2/3 修复规格](#4-td-123-修复规格)
5. [模块规格](#5-模块规格)
   - 5.1 UniverseFilter
   - 5.2 BaseStrategy & StrategyScore
   - 5.3 TrendStrategy
   - 5.4 MeanReversionStrategy
   - 5.5 MomentumStrategy
   - 5.6 ValueStrategy
   - 5.7 Scorer
   - 5.8 CandidatePoolManager
   - 5.9 ScoringService
   - 5.10 WatchlistService
6. [数据库 Schema](#6-数据库-schema)
7. [API 端点规格](#7-api-端点规格)
8. [测试用例](#8-测试用例)
9. [任务计划](#9-任务计划)
10. [验收标准（DoD）](#10-验收标准dod)

---

## 1. 阶段目标与交付物

### 1.1 目标

在 Phase 3 市场状态识别的基础上，实现多策略因子评分引擎：

- **TD-1/2/3 修复**：补齐 ROE/成长性、净资产、申万行业分类数据，为策略计算提供完整数据基础
- **UniverseFilter**：基本面底线过滤（六类场景），生成每日可投资宇宙
- **四大策略引擎**：趋势、均值回归、动量、价值，全部遵循 SDD §7.2 规格
- **Scorer**：三状态权重矩阵（SDD §7.5），输出综合评分
- **CandidatePoolManager**：持仓保护 + 白名单机制（SDD §8.2）
- **REST API**：候选池查询、单股评分历史、黑白名单 CRUD

### 1.2 主要交付物

| 交付物 | 说明 |
|--------|------|
| `data/adapters/tushare.py`（扩展） | TD-1/2/3：新增 `fetch_financial_by_stock()`、`fetch_balance_sheet()`、`fetch_stock_industry()` |
| `engine/universe.py` | `UniverseFilter`（六类底线过滤，黑名单集成） |
| `engine/strategies/base.py` | `BaseStrategy` ABC、`StrategyScore` dataclass |
| `engine/strategies/trend.py` | `TrendStrategy`（MA 排列 + MACD + 价格突破） |
| `engine/strategies/mean_reversion.py` | `MeanReversionStrategy`（RSI + 乖离率 + 布林带） |
| `engine/strategies/momentum.py` | `MomentumStrategy`（3M/6M 涨幅 + 行业相对强度 + 追高剔除） |
| `engine/strategies/value.py` | `ValueStrategy`（PE/PB 历史分位 + ROE + 价值陷阱规避） |
| `engine/scorer.py` | `Scorer`（三状态权重矩阵，SDD §7.5） |
| `engine/pool.py` | `CandidatePoolManager`（持仓保护 + 白名单） |
| `services/strategy_service.py` | `ScoringService`（流程编排，含 asyncio.to_thread 并发） |
| `services/watchlist_service.py` | `WatchlistService`（黑白名单 CRUD） |
| `schemas/scoring.py` | Pydantic schemas（评分响应、候选池、黑白名单） |
| `api/v1/market.py`（扩展） | `/market/pool`、`/market/stock/{ts_code}/score` |
| `api/v1/watchlist.py`（新增） | `/watchlist/` GET/POST/DELETE |
| `api/deps.py`（扩展） | `get_scoring_service()`、`get_watchlist_service()` |
| 全量测试套件 | 约 50 个 Phase 4 新增测试用例 |

---

## 2. 前置条件

- Phase 1–3 全部通过验收（DB schema、行情/财务数据、市场状态识别）
- `candidate_pool`、`user_watchlist`、`signal_score_snapshot` 三张表已在 Phase 1 创建，**Phase 4 无新迁移**
- TD-1/2/3 修复在 Phase 4 开始时同步推进（见 §4），策略集成测试须等对应 TD 修复后运行

> **不在本 Phase 范围内（排除项）：**
> - FactorMonitorEngine（IC/IR 计算）→ Phase 7，届时 Phase 6 持仓收益数据已就绪
> - DailyPipeline CP2/CP3 完整串联 → Phase 7
> - 信号生成（SignalGenerator、PositionSizer）→ Phase 5
> - BacktestEngine → Phase 8

---

## 3. 新增项目结构

```
backend/src/quantpilot/
├── engine/
│   ├── market_state.py           # Phase 3（已有）
│   ├── universe.py               # 【新增】UniverseFilter
│   ├── scorer.py                 # 【新增】Scorer
│   ├── pool.py                   # 【新增】CandidatePoolManager
│   └── strategies/               # 【新增】策略子包
│       ├── __init__.py
│       ├── base.py               # BaseStrategy ABC、StrategyScore
│       ├── trend.py              # TrendStrategy
│       ├── mean_reversion.py     # MeanReversionStrategy
│       ├── momentum.py           # MomentumStrategy
│       └── value.py              # ValueStrategy
├── services/
│   ├── strategy_service.py       # 【新增】ScoringService
│   └── watchlist_service.py      # 【新增】WatchlistService
├── schemas/
│   └── scoring.py                # 【新增】评分/候选池/黑白名单 schemas
├── api/v1/
│   ├── market.py                 # 【扩展】新增 /pool、/stock/{ts_code}/score
│   └── watchlist.py              # 【新增】/watchlist/* 端点
└── api/deps.py                   # 【扩展】新增 get_scoring_service / get_watchlist_service
```

---

## 4. TD-1/2/3 修复规格

### 4.1 TD-1：ROE 及成长性指标（逐股批量查询）

**问题**：`fina_indicator` API 不支持全市场按 period 查询，`roe`/`net_profit_yoy`/`revenue_yoy`/`debt_to_asset` 全为 NULL。

**修复**：在 `TushareAdapter` 中新增 `fetch_financial_by_stock()` 方法，按 ts_code 列表逐股查询。

```python
async def fetch_financial_by_stock(
    self,
    ts_codes: list[str],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """
    逐股调用 fina_indicator API，每批 50 只，批次间 sleep(0.3s)。
    字段：ts_code, ann_date（公告日，PIT 用）, end_date（报告期），
          roe, netprofit_yoy, tr_yoy（营收同比）, debt_to_assets
    映射到标准列：publish_date=ann_date, report_period=end_date,
                  roe, net_profit_yoy=netprofit_yoy,
                  revenue_yoy=tr_yoy, debt_to_asset=debt_to_assets
    """
```

### 4.2 TD-2：净资产（balancesheet API）

**问题**：`total_equity` 在 `fina_indicator` 中不存在，`balancesheet` API 字段为 `total_hldr_eqy_exc_min_int`。

**修复**：新增 `fetch_balance_sheet()` 方法。

```python
async def fetch_balance_sheet(
    self,
    ts_codes: list[str],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """
    调用 Tushare balancesheet API。
    字段：ts_code, ann_date, end_date, total_hldr_eqy_exc_min_int
    映射：publish_date=ann_date, report_period=end_date,
          total_equity=total_hldr_eqy_exc_min_int（单位：元）
    结果合并入 financial_data 表的 total_equity 列。
    """
```

### 4.3 TD-3：申万行业分类

**问题**：`stock_info.sw_industry_l1/l2` 当前为 Tushare 自有分类占位，非申万分类。

**修复**：新增 `fetch_stock_industry()` 方法，调用 `index_classify` + `stock_industry` API。

```python
async def fetch_stock_industry(self) -> pd.DataFrame:
    """
    调用 Tushare stock_industry API（market='E'，src='SW2021'）。
    字段：ts_code, industry_name（一级），l2_name（二级）
    映射：sw_industry_l1=industry_name, sw_industry_l2=l2_name
    写入 stock_info 表，更新所有在库股票的行业分类。
    """
```

**执行时机**：Phase 4 启动时一次性执行历史回填；此后每月末 MonthlyScheduler 触发增量更新（Phase 7 实现调度，Phase 4 只实现方法本身）。

### 4.4 TD-1/2/3 Pipeline 集成缺口（移交 Phase 5 前置任务）

> ⚠️ **Phase 4 的 TD 修复是不完整的**：三个新 Adapter 方法均已实现，但**均未接入 ingestion pipeline**。

**现状**：
- `ingest_history` 和日常 `ingest_daily` 仍调用旧的 `fetch_financial_data`（仅能获取 pe_ttm/pb，roe/total_equity/sw_industry 不会被填充）
- `backfill_td123.py` 是临时手动脚本，不构成可靠的生产方案

**后果**：即使是全新生产部署，在 Phase 5 pipeline 集成完成之前，roe/total_equity/申万行业均无法通过正常 ingestion 流程写入。

**Phase 5 必须完成的 pipeline 集成任务**（Phase 5 开始前的前置条件）：

| 任务 | 描述 | 影响 |
|------|------|------|
| P5-PRE-1 | 扩展 `ingest_history`，加入 `fetch_financial_by_stock` + `fetch_balance_sheet` + `fetch_stock_industry` | 首次部署无需手动回填 |
| P5-PRE-2 | 在调度器中添加季度财务数据任务（每季报季后触发） | 日常运营 roe/total_equity 保持最新 |
| P5-PRE-3 | 退役 `backfill_td123.py`，在其文件头注明"Phase 5 完成后本脚本作废" | 消除手动操作依赖 |

**验收标准**：从空库执行完 `ingest_history` 后，无需任何手动命令，roe/total_equity/sw_industry_l1 即有有效数据。

---

## 5. 模块规格

### 5.1 UniverseFilter（`engine/universe.py`）

Engine 层，纯函数，无 IO。输入来自 ScoringService 预加载的市场快照。

**过滤规则**（全部基于当日数据，SDD §5.4 必测场景）：

| 序号 | 条件 | 数据来源 | 豁免 |
|------|------|----------|------|
| F-1 | 非 ST/\*ST | `is_st=True` | 无 |
| F-2 | 上市满 60 交易日 | `list_date`，用 TradingCalendar 精确计算 | 无 |
| F-3 | 非停牌 | `is_suspended=True` | 无 |
| F-4 | 净资产为正 | `total_equity > 0`（需 TD-2 修复） | 金融股豁免（`sw_industry_l1 in FINANCIAL_INDUSTRIES`） |
| F-5 | 非连续亏损 | `net_profit_yoy` 最近两期非全为负（需 TD-1 修复）<br>**【降级实现】** `get_latest_financial()` 基于 DISTINCT ON 仅返回单期；当前退化为最新一期非负。待 Repository 新增 `get_latest_n_financials(n=2)` 后恢复完整两期检查。 | 金融股豁免 |
| F-6 | 非高杠杆 | `debt_to_asset < 0.9`（需 TD-1 修复） | 金融股豁免 |
| F-7 | 流动性充足 | 20 日均日成交额 ≥ `min_avg_amount`（默认 500 万元，可配置）<br>**【降级实现】** 当前使用当日单日 `amount` 代替 20 日滚动均值。待 Repository 新增 `get_avg_amount()` 后修复。 | 无 |
| F-8 | 非涨停封死 | `limit_up=True` 且当日成交量为 0 时排除（无法买入） | 无 |

> **TD 依赖说明**：F-4/F-5/F-6 在 TD 修复完成前，对应字段为 NULL 时**跳过该条件**（不过滤），并在日志中记录 `universe_filter_skipped_null_field`。F-7/F-8 所需字段（`amount`/`limit_up`/`vol`）来自 `daily_quotes`，Phase 2 已入库，无 TD 依赖。

**黑名单集成**（在 ScoringService 层实现，不在 Engine 层）：过滤后的宇宙再移除用户黑名单股票。

```python
class UniverseFilter:
    FINANCIAL_INDUSTRIES: frozenset[str] = frozenset({
        "银行", "证券", "保险", "多元金融",  # 申万一级行业名称
    })
    MIN_AVG_AMOUNT_DEFAULT: int = 5_000_000   # 500 万元，SDD §14.3 可配置

    def filter(
        self,
        stock_info: pd.DataFrame,    # index=ts_code，含 is_st/list_date/is_suspended/sw_industry_l1
        financials: pd.DataFrame,    # index=ts_code，含 total_equity/net_profit_yoy/debt_to_asset
        daily_quotes: pd.DataFrame,  # index=ts_code，含 amount/vol/limit_up（F-7/F-8 专用）
        today: date,
        calendar: TradingCalendar,
        min_avg_amount: int = MIN_AVG_AMOUNT_DEFAULT,
    ) -> pd.Index:
        """返回通过全部过滤条件的 ts_code 集合（pd.Index）。纯函数，无 IO。"""
```

### 5.2 BaseStrategy & StrategyScore（`engine/strategies/base.py`）

```python
class MarketSnapshot(TypedDict):
    """由 ScoringService 构建，只读传入各策略。使用 TypedDict 提供静态类型安全。"""
    trade_date: date
    adj_prices: pd.DataFrame       # index=ts_code，columns=trade_date，后复权收盘价（近 180 日历天）
    daily_quotes: pd.DataFrame     # index=ts_code，最新一日行情（含 pe_ttm/pb/turnover_rate/amount/vol/limit_up）
    financials: pd.DataFrame       # index=ts_code，最新一期财务数据（PIT）
    pe_pb_history: pd.DataFrame    # index=(ts_code, trade_date)，universe 过滤后近 5 年 pe_ttm/pb（ValueStrategy 专用）
    # index=index_code，columns=trade_date，Wide 格式（与 adj_prices 结构一致）
    index_adj_prices: pd.DataFrame


@dataclass(frozen=True)
class StrategyScore:
    ts_code: str
    raw_factors: dict[str, float]   # 原始因子值（用于数据血缘/归因）
    score: float                    # 0–100，横截面百分位 Rank IC 归一化
    reason: str                     # 可读解释（面向 L1 用户）


class BaseStrategy(ABC):
    name: str           # 策略标识符，如 'trend'
    display_name: str   # 中文名，如 '趋势跟踪'
    weights: dict[str, float]  # 策略内因子权重，须 sum(weights.values()) == 1.0

    @abstractmethod
    def compute_raw_factors(
        self,
        universe: pd.Index,            # 通过 UniverseFilter 的 ts_code 集合
        market_data: MarketSnapshot,   # 只读，禁止修改内部任何 DataFrame
    ) -> pd.DataFrame:
        """
        计算原始因子值。index=ts_code，列=各因子名。
        纯函数，禁止修改 market_data 内任何 DataFrame。
        对无法计算的标的返回 NaN（横截面 rank 时自动排除）。
        """

    def score(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> list[StrategyScore]:
        """
        完整评分流程（由 ScoringService 通过 asyncio.to_thread 并发调用）：
        1. compute_raw_factors() → raw（DataFrame）
        2. 横截面 Rank 百分位归一化：raw.rank(pct=True) * 100，∈[0, 100]
        3. 策略内加权：(normalized * pd.Series(self.weights)).sum(axis=1)
        4. 逐行构建 StrategyScore（含 reason 文本）
        """

    @abstractmethod
    def _build_reason(
        self, ts_code: str, raw_row: pd.Series, final_score: float
    ) -> str: ...
```

### 5.3 TrendStrategy（`engine/strategies/trend.py`）

**因子与权重**（SDD §7.2.1）：

| 因子 | 计算逻辑 | 权重 |
|------|----------|------|
| `ma_alignment` | MA5 > MA10 > MA20 > MA60 满足条件数 / 3（0~1，越高越多头） | 40% |
| `macd_signal` | DIF 与 DEA 的关系：DIF > DEA > 0 = 1.0，DIF > DEA < 0 = 0.5，else = 0.0 | 30% |
| `price_breakout` | close / rolling(20).max()，越接近 1 得分越高（> 0.98 = 高分） | 30% |

**技术指标**：使用 `pandas_ta`，输入为后复权收盘价（`adj_prices`），传入前 `.astype(float)`。

**理由模板**：`"均线{多头/空头}排列（{n}/3 条件满足），MACD {金叉/死叉/中性}，价格{突破/未突破}近期高点。"`

### 5.4 MeanReversionStrategy（`engine/strategies/mean_reversion.py`）

**因子与权重**（SDD §7.2.2）：

| 因子 | 计算逻辑 | 权重 |
|------|----------|------|
| `rsi_oversold` | 14 日 RSI，越低分越高（RSI 越低越超卖） | 35% |
| `price_deviation` | (MA20 - close) / MA20，乖离率越大（偏低）得分越高 | 35% |
| `bb_position` | (close - BB_lower) / (BB_upper - BB_lower) 的反转，越接近下轨得分越高 | 30% |

**理由模板**：`"RSI(14)={rsi:.1f}（{超卖/正常/超买}），偏离MA20={dev:.1f}%，布林带位置={bb_pos:.2f}。"`

### 5.5 MomentumStrategy（`engine/strategies/momentum.py`）

**因子与权重**（SDD §7.2.3）：

| 因子 | 计算逻辑 | 权重 |
|------|----------|------|
| `return_3m` | 近 60 交易日后复权收益率，横截面 rank | 40% |
| `rs_6m` | 近 120 交易日收益率 vs 沪深 300 同期收益率之差，横截面 rank | 35% |
| `industry_rs` | 标的 60 日收益率 vs 所属申万一级行业均值之差（需 TD-3 修复） | 25% |

**追高剔除约束**（SDD §7.2.3，在 `score()` 方法中、返回前施加）：
```python
# 近 1 个月（20 交易日）涨幅排名全市场前 5% 的股票，策略评分强制置 0
# 注意：StrategyScore 是 frozen dataclass，必须用列表推导式重建，不能循环变量重绑定
top5pct_threshold = return_1m.quantile(0.95)
result = [
    StrategyScore(s.ts_code, s.raw_factors, score=0.0, reason="近1月涨幅前5%，追高剔除。")
    if return_1m.get(s.ts_code, 0) >= top5pct_threshold
    else s
    for s in result
]
```

> **TD-3 依赖**：`industry_rs` 因子在 TD-3 修复完成前，`sw_industry_l1` 为占位值时，此因子得分置 50（中性），日志记录 `momentum_industry_rs_placeholder`。

**理由模板**：`"3月涨幅={r3m:.1f}%，相对指数{超额/落后}{r6m_diff:.1f}%，行业相对强度={rs_ind:.1f}%。"`

### 5.6 ValueStrategy（`engine/strategies/value.py`）

**因子与权重**（SDD §7.2.4）：

| 因子 | 计算逻辑 | 权重 |
|------|----------|------|
| `pe_percentile` | 当前 PE(TTM) 在过去 5 年中的百分位，越低估（低百分位）得分越高 | 35% |
| `pb_percentile` | 当前 PB 在过去 5 年中的百分位，越低估（低百分位）得分越高 | 30% |
| `roe_quality` | 最近一期 ROE 的横截面 rank（需 TD-1 修复） | 35% |

**历史分位计算**（从 `pe_pb_history` 中查询）：
```python
# 每只股票：当前 PE 在该股近 5 年 PE 历史中的百分位
# percentile_rank = (历史中小于当前值的数量) / 历史总数量
# 低估 = 低百分位 → 高得分，故取 (1 - percentile_rank) * 100
```

**价值陷阱规避**（SDD §7.2.4，在 `score()` 方法返回前施加）：
```python
# 若该标的 ROE < 所属申万一级行业当日中位数 ROE，最终得分截断至 50
# 注意：StrategyScore 是 frozen dataclass，必须用列表推导式重建，不能循环变量重绑定
industry_median_roe = financials.groupby("sw_industry_l1")["roe"].transform("median")
result = [
    StrategyScore(
        s.ts_code, s.raw_factors,
        score=min(s.score, 50.0),
        reason=s.reason + "（ROE 低于行业中值，得分已限制在50）",
    )
    if financials.loc[s.ts_code, "roe"] < industry_median_roe[s.ts_code]
    else s
    for s in result
]
```

> **TD-1 依赖**：`roe_quality` 因子在 TD-1 修复前为 NULL，按比例归一化将其权重分配给剩余因子（与 Scorer.aggregate() 缺失策略处理方式一致）：
> - pe_percentile：35 + 35 × 35/65 ≈ **53.8%**
> - pb_percentile：30 + 35 × 30/65 ≈ **46.2%**
>
> 价值陷阱规避在 TD-1 修复前跳过，日志记录 `value_roe_placeholder`。

**理由模板**：`"PE历史分位={pe_pct:.0f}%（{低估/合理/高估}），PB历史分位={pb_pct:.0f}%，ROE={roe:.1f}%{可选：（ROE 低于行业中值，得分已限制在50）}。"`

### 5.7 Scorer（`engine/scorer.py`）

纯函数，无 IO。接收四大策略输出，按市场状态权重矩阵计算综合评分。

**权重矩阵**（SDD §7.5 权威，优先于 system_design §8.1）：

| 市场状态 | 趋势 | 动量 | 均值回归 | 价值 |
|----------|------|------|----------|------|
| UPTREND | 40% | 25% | 15% | 20% |
| DOWNTREND | 10% | 5% | 15% | 70% |
| OSCILLATION | 15% | 15% | 40% | 30% |

```python
WEIGHTS: dict[MarketStateEnum, dict[str, float]] = {
    MarketStateEnum.UPTREND:     {"trend": 0.40, "momentum": 0.25, "mean_reversion": 0.15, "value": 0.20},
    MarketStateEnum.DOWNTREND:   {"trend": 0.10, "momentum": 0.05, "mean_reversion": 0.15, "value": 0.70},
    MarketStateEnum.OSCILLATION: {"trend": 0.15, "momentum": 0.15, "mean_reversion": 0.40, "value": 0.30},
}

# DB 列名映射（CandidatePool.reversion_score 对应 mean_reversion 策略）
SCORE_COLUMN_MAP: dict[str, str] = {
    "trend":           "trend_score",
    "momentum":        "momentum_score",
    "mean_reversion":  "reversion_score",   # DB 列名为 reversion_score
    "value":           "value_score",
}

@dataclass(frozen=True)
class CompositeScore:
    ts_code: str
    composite_score: float          # 0–100
    trend_score: float
    momentum_score: float
    reversion_score: float          # 与 DB 列名一致
    value_score: float
    market_state: MarketStateEnum
    score_breakdown: dict           # {"trend": {"score": x, "weight": 0.40, "contribution": y}, ...}
    explanation: str                # 合并各策略 reason

class Scorer:
    def aggregate(
        self,
        market_state: MarketStateEnum,
        strategy_scores: dict[str, list[StrategyScore]],  # key = strategy.name
    ) -> list[CompositeScore]:
        """
        按市场状态权重加权求和。
        strategy_scores 中缺失的策略（数据不足时）权重重新归一化分配给其余策略。
        """
```

### 5.8 CandidatePoolManager（`engine/pool.py`）

Engine 层，**纯函数，无 IO**（符合 CLAUDE.md §6 架构约束）。所有 DB 读写由 ScoringService 承接。

```python
@dataclass(frozen=True)
class PoolEntry:
    ts_code: str
    composite_score: float | None
    trend_score: float | None
    momentum_score: float | None
    reversion_score: float | None
    value_score: float | None
    market_state: str | None
    in_pool: bool
    is_holding: bool


class CandidatePoolManager:
    """SDD §8.2：持仓保护 + 白名单机制（纯函数，由 ScoringService 注入外部数据）"""

    def __init__(self, pool_capacity: int = 20):
        self.pool_capacity = pool_capacity

    def compute_pool(
        self,
        composite_scores: list[CompositeScore],
        holding_codes: frozenset[str] | set[str],   # ScoringService 注入；Phase 4 为空集
        whitelist_codes: frozenset[str] | set[str],  # ScoringService 从 DB 查询后注入
    ) -> list[PoolEntry]:
        """
        入池规则（SDD §8.2）：
        1. composite_score 排名前 pool_capacity 只
        2. 持仓保护：holding_codes 无论评分高低强制入池，is_holding=True
        3. 白名单：WHITELIST 标的额外入池

        返回 list[PoolEntry]，不执行任何 DB 操作（由 ScoringService 负责 upsert）。
        """
        scores_map = {s.ts_code: s for s in composite_scores}

        sorted_codes = sorted(scores_map, key=lambda c: scores_map[c].composite_score, reverse=True)
        pool_codes: set[str] = set(sorted_codes[:self.pool_capacity])
        pool_codes |= holding_codes
        pool_codes |= whitelist_codes

        return [
            PoolEntry(
                ts_code=ts_code,
                composite_score=scores_map[ts_code].composite_score if ts_code in scores_map else None,
                trend_score=scores_map[ts_code].trend_score if ts_code in scores_map else None,
                momentum_score=scores_map[ts_code].momentum_score if ts_code in scores_map else None,
                reversion_score=scores_map[ts_code].reversion_score if ts_code in scores_map else None,
                value_score=scores_map[ts_code].value_score if ts_code in scores_map else None,
                market_state=scores_map[ts_code].market_state.value if ts_code in scores_map else None,
                in_pool=True,
                is_holding=(ts_code in holding_codes),
            )
            for ts_code in pool_codes
        ]
```

> **Phase 7 注意**：`holding_codes` 在 Phase 4 中来自 ScoringService 传入的空集合（账户系统 Phase 6 后才有真实持仓）。Phase 4 的持仓保护逻辑已完整实现，Phase 6/7 串联时只需传入真实持仓即可。

### 5.9 ScoringService（`services/strategy_service.py`）

Service 层，负责编排 IO 和 Engine 调用。

```python
class ScoringService:
    def __init__(
        self,
        repo: MarketDataRepository,
        universe_filter: UniverseFilter,
        strategies: list[BaseStrategy],  # 四大策略实例
        scorer: Scorer,
        pool_manager: CandidatePoolManager,
        calendar: TradingCalendar,
    ): ...

    async def run_daily_scoring(
        self,
        trade_date: date,
        holding_codes: frozenset[str] | set[str] = frozenset(),  # Phase 6 前为空集
    ) -> list[CompositeScore]:
        """
        完整日度评分流程：
        1. 加载市场快照（_build_market_snapshot()）
        2. UniverseFilter.filter() → universe（pd.Index）
        3. 移除黑名单（DB 查询 list_type='BLACKLIST'）
        4. 获取当日市场状态（直接查 DB market_state_history）
        5. asyncio.gather(*[asyncio.to_thread(s.score, universe, market_data) for s in strategies])
        6. 构建 scores_by_name（gather 返回 tuple，需转换为 dict[str, list[StrategyScore]]）：
               scores_by_name = {s.name: scores for s, scores in zip(self.strategies, raw_scores)}
        7. Scorer.aggregate(market_state, scores_by_name) → composite_scores
        8. 查询白名单（DB list_type='WHITELIST'）+ 查询上一交易日候选池（淡出标记用）
        9. pool_entries = pool_manager.compute_pool(composite_scores, holding_codes, whitelist_codes)
        10. 批量 upsert pool_entries → candidate_pool（in_pool=True）
        11. 淡出标记：prev_pool_codes - {e.ts_code for e in pool_entries} 的标的 upsert in_pool=False
        12. 返回 composite_scores（供 API 层查询）

        注：signal_score_snapshot 在 Phase 5 Signal 生成后写入；Phase 4 不写此表（signal_id FK 约束）。
        """

    async def _build_market_snapshot(
        self, trade_date: date, universe: pd.Index | None = None
    ) -> MarketSnapshot:
        """
        从 DB 构建 MarketSnapshot：
        - adj_prices：近 180 日历天（≈120 交易日，覆盖 MomentumStrategy 6M 窗口）
        - daily_quotes：当日行情（含 pe_ttm/pb/amount/vol/limit_up）
        - financials：最新财务数据（PIT，publish_date <= trade_date）
        - pe_pb_history：仅加载 universe 内股票的近 5 年 pe_ttm/pb（C-09：按 universe 过滤
          避免全市场 5000 只 × 5 年 ≈ 625 万行全量加载）
        - index_adj_prices：沪深 300 后复权近 180 日历天
        注：所有 NUMERIC 列传入策略前调用 .astype(float)

        V1.0 整改 Batch 2 — B2-3：start_pepb 改用 `trade_date - timedelta(days=365 * N)` 替代
        `date(trade_date.year - N, trade_date.month, trade_date.day)`，避免闰年 2-29 在非闰年
        构造 ValueError 导致 5 年一次评分流水线全失败。
        """
```

### 5.10 WatchlistService（`services/watchlist_service.py`）

```python
class WatchlistService:
    def __init__(self, repo: MarketDataRepository): ...

    async def get_list(
        self,
        list_type: Literal["BLACKLIST", "WHITELIST"] | None = None,
    ) -> list[WatchlistItem]: ...

    async def add(
        self,
        ts_code: str,
        list_type: Literal["BLACKLIST", "WHITELIST"],
        note: str = "",
    ) -> WatchlistItem:
        """ts_code + list_type 唯一约束，重复添加返回已有记录（幂等）"""

    async def remove(
        self,
        ts_code: str,
        list_type: Literal["BLACKLIST", "WHITELIST"],
    ) -> None:
        """不存在时静默成功（幂等）"""
```

---

## 6. 数据库 Schema

**Phase 4 无新迁移**。所有涉及的表在 Phase 1 已创建：

| 表 | 迁移文件 | Phase 4 新增操作 |
|----|----------|-----------------|
| `candidate_pool` | `0001_initial_schema.py` | `upsert_candidate_pool()`、`get_pool_codes(prev_date)`（淡出标记用） |
| `user_watchlist` | `0001_initial_schema.py` | `get_whitelist_codes()`、`add_watchlist()`、`remove_watchlist()` |
| `signal_score_snapshot` | `0001_initial_schema.py` | **Phase 4 不写入**（`signal_id` FK 约束，Signal 在 Phase 5 生成；届时回填） |

**candidate_pool 字段说明**（确认与 ORM 一致）：

| 列 | 类型 | 说明 |
|----|------|------|
| `ts_code` | String(10) | 股票代码 |
| `trade_date` | Date | 交易日 |
| `composite_score` | Numeric(5,2) | 综合得分 0–100 |
| `trend_score` | Numeric(5,2) | 趋势策略得分 |
| `reversion_score` | Numeric(5,2) | 均值回归得分（DB 列名，对应策略 key `mean_reversion`） |
| `momentum_score` | Numeric(5,2) | 动量策略得分 |
| `value_score` | Numeric(5,2) | 价值策略得分 |
| `market_state` | String(20) | 当日市场状态 |
| `in_pool` | Boolean | 是否在候选池中 |
| `is_holding` | Boolean | 是否为持仓保护标的 |

> `rank` 和 `is_watchlist` 为运行时计算字段，**不持久化**：`rank` 由 API 层按 `composite_score` 排序后序号赋值；`is_watchlist` 由 API 层 JOIN `user_watchlist` 派生。

---

## 7. API 端点规格

所有端点需 JWT 认证（`Depends(get_current_user)`）。响应格式遵循统一规范 `{"code": 0, "data": ..., "msg": "ok"}`。

### 7.1 扩展 `api/v1/market.py`

#### GET `/api/v1/market/pool`

候选股池列表（最新一个交易日）。

**请求参数**（Query）：
| 参数 | 类型 | 说明 |
|------|------|------|
| `trade_date` | date（可选） | 默认最新交易日 |
| `in_pool_only` | bool（默认 true） | false 时返回含淡出标的 |
| `sort_by` | str（默认 `composite_score`） | 排序字段 |

**响应 `data`**：
```json
{
  "trade_date": "2026-04-01",
  "market_state": "UPTREND",
  "pool": [
    {
      "rank": 1,
      "ts_code": "000001.SZ",
      "name": "平安银行",
      "composite_score": 85.3,
      "trend_score": 90.1,
      "momentum_score": 88.2,
      "reversion_score": 70.5,
      "value_score": 80.0,
      "is_holding": false,
      "is_watchlist": true
    }
  ],
  "total": 20
}
```

#### GET `/api/v1/market/stock/{ts_code}/score`

单股历史评分走势。

**请求参数**（Query）：
| 参数 | 类型 | 说明 |
|------|------|------|
| `days` | int（默认 30） | 最近 N 个交易日 |

**响应 `data`**：
```json
{
  "ts_code": "000001.SZ",
  "history": [
    {
      "trade_date": "2026-04-01",
      "composite_score": 85.3,
      "trend_score": 90.1,
      "momentum_score": 88.2,
      "reversion_score": 70.5,
      "value_score": 80.0,
      "market_state": "UPTREND"
    }
  ]
}
```

### 7.2 新增 `api/v1/watchlist.py`

#### GET `/api/v1/watchlist`

| 参数 | 类型 | 说明 |
|------|------|------|
| `list_type` | `BLACKLIST` \| `WHITELIST`（可选） | 不传返回全部 |

**响应**：`{"code": 0, "data": [{"ts_code": "...", "list_type": "...", "note": "...", "created_at": "..."}]}`

#### POST `/api/v1/watchlist`

**请求体**：`{"ts_code": "000001.SZ", "list_type": "BLACKLIST", "note": "可选备注"}`

**响应**：新增的记录，幂等（已存在时返回现有记录，code=0）。

#### DELETE `/api/v1/watchlist/{ts_code}`

**请求参数**（Query）：`list_type=BLACKLIST`

**响应**：`{"code": 0, "data": null, "msg": "ok"}`，不存在时同样返回成功（幂等）。

---

## 8. 测试用例

### 8.1 UniverseFilter 单元测试（`tests/unit/test_universe.py`）

| ID | 场景 | 预期 |
|----|------|------|
| URF-01 | ST 股票过滤 | `is_st=True` 的股票不在结果中 |
| URF-02 | 次新股过滤 | 上市不足 60 交易日的股票被排除 |
| URF-03 | 停牌过滤 | `is_suspended=True` 被排除 |
| URF-04 | 净资产为负过滤 | `total_equity <= 0` 被排除；金融股豁免 |
| URF-05 | 连续亏损过滤 | 最近两期 `net_profit_yoy` 均为负被排除；金融股豁免 |
| URF-06 | 高杠杆过滤 | `debt_to_asset >= 0.9` 被排除；金融股豁免 |
| URF-07 | NULL 字段跳过 | TD 修复前，NULL 字段对应条件不过滤 |
| URF-08 | 组合场景 | 同时满足多条排除条件只过滤一次 |
| URF-09 | 流动性过滤 | 20 日均成交额 < 500 万元的股票被排除；高于阈值的正常通过 |
| URF-10 | 涨停封死过滤 | `limit_up=True` 且 `vol=0` 时被排除；有成交量的涨停股正常通过 |

### 8.2 策略单元测试（`tests/unit/test_strategies.py`）

| ID | 场景 | 预期 |
|----|------|------|
| STR-01 | 横截面百分位边界 | 全市场相同因子值 → 所有标的得分 50 |
| STR-02 | 极端离群值 | 离群值不导致其余标的得分异常 |
| STR-03 | 全 NaN | NaN 标的被排除，返回列表长度缩减 |
| STR-04 | weights 权重和 | `sum(strategy.weights.values()) == 1.0` |
| STR-05 | market_data 只读 | `compute_raw_factors` 执行后 market_data 内容不变 |
| TRD-01 | 均线多头排列 | MA5>MA10>MA20>MA60 时 `ma_alignment` 因子接近 1.0 |
| TRD-02 | MACD 金叉 | DIF>DEA>0 时 `macd_signal=1.0` |
| TRD-03 | 价格突破近期高点 | `close == rolling_max_20` 时 `price_breakout` 最高 |
| REV-01 | RSI 超卖 | RSI=20 时得分显著高于 RSI=70 |
| REV-02 | 价格偏离均线 | 价格远低于 MA20 时 `price_deviation` 高分 |
| REV-03 | 布林带下轨 | 处于下轨时 `bb_position` 高分 |
| MOM-01 | 3 月涨幅排名 | 涨幅最高的标的得分最高 |
| MOM-02 | 追高剔除 | 近 1M 涨幅前 5% 的标的 momentum_score=0 |
| MOM-03 | TD-3 未修复时降级 | `sw_industry_l1` 为占位值时 `industry_rs` 置 50，日志有告警 |
| VAL-01 | PE 历史低分位 | 当前 PE 处于历史低位时得分高 |
| VAL-02 | 价值陷阱截断 | `roe < industry_median_roe` 时最终得分 ≤ 50 |
| VAL-03 | TD-1 未修复时降级 | `roe=NULL` 时跳过截断，权重重新归一化 |

### 8.3 Scorer 单元测试（`tests/unit/test_scorer.py`）

| ID | 场景 | 预期 |
|----|------|------|
| SCR-01 | UPTREND 权重 | 趋势策略权重 40% |
| SCR-02 | DOWNTREND 权重 | 价值策略权重 70% |
| SCR-03 | OSCILLATION 权重 | 均值回归权重 40% |
| SCR-04 | 权重归一化 | 缺失策略时其余权重自动重新归一化，总和仍为 1.0 |
| SCR-05 | score_breakdown | 包含各策略 score/weight/contribution 三个字段 |

### 8.4 CandidatePoolManager 单元测试（`tests/unit/test_pool.py`）

| ID | 场景 | 预期 |
|----|------|------|
| POOL-01 | 前 N 入池 | composite_score 最高的 N 只入池 |
| POOL-02 | 持仓保护 | `holding_codes` 中的标的无论评分高低都入池，`is_holding=True` |
| POOL-03 | 白名单入池 | `user_watchlist` 中 WHITELIST 标的额外入池 |
| POOL-04 | 总数可超 N | 持仓保护 + 白名单可使池容量超出 `pool_capacity` |
| POOL-05 | 淡出标的标记 | `compute_pool()` 返回入池集合，ScoringService 对差集（昨日在池∖今日在池）执行 `in_pool=False` upsert |

### 8.5 E2E 测试（`tests/e2e/test_scoring_api.py`）

| ID | 端点 | 场景 | 预期 |
|----|------|------|------|
| SAPI-01 | GET /market/pool | 正常返回 | code=0，包含 rank/is_holding/is_watchlist |
| SAPI-02 | GET /market/pool | `trade_date` 参数指定 | 返回指定日期数据 |
| SAPI-03 | GET /market/stock/{ts_code}/score | 正常返回 | 含 history 数组 |
| SAPI-04 | GET /market/pool | 未认证 | code=401 |
| WAPI-01 | GET /watchlist | 空列表 | code=0，data=[] |
| WAPI-02 | POST /watchlist | 添加黑名单 | code=0，返回新记录 |
| WAPI-03 | POST /watchlist | 重复添加（幂等） | code=0，不报错 |
| WAPI-04 | DELETE /watchlist/{ts_code} | 正常删除 | code=0 |
| WAPI-05 | DELETE /watchlist/{ts_code} | 不存在时 | code=0（幂等） |

### 8.6 集成测试（`tests/integration/test_scoring.py`）

| ID | 场景 | 验证点 |
|----|------|--------|
| INT-04 | 全流程评分 | ScoringService.run_daily_scoring() 写入 candidate_pool |
| INT-05 | 持仓保护集成 | 传入 holding_codes 后 candidate_pool 中 is_holding 正确 |
| INT-06 | 黑名单过滤 | 黑名单股票不出现在候选池 |
| INT-07 | WatchlistService CRUD | add → get → remove 完整流程 |
| INT-08 | 白名单入池 | 白名单股票出现在候选池（即使评分未进前 N） |

### 8.7 V1.0 整改 Batch 2 — B2-3 闰年回归（`tests/unit/test_strategy_service_dates.py`）

| ID | 场景 | 预期 |
|----|------|------|
| LEAP-01 | trade_date=2024-02-29 | start_pepb 计算不抛 ValueError，落在 2019 年 |
| LEAP-02 | trade_date=2028-02-29 | start_pepb 计算不抛 ValueError |
| LEAP-03 | 非 2-29 日期基线 | start_pepb 落在 yr-5，间隔 = 365 × 5 日 |
| LEAP-04 | 旧实现反向断言 | `date(yr-5, 2, 29)` 在 yr-5 非闰年时确实抛 ValueError（防回归测试失效）|

---

## 9. 任务计划

| # | 任务 | 类型 | 依赖 |
|---|------|------|------|
| T-01 | TD-3 修复：`fetch_stock_industry()` 方法实现 | 数据层 | — |
| T-01b | TD-3 回填：对全库现有股票执行 `fetch_stock_industry()` 历史回填 | 数据层 | T-01 |
| T-02 | TD-1 修复：`fetch_financial_by_stock()` 方法实现 | 数据层 | — |
| T-02b | TD-1 回填：对全库现有股票执行 `fetch_financial_by_stock()` 历史财务数据回填 | 数据层 | T-02 |
| T-03 | TD-2 修复：`fetch_balance_sheet()` 方法实现 + 历史回填（一次性脚本） | 数据层 | — |
| T-04 | MarketDataRepository 扩展：candidate_pool / watchlist / `get_pool_codes()` 方法 | 数据层 | — |
| T-05 | UniverseFilter + 单元测试 URF-01~10（RED→GREEN） | Engine | T-04 |
| T-06 | BaseStrategy + StrategyScore + MarketSnapshot TypedDict + 单元测试 STR-01~05 | Engine | — |
| T-07 | TrendStrategy + 单元测试 TRD-01~03 | Engine | T-06 |
| T-08 | MeanReversionStrategy + 单元测试 REV-01~03 | Engine | T-06 |
| T-09 | MomentumStrategy + 单元测试 MOM-01~03 | Engine | T-06 |
| T-10 | ValueStrategy + 单元测试 VAL-01~03 | Engine | T-06 |
| T-11 | Scorer + 单元测试 SCR-01~05 | Engine | T-06~T-10 |
| T-12 | CandidatePoolManager + 单元测试 POOL-01~05 | Engine | T-11 |
| T-13 | WatchlistService + E2E WAPI-01~05 | Service + API | T-04 |
| T-14 | ScoringService（run_daily_scoring）+ schemas/scoring.py | Service | T-05、T-11、T-12 |
| T-15 | market.py 扩展：/market/pool + /market/stock/{ts_code}/score | API | T-14 |
| T-16 | E2E 测试 SAPI-01~04 | E2E | T-15 |
| T-17 | 集成测试 INT-04~08 | Integration | T-13、T-14 |
| T-18 | TD-1/2 集成验证（ValueStrategy + MomentumStrategy 完整路径） | Integration | T-02b、T-03、T-09、T-10 |

**并行原则**：T-01/T-02/T-03/T-04（数据层）可与 T-06~T-10（策略单元）并行推进。T-01b/T-02b（回填）须在对应方法（T-01/T-02）实现后执行。T-18（集成验证）最后运行。

---

## 10. 验收标准（DoD）

- [~] TD-1/2/3 全部修复：三个适配器方法（`fetch_financial_by_stock`、`fetch_balance_sheet`、`fetch_stock_industry`）已实现并通过单元测试；**pipeline 接入（`ingest_history`/`ingest_daily` 调用）推迟至 Phase 5（见 phase5_signals.md §2 P5-PRE-1/2/3）**，生产路径下 `financial_data.roe` 等字段及 `stock_info.sw_industry_l1` 在 Phase 5 前仍为 NULL/占位值
- [ ] `UniverseFilter` 通过 URF-01~10 全部 10 个单元测试，含金融股豁免、NULL 字段处理、流动性和涨停封死过滤
- [ ] 四大策略各自通过对应单元测试（TRD/REV/MOM/VAL），权重矩阵与 SDD §7.5 完全一致
- [ ] `Scorer` 三状态权重矩阵测试 SCR-01~05 全部通过
- [ ] `CandidatePoolManager` 持仓保护 + 白名单逻辑测试 POOL-01~05 全部通过
- [ ] `/market/pool`、`/market/stock/{ts_code}/score`、`/watchlist/*` 共 9 个 E2E 测试全部通过
- [ ] 集成测试 INT-04~08 全部通过（INT-04 验证 candidate_pool 写入，不含 signal_score_snapshot）
- [ ] Engine 层（`engine/`）无任何 IO 调用（grep 验证）
- [ ] `ruff check` 无错误

**Phase 4 明确不包含（移交 Phase 5）：**
- `ingest_history` 扩展（接入 TD-1/2/3 新方法）→ P5-PRE-1
- 季度财务调度任务 → P5-PRE-2
- `backfill_td123.py` 退役 → P5-PRE-3
- [ ] `uv run pytest tests/unit/ tests/e2e/ -v` 全部通过（含 Phase 1–3 既有测试回归）
