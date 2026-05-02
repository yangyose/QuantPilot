# Phase 5 设计文档：信号生成

> **状态：** 完成 + 代码审查通过（D-01~D-10 全部验收 ✓）
> **依据文档：** SDD §9（信号生成系统）、§10.1（仓位控制模型）、§10.2（风险矩阵）、§10.3（止损机制）；system_design §5.9、§9 Phase 5 行
> **日期：** 2026-04-06

---

## 1. 引言

### 1.1 范围

Phase 5 交付以下内容：

| 类型 | 模块 | 位置 |
|------|------|------|
| 前置任务 | P5-PRE-1：DataService 内化历史补录方法（行业/财务/净资产） | `services/data_service.py` |
| 前置任务 | P5-PRE-2：MonthlyScheduler 季度财务调度任务 | `pipeline/monthly_scheduler.py` |
| 前置任务 | P5-PRE-3：退役 `backfill_td123.py` | `scripts/backfill_td123.py`（删除） |
| 前置任务 | P5-PRE-4：恢复 UniverseFilter F-5/F-7 降级实现 | `engine/universe.py` + `data/repository.py` |
| Engine 层 | SignalGenerator（含 RiskParams、TradeSignal） | `engine/signal.py` |
| Engine 层 | PositionSizer（含 PositionConfig） | `engine/position.py` |
| Engine 层 | RiskChecker（含 RiskWarning） | `engine/risk.py` |
| Service 层 | SignalService（CRUD + 过期扫描） | `services/signal_service.py` |
| Repository | signals 相关 CRUD + 均量/财务历史查询 | `data/repository.py` |
| Schema | signals 相关 Pydantic 模型 | `schemas/signals.py` |
| API | signals 组 4 个端点 | `api/v1/signals.py` |

### 1.2 显式推迟的内容

| 模块 | 推迟至 | 原因 |
|------|--------|------|
| AccountService（账户/持仓 CRUD API） | Phase 6 | 账户管理是 Phase 6 独立功能块 |
| DailyPipeline CP3（日度信号自动生成全流程） | Phase 7 | 依赖 Phase 6 的 AccountService |
| LineageService（信号-快照绑定封装） | Phase 7 | Phase 5 在 SignalService 内直接写 SignalScoreSnapshot |
| 凯利仓位法（PositionSizer 第二阶段） | Phase 5 不实现 | SDD 标注 V1.5，超出 V1.0 范围 |
| 移动止损/时间止损 | Phase 5 不实现 | SDD 标注 V1.5 |

### 1.3 Phase 启动核查结论

1. system_design §9 Phase 5 行已列出本 phase 全部模块（P5-PRE-1/2/3 + SignalGenerator/PositionSizer/RiskChecker）。
2. P5-PRE-4（恢复降级实现）来自 Phase 4 设计文档的【降级说明】，此前未在 §9 中独立列出——**归入本 phase 实现，不更新 §9**（属于 Phase 4 技术债清偿，非新功能）。
3. 孤儿端点核查：`GET/POST/PATCH /signals*` 共 4 个端点归属 Phase 5 ✓。
4. `AccountService` 相关端点归属 Phase 6 ✓；`LineageService` 归属 Phase 7 ✓。

---

## 2. 前置任务（P5-PRE）

### P5-PRE-1：DataService 内化历史补录方法

**目标**：将 `backfill_td123.py` 脚本的功能内化到 DataService，退役手动脚本。

新增两个方法：

```python
class DataService:
    async def refresh_industry_classification(self) -> int:
        """重新获取全市场申万行业分类，更新 stock_info.sw_industry_l1/l2。
        调用 adapter.fetch_stock_industry() → upsert_stock_list()。
        幂等，可重复调用。返回更新行数。
        """

    async def refresh_financials_full(
        self,
        ts_codes: list[str] | None = None,
        batch_size: int = 50,
    ) -> dict:
        """按股票逐一补录 ROE/成长性指标和 total_equity（净资产）。
        ts_codes=None → 取全部活跃股票（is_active=True）。
        每批 batch_size 只，批次间 sleep 0.3s（避免 Tushare 速率限制）。
        返回 {success_count, fail_count, failed_codes}。
        """
```

