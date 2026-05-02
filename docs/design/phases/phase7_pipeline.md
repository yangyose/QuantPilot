# Phase 7 设计文档：DailyPipeline + 因子监控 + 报告

**版本**：v1.1  
**日期**：2026-04-10  
**依据文档**：`QuantPilot_SDD.md` §6.5、§7.4、§12.5、§15.3、§15.6；`system_design.md` §3/§5/§6/§9

---

## 1. 范围与前置条件

### 1.1 Phase 7 交付范围

| 模块 | 文件 | 说明 |
|------|------|------|
| DailyPipeline | `pipeline/daily_pipeline.py` | CP1/CP2/CP3 + 盯市 + 自动分红 |
| 调度器 | `pipeline/scheduler.py` | 扩展：注册完整流水线 + 月末 + 周报 Job |
| MonthlyScheduler | `pipeline/monthly_scheduler.py` | 扩展：因子监控 + 月报生成 |
| FactorMonitorEngine | `engine/factor_monitor.py` | 纯函数：IC/IR/半衰期计算 |
| FactorMonitorService | `services/factor_monitor_service.py` | IC 存储、告警触发 |
| ReportService | `services/report_service.py` | 周报/月报/自定义报告生成 |
| LineageService | `services/lineage_service.py` | V1.0 最小实现：信号-快照绑定 |
| NotificationService | `services/notification_service.py` | **no-op stub**（Phase 10 替换） |
| AccountService 扩展 | `services/account_service.py` | 新增 `mark_to_market()`（Phase 6 推迟项） |
| DataService 扩展 | `services/data_service.py` | 新增 `fetch_dividends()`（Phase 6 推迟项） |
| TushareAdapter 扩展 | `data/adapters/tushare.py` | 新增 `fetch_dividend_data()` |
| daily_portfolio_value 表 | `alembic/versions/` | 新表 + 迁移 |
| Pipeline API | `api/v1/pipeline.py` | GET /pipeline/status、POST /pipeline/trigger |
| FactorQuality API | `api/v1/factor_quality.py` | GET /factor-quality、GET /factor-quality/history |
| Reports API | `api/v1/reports.py` | GET /reports、GET /reports/{id}、POST /reports/generate |
| SignalService 扩展 | `services/signal_service.py` | 新增 `generate_for_date(trade_date)` — Pipeline CP3 调用路径 |
| api/deps.py 扩展 | `api/deps.py` | 新增 `get_factor_monitor_service` / `get_report_service` / `get_lineage_service` |

**接收 Phase 6 推迟项：**
- `AccountService.mark_to_market(trade_date)` ← Phase 6 推迟
- `fetch_dividends()` 自动分红处理（除权日触发 + 批量成本价调整）← Phase 6 推迟