**实现要点**：
- `refresh_financials_full` 对每只股票调用 `adapter.fetch_financial_by_stock(ts_code)` 和 `adapter.fetch_balance_sheet(ts_code)` 后，合并到 `financial_data` 表（upsert）
- 失败的 ts_code 仅记录日志，不中断整批

> **【降级说明】首次部署需手动初始化**：Phase 4 验收标准要求"执行完 `ingest_history` 后无需手动命令即有有效数据"。本方案将补录逻辑拆为独立方法而非嵌入 `ingest_history`，因此**首次部署时须额外执行一次初始化**：
> ```bash
> uv run python -c "import asyncio; from quantpilot.services.data_service import DataService; ..."
> ```
> 或通过 Phase 7 上线后的 `/data/ingest/history` 端点触发。季度调度任务（P5-PRE-2）上线后，后续更新自动维护，无需再次手动操作。如需彻底消除手动依赖，可在未来将两个方法集成进 `ingest_history` 的初次执行路径（数据库为空时自动触发）。

### P5-PRE-2：MonthlyScheduler 季度财务调度任务

在 `pipeline/monthly_scheduler.py` 中添加：

```python
async def run_quarterly_financial_refresh(self, as_of_date: date) -> None:
    """每季末（3月、6月、9月、12月最后一个交易日）执行全量财务补录。
    仅在 as_of_date 所在月份为 3/6/9/12 月时执行，其他月份跳过。
    """
```

调度由 Phase 7 的 APScheduler 统一注册。Phase 5 只实现方法本身（月末由外部调用），不在 scheduler.py 中注册（避免 Phase 5 依赖未实现的 DailyPipeline）。

### P5-PRE-3：退役 backfill_td123.py

删除 `backend/scripts/backfill_td123.py`，替代方案：
- 一次性补录：`uv run python -c "import asyncio; from ..data_service import ...; asyncio.run(svc.refresh_financials_full())"`
- 或通过 Phase 7 的 `/pipeline/trigger` 手动触发

### P5-PRE-4：恢复 UniverseFilter F-5/F-7 降级实现

**Repository 新增方法**（在 `data/repository.py`）：

```python
async def get_avg_amount(
    self,
    ts_codes: list[str],
    trade_date: date,
    window: int = 20,
) -> pd.DataFrame:
    """返回各股票在 trade_date 之前 window 个自然交易日（不含当日）的日均成交额。
    index=ts_code，columns=[avg_amount]（元）。
    利用窗口函数 ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC)
    实现，不依赖 TradingCalendar。
    """

async def get_latest_n_financials(
    self,
    ts_codes: list[str],
    as_of_date: date,
    n: int = 2,
) -> pd.DataFrame:
    """按 PIT 原则返回每只股票最近 n 个报告期的财务数据。
    index=(ts_code, report_period)，columns=FinancialData 各字段。
    ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY report_period DESC) <= n
    """
```

**UniverseFilter 更新**：
- F-5（`_filter_fundamentals`）：改为调用 `get_latest_n_financials(n=2)` 后，过滤掉最近两期 `net_profit` 均为 NaN/≤0 的股票。若不足 2 期数据，降级为单期（保留原行为）。
- F-7（`_filter_liquidity`）：改为调用 `get_avg_amount(window=20)` 得到 20 日均成交额，替代当日成交额。

**注意**：F-5/F-7 恢复后，原有的 `【降级说明】` 注释应改为正常注释。

---

## 3. Engine 层设计（纯函数，无 IO）

### 3.1 数据结构定义

**文件：`engine/signal.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date

@dataclass
class RiskParams:
    """SignalGenerator 参数（均可由 user_config 覆盖）。"""
    buy_threshold: float = 80.0          # 综合评分买入阈值（SDD §9.1）
    sell_threshold: float = 40.0         # 综合评分卖出阈值（SDD §9.2）
    stop_loss_pct: float = 0.08          # 硬止损比例（SDD §10.3）
    add_cost_deviation_pct: float = 0.10 # 加仓条件：价格偏离成本价≤±10%（SDD §10.1）
    min_liquidity_amount: float = 5_000_000.0  # 流动性阈值：20日均成交额≥500万元
    price_low_mult: float = 0.99         # 建议买入价区间下限：close × 0.99
    price_high_mult: float = 1.02        # 建议买入价区间上限：close × 1.02
    stop_loss_from_entry_pct: float = 0.08  # 止损价 = 建议买入价均值 × (1 - 8%)
    signal_strong_threshold: float = 90.0   # STRONG 阈值（SDD §9.1）


@dataclass
class TradeSignal:
    """Engine 层信号（纯函数输出），由 SignalService 映射为 ORM Signal 入库。"""
    ts_code: str
    signal_type: str                   # 'BUY' / 'SELL'
    trade_date: date
    score: float                       # 综合评分 0-100
    suggested_price_low: float | None = None
    suggested_price_high: float | None = None
    stop_loss_price: float | None = None
    suggested_pct: float | None = None  # PositionSizer 填充：建议买入占总资产比例
    signal_strength: str | None = None  # 'STRONG' / 'MODERATE'（仅买入信号）
    liquidity_note: str | None = None
    t1_warning: str = "A股T+1制度：买入当日不可卖出"
    reason: str = ""
    # 数据血缘（SignalService.save 写入 SignalScoreSnapshot 时使用）
    score_breakdown: dict | None = None  # {strategy: {score, weight, contribution}}
    raw_factors: dict | None = None      # {factor_name: value}
```

**命名约定**：Engine 层用 `TradeSignal`（dataclass），ORM 层用 `Signal`（models.business）。Service 层 import 时明确别名：
```python
from quantpilot.engine.signal import TradeSignal
from quantpilot.models.business import Signal as SignalModel
```

---

**文件：`engine/position.py`**

```python
@dataclass
class PositionConfig:
    single_pct: float = 0.10          # 单笔仓位比例（SDD §10.1）
    max_single_stock_pct: float = 0.20 # 单股持仓上限
    max_total_pct: float = 0.80        # 总仓位上限（调节前）
    min_cash_pct: float = 0.20         # 最低现金保留

    # 市场状态调节系数（SDD §10.1）
    uptrend_multiplier: float = 1.00
    oscillation_multiplier: float = 0.75
    downtrend_multiplier: float = 0.50
```

---

**文件：`engine/risk.py`**

```python
@dataclass(frozen=True)
class RiskWarning:
    ts_code: str
    warning_type: str  # 'CONCENTRATION_STOCK' | 'CONCENTRATION_INDUSTRY' | 'DRAWDOWN'
    message: str
    severity: str      # 'WARN'（附加到 signal.reason，不阻断）| 'BLOCK'（移除对应 BUY 信号）
```

---

### 3.2 SignalGenerator（`engine/signal.py`）