**推迟至 Phase 8：**
- `services/performance_service.py` — PerformanceService（SDD §12）
- `api/v1/performance.py` — /performance/* 4 端点

**推迟至 Phase 10：**
- `notification/base.py`、`notification/wxpusher.py` — 真实 WxPusher 实现

### 1.2 前置条件

- Phase 2：DataService / DataSourceAdapter / TradingCalendar / MarketDataRepository ✓
- Phase 3：MarketStateService ✓
- Phase 4：ScoringService（日度评分）✓
- Phase 5：SignalService（含信号过期扫描）、SignalScoreSnapshot ORM ✓
- Phase 6：AccountService（含 get_all_positions()）、SettingsService ✓
- ORM 模型已存在（Phase 1 建表）：`PipelineRun`、`FactorIcHistory`、`Report`、`Signal`、`SignalScoreSnapshot` ✓
- **新增**：`daily_portfolio_value` 表（本 phase 创建迁移）

---

## 2. 设计决策（解决 §9 五项待定）

### 决策 D7-1：DailyPipeline 盯市步骤

**决策**：mark_to_market 在 CP3 完成后执行，作为 Pipeline 第 4 步（不新增检查点字段）。

```
CP1：数据采集校验   → pipeline_run.cp1_data_ready = True
CP2：全市场评分     → pipeline_run.cp2_scoring_done = True
CP3：信号生成       → pipeline_run.cp3_signals_done = True
Step4：盯市 + 净值快照（best-effort：失败仅记录日志，不回滚整个 pipeline_run）
Step5：自动分红处理（best-effort）
Step6：信号过期扫描（已有，Phase 5 实现）
```

`mark_to_market(trade_date)` 行为：
1. 查询 `daily_quote` 中 `trade_date` 当日 close（精确日期匹配，非 DISTINCT ON）
2. 遍历所有账户的所有持仓，更新 `current_price / market_value / pnl_pct`
3. 更新 `account.total_assets = cash + SUM(market_value)`
4. 向 `daily_portfolio_value` 写入快照（ON CONFLICT DO UPDATE）

### 决策 D7-2：净值曲线快照存储

**决策**：新建 `daily_portfolio_value` 表，在 Step4 写入。

```sql
CREATE TABLE daily_portfolio_value (
    id              BIGSERIAL PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    trade_date      DATE NOT NULL,
    total_value     NUMERIC(15,2) NOT NULL,   -- 总资产
    cash            NUMERIC(15,2) NOT NULL,   -- 现金
    position_value  NUMERIC(15,2) NOT NULL,   -- 持仓市值
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (account_id, trade_date)
);
CREATE INDEX ix_dpv_account_date ON daily_portfolio_value (account_id, trade_date DESC);
```

Phase 8 PerformanceService 通过此表计算净值曲线（P95 ≤ 500ms 要求）。

### 决策 D7-3：周报 APScheduler Job

**决策**：每周六 09:00 Asia/Shanghai 触发，生成上一个自然周（Mon–Fri）的周报。

```python
scheduler.add_job(
    _weekly_report_job,
    trigger=CronTrigger(day_of_week="sat", hour=9, timezone="Asia/Shanghai"),
    id="weekly_report",
    replace_existing=True,
    misfire_grace_time=7200,
)
```

周报数据范围：上周一 00:00 ~ 上周五 23:59（本地时区）。
若本周内无交易日（节假日），生成空周报（content={} 或 summary="本周无交易日"），不跳过。

### 决策 D7-4：CP1 data_snapshot_version 生成算法

**决策**：使用 UTC 时间戳字符串 `"YYYYMMDDTHHMMSSZ"` 格式（16 字符）。

```python
from datetime import datetime, timezone
version = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
# 示例：20260410T093000Z
```

**重跑语义**：若当日 `pipeline_run` 已存在且 `cp1_data_ready=True`，复用 `data_snapshot_version`，跳过 CP1 的数据采集步骤，直接进入 CP2。

### 决策 D7-5：因子半衰期计算

**决策**：用 IC 历史序列的自相关系数估计半衰期。

```python
def calc_half_life(ic_series: list[float]) -> float | None:
    """返回 IC 半衰期（月），数据不足时返回 None（降级）。
    
    最小要求：≥ 6 个月的 IC 数据点。
    计算方法：对 IC 序列拟合一阶自回归 dIC_t = a + b*IC_{t-1} + ε，
    半衰期 = -ln(2) / ln(|b|)。
    若 |b| >= 1（非平稳）→ 返回 None。
    """
```

若历史 IC 记录 < 6 个月，`half_life_days = NULL`，不报错。
对应 SDD §7.4 V1.0 监控并展示（不自动调权）。

---

## 3. 模块设计

### 3.1 DailyPipeline

```python
class DailyPipeline:
    """日度流水线：CP1→CP2→CP3→盯市→自动分红→信号过期扫描。"""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        adapter: DataSourceAdapter,
        validator: DataValidator,
        calendar: TradingCalendar,
        market_state_engine: MarketStateEngine,
    ) -> None: ...

    async def run(self, trade_date: date) -> PipelineRun:
        """运行完整流水线。返回 PipelineRun 记录。"""
        run = await self._get_or_create_run(trade_date)
        if not run.cp1_data_ready:
            await self._cp1_ingest(run, trade_date)
        if not run.cp2_scoring_done:
            await self._cp2_scoring(run, trade_date)
        if not run.cp3_signals_done:
            await self._cp3_signals(run, trade_date)
        await self._step4_mark_to_market(run, trade_date)   # best-effort
        await self._step5_auto_dividends(run, trade_date)   # best-effort
        await self._step6_expire_signals(run, trade_date)   # best-effort
        run.status = "SUCCESS"
        run.finished_at = datetime.now(tz=timezone.utc)
        return run

    async def _cp1_ingest(self, run: PipelineRun, trade_date: date) -> None:
        """CP1：数据采集 + 校验。写 cp1_data_ready + data_snapshot_version。"""

    async def _cp2_scoring(self, run: PipelineRun, trade_date: date) -> None:
        """CP2：全市场评分（ScoringService.run_daily_scoring）。写 cp2_scoring_done。"""

    async def _cp3_signals(self, run: PipelineRun, trade_date: date) -> None:
        """CP3：信号生成（SignalService.generate_for_date）。写 cp3_signals_done。"""

    async def _step4_mark_to_market(self, run: PipelineRun, trade_date: date) -> None:
        """盯市：更新所有账户持仓当日收盘价/市值 + 写 daily_portfolio_value。"""

    async def _step5_auto_dividends(self, run: PipelineRun, trade_date: date) -> None:
        """自动分红处理：从 Tushare 获取当日除权数据，触发 record_dividend。"""

    async def _step6_expire_signals(self, run: PipelineRun, trade_date: date) -> None:
        """信号过期扫描（Phase 5 SignalService.expire_old_signals 委托调用）。"""
```

**CP3 说明**：Phase 7 中 CP3 调用 `SignalService.generate_for_date(trade_date)`。该方法尚未实现（Phase 5 只有手动触发路径），Phase 7 需补充：
- `SignalService.generate_for_date(trade_date)` — 从评分快照生成当日信号列表

【降级说明】若 ScoringService 因数据不足返回空结果，CP2 记录为 done 但 signal_count=0；CP3 正常完成，不报错。

### 3.2 AccountService 扩展（mark_to_market）

```python
async def mark_to_market(self, trade_date: date) -> list[Account]:
    """按指定交易日的 daily_quote.close 更新所有账户的持仓价格和净值快照。
    
    与 sync_account() 区别：
    - sync_account()：用 DISTINCT ON 取各股最新价（任意日期），单账户
    - mark_to_market()：精确匹配 trade_date，所有账户，写 daily_portfolio_value
    
    返回已更新的 Account 列表。
    """
    positions = await self.get_all_positions()  # 所有账户所有持仓
    ts_codes = list({p.ts_code for p in positions})
    
    # 精确匹配当日价格（不用 DISTINCT ON）
    stmt = select(DailyQuote.ts_code, DailyQuote.close).where(
        DailyQuote.ts_code.in_(ts_codes),
        DailyQuote.trade_date == trade_date,
    )
    ...
    # 写 daily_portfolio_value (ON CONFLICT DO UPDATE)
    ...
```

### 3.3 DataService 扩展（fetch_dividends）

```python
async def fetch_dividends(self, trade_date: date) -> int:
    """从 Tushare 获取 trade_date 除权（ex_date）的股票分红数据。
    
    对每只当日除权且账户中有持仓的股票，调用 AccountService.record_dividend()。
    返回处理的分红笔数。
    
    数据源：TushareAdapter.fetch_dividend_data(trade_date)
    仅处理 ex_date == trade_date 的记录（精确日期匹配）。
    """
```

TushareAdapter 新增：
```python
async def fetch_dividend_data(self, trade_date: date) -> pd.DataFrame:
    """获取指定日期除权登记的分红数据。
    columns: ts_code, ex_date, cash_div（每股现金分红，元）
    API: tushare fina_dividend（fields: ts_code, ex_date, cash_div）
    """
```

### 3.4 FactorMonitorEngine

```python
class FactorMonitorEngine:
    """纯函数，无 IO。月末由 MonthlyScheduler 调用。"""

    def calc_ic(
        self,
        factor_values: pd.Series,      # index=ts_code
        forward_returns: pd.Series,    # index=ts_code，下期 return_window 日收益率
    ) -> float | None:
        """Rank IC（Spearman 秩相关），nan_policy='omit'。
        样本 < 5 时返回 None。
        """

    def calc_ic_ir(
        self,
        ic_series: list[float],
        window: int = 3,               # 滚动月数
    ) -> tuple[float | None, float | None, float | None]:
        """返回 (ic_mean, ic_std, ir)。数据不足时各字段返回 None。"""

    def calc_half_life(self, ic_series: list[float]) -> float | None:
        """一阶自回归估计半衰期（月）。数据 < 6 个点时返回 None。"""

    def detect_alert(
        self,
        ic_mean: float | None,
        ir: float | None,
        half_life_days: float | None,
        recent_ic_signs: list[float],   # 最近 3 个月 IC 值
    ) -> str | None:
        """返回告警类型或 None：
        - 'DECAY'：最近 3 个月 IC 均为负
        - 'INEFFICIENT'：IR < 0.3
        - 'FAST_DECAY'：half_life_days < 5
        优先级：DECAY > FAST_DECAY > INEFFICIENT
        """
```

**【接口演进说明】** 本 Phase 对 `system_design §5.5` 的 `FactorMonitorEngine` 接口进行细化重构：

| 变更 | system_design §5.5（原） | Phase 7 实现（本文档） |
|------|--------------------------|------------------------|
| `calc_ic` 返回类型 | `-> float` | `-> float \| None`（样本 < 5 返回 None） |
| `calc_ic_batch` | 批量方法 `(calc_month, return_window)` | **拆除**，改由 `FactorMonitorService.run_monthly` 编排 |
| `calc_ic_ir` | 不存在 | 新增独立纯函数，返回 `(ic_mean, ic_std, ir)` |
| `calc_half_life` | 内含于 `calc_ic_batch` | 新增独立纯函数，支持单独调用 |
| `detect_alert` | `(record: FactorIcRecord)` | `(ic_mean, ir, half_life_days, recent_ic_signs: list[float])` — 原签名无法实现 DECAY 连续 3 月判断，本 Phase 修正 |

`system_design §5.5` 须在本 Phase 实现完成后同步更新为上述细粒度接口（已在 §7 修订历史中标注）。

### 3.5 FactorMonitorService

```python
class FactorMonitorService:
    def __init__(self, session: AsyncSession, engine: FactorMonitorEngine) -> None: ...

    async def run_monthly(self, calc_month: date, return_window: int = 20) -> int:
        """计算并存储当月所有策略/因子 IC/IR/半衰期。返回写入行数。
        
        数据流：
        1. 从 candidate_pool 取 calc_month 当日五列策略评分（作为因子值）
           【降级说明】数据源为 candidate_pool（in_pool 全量），非 signal_score_snapshot.raw_factors
           单因子值（后者仅覆盖生成信号的少量股票，覆盖面不足）。
        2. 从 daily_quote 取下期 return_window 日收益率
        3. 逐（strategy, factor）对调用 FactorMonitorEngine.calc_ic()
        4. 取历史 IC 序列计算 IC_mean/IC_std/IR/half_life
        5. upsert factor_ic_history（ON CONFLICT DO UPDATE）
        6. 触发告警通知（NotificationService.notify_factor_alert，no-op stub）
        """

    async def get_latest(self, strategy_name: str | None = None) -> list[FactorIcHistory]:
        """取每个（strategy, factor）最新一条记录。"""

    async def get_history(
        self,
        strategy_name: str | None = None,
        factor_name: str | None = None,
        limit: int = 12,
    ) -> tuple[list[FactorIcHistory], int]:
        """取历史 IC 趋势（按 calc_month DESC）。返回 (records, total_count)。"""
```

### 3.6 ReportService

```python
class ReportService:
    def __init__(self, session: AsyncSession) -> None: ...

    async def generate_weekly(self, week_end: date) -> Report:
        """生成周报 content（JSON）：
        {
          "period": {"start": "...", "end": "..."},
          "trade_summary": {交易笔数, 买入笔数, 卖出笔数},
          "pnl_delta": 周内盈亏变化（daily_portfolio_value 差值）,
          "new_signals": 本周新生成信号数,
          "top_gainers": [...],  # 本周最佳持仓
        }
        若本周无交易日 → content={"summary": "本周无交易日"}，正常写入。
        """

    async def generate_monthly(self, month_end: date) -> Report:
        """生成月报 content（JSON）：
        {
          "period": {...},
          "monthly_return": ...,
          "trade_count": ...,
          "factor_alerts": [...],  # 当月因子告警列表
          "top_holdings": [...],
        }
        V1.0 不含图表数据（前端 Phase 9 自行渲染），仅输出结构化 JSON。
        """

    async def generate_custom(self, start: date, end: date) -> Report:
        """用户触发的自定义时间段报告。"""

    async def get_list(
        self,
        report_type: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Report], int]:
        """查询历史报告列表（分页）。"""

    async def get_by_id(self, report_id: int) -> Report | None:
        """按 ID 获取报告详情。"""
```

### 3.7 LineageService（V1.0 最小实现）

```python
class LineageService:
    """V1.0：信号与当日评分快照绑定。V1.5 实现完整因子级溯源（SDD §15.6）。"""

    def __init__(self, session: AsyncSession) -> None: ...

    async def get_signal_lineage(self, signal_id: int) -> dict | None:
        """返回信号的数据血缘摘要：
        {
          "signal_id": ...,
          "trade_date": ...,
          "score_snapshot": {ts_code, composite_score, market_state, score_breakdown},
          "pipeline_run": {trade_date, cp1_at, cp2_at, cp3_at, data_snapshot_version},
        }
        signal_id 不存在 → None。
        """
```

**注意**：Phase 5 已在 `/signals/{id}/lineage` 端点中实现了基础血缘查询（从 SignalScoreSnapshot 读取），`LineageService` 在 Phase 7 中作为服务层封装，将现有逻辑迁移进来。Phase 5 的 `signals.py` 路由直接调用 repo，Phase 7 重构为调用 `LineageService`。

### 3.8 NotificationService（no-op stub）

```python
class NotificationService:
    """Phase 7: no-op stub。Phase 10 替换为真实 WxPusher 实现。
    
    【降级说明】当前所有 notify_* 方法均为空操作，仅记录日志。
    恢复条件：Phase 10 实现 WxPusher 后替换此类。
    """

    async def notify_factor_alert(self, alert_type: str, strategy: str, factor: str) -> None:
        logger.info("notify_factor_alert(no-op): %s %s.%s", alert_type, strategy, factor)

    async def notify_market_state_change(self, old_state: str, new_state: str) -> None:
        logger.info("notify_market_state_change(no-op): %s→%s", old_state, new_state)
```

**【接入策略】`notify_market_state_change` 在 Phase 7 不接入调用链（选项 B）**：  
Phase 7 仅定义接口存根，不修改 `MarketStateService`。  
Phase 10 实现真实 WxPusher 后，统一在 `MarketStateService` 注入 `NotificationService`，调用 `notify_market_state_change`。  
`notify_factor_alert` 由 `FactorMonitorService.run_monthly` 调用（已在 §3.5 中描述）。

### 3.9 SignalService 扩展（generate_for_date）

```python
async def generate_for_date(self, trade_date: date) -> list[SignalModel]:
    """从 candidate_pool 评分快照生成当日信号列表（Pipeline CP3 调用路径）。

    与手动触发路径（POST /signals/generate → 实时评分）的区别：
    CP2 已完成评分并将结果写入 candidate_pool；本方法直接读取快照，避免重复计算。

    数据流：
    1. 读取 candidate_pool WHERE trade_date=trade_date AND in_pool=True，按 composite_score DESC
    2. 候选池为空 → 返回 []，不报错（写 cp3_signals_done=True，signal_count=0）
    3. 将每条 CandidatePool 记录转换为 TradeSignal（BUY，score=composite_score）
    4. 调用 self.save(signals, trade_date, composite_df) 持久化 Signal + SignalScoreSnapshot
    5. 返回已写入 DB 的 Signal ORM 列表

    【已升级】Phase 10 §7.1 完整化：generate_for_date 在 ConfigService + AccountService 注入后
    完整调用 SignalGenerator → PositionSizer → RiskChecker，支持持仓/行业集中度风控。

    V1.0 整改 Batch 2 — B2-1：CP3 中调 RiskChecker 时补传 account_max_drawdown_pct（来自
    AccountService.get_current_drawdown，基于 daily_portfolio_value 历史净值计算）和
    max_drawdown_pct（来自 RiskLimitsConfig.max_drawdown_pct，新增字段，默认 0.20），
    使账户回撤 WARN 级告警（SDD §10.2）实际可触发（此前漏传 → 默认 None → 检查永不通过）。
    """
```

### 3.10 MonthlyScheduler 扩展

在 Phase 5 已有 `run_quarterly_financial_refresh()` 基础上新增：

```python
async def run_factor_monitoring(self, calc_month: date) -> None:
    """月末执行因子质量监控。"""

async def run_monthly_report(self, month_end: date) -> None:
    """月末生成月报。"""

async def run_all(self, month_end: date) -> None:
    """月末总入口：quarterly_refresh（条件执行）+ factor_monitoring + monthly_report。
    
    非交易日处理：若触发日为非交易日（周末/节假日），调用
    calendar.prev_trade_date(month_end) 取当月最后交易日作为 calc_month，
    确保因子监控数据使用完整当月收益率后再计算。
    """
```

### 3.11 Scheduler 扩展

在现有 `daily_ingest` Job 基础上替换/新增：

```python
def create_scheduler(...) -> AsyncIOScheduler:
    # 原有 daily_ingest → 替换为完整 DailyPipeline job（17:30 Asia/Shanghai）
    scheduler.add_job(_daily_pipeline_job, ...)

    # 新增：月末 Job（每月最后一个自然日 20:00，由 MonthlyScheduler 判断是否为交易日）
    scheduler.add_job(_monthly_job, CronTrigger(day="last", hour=20), ...)

    # 新增：周报 Job（每周六 09:00）
    scheduler.add_job(_weekly_report_job, CronTrigger(day_of_week="sat", hour=9), ...)
```

---

## 4. 数据模型

### 4.1 新建 daily_portfolio_value 表

新建迁移文件 `alembic/versions/0005_daily_portfolio_value.py`（实际序号为 0005，前序迁移仅至 0004）：

```python
def upgrade() -> None:
    op.create_table(
        "daily_portfolio_value",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("total_value", sa.Numeric(15, 2), nullable=False),
        sa.Column("cash", sa.Numeric(15, 2), nullable=False),
        sa.Column("position_value", sa.Numeric(15, 2), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("account_id", "trade_date", name="uq_dpv_account_date"),
    )
    op.create_index("ix_dpv_account_date", "daily_portfolio_value",
                    ["account_id", sa.text("trade_date DESC")])
```

ORM 模型新增至 `models/account.py`：
```python
class DailyPortfolioValue(Base):
    __tablename__ = "daily_portfolio_value"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("account.id", ondelete="CASCADE"))
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    position_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        UniqueConstraint("account_id", "trade_date", name="uq_dpv_account_date"),
        Index("ix_dpv_account_date", "account_id", text("trade_date DESC")),
    )
```

---

## 5. API 端点设计

### 5.1 /pipeline/* （2 端点）

**GET /pipeline/status**

Query params: `trade_date: date | None`（省略则取最新）

Response:
```json
{
  "code": 0,
  "data": {
    "trade_date": "2026-04-10",
    "status": "SUCCESS",
    "started_at": "...",
    "finished_at": "...",
    "signal_count": 5,
    "cp1_data_ready": true,
    "cp1_at": "...",
    "data_snapshot_version": "20260410T093000Z",
    "cp2_scoring_done": true,
    "cp2_at": "...",
    "cp3_signals_done": true,
    "cp3_at": "...",
    "error_msg": null
  },
  "msg": "ok"
}
```

无记录 → `data: null`，HTTP 200。

**POST /pipeline/trigger**

Body: `{"trade_date": "2026-04-10"}`（可选，省略取今日）

Response: 同 GET /pipeline/status 的 PipelineRunItem。

错误：非交易日 → 400 `"非交易日，无法触发流水线"`。

### 5.2 /factor-quality/* （2 端点）

**GET /factor-quality**

Query params: `strategy_name: str | None`

Response:
```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "calc_month": "2026-03-31",
        "strategy_name": "TrendStrategy",
        "factor_name": "adx_value",
        "ic_value": 0.123,
        "ic_mean_3m": 0.085,
        "ic_std_3m": 0.042,
        "ir_3m": 2.02,
        "half_life_days": 12.5,
        "return_window": 20,
        "alert_status": null
      }
    ]
  },
  "msg": "ok"
}
```

> `calc_month` 随每条 item 返回（不同策略/因子可能来自不同月份的计算结果），无顶层聚合字段。

**GET /factor-quality/history**

Query params: `strategy_name: str | None`，`factor_name: str | None`，`limit: int = 12`

Response: `{"data": {"items": [...], "total": N}}`

### 5.3 /reports/* （3 端点）

**GET /reports**

Query params: `report_type: str | None`，`start_date: date | None`，`end_date: date | None`，`limit: int = 20`，`offset: int = 0`

Response: `{"data": {"items": [ReportItem], "total": N}}`

**GET /reports/{report_id}**

Response: `{"data": ReportDetail}`（含完整 content JSON）

不存在 → 404。

**POST /reports/generate**

Body: `{"start_date": "...", "end_date": "..."}`

Response: `{"data": ReportItem}`（生成的报告概览，不含 content 全文）

---

## 6. 测试计划

### 6.1 单元测试 `tests/unit/`

- `test_factor_monitor_engine.py`：IC 计算（正相关/负相关/NaN 处理）、IC_IR 滚动窗口、半衰期计算、告警检测

### 6.2 E2E 测试 `tests/e2e/`

- `test_pipeline_api.py`：GET /pipeline/status 401/200（data null）；POST /pipeline/trigger 401/400（非交易日）/200
- `test_factor_quality_api.py`：GET /factor-quality 401/200；GET /factor-quality/history 401/200
- `test_reports_api.py`：GET /reports 401/200；GET /reports/{id} 401/404；POST /reports/generate 401/422/200

### 6.3 集成测试 `tests/integration/`

- `test_int_daily_pipeline.py`：CP1→CP2→CP3 全流程（mock Tushare）；断点续传（cp1_done=True 跳过 CP1）；mark_to_market 写入 daily_portfolio_value
- `test_int_factor_monitor_service.py`：run_monthly 写入 FactorIcHistory；告警字段正确；get_latest 返回最新
- `test_int_signal_generate_for_date.py`（Phase 10 新增）：CP3 完整链路集成测试
  - **V1.0 整改 Batch 2 — B2-6 新增**：INT-SIG-GEN-01d（账户回撤 25% > 默认阈值 20% → DRAWDOWN WARN 入 InAppNotification；B2-1 闭环验证 CP3 已正确传 max_drawdown_pct）

### 6.4 冒烟测试 `tests/smoke/test_api_live.py`

| 编号 | 端点 | 预期 |
|------|------|------|
| API-48 | GET /pipeline/status | 无鉴权 → 401 |
| API-49 | GET /pipeline/status | 有鉴权 → 200（data null 或含结构） |
| API-50 | POST /pipeline/trigger | 无鉴权 → 401 |
| API-51 | GET /factor-quality | 无鉴权 → 401 |
| API-52 | GET /factor-quality | 有鉴权 → 200（含 items 列表） |
| API-53 | GET /factor-quality/history | 无鉴权 → 401 |
| API-54 | GET /reports | 无鉴权 → 401 |
| API-55 | GET /reports | 有鉴权 → 200（含 items/total） |
| API-56 | GET /reports/999 | 有鉴权 → 404 |
| API-57 | POST /reports/generate | 无鉴权 → 401 |

---

## 7. DoD（验收标准）

| 编号 | 验收项 |
|------|--------|
| D-01 | `engine/factor_monitor.py` 实现完整（IC/IR/半衰期/告警检测，单元测试通过） |
| D-02 | `services/factor_monitor_service.py` 实现完整（run_monthly/get_latest/get_history） |
| D-03 | `services/report_service.py` 实现完整（weekly/monthly/custom/get_list/get_by_id） |
| D-04 | `services/lineage_service.py` V1.0 最小实现（get_signal_lineage） |
| D-05 | `services/notification_service.py` no-op stub（含 `【降级说明】`） |
| D-06 | `AccountService.mark_to_market(trade_date)` 实现；`DailyPortfolioValue` 模型 + 迁移通过 |
| D-07 | `DataService.fetch_dividends()` 实现；`TushareAdapter.fetch_dividend_data()` 实现 |
| D-08 | `pipeline/daily_pipeline.py` 完整实现（CP1→CP2→CP3→Step4→Step5→Step6） |
| D-08a | `SignalService.generate_for_date(trade_date)` 实现（从 candidate_pool 生成信号）；CP3 集成测试验证自动信号生成路径 |
| D-09 | `pipeline/scheduler.py` 注册完整 Pipeline Job + 月末 Job + 周报 Job |
| D-10 | `pipeline/monthly_scheduler.py` 新增 `run_factor_monitoring()` + `run_monthly_report()`（含非交易日回溯逻辑） |
| D-11 | REST API /pipeline/* /factor-quality/* /reports/* 全部实现并注册到 main.py；`api/deps.py` 新增 `get_factor_monitor_service` / `get_report_service` / `get_lineage_service` |
| D-12 | E2E 测试全部通过（/pipeline、/factor-quality、/reports 三组，含 401/200/404/422） |
| D-12a | 重构后，现有 Phase 5 `/signals/{id}/lineage` E2E 测试（`test_signals_api.py` lineage 相关用例）全部通过 |
| D-13 | 集成测试全部通过（DailyPipeline 断点续传；FactorMonitorService 月末写入） |
| D-14 | `tests/smoke/test_api_live.py` 新增 API-48~57 冒烟测试 |
| D-15 | `uv run ruff check src/ tests/` 输出 0 error |

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-10 | 初稿，Phase 7 启动核查完成，5 项设计待定全部决策 |
| v1.1 | 2026-04-12 | 设计评审修复：补充 SignalService.generate_for_date 设计规格（§1.1/§3.9/D-08a）；§3.4 接口演进说明（system_design §5.5 待同步）；§3.8 明确 notify_market_state_change Phase 7 不接入；§5.2 calc_month 下移至 item；§4.1 ORM 补 Index；§3.10 非交易日回溯逻辑；§1.1/D-11 补 deps.py；D-12a 补回归测试 |
| v1.2 | 2026-04-13 | 代码评审修复（C-01~C-07）及文档同步：§3.1 移除 scoring_service_factory（实现未使用此参数）；§3.5 data source 更正为 candidate_pool（含降级说明，覆盖面优于 signal_score_snapshot）；§3.5 get_history 返回值更正为 tuple[list, int]（含 total_count）；§4.1 迁移文件序号更正 0009→0005（实际前序迁移仅至 0004）；D-13 集成测试全部实现（INT-DP-01~03 + INT-FM-01~05，316 tests passed） |