```python
class SignalGenerator:
    """纯函数，无 IO。由 DailyPipeline CP3 调用（Phase 7），或测试直接调用。"""

    def generate(
        self,
        composite_scores: pd.DataFrame,
        # index=ts_code，columns 含 composite_score/trend_score/...
        # 由 ScoringService 产出；score_breakdown/raw_factors 为可选列
        current_positions: list[Position],
        market_state: MarketState,
        snapshot_quotes: pd.DataFrame,
        # index=ts_code，必须含：close, is_suspended, limit_up, avg_amount
        # avg_amount 由 get_avg_amount() 预先填充（P5-PRE-4 恢复后）
        trade_date: date,
        # 信号日期，用于构造 TradeSignal.trade_date；设计规格遗漏，实现已补充
        risk_params: RiskParams | None = None,
    ) -> list[TradeSignal]:
        """
        买入信号逻辑（SDD §9.1，全部条件须同时满足）：
        1. composite_score > risk_params.buy_threshold（默认80）
        2. is_suspended=False 且 limit_up=False（非停牌、非涨停）
        3. avg_amount >= min_liquidity_amount（流动性检查）
        4. 当前无持仓，或符合加仓规则（见下）

        加仓规则（SDD §10.1，任一满足）：
        - 持仓浮盈 > 0（pnl_pct > 0）
        - 当前价偏离成本价 ≤ ±10% 且市场状态非下跌趋势

        卖出信号逻辑（SDD §9.2，任一满足）：
        1. 持仓股 composite_score < risk_params.sell_threshold（默认40）
        2. 硬止损：pnl_pct <= -stop_loss_pct（-8%）

        买入价区间：[close × 0.99, close × 1.02]
        止损价：价格区间均值 × (1 - 8%)
        signal_strength：score ≥ 90 → 'STRONG'；80-89 → 'MODERATE'（仅买入信号）
        t1_warning：买入信号必填

        同一标的同日不同时产生买入和卖出信号（SDD §9.5）：
        持仓标的评分处于 [sell_threshold, buy_threshold] 区间时不产生任何信号。
        """
```

---

### 3.3 PositionSizer（`engine/position.py`）

```python
class PositionSizer:
    """纯函数，无 IO。在 SignalGenerator 之后由 DailyPipeline CP3 调用。"""

    def suggest(
        self,
        signals: list[TradeSignal],
        account_total_assets: float,
        account_cash: float,
        current_positions: list[Position],
        market_state: MarketState,
        config: PositionConfig | None = None,
    ) -> list[TradeSignal]:
        """
        为每个 BUY 信号填充 suggested_pct 字段（SDD §10.1 固定比例法）：

        有效总仓位上限 = max_total_pct × market_multiplier：
          UPTREND × 1.00, OSCILLATION × 0.75, DOWNTREND × 0.50

        当前已用仓位 = sum(position.market_value) / total_assets

        可用仓位 = max(0, 有效总仓位上限 - 当前已用仓位 - min_cash_pct)

        单笔仓位 = min(single_pct, 单股剩余额度, 可用仓位)
        若可用仓位 < single_pct × 0.5 → suggested_pct = None（资金不足，不建议买入）

        单股剩余额度：
          单股上限（20%）- 该标的当前持仓占比；已超限则 = 0

        SELL 信号不填充 suggested_pct（保持 None）。
        返回修改后的 signals 列表（frozen dataclass → 列表推导式重建）。
        """
```

---

### 3.4 RiskChecker（`engine/risk.py`）

```python
class RiskChecker:
    """纯函数，无 IO。检查集中度风险和账户回撤风险（SDD §10.2）。"""

    def check(
        self,
        signals: list[TradeSignal],
        current_positions: list[Position],
        account_total_assets: float,
        stock_industry: pd.DataFrame,
        # index=ts_code，columns 含 sw_industry_l1
        max_single_stock_pct: float = 0.20,
        max_industry_pct: float = 0.30,
        account_max_drawdown_pct: float | None = None,
        # Phase 5 无 AccountService，调用方传 None 跳过回撤检查；
        # Phase 7 DailyPipeline CP3 集成时从 Account 对象读取实际最大回撤并传入。
        # V1.0 整改 Batch 2 — B2-1：CP3（services/signal_service.generate_for_date）现已通过
        # AccountService.get_current_drawdown(account_id) 计算账户当前最大回撤并传入此参数，
        # max_drawdown_pct 改由 RiskLimitsConfig.max_drawdown_pct（默认 0.20）注入。
        max_drawdown_pct: float = 0.20,
    ) -> list[RiskWarning]:
        """
        检查买入信号执行后的集中度风险 + 账户回撤风险（SDD §10.2）：

        单股集中度（BLOCK）：执行该信号后该标的占比 > max_single_stock_pct
            → warning_type='CONCENTRATION_STOCK', severity='BLOCK'
        行业集中度（BLOCK）：执行该信号后同行业合计占比 > max_industry_pct
            → warning_type='CONCENTRATION_INDUSTRY', severity='BLOCK'
        账户回撤（WARN）：account_max_drawdown_pct 非 None 且 > max_drawdown_pct
            → ts_code='ACCOUNT', warning_type='DRAWDOWN', severity='WARN'

        集中度检查的是"执行后"状态（预估持仓 + 建议仓位），仅对 BUY 信号执行。
        BLOCK 级告警对应信号由 SignalService.save() 移除（不持久化）。
        WARN 级告警追加到对应 signal.reason 字段。
        返回所有告警列表（可能为空）。
        """
```

---

## 4. Service 层设计

### 4.1 SignalService（`services/signal_service.py`）

```python
class SignalService:
    """信号 CRUD + 过期扫描。不负责生成信号（那是 Engine 层的职责）。"""

    def __init__(self, repo: MarketDataRepository) -> None:
        self._repo = repo

    async def save(
        self,
        signals: list[TradeSignal],
        trade_date: date,
        composite_df: pd.DataFrame | None = None,
        # 若提供，则同时写入 SignalScoreSnapshot（数据血缘最小实现）
        # columns 须含 score_breakdown / raw_factors（JSONB）
        risk_warnings: list[RiskWarning] | None = None,
        # BLOCK 级：对应信号从 signals 中移除，不持久化
        # WARN 级：告警 message 追加到对应信号的 reason 字段
    ) -> int:
        """批量 upsert 信号（ON CONFLICT ts_code, trade_date, signal_type）。
        若提供 risk_warnings：先移除 BLOCK 级告警对应的信号，再将 WARN 级追加到 signal.reason。
        若提供 composite_df，则为每个未被阻断的信号写入 SignalScoreSnapshot。
        返回实际 upsert 行数（BLOCK 信号已移除后的数量）。
        """

    async def get_today_signals(
        self,
        trade_date: date,
        signal_type: str | None = None,
        status: str | None = None,
    ) -> list[SignalModel]:
        """查询指定日期的信号列表，支持按 signal_type / status 过滤。"""

    async def get_signal_history(
        self,
        ts_code: str | None = None,
        signal_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SignalModel]:
        """查询历史信号（分页），支持按 ts_code / signal_type / status 过滤。"""

    async def update_status(
        self,
        signal_id: int,
        new_status: str,
    ) -> SignalModel:
        """更新信号状态（仅允许：NEW→VIEWED，NEW/VIEWED→ACTED）。
        非法转换抛出 ValueError。返回更新后的 Signal。
        """

    async def get_lineage(
        self,
        signal_id: int,
    ) -> tuple[SignalModel, SignalScoreSnapshot | None]:
        """返回信号及其评分快照（若存在）。信号不存在时抛出 404。"""

    async def expire_old_signals(
        self,
        as_of_date: date,
        ttl_days: int = 3,
    ) -> int:
        """将 (NEW / VIEWED) 状态且 trade_date < as_of_date - ttl_days 的信号改为 EXPIRED。
        由 DailyPipeline 在每日数据入库完成后调用（Phase 7 集成）。
        返回过期信号数量。
        Phase 5 独立测试时可直接调用此方法。
        """
```

**状态机约束（SDD §9.4）**：
- 合法转换：NEW→VIEWED、NEW→ACTED、VIEWED→ACTED
- 不可从 EXPIRED/SUPERSEDED 手动转换
- SUPERSEDED 由同一标的更新信号产生时自动设置（在 `save()` 的 upsert 逻辑中：若同一 ts_code 当日已有信号，将旧信号置 SUPERSEDED）

### 4.2 Repository 扩展（`data/repository.py`）

新增以下方法（按功能分组）：

**信号 CRUD**：
```python
async def upsert_signals(self, rows: list[dict]) -> int
    # ON CONFLICT (ts_code, trade_date, signal_type) DO UPDATE

async def get_signals_by_date(
    self, trade_date: date,
    signal_type: str | None = None,
    status: str | None = None,
) -> list[Signal]

async def get_signal_history(
    self, ts_code: str | None = None,
    signal_type: str | None = None,
    status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> list[Signal]

async def update_signal_status(self, signal_id: int, status: str) -> Signal | None

async def get_signal_by_id(self, signal_id: int) -> Signal | None

async def get_signal_snapshot(self, signal_id: int) -> SignalScoreSnapshot | None

async def upsert_signal_snapshots(self, rows: list[dict]) -> int

async def expire_signals_before(self, cutoff_date: date) -> int
    # UPDATE signal SET status='EXPIRED'
    # WHERE status IN ('NEW','VIEWED') AND trade_date < :cutoff_date
```

**持仓/账户基础查询**（Phase 6 AccountService 的底层依赖提前提供）：
```python
async def get_positions_by_account(self, account_id: int) -> list[Position]
async def get_account_by_id(self, account_id: int) -> Account | None
async def get_default_account(self) -> Account | None
    # 返回 id 最小的账户（单账户场景）
```

**P5-PRE-4：均量与财务历史查询**：
```python
async def get_avg_amount(
    self, ts_codes: list[str], trade_date: date, window: int = 20,
) -> pd.DataFrame
    # ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) <= window
    # 返回 index=ts_code，columns=['avg_amount']

async def get_latest_n_financials(
    self, ts_codes: list[str], as_of_date: date, n: int = 2,
) -> pd.DataFrame
    # ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY report_period DESC) <= n
    # WHERE publish_date <= as_of_date（PIT）
    # 返回 index=(ts_code, report_period)
```

---

## 5. Schemas（`schemas/signals.py`）

```python
class SignalResponse(BaseModel):
    id: int
    ts_code: str
    signal_type: str
    trade_date: date
    score: float | None
    suggested_pct: float | None
    suggested_price_low: float | None
    suggested_price_high: float | None
    stop_loss_price: float | None
    signal_strength: str | None
    liquidity_note: str | None
    t1_warning: str | None
    reason: str | None
    status: str
    created_at: datetime | None
    model_config = ConfigDict(from_attributes=True)

class SignalStatusUpdate(BaseModel):
    status: str  # VIEWED / ACTED（仅允许这两个值，API 层校验）

class SignalLineageResponse(BaseModel):
    signal: SignalResponse
    snapshot: SignalSnapshotResponse | None

class SignalSnapshotResponse(BaseModel):
    trade_date: date
    composite_score: float | None
    trend_score: float | None
    reversion_score: float | None
    momentum_score: float | None
    value_score: float | None
    market_state: str | None
    score_breakdown: dict | None
    raw_factors: dict | None
    model_config = ConfigDict(from_attributes=True)
```

---

## 6. API 端点（`api/v1/signals.py`）

前缀：`/api/v1`，全部端点需 JWT 认证。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/signals` | 今日信号列表（可按 trade_date / signal_type / status 过滤） |
| GET | `/signals/history` | 历史信号记录（分页，可按 ts_code / signal_type / status 过滤） |
| PATCH | `/signals/{id}/status` | 更新信号状态（VIEWED / ACTED） |
| GET | `/signals/{id}/lineage` | 信号数据血缘（含评分快照） |

**GET /signals** 参数：
- `trade_date: date | None`（默认今日）
- `signal_type: str | None`（BUY / SELL）
- `status: str | None`（NEW / VIEWED / ACTED / EXPIRED）

**GET /signals/history** 参数：
- `ts_code: str | None`
- `signal_type: str | None`
- `status: str | None`
- `limit: int = 50, offset: int = 0`

**PATCH /signals/{id}/status** 约束：
- 仅接受 `status in {"VIEWED", "ACTED"}`，其他值返回 422
- 非法状态转换返回 400 + 错误说明

---

## 7. 依赖注入（`api/deps.py`）

新增：

```python
def get_signal_service(repo: MarketDataRepository = Depends(get_repo)) -> SignalService:
    return SignalService(repo)
```

---

## 8. TDD 计划

### 8.1 单元测试（`tests/unit/`，无 DB）

#### test_signal_generator.py

| ID | 场景 | 验证点 |
|----|------|--------|
| SGN-01 | 评分>80、非停牌、非涨停、无持仓 → BUY 信号 | signal_type=='BUY', price_low=close×0.99 |
| SGN-02 | 评分≤80 → 无 BUY 信号 | 返回空列表 |
| SGN-03 | 涨停（limit_up=True）→ 无 BUY | 涨停时不建议追入 |
| SGN-04 | 已持仓且盈利（pnl_pct=0.05）→ 生成加仓 BUY | signal_type=='BUY' |
| SGN-05 | 已持仓价格偏离>10% 且下跌趋势 → 不加仓 | 返回空列表 |
| SGN-06 | 持仓股评分<40 → SELL 信号 | signal_type=='SELL' |
| SGN-07 | 持仓浮亏≥8%（pnl_pct=-0.08）→ SELL（硬止损） | 触发原因含 stop_loss |
| SGN-08 | 评分≥90 → STRONG；80-89 → MODERATE | signal_strength 字段 |
| SGN-09 | 流动性不足（avg_amount<500万）→ 无 BUY 信号 | 低成交量股票被过滤 |
| SGN-10 | 同一标的同日评分在[40,80]区间 → 无信号 | 持有区间不产生信号 |

#### test_position_sizer.py

| ID | 场景 | 验证点 |
|----|------|--------|
| PSZ-01 | UPTREND，无持仓，总资产10万 → suggested_pct≈0.10 | 单笔10%满足所有约束 |
| PSZ-02 | DOWNTREND，有效总仓位上限降为40%，当前已用35% → 可用5%，但 < 0.05（单笔一半），suggested_pct=None | 资金不足不建议买入 |
| PSZ-03 | 单股已持仓15%，买入后会达20%上限 → suggested_pct调整为剩余5% | 单股上限约束 |
| PSZ-04 | OSCILLATION，可用仓位充足 → max_total×0.75 计算正确 | 震荡市系数0.75 |

#### test_risk_checker.py

| ID | 场景 | 验证点 |
|----|------|--------|
| RSK-01 | 买入后单股达22%（超过20%上限）→ 单股集中度告警 | warning_type=='CONCENTRATION_STOCK', severity=='BLOCK' |
| RSK-02 | 同行业持仓已28%，买入后达32%（超30%）→ 行业集中度告警 | warning_type=='CONCENTRATION_INDUSTRY', severity=='BLOCK' |
| RSK-03 | account_max_drawdown_pct=0.22（超过20%阈值）→ 回撤告警 | warning_type=='DRAWDOWN', severity=='WARN', ts_code=='ACCOUNT' |
| RSK-04 | 无超标，account_max_drawdown_pct=None → 返回空列表 | len(warnings)==0 |

#### test_universe_restored.py（P5-PRE-4 恢复测试）

| ID | 场景 | 验证点 |
|----|------|--------|
| URF-F5r | 仅一期财务数据 → 降级为单期（不报错） | 单期可用时仍入池 |
| URF-F5r2 | 两期均有盈利 → 通过F-5过滤 | 通过 |
| URF-F7r | avg_amount<500万 → 被F-7过滤 | 低流动性标的移出 |
| URF-F7r2 | avg_amount>=500万 → 通过F-7过滤 | 通过 |

### 8.2 E2E 测试（`tests/e2e/test_signals_api.py`，ASGI 无 DB）

| ID | 端点 | 验证点 |
|----|------|--------|
| SAPI-01 | GET /signals（mock repo 返回空列表） | 200, data=[] |
| SAPI-02 | GET /signals（mock repo 返回2条信号） | 200, data长度=2，字段完整 |
| SAPI-03 | PATCH /signals/1/status，status=VIEWED | 200，状态更新 |
| SAPI-04 | PATCH /signals/1/status，status=INVALID | 422，errors 字段存在 |
| SAPI-05 | GET /signals/1/lineage（mock返回信号+快照） | 200，snapshot字段非空 |
| SAPI-06 | GET /signals/history，带 ts_code 过滤 | 200，结果按过滤条件正确 |

### 8.3 集成测试（`tests/integration/test_signal_service.py`，需要 PostgreSQL）

| ID | 场景 | 验证点 |
|----|------|--------|
| INT-SVC-01 | save() 写入2条信号 → get_today_signals() 返回2条 | 信号正确存入 DB |
| INT-SVC-02 | save() 重复写同一信号（upsert）→ 不报错，仍只有1条 | 幂等性 |
| INT-SVC-03 | expire_old_signals(as_of_date, ttl=3)：trade_date=3日前的NEW→EXPIRED | 过期扫描正确 |
| INT-SVC-04 | get_lineage(signal_id) → 返回信号及其 SignalScoreSnapshot | 血缘关联正确 |
| INT-SVC-05 | update_status(ACTED) → get_by_id 状态更新 | 状态变更持久化 |

---

## 9. 验收标准（DoD）

| # | 标准 |
|---|------|
| D-01 | P5-PRE-1~4 全部完成：DataService 新增两个方法，MonthlyScheduler 新增季度任务，backfill_td123.py 删除，F-5/F-7 恢复 |
| D-02 | Engine 层（signal.py / position.py / risk.py）严格无 IO（无 DB 调用、无文件 IO、无网络调用） |
| D-03 | 单元测试 10+4+4+4=22 个测试用例全部通过（SGN/PSZ/RSK/URF-*r） |
| D-04 | E2E 测试 6 个测试用例全部通过（SAPI-01~06） |
| D-05 | 集成测试 5 个测试用例全部通过（INT-SVC-01~05） |
| D-06 | `uv run ruff check src/ tests/` 输出 0 错误 |
| D-07 | signals API 4 个端点可通过 Swagger UI 手动测试（鉴权通过后返回正确格式） |
| D-08 | SignalService.expire_old_signals() 可独立调用（不依赖 DailyPipeline） |
| D-09 | 全部已有测试（Phase 1-4 的 141 个 unit/e2e 用例）回归通过 |
| D-10 | `tests/smoke/test_api_live.py` 新增 API-28~33 共 6 个冒烟测试全部通过（无鉴权→401、有鉴权→200 含结构断言、422/404 错误路径） |

---

## 附：跨 Phase 依赖说明

```
Phase 5 Engine 层（纯函数）
  └─ 输入：composite_scores（Phase 4 产出）、MarketState（Phase 3 产出）、Position（ORM模型，Phase 1 定义）
  └─ 输出：list[TradeSignal]（Phase 7 DailyPipeline CP3 消费）

Phase 5 SignalService
  └─ CRUD 层，不依赖 AccountService（Phase 6）
  └─ get_positions_by_account / get_default_account 由 Repository 直接提供

Phase 7 DailyPipeline CP3 调用链（Phase 5 组件已就绪）：
  signals = signal_engine.generate(composite, positions, market_state, quotes, risk_params)
  signals = position_engine.suggest(signals, total_assets, cash, positions, market_state)
  risk_warnings = risk_engine.check(signals, positions, total_assets, stock_info)
  # BLOCK 级：对应信号移除；WARN 级：追加到 reason（SDD §10.2）
  await signal_service.save(signals, trade_date, composite_df, risk_warnings=risk_warnings)
  await signal_service.expire_old_signals(trade_date)
  # Phase 7 stub：await notifier.send_with_fallback(signals)（no-op）
```
