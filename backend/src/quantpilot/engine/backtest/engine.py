"""BacktestEngine：回测主引擎（Phase 8，SDD §7.7）。Engine 层纯函数，无 IO。"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from quantpilot.engine.backtest.report import DISCLAIMER, BacktestReport
from quantpilot.engine.forecast_override import apply_forecast_roe_override
from quantpilot.engine.market_state import MarketStateEnum

logger = logging.getLogger(__name__)

# SDD-EXT-02s（V1.5-A A2）：无量一字板换手率阈值。turnover_rate 入库为小数
# （TushareAdapter.fetch_daily_quotes `df["turnover_rate"] /= 100`），故 0.01 = 1%。
# 收盘涨停且换手率 < 该值 → 判定 BUY 不可成交（无量一字板特征）。
_LIMIT_UP_ILLIQUID_TURNOVER = 0.01


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """回测参数（SDD §7.7, §10.5）。"""
    start_date: date
    end_date: date
    initial_capital: float
    strategy_config: dict
    account_config: dict
    commission_rate: float = 0.00025   # 双向佣金 0.025%（SDD §10.5）
    stamp_tax_rate: float = 0.0005     # 印花税 0.05%（仅卖出，SDD §10.5）
    slippage_rate: float = 0.001       # 滑点估算 0.1%（SDD §10.5）
    # V1.5-A A1b（SDD §16 滑点敏感性）：多滑点情景对比。非 None 时由
    # BacktestService.run_slippage_comparison 复用同一 bundle 串行跑各档，产出对比报告；
    # engine.run 本身不消费此字段（仍用单一 slippage_rate），是编排层声明字段。
    slippage_scenarios: list[float] | None = None
    # V1.0 整改 Batch 3 — B3-2：T+1 撮合规则
    # "OPEN_T1"（默认）= T 日生成信号、T+1 日开盘价撮合（A 股实际规则）
    # "CLOSE_T" = 当日 close 撮合（保留兼容旧测试，违反 T+1，仅供回归对比）
    execution_price: str = "OPEN_T1"


@dataclass
class BacktestDataBundle:
    """
    由 BacktestService 预加载的全量历史数据。
    BacktestEngine 严格无 IO（CLAUDE.md §6），通过此结构接收数据。

    V1.0 整改 Batch 3 — B3-1/3/5/6/7/8 扩展：
    - daily_quotes：含 close/open/limit_up/limit_down/is_suspended/is_st/avg_amount 全字段
    - stock_info 新增 delist_date 列（B3-6）
    - is_st/is_suspended 改为 PIT 时点切片（B3-5，从 daily_quote 取最近一日）
    - financials 含 publish_date PIT 字段，UniverseFilter 在主循环按 trade_date 切片（B3-7）
    - pe_pb_history 真实加载（B3-3，ValueStrategy 真实分位数）
    - index_history 含 HS300 OHLC（B3-3，Momentum 相对强度真实可计算）
    - daily_quotes 加载时走 DataValidator（B3-8，无效行打标剔除）
    """
    adj_prices: pd.DataFrame       # index=trade_date, columns=ts_code（后复权价格）
    stock_info: pd.DataFrame       # index=ts_code，含 list_date/delist_date/sw_industry_l1
    financials: pd.DataFrame       # 扁平、已按 (ts_code, publish) 排序（`_prepare_financials`
                                   # 的产物；旧形态 MultiIndex(ts_code, report_period) 仍接受）
    hs300_history: pd.DataFrame    # HS300 OHLCV 历史（index=trade_date 或含 trade_date 列）
    # B3-1：完整字段日线，index=(trade_date, ts_code)
    daily_quotes: pd.DataFrame = field(default_factory=pd.DataFrame)
    # B3-3：(ts_code, publish_date) 历史 PE/PB（ValueStrategy 真实分位数）
    # ⚠️ 2026-09-16 起 Service **不再填它**（留空 DataFrame）：6 交易日回测把 ~150 万行
    # 拉进内存是峰值 3530 MB 的主项。分位改在 PostgreSQL 内算好、按日放进下面两个 dict
    # （与生产 `_build_market_snapshot` 同一条路）。字段保留是为了旧 bundle / 单测兼容：
    # 两个 dict 都空时 ValueStrategy 仍会回落到读它。
    pe_pb_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    # 2026-09-16：trade_date → 当日 `1 - pct_rank` 分位 Series（index=ts_code），
    # 5 年窗口、与生产 `get_pe_pb_percentile_bulk` 同语义；2026-09-21 起由 Service 在内存里
    # 用紧凑数组算（`backtest_service.pe_pb_percentile_in_memory`），逐码与 SQL 相同。
    pe_percentile_by_date: dict[date, pd.Series] = field(default_factory=dict)
    pb_percentile_by_date: dict[date, pd.Series] = field(default_factory=dict)
    # B3-3：HS300 后复权累计价（Momentum.rs_6m 真实计算；index=trade_date）
    index_adj_prices: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    # Phase 14 §14-3：5y 月末 rebalance active_weights 时序，键 (state_str, effective_date)。
    # 值含 weights / weights_source / orthogonalize_order / hysteresis_status；
    # 主循环用 max(effective_date) <= trade_date AND state 做 PIT 前向查找。
    # 空 dict → BacktestEngine 走 aggregate_legacy 降级（保留旧 mock 回测兼容）。
    active_weights_history: dict[tuple[str, date], dict] = field(default_factory=dict)
    # SDD-EXT-03（V1.5-A A5b 回测路径）：业绩预告/快报全量行，扁平 DataFrame（列
    # ts_code/report_period/pre_announce_date/est_net_profit/data_priority）。主循环
    # _get_forecast_at 按 trade_date 做 PIT 内存切片（Engine 无 IO），真空期用于前瞻 ROE
    # 覆盖（与生产 ScoringService._build_market_snapshot 对称）。空 → 不覆盖，退化为原 roe。
    forecast: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass
class BacktestResult:
    """回测结果。"""
    daily_nav: pd.Series            # index=trade_date, values=净值（初始=1.0）
    daily_positions: pd.DataFrame   # 每日持仓明细（不持久化，见 phase8_backtest.md §2.1 降级说明）
    signal_history: list[dict]      # 每日交易记录
    performance: dict               # 绩效报告（SDD 附录 C）
    disclaimer: str                 # SDD §7.7.4 局限性声明
    # Phase 14 §14-3：聚合分支统计——'real_5step' / 'legacy_fallback' / 'real_5step_failed' /
    # 'mixed'（多日 + 路径不同时取众数 + 后缀）。供前端展示「本次回测是否走 5 步管线」。
    pipeline_mode: str = "legacy_fallback"


@dataclass
class _VirtualPosition:
    """回测中的虚拟持仓。"""
    ts_code: str
    shares: int
    cost_price: float               # WAC 成本价
    open_date: date
    pnl_pct: float = 0.0
    market_value: float = 0.0


# ---------------------------------------------------------------------------
# 交易成本纯函数（INV-BT-01~03 测试目标）
# ---------------------------------------------------------------------------

def _buy_cost_per_unit(price: float, config: BacktestConfig) -> float:
    """BUY 每股实际成本 = price × (1 + commission + slippage)。"""
    return price * (1 + config.commission_rate + config.slippage_rate)


def _sell_proceeds_per_unit(price: float, config: BacktestConfig) -> float:
    """SELL 每股净收入 = price × (1 - commission - stamp_tax - slippage)。"""
    return price * (1 - config.commission_rate - config.stamp_tax_rate - config.slippage_rate)


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

def _pit_mask(col: pd.Series, trade_date: date) -> pd.Series:
    """`publish_date <= trade_date` 的向量化判定，语义与原逐行 lambda 逐元素相同。

    原实现 `col.apply(lambda d: d is not None and pd.notna(d) and pd.Timestamp(d).date() <= td)`
    在 6 日回测里被调 910 万次、占 24 秒——Engine 主循环里唯一纯 Python 逐行的地方。
    `pd.to_datetime(errors="coerce")` 把 None / NaT / NaN / 非法值统一成 NaT → False；
    `.dt.normalize()` 对齐到日与 `.date()` 比较等价。`test_backtest_forecast_override.py`
    用六种输入逐元素对照原 lambda 钉死。
    """
    ts = pd.to_datetime(col, errors="coerce")
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_localize(None)
    return (ts.dt.normalize() <= pd.Timestamp(trade_date)).fillna(False).astype(bool)


# 与 `data.repository._FUND_LOOKBACK_DAYS` 相等（`test_backtest_latest_financials.py` 钉死）；
# Engine 不 import data 层，故此处复制一份常量。
_FUND_LOOKBACK_DAYS = 450
_PUB_COL = "_pub"  # `_prepare_financials` 加的规范化 publish_date（datetime64，NaT 已剔）
_DAILY_FIELDS = ("publish_date", "pe_ttm", "pb", "dividend_yield")
_FUND_FIELDS = ("roe", "net_profit_yoy", "revenue_yoy", "debt_to_asset", "total_equity")
_FUND_HAVING = ("roe", "net_profit_yoy", "revenue_yoy", "total_equity")


def _prepare_financials(financials: pd.DataFrame) -> pd.DataFrame:
    """把 bundle 里的 financials 整理成**一次排好序**的扁平表，供逐日切片免排序。

    - MultiIndex(ts_code, report_period) → 列
    - `_pub` = `pd.to_datetime(publish_date, errors="coerce").normalize()`，NaT 行剔除
      （SQL `publish_date <= as_of` 对 NULL 恒假，语义相同）
    - 按 (ts_code, _pub, report_period) 升序**稳定**排序

    ⚠️ 为什么必须排序（2026-09-22 发现）：原 `_get_financials_at` 用 `groupby(level=0).last()`
    取「最新一期」，而它取的是**帧内顺序**的末行——bundle 的 SELECT 没有 ORDER BY，堆序
    在 2026-09-08 `repair_financial_lookahead` 改写 318 万行后已被打乱：5434 实测 5554/5665 只
    股票的行序非时间序，`_get_financials_at` 给出的 publish_date 与真正的最新行**不同的占 97%**。
    生产 `get_latest_financial` 是确定性 SQL，不受影响；自 09-08 起的所有回测都受影响。
    已带 `_pub` 列的帧原样返回（幂等）。
    """
    if financials is None or financials.empty:
        return pd.DataFrame()
    if _PUB_COL in financials.columns:
        return financials
    is_mi = isinstance(financials.index, pd.MultiIndex)
    df = financials.reset_index() if is_mi else financials.copy()
    if "publish_date" not in df.columns or "ts_code" not in df.columns:
        return pd.DataFrame()
    pub = pd.to_datetime(df["publish_date"], errors="coerce")
    if getattr(pub.dt, "tz", None) is not None:
        pub = pub.dt.tz_localize(None)
    df[_PUB_COL] = pub.dt.normalize()
    df = df[df[_PUB_COL].notna()]
    keys = ["ts_code", _PUB_COL] + (["report_period"] if "report_period" in df.columns else [])
    return df.sort_values(keys, kind="mergesort").reset_index(drop=True)


def _latest_financials_at(
    financials: pd.DataFrame, trade_date: date, lookback_days: int = _FUND_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """内存里复现生产 `repository.get_latest_financial` 的语义（逐股确定性，与帧顺序无关）。

    - **日频段**：每股 `publish_date <= trade_date` 的最新一行 → publish_date / pe_ttm / pb /
      dividend_yield（有哪列取哪列）。没有任何行的股不出现。
    - **基本面段（LOCF）**：`publish_date ∈ [trade_date - lookback, trade_date]` 的行按
      (ts_code, report_period) 取各字段 max（忽略 NaN），只留 roe / net_profit_yoy /
      revenue_yoy / total_equity 任一有值的期，再取每股**最近报告期** → report_period +
      roe / net_profit_yoy / revenue_yoy / debt_to_asset / total_equity。
    - **total_equity 单独 LOCF**：共用期该字段缺失时，回填「最近**有该字段值**的期」的值；
      共用期有值时不得被旧期盖住；`report_period` 不动（A5b 真空判定依赖它）。
    - 日频左连基本面；基本面超出回看窗口 → 那些列 NaN / report_period NaN。

    `tests/unit/test_backtest_latest_financials.py` 用与 `test_data_repository.py` 03b~03j
    相同的样本钉语义；调用点在 `BacktestEngine._get_financials_at`。
    输入可以是 bundle 原始帧或 `_prepare_financials` 的产物（后者逐日免排序）。
    """
    flat = _prepare_financials(financials)
    if flat.empty:
        return pd.DataFrame()
    td = pd.Timestamp(trade_date)
    pit = flat[flat[_PUB_COL] <= td]
    if pit.empty:
        return pd.DataFrame()
    daily = pit.drop_duplicates("ts_code", keep="last").set_index("ts_code")
    daily_cols = [c for c in _DAILY_FIELDS if c in daily.columns]
    out = daily[daily_cols].copy()

    fund_fields = [c for c in _FUND_FIELDS if c in flat.columns]
    having = [c for c in _FUND_HAVING if c in flat.columns]
    fund_out_cols = ["report_period"] + fund_fields
    src = pit[pit[_PUB_COL] >= td - pd.Timedelta(days=lookback_days)]
    if not fund_fields or "report_period" not in src.columns or src.empty:
        for c in fund_out_cols:
            out[c] = np.nan
        return out
    agg = src.groupby(["ts_code", "report_period"], sort=False)[fund_fields].max()
    if having:
        agg = agg[agg[having].notna().any(axis=1)]
    agg = agg.reset_index().sort_values(["ts_code", "report_period"], kind="mergesort")
    fund = agg.drop_duplicates("ts_code", keep="last").set_index("ts_code")
    if "total_equity" in fund_fields:
        te = (
            agg[agg["total_equity"].notna()]
            .drop_duplicates("ts_code", keep="last")
            .set_index("ts_code")["total_equity"]
        )
        fund["total_equity"] = fund["total_equity"].where(
            fund["total_equity"].notna(), te.reindex(fund.index)
        )
    return out.join(fund[fund_out_cols], how="left")


def _financials_history_at(financials: pd.DataFrame, trade_date: date, n: int = 4) -> pd.DataFrame:
    """内存里复现生产 `repository.get_latest_n_financials(n)` 的语义（L-FID，2026-09-17 拍板 6-A）。

    `publish_date <= trade_date` → 每 (ts_code, report_period) 只留 publish_date 最新一行 →
    按 report_period 倒序每股取前 n。输出 MultiIndex(ts_code, report_period)，供
    `UniverseFilter.filter(financials_history=...)` 做 F-5「最近 2 个有值期皆负才剔」——
    此前引擎不传它，F-5 走「单期为负即剔」降级分支，回测 universe 比生产少约 977 只
    （2026-08-25 实测）。`test_backtest_universe_parity.py` 钉语义与调用点。
    """
    if financials is None or financials.empty or "publish_date" not in financials.columns:
        return pd.DataFrame()
    if _PUB_COL in financials.columns:
        # `_prepare_financials` 的产物：已按 (ts_code↑, publish↑, report_period↑) 排好 →
        # 掩码后先按 (ts_code, report_period) 去重 keep="last"（= 每期 publish 最新一行），
        # 150 万行缩成约 4.5 万行，再按报告期倒序排这一小片。逐日重排 150 万行曾在
        # 30 日回测里占 30 s（2026-09-22 剖析）。
        df = financials[(financials[_PUB_COL] <= pd.Timestamp(trade_date)).to_numpy()]
        if df.empty:
            return pd.DataFrame()
        df = df.drop_duplicates(subset=["ts_code", "report_period"], keep="last")
        df = df.sort_values(["ts_code", "report_period"], ascending=[True, False], kind="mergesort")
    else:
        df = financials[_pit_mask(financials["publish_date"], trade_date).to_numpy()]
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index()
        if "ts_code" not in df.columns or "report_period" not in df.columns:
            return pd.DataFrame()
        df = df.sort_values(
            ["ts_code", "report_period", "publish_date"], ascending=[True, False, False]
        )
    df = df.drop_duplicates(subset=["ts_code", "report_period"], keep="first")
    df = df.groupby("ts_code", sort=False).head(n)
    return df.set_index(["ts_code", "report_period"])


def _avg_amount_before(amount_wide: pd.DataFrame, trade_date: date, window: int = 20) -> pd.Series:
    """复现生产 `repository.get_avg_amount(window)`：`trade_date` **之前**（不含当日）最近
    `window` 个交易日的 `AVG(amount)`（SQL AVG 忽略 NULL；一行都没有 → NaN）。
    `amount_wide`：index=trade_date（DatetimeIndex 或 date），columns=ts_code。
    """
    if amount_wide is None or amount_wide.empty:
        return pd.Series(dtype=float)
    idx = pd.to_datetime(amount_wide.index)
    before = amount_wide[idx < pd.Timestamp(trade_date)]
    if before.empty:
        return pd.Series(np.nan, index=amount_wide.columns, dtype=float)
    return before.tail(window).mean(axis=0, skipna=True)


class BacktestEngine:
    """
    回测主引擎（SDD §7.7.1）。

    核心约束：
    - 严格无 IO（CLAUDE.md §6）；全部历史数据通过 BacktestDataBundle 传入。
    - 必须注入与 DailyPipeline 相同的 strategies/scorer/signal_engine 实例（SDD §7.7.1）。
    - 使用 backward_adjusted 后复权价格（SDD §7.7.3）。
    - 标的池基于历史 PIT 数据（SDD §5.2）。
    """

    def __init__(
        self,
        strategies: list[Any],
        market_state_engine: Any,
        universe_filter: Any,          # UniverseFilter（CLAUDE.md §6 no-IO）
        scorer: Any,
        signal_engine: Any,            # SignalGenerator
        position_engine: Any,          # PositionSizer
        price_provider: Any,           # AdjustedPriceProvider（接口文档兼容保留）
        calendar: Any,                 # TradingCalendar
    ) -> None:
        self._strategies = strategies
        self._market_state_engine = market_state_engine
        self._universe_filter = universe_filter
        self._scorer = scorer
        self._signal_engine = signal_engine
        self._position_engine = position_engine
        self._price_provider = price_provider
        self._calendar = calendar

    def run(
        self,
        config: BacktestConfig,
        data: BacktestDataBundle,
        progress_cb: Callable[[str, int, float], None] | None = None,
        position_sink: Callable[[date, list[dict]], None] | None = None,
    ) -> BacktestResult:
        """
        回测主循环（同步；由 BacktestService 通过 asyncio.to_thread 包装）。

        参数：
          config      — 回测参数
          data        — BacktestService 预加载的历史数据
          progress_cb — 可选进度回调 (trade_date_str, progress_pct, current_nav)，每 100 日触发一次
          position_sink — V1.5-A A1（S6-GAP-02）：可选每日持仓 sink 回调
                          (trade_date, day_snapshots)。非 None 时引擎**不在内存累积**
                          position_snapshots（result.daily_positions 返回空 DataFrame），
                          每交易日把当日持仓明细交给 sink 流式落库（内存 O(batch) 常量）；
                          None 时保留旧行为（累积成 result.daily_positions，兼容既有测试/mock）。
        """
        trade_dates = self._calendar.get_trade_dates(config.start_date, config.end_date)
        if not trade_dates:
            return BacktestResult(
                daily_nav=pd.Series(dtype=float),
                daily_positions=pd.DataFrame(),
                signal_history=[],
                performance=BacktestReport.generate({}, [], config),
                disclaimer=DISCLAIMER,
            )

        # L-FID：成交额宽表（index=trade_date, columns=ts_code）只建一次，逐日切 20 日均量给 F-7
        amount_wide = pd.DataFrame()
        if not data.daily_quotes.empty and "amount" in data.daily_quotes.columns:
            try:
                amount_wide = data.daily_quotes["amount"].unstack("ts_code").sort_index()
            except Exception:
                logger.exception("backtest_amount_wide_failed")

        # 预处理：adj_prices 转 index=trade_date
        adj_prices = data.adj_prices
        if not isinstance(adj_prices.index, pd.DatetimeIndex):
            try:
                has_col = "trade_date" in adj_prices.columns
                adj_prices = adj_prices.set_index("trade_date") if has_col else adj_prices
            except Exception:
                logger.exception("backtest_adj_prices_index_normalize_error")

        virtual_positions: dict[str, _VirtualPosition] = {}
        cash = config.initial_capital
        nav: dict[date, float] = {}
        all_trade_records: list[dict] = []
        position_snapshots: list[dict] = []
        # V1.0 整改 Batch 3 — B3-2：T+1 撮合用待执行队列（T 日生成 → T+1 日撮合）
        pending_signals: list = []
        # Phase 14 §14-3：每日 pipeline_mode 统计，最终聚合到 BacktestResult.pipeline_mode
        pipeline_mode_counter: dict[str, int] = {}

        total = len(trade_dates)
        # 财务表一次整理、排序，逐日只做掩码 + 去重（两个 PIT 函数共用同一份）
        fin_flat = _prepare_financials(data.financials)

        for i, trade_date in enumerate(trade_dates):
            # ---------- 0. T+1 撮合（B3-2）：先执行 T-1 日 pending_signals ----------
            if pending_signals and config.execution_price == "OPEN_T1":
                exec_quotes = self._get_quotes_at(
                    adj_prices, trade_date, ts_codes_filter=None,
                    daily_quotes=data.daily_quotes,
                )
                if not exec_quotes.empty:
                    trade_records, cash, virtual_positions = _execute_signals(
                        pending_signals, virtual_positions, exec_quotes,
                        cash, config, trade_date, use_open_price=True,
                    )
                    all_trade_records.extend(trade_records)
                pending_signals = []

            # ---------- a. PIT 过滤：当日可用股票（B3-6 含 delist_date）----------
            stock_info_t = self._get_stock_info_at(data.stock_info, trade_date)
            if stock_info_t.empty:
                nav[trade_date] = self._calc_nav(
                    virtual_positions, {}, cash, config.initial_capital
                )
                continue

            # ---------- b. PIT 财务数据（B3-7：UniverseFilter F-5 真实启用）----
            financials_t = self._get_financials_at(fin_flat, trade_date)

            # A5b（SDD-EXT-03）：真空期前瞻 ROE 覆盖——快报/预告报告期晚于最近正式财报期时，
            # 用 est_net_profit/total_equity 覆盖 financials.roe（与生产 ScoringService
            # ._build_market_snapshot 同一纯函数 apply_forecast_roe_override，回测/生产对称）。
            if not financials_t.empty:
                forecast_t = self._get_forecast_at(data.forecast, trade_date)
                if not forecast_t.empty:
                    financials_t = apply_forecast_roe_override(financials_t, forecast_t)

            # ---------- c. 当日行情快照（B3-1 含全字段；B3-5 PIT is_st/is_suspended）
            quotes_t = self._get_quotes_at(
                adj_prices, trade_date, ts_codes_filter=stock_info_t.index,
                daily_quotes=data.daily_quotes,
            )
            if quotes_t.empty:
                nav[trade_date] = self._calc_nav(
                    virtual_positions, {}, cash, config.initial_capital
                )
                continue

            # PIT is_st/is_suspended 注入（B3-5 设计意图的缺失实现）：stock_info 是静态基本
            # 信息（_load_data_bundle 只放 list_date/delist_date/sw_industry_l1），而 is_st/
            # is_suspended 随日期变化、只在当日 quotes_t 里有。UniverseFilter F-1/F-3 直接读
            # stock_info["is_st"]/["is_suspended"]，缺列会 KeyError → universe 整日空 → 回测
            # 退化（NAV 恒 1.0）。故在过滤前从当日 quotes_t 时点并入。
            stock_info_t = stock_info_t.copy()
            for _pit_col in ("is_st", "is_suspended"):
                if _pit_col in quotes_t.columns:
                    stock_info_t[_pit_col] = (
                        quotes_t[_pit_col].reindex(stock_info_t.index).fillna(False).astype(bool)
                    )
                elif _pit_col not in stock_info_t.columns:
                    stock_info_t[_pit_col] = False

            # ---------- d. Universe 过滤（L-FID：与生产同口径）----------
            # F-7 用 20 日均成交额（生产 `get_avg_amount(window=20)`），不是单日 amount；
            # F-5 传最近 4 个报告期的 PIT 历史（生产 `get_latest_n_financials(n=4)`），
            # 让「最近 2 个有值期皆负才剔」在回测里同样生效。
            if not amount_wide.empty:
                quotes_t = quotes_t.copy()
                quotes_t["avg_amount"] = _avg_amount_before(
                    amount_wide, trade_date, window=20
                ).reindex(quotes_t.index)
            financials_hist_t = _financials_history_at(fin_flat, trade_date, n=4)
            try:
                universe_idx = self._universe_filter.filter(
                    stock_info_t, financials_t, quotes_t, trade_date, self._calendar,
                    financials_history=(
                        financials_hist_t if not financials_hist_t.empty else None
                    ),
                )
            except TypeError:
                # 签名不匹配是编码错误，不是「当日没数据」——吞成空 universe 会让整段回测
                # 静默变成零信号（2026-09-17 加 financials_history 时替身没跟上，就是这个形态）。
                raise
            except Exception:
                logger.exception("backtest_universe_filter_error date=%s", trade_date)
                universe_idx = stock_info_t.index[:0]

            # 限制 universe 为有当日行情的股票（无价格则无法成交；也避免对无数据股票做无效循环）
            if not quotes_t.empty and len(universe_idx) > 0:
                universe_idx = universe_idx[universe_idx.isin(quotes_t.index)]

            # ---------- e. 市场状态识别 ----------
            # A3：_get_market_state 返回 (enum, breadth_weak)；breadth_weak 由当日
            # daily_quotes 的 NH-NL 市场宽度决定（UPTREND 且 NH-NL≤0）。
            market_state, breadth_weak = self._get_market_state(
                data.hs300_history, trade_date, daily_quotes=data.daily_quotes,
            )

            # ---------- f. 策略评分（B3-3：传入真实 pe_pb_history + index_adj_prices）
            #            Phase 14 §14-3 改造 1：MarketSnapshot 补 industry / market_cap / beta，
            #            供 Scorer.aggregate 5 步管线 Step 2（行业 + 市值中性化）使用。
            strategy_factors: dict[str, pd.DataFrame] = {}
            strategy_scores_dict: dict[str, list] = {}
            market_snap: dict = {}
            if len(universe_idx) > 0:
                td_ts = pd.Timestamp(trade_date)
                adj_hist = adj_prices.loc[:td_ts].T
                # B3-3：pe_pb_history 时点切片（publish_date <= trade_date）
                pe_pb_t = self._slice_pe_pb_history_at(data.pe_pb_history, trade_date)
                # B3-3：index_adj_prices 截至当日（HS300 累计 close）。
                # MomentumStrategy 期望 wide DataFrame（index=index_code, columns=trade_date，
                # 与 ScoringService._build_market_snapshot 的 pivot_table 同构），按
                # `index_prices.columns` 取 rs_6m。data.index_adj_prices 是 Series →
                # 必须转 1 行 wide DataFrame，否则 momentum 读 .columns 抛 AttributeError
                # 被吞 → 整个 momentum 策略被跳过。
                _idx_series = self._slice_index_at(data.index_adj_prices, trade_date)
                if isinstance(_idx_series, pd.Series) and not _idx_series.empty:
                    idx_adj_t = _idx_series.to_frame().T
                    idx_adj_t.index = ["000300.SH"]
                elif isinstance(_idx_series, pd.DataFrame):
                    idx_adj_t = _idx_series
                else:
                    idx_adj_t = pd.DataFrame()

                # §14-3：行业 dict（从 stock_info_t.sw_industry_l1 派生 PIT）
                industry_map: dict[str, str] = {}
                if "sw_industry_l1" in stock_info_t.columns:
                    sw = stock_info_t["sw_industry_l1"].dropna()
                    industry_map = {str(k): str(v) for k, v in sw.items()}

                # §14-3：market_cap Series（从 quotes_t.float_mkt_cap PIT 切片）
                market_cap_series: pd.Series | None = None
                if "float_mkt_cap" in quotes_t.columns:
                    mc = quotes_t["float_mkt_cap"].dropna()
                    if not mc.empty:
                        market_cap_series = mc.astype(float)

                # ValueStrategy 从 daily_quotes 读 pe_ttm/pb，但 daily_quote 表无此列——
                # 它们在 financials（财报 PIT）。与 ScoringService._build_market_snapshot 一致，
                # 把 pe_ttm/pb 从 financials_t 并入 quotes_t（否则 value 的 pe/pb 分位恒 NaN →
                # value 策略整条被跳过）。
                if not financials_t.empty and {"pe_ttm", "pb"}.issubset(financials_t.columns):
                    quotes_t = quotes_t.join(
                        financials_t[["pe_ttm", "pb"]].reindex(quotes_t.index), how="left",
                    )
                # MomentumStrategy 行业相对强度从 financials 读 sw_industry_l1，但 backtest
                # 的 financials 无此列（行业在 stock_info）。与 live 一致，把 sw_industry_l1
                # 从 stock_info_t 并入 financials_t（否则 industry_rs 恒中性回落 50）。
                if not financials_t.empty and "sw_industry_l1" in stock_info_t.columns:
                    financials_t = financials_t.copy()
                    financials_t["sw_industry_l1"] = (
                        stock_info_t["sw_industry_l1"].reindex(financials_t.index)
                    )

                from quantpilot.engine.strategies.base import MarketSnapshot
                market_snap: MarketSnapshot = {
                    "trade_date": trade_date,
                    "adj_prices": adj_hist,
                    "daily_quotes": quotes_t,
                    "financials": financials_t,
                    "pe_pb_history": pe_pb_t,
                    # 2026-09-16：预计算分位（SQL 下推）。键**必须存在**且与生产
                    # `_build_market_snapshot` 同名——ValueStrategy 见到就不读 pe_pb_history。
                    # 没预计算（旧 bundle）→ None → 回落历史路径。
                    "pe_percentile": data.pe_percentile_by_date.get(trade_date),
                    "pb_percentile": data.pb_percentile_by_date.get(trade_date),
                    "index_adj_prices": idx_adj_t,
                    "industry": industry_map,
                    "market_cap": market_cap_series,
                    "beta": None,  # V1.0 永远 None，与 ScoringService._build_market_snapshot 一致
                }

                # §14-3 改造 2：策略循环切 compute_strategy_factors（5 步管线入口）
                #            同时仍保留 s.score 路径以备 legacy_fallback 分支使用
                for s in self._strategies:
                    try:
                        factor_df = s.compute_strategy_factors(universe_idx, market_snap)
                        strategy_factors[s.name] = factor_df
                    except Exception:
                        logger.exception(
                            "backtest_strategy_compute_factors_error strategy=%s date=%s",
                            s, trade_date,
                        )

            # ---------- g. 聚合评分（§14-3 改造 3：二路径选择） ----------
            from quantpilot.engine.scorer import WINSORIZE_MIN_SAMPLES

            market_state_str = (
                market_state.value if hasattr(market_state, "value") else str(market_state)
            )
            # A3 方案(a)：breadth_weak 时按 OSCILLATION 查权重压制趋势，market_state
            # enum 仍保 UPTREND 传给 aggregate（与生产 StrategyService.score_universe 对称）。
            weight_lookup_state = (
                MarketStateEnum.OSCILLATION.value if breadth_weak else market_state_str
            )
            weights_record = self._lookup_active_weights(
                trade_date, weight_lookup_state, data.active_weights_history,
            )

            composite_scores: list = []
            day_pipeline_mode = "legacy_fallback"

            if (len(universe_idx) < WINSORIZE_MIN_SAMPLES
                    or weights_record["weights"] is None
                    or not strategy_factors):
                # 降级路径：universe 不足 / active_weights 未就绪 / 因子矩阵全失败
                # → 走 Phase 4 aggregate_legacy（需 s.score 0-100 输出，临时再算一次）
                # A3 注：breadth_weak 压制作用于 real_5step（生产等价）路径的
                # weight_lookup_state；本 legacy 分支是冷启动/低样本降级路径（生产恒走
                # 5 步不入此支），权重细化在此已无意义，保留原 market_state 加权。
                if len(universe_idx) > 0:
                    for s in self._strategies:
                        try:
                            strategy_scores_dict[s.name] = s.score(universe_idx, market_snap)
                        except Exception:
                            logger.exception(
                                "backtest_strategy_score_legacy_error strategy=%s date=%s",
                                s, trade_date,
                            )
                if strategy_scores_dict:
                    try:
                        composite_scores = self._scorer.aggregate_legacy(
                            market_state, strategy_scores_dict,
                        )
                    except Exception:
                        logger.exception(
                            "backtest_scorer_aggregate_legacy_error date=%s", trade_date,
                        )
                        composite_scores = []
                day_pipeline_mode = "legacy_fallback"
            else:
                # 真 5 步路径：直接调既有 Scorer.aggregate（engine 层纯函数）
                try:
                    composite_scores = self._scorer.aggregate(
                        market_state=market_state,
                        strategy_factors=strategy_factors,
                        snapshot=market_snap,
                        weights_runtime=weights_record["weights"],
                        weights_source=weights_record["weights_source"],
                        orthogonalize_order=weights_record["orthogonalize_order"],
                        hysteresis_status=weights_record["hysteresis_status"],
                        single_strategy_mode=False,
                    )
                    day_pipeline_mode = "real_5step"
                except Exception:
                    logger.exception("backtest_scorer_aggregate_error date=%s", trade_date)
                    composite_scores = []
                    day_pipeline_mode = "real_5step_failed"

            pipeline_mode_counter[day_pipeline_mode] = (
                pipeline_mode_counter.get(day_pipeline_mode, 0) + 1
            )

            # 转换为 SignalGenerator 期望的 DataFrame 格式（Phase 11 §5：派生分位字段）
            # §14-3：real_5step 路径下 CompositeScore 已含真 composite_z / composite_pct_in_market /
            #       weights_source；legacy_fallback 路径从 composite_score 反推（保持旧行为）。
            if composite_scores:
                rows = [
                    {
                        "ts_code": cs.ts_code,
                        "composite_score": cs.composite_score,
                        "score_breakdown": cs.score_breakdown,
                        "raw_factors": None,
                        "composite_z": getattr(cs, "composite_z", None),
                        "composite_pct_in_market": getattr(
                            cs, "composite_pct_in_market", None,
                        ),
                        "weights_source": getattr(cs, "weights_source", None),
                    }
                    for cs in composite_scores
                ]
                composite = pd.DataFrame(rows).set_index("ts_code")
                # Phase 11 §5 派生字段：若 Scorer.aggregate 未填（aggregate_legacy 路径），
                # 仍从 composite_score (0-100) 反推 Φ⁻¹(score/100)
                if composite["composite_z"].isna().all():
                    from scipy.stats import norm as _norm
                    clipped = composite["composite_score"].clip(lower=0.1, upper=99.9) / 100.0
                    composite["composite_z"] = clipped.apply(
                        lambda p: float(_norm.ppf(p)) if pd.notna(p) else None
                    )
                if composite["composite_pct_in_market"].isna().all():
                    composite["composite_pct_in_market"] = composite[
                        "composite_score"
                    ].rank(pct=True, ascending=False)
                if composite["weights_source"].isna().all():
                    composite["weights_source"] = "default_matrix"
            else:
                composite = pd.DataFrame()

            # ---------- h. 信号生成 ----------
            virtual_position_list = list(virtual_positions.values())
            if not composite.empty:
                try:
                    signals = self._signal_engine.generate(
                        composite,
                        virtual_position_list,
                        market_state,
                        quotes_t,
                        trade_date,
                        risk_params=None,
                    )
                except Exception:
                    # B3-9：信号生成失败用 logger.exception
                    logger.exception("backtest_signal_gen_error date=%s", trade_date)
                    signals = []
            else:
                signals = []

            # ---------- h2. PositionSizer ----------
            if signals:
                try:
                    total_mv = sum(p.market_value for p in virtual_positions.values())
                    signals = self._position_engine.suggest(
                        signals,
                        config.initial_capital + total_mv,
                        cash,
                        virtual_position_list,
                        market_state,
                        # 【降级说明D8-P3-07】使用 PositionSizer 默认参数，V1.5 传入 PositionConfig
                        config=None,
                    )
                except Exception:
                    # B3-9：position sizer 失败用 logger.exception
                    logger.exception("backtest_position_sizer_error date=%s", trade_date)

            # ---------- h3. RiskChecker（B3-4：BLOCK 信号被移除，WARN 写入 reason）----
            if signals:
                signals = self._apply_risk_checker(
                    signals, virtual_positions, cash, quotes_t,
                    market_state, trade_date,
                )

            # ---------- i. 执行信号 ----------
            # B3-2：T+1 模式（默认）→ 信号入 pending_signals 队列，T+1 日开盘撮合
            # CLOSE_T 兼容模式 → 当日 close 撮合（保留旧契约给冒烟测试）
            if config.execution_price == "OPEN_T1":
                pending_signals = list(signals)
            else:
                trade_records, cash, virtual_positions = _execute_signals(
                    signals, virtual_positions, quotes_t, cash, config, trade_date,
                    use_open_price=False,
                )
                all_trade_records.extend(trade_records)

            # ---------- j. 计算净值 ----------
            prices = {
                ts_code: float(quotes_t.loc[ts_code, "close"])
                if ts_code in quotes_t.index and "close" in quotes_t.columns
                else p.cost_price
                for ts_code, p in virtual_positions.items()
            }
            nav[trade_date] = self._calc_nav(
                virtual_positions, prices, cash, config.initial_capital
            )

            # ---------- k. 记录持仓快照 ----------
            # A1（S6-GAP-02）：当日持仓明细。有 sink → 流式交出、不累积（内存 O(batch)）；
            # 无 sink → 累积到 position_snapshots（旧行为，兼容既有测试/mock）。
            day_snapshots = [
                {
                    "trade_date": trade_date,
                    "ts_code": ts_code,
                    "shares": pos.shares,
                    "cost_price": pos.cost_price,
                    "market_value": pos.market_value,
                }
                for ts_code, pos in virtual_positions.items()
            ]
            if position_sink is not None:
                position_sink(trade_date, day_snapshots)
            else:
                position_snapshots.extend(day_snapshots)

            # ---------- l. 进度回调 ----------
            # 每个交易日都回调（短回测也能看到实时进度）
            if progress_cb:
                progress_pct = (i + 1) * 100 // total
                progress_cb(str(trade_date), progress_pct, nav[trade_date])

        performance = BacktestReport.generate(nav, all_trade_records, config)
        daily_nav_series = pd.Series(
            [nav[d] for d in trade_dates if d in nav],
            index=[d for d in trade_dates if d in nav],
        )
        daily_positions_df = pd.DataFrame(position_snapshots)

        # §14-3：聚合每日 pipeline_mode 为单一标签（众数 + 多种共存时加 mixed_ 前缀）
        if pipeline_mode_counter:
            modes_sorted = sorted(
                pipeline_mode_counter.items(), key=lambda kv: kv[1], reverse=True,
            )
            top_mode = modes_sorted[0][0]
            agg_mode = f"mixed_{top_mode}" if len(modes_sorted) > 1 else top_mode
        else:
            agg_mode = "legacy_fallback"

        return BacktestResult(
            daily_nav=daily_nav_series,
            daily_positions=daily_positions_df,
            signal_history=all_trade_records,
            performance=performance,
            disclaimer=DISCLAIMER,
            pipeline_mode=agg_mode,
        )

    # ------------------------------------------------------------------
    # 辅助方法（PIT 数据切片）
    # ------------------------------------------------------------------

    def _get_stock_info_at(self, stock_info: pd.DataFrame, trade_date: date) -> pd.DataFrame:
        """PIT 过滤：返回上市日 <= trade_date 且未退市的股票基本信息。

        V1.0 整改 Batch 3 — B3-6：增加 delist_date 过滤（trade_date < delist_date 时仍可用）；
        list_date=None 视为已上市（无上市日数据时默认可用）。
        """
        if stock_info.empty:
            return stock_info
        if "list_date" not in stock_info.columns:
            return stock_info
        list_mask = stock_info["list_date"].apply(
            lambda d: (d is None) or (not pd.notna(d)) or (pd.Timestamp(d).date() <= trade_date)
        )
        # B3-6：delist_date 时点过滤（退市日 > trade_date 才可交易；未退市 delist_date=None 通过）
        if "delist_date" in stock_info.columns:
            delist_mask = stock_info["delist_date"].apply(
                lambda d: (d is None) or (not pd.notna(d)) or (pd.Timestamp(d).date() > trade_date)
            )
            return stock_info[list_mask & delist_mask]
        return stock_info[list_mask]

    def _get_financials_at(self, financials: pd.DataFrame, trade_date: date) -> pd.DataFrame:
        """PIT 财务快照：与生产 `get_latest_financial` 同语义（见 `_latest_financials_at`）。

        ~~原实现 `groupby(level=0).last()`~~ 取的是帧内顺序的末行，结果随 SELECT 的堆序而变
        （2026-09-22 发现，5434 上 97% 股票取错行）；现在逐股确定性，且 `run()` 传入的是
        `_prepare_financials` 预排序帧，逐日只做掩码 + 去重。没有 publish_date 列的帧
        （旧 bundle / 单测替身）原样返回。
        """
        if financials.empty or "publish_date" not in financials.columns:
            return financials
        return _latest_financials_at(financials, trade_date)

    def _get_forecast_at(self, forecast: pd.DataFrame, trade_date: date) -> pd.DataFrame:
        """A5b（SDD-EXT-03）PIT 切片：从预加载全量 forecast 取 ``pre_announce_date <=
        trade_date`` 中每股**报告期最新**、同报告期 ``data_priority`` 高者（快报 2 > 预告 1）
        的一行。与生产 ``repository.get_latest_forecast`` 同语义，但对内存做切片保持 Engine
        无 IO。返回 index=ts_code（含 report_period / est_net_profit）；无可用行 → 空 DataFrame。
        """
        if forecast is None or forecast.empty or "pre_announce_date" not in forecast.columns:
            return pd.DataFrame()
        try:
            pit = forecast[_pit_mask(forecast["pre_announce_date"], trade_date).to_numpy()]
            if pit.empty:
                return pd.DataFrame()
            # 升序排序后 drop_duplicates keep="last" 取每股最后一行（= 报告期最新、同期
            # data_priority 高者、再新的 pre_announce），行级一致（避免 groupby.last()
            # 逐列取末非空可能跨行拼接）。等价 get_latest_forecast 的 DISTINCT ON ... DESC。
            pit = pit.sort_values(
                ["report_period", "data_priority", "pre_announce_date"],
                ascending=[True, True, True],
            )
            return pit.drop_duplicates(subset="ts_code", keep="last").set_index("ts_code")
        except Exception:
            logger.exception("backtest_get_forecast_at_error date=%s", trade_date)
            return pd.DataFrame()

    def _get_quotes_at(
        self,
        adj_prices: pd.DataFrame,
        trade_date: date,
        ts_codes_filter: pd.Index | None = None,
        daily_quotes: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """
        提取当日行情快照。

        V1.0 整改 Batch 3 — B3-1/5 扩展：
        - daily_quotes 非空时返回完整字段 DataFrame（含 close/open/limit_up/limit_down/
          is_suspended/is_st/amount），index=ts_code；MultiIndex(trade_date, ts_code) 切片。
        - daily_quotes 为空时降级为仅 close（adj_prices 单日截面），保留旧契约。
        """
        try:
            if daily_quotes is not None and not daily_quotes.empty:
                # B3-1：MultiIndex(trade_date, ts_code) → 当日全字段 DataFrame
                td_pd = pd.Timestamp(trade_date)
                if isinstance(daily_quotes.index, pd.MultiIndex):
                    try:
                        slice_t = daily_quotes.xs(td_pd, level="trade_date")
                    except KeyError:
                        try:
                            slice_t = daily_quotes.xs(trade_date, level="trade_date")
                        except KeyError:
                            slice_t = daily_quotes.iloc[0:0]
                    if not slice_t.empty:
                        if ts_codes_filter is not None:
                            slice_t = slice_t[slice_t.index.isin(ts_codes_filter)]
                        return slice_t.copy()

            # 降级：仅 close 截面（旧契约保留）
            td = pd.Timestamp(trade_date)
            if td in adj_prices.index:
                row = adj_prices.loc[td]
                if isinstance(row, pd.Series):
                    df = row.rename("close").to_frame()
                    if ts_codes_filter is not None:
                        df = df[df.index.isin(ts_codes_filter)]
                    return df
                return row
        except Exception:
            logger.exception("backtest_get_quotes_at_error date=%s", trade_date)
        return pd.DataFrame()

    # B3-3：pe_pb_history 时点切片
    def _slice_pe_pb_history_at(
        self, pe_pb_history: pd.DataFrame, trade_date: date,
    ) -> pd.DataFrame:
        """返回 publish_date <= trade_date 的 pe_pb_history（ValueStrategy 真实分位数）。"""
        if pe_pb_history.empty:
            return pe_pb_history
        try:
            td = pd.Timestamp(trade_date)
            if isinstance(pe_pb_history.index, pd.MultiIndex):
                # MultiIndex(ts_code, publish_date)。publish_date 来自 financial_data.publish_date，
                # 可能是 Python date 对象（object dtype）→ 与 Timestamp 比较抛
                # 'Cannot compare Timestamp with datetime.date' 被吞 → PIT 切片失效（每日报错）。
                # 先 to_datetime 统一为 DatetimeIndex 再比较。
                pubs = pd.to_datetime(pe_pb_history.index.get_level_values("publish_date"))
                mask = pubs <= td
                return pe_pb_history[mask]
        except Exception:
            logger.exception("backtest_pe_pb_slice_error date=%s", trade_date)
        return pe_pb_history

    # B3-3：index_adj_prices 时点切片
    def _slice_index_at(
        self, index_adj_prices: pd.Series, trade_date: date,
    ) -> pd.Series:
        """返回截至 trade_date 的 HS300 累计后复权 close（Momentum.rs_6m 真实计算）。"""
        if index_adj_prices.empty:
            return index_adj_prices
        try:
            td = pd.Timestamp(trade_date)
            idx = index_adj_prices.index
            if not isinstance(idx, pd.DatetimeIndex):
                idx = pd.to_datetime(idx)
                index_adj_prices = pd.Series(
                    index_adj_prices.values, index=idx, name=index_adj_prices.name,
                )
            return index_adj_prices.loc[:td]
        except Exception:
            logger.exception("backtest_index_slice_error date=%s", trade_date)
            return index_adj_prices

    def _get_market_state(
        self,
        hs300_history: pd.DataFrame,
        trade_date: date,
        daily_quotes: pd.DataFrame | None = None,
    ) -> tuple[MarketStateEnum, bool]:
        """识别截至 trade_date 的市场状态。

        identify_latest 返回 MarketStateRecord | None；本方法抽出 (market_state, breadth_weak)。
        历史不足（暖启动期）时降级为 (OSCILLATION, False)。

        V1.5-A A3（SDD-EXT-07）：``daily_quotes``（bundle 全字段 MultiIndex）非空时，
        从中算当日 NH-NL 市场宽度传入 identify_latest，得回测侧 breadth_weak（与生产
        同一 compute_breadth_weak）；缺省 None → nh_nl=None → breadth_weak 恒 False。
        """
        try:
            if hs300_history is None or hs300_history.empty:
                return MarketStateEnum.OSCILLATION, False
            if "trade_date" in hs300_history.columns:
                hist = hs300_history[
                    hs300_history["trade_date"].apply(
                        lambda d: pd.Timestamp(d).date() <= trade_date
                    )
                ].copy()
                # MarketStateEngine.identify 以 DataFrame index 为交易日（逐行 idx.date()）。
                # _load_data_bundle 产出的 hs300_history 是整数 RangeIndex + trade_date 列，
                # 必须转成 date 索引，否则 identify 用 int 索引报
                # 'int object has no attribute date' → 被吞 → 恒回落 OSCILLATION（回测退化）。
                hist.index = pd.DatetimeIndex(pd.to_datetime(hist["trade_date"]))
            else:
                idx = hs300_history.index
                if not isinstance(idx, pd.DatetimeIndex):
                    idx = pd.to_datetime(idx)
                hist = hs300_history[idx <= pd.Timestamp(trade_date)]
            if hist.empty:
                return MarketStateEnum.OSCILLATION, False

            # A3：算当日 NH-NL 传入（供 breadth_weak）。identify_latest 只看 hist 末日
            # 记录 → nh_nl_series 键须对齐 hist.index[-1]（未必等于 trade_date）。
            nh_nl_val = self._backtest_nh_nl_value(daily_quotes, trade_date)
            nh_nl_series = (
                pd.Series({hist.index[-1]: nh_nl_val}) if nh_nl_val is not None else None
            )
            record = self._market_state_engine.identify_latest(
                hist, nh_nl_series=nh_nl_series,
            )
            if record is None:
                return MarketStateEnum.OSCILLATION, False
            return record.market_state, record.breadth_weak
        except Exception:
            # B3-9：原 except 静默吞，改 logger.exception
            logger.exception("backtest_market_state_error date=%s", trade_date)
            return MarketStateEnum.OSCILLATION, False

    def _backtest_nh_nl_value(
        self, daily_quotes: pd.DataFrame | None, trade_date: date
    ) -> float | None:
        """A3：从 bundle daily_quotes（MultiIndex(trade_date, ts_code)）算 trade_date
        当日 NH-NL 差值（float）。取 [trade_date-90 日历日, trade_date] 的 close pivot
        成 wide（近似 ≥60 交易日），调 MarketStateEngine.compute_nh_nl_diff。
        数据不足 / 无列 → None。

        【设计待定：回测 NH-NL 性能——逐日重算 60 日窗口，实施期 profile，
        必要时预算全期 NH-NL 时序一次】
        """
        if daily_quotes is None or daily_quotes.empty:
            return None
        if not isinstance(daily_quotes.index, pd.MultiIndex) or "close" not in daily_quotes.columns:
            return None
        try:
            td = pd.Timestamp(trade_date)
            start = td - pd.Timedelta(days=90)
            lvl_ts = pd.to_datetime(daily_quotes.index.get_level_values("trade_date"))
            mask = (lvl_ts >= start) & (lvl_ts <= td)
            sliced = daily_quotes.loc[mask, ["close"]]
            if sliced.empty:
                return None
            close_wide = sliced["close"].unstack("ts_code").sort_index()
            return self._market_state_engine.compute_nh_nl_diff(close_wide)
        except Exception:
            logger.exception("backtest_nh_nl_compute_error date=%s", trade_date)
            return None

    # V1.0 整改 Batch 3 — B3-4：RiskChecker 集成
    def _apply_risk_checker(
        self,
        signals: list,
        virtual_positions: dict[str, _VirtualPosition],
        cash: float,
        quotes_t: pd.DataFrame,
        market_state: MarketStateEnum,
        trade_date: date,
    ) -> list:
        """构造虚拟账户上下文调 RiskChecker.check，移除 BLOCK 信号、WARN 写入信号 reason。

        与实盘 SignalService 一致：BLOCK BUY 信号被剔除，WARN 不剔除（仅记录到 reason）。
        集中度 / 行业集中度 / 账户回撤三层覆盖，回撤阈值复用 RiskLimitsConfig 默认 0.20。
        """
        from quantpilot.engine.risk import RiskChecker

        # 构造与实盘相同形状的"持仓快照"（含 ts_code/market_value/sw_industry_l1）
        class _Pos:
            __slots__ = ("ts_code", "market_value", "shares")

            def __init__(self, ts_code: str, market_value: float, shares: int) -> None:
                self.ts_code = ts_code
                self.market_value = market_value
                self.shares = shares

        position_snapshots = []
        position_mv = 0.0
        for ts_code, p in virtual_positions.items():
            price = (
                float(quotes_t.loc[ts_code, "close"])
                if ts_code in quotes_t.index and "close" in quotes_t.columns
                else p.cost_price
            )
            mv = price * p.shares
            position_mv += mv
            position_snapshots.append(_Pos(ts_code, mv, p.shares))
        total_assets = cash + position_mv

        # stock_industry：从当日 quotes_t 读取 sw_industry_l1（B3-1 daily_quotes 已含此列）
        if "sw_industry_l1" in quotes_t.columns:
            industry_df = quotes_t[["sw_industry_l1"]].copy()
        else:
            industry_df = pd.DataFrame()

        try:
            checker = RiskChecker()
            warnings = checker.check(
                signals=signals,
                current_positions=position_snapshots,
                account_total_assets=total_assets,
                stock_industry=industry_df,
            )
        except Exception:
            # B3-9：RiskChecker 失败用 logger.exception，放行原信号（避免阻断回测）
            logger.exception("backtest_risk_checker_error date=%s", trade_date)
            return signals

        # BLOCK BUY 被剔除；WARN 写入 reason
        blocked: set[str] = {w.ts_code for w in warnings if w.severity == "BLOCK"}
        warn_msgs: dict[str, list[str]] = {}
        for w in warnings:
            if w.severity == "WARN":
                warn_msgs.setdefault(w.ts_code, []).append(w.message)

        result = []
        for sig in signals:
            if sig.signal_type == "BUY" and sig.ts_code in blocked:
                continue
            if sig.ts_code in warn_msgs:
                msg = "; ".join(warn_msgs[sig.ts_code])
                sig.reason = (sig.reason + " | " + msg) if sig.reason else msg
            result.append(sig)
        return result

    def _lookup_active_weights(
        self,
        trade_date: date,
        market_state_str: str,
        history: dict[tuple[str, date], dict],
    ) -> dict:
        """Phase 14 §14-3：前向查找 active_weights snapshot。

        条件：``max(effective_date) <= trade_date AND state == market_state_str``。

        找不到（state 不存在 / 全部 effective_date 都晚于 trade_date / history 空）
        → 返回 ``{"weights": None, "weights_source": "default_matrix",
        "orthogonalize_order": [], "hysteresis_status": "stable"}`` sentinel
        触发主循环 §14-3 改造 3 的降级路径（aggregate_legacy）。
        """
        candidates = [
            (eff_date, rec) for (state, eff_date), rec in history.items()
            if state == market_state_str and eff_date <= trade_date
        ]
        if not candidates:
            return {
                "weights": None,
                "weights_source": "default_matrix",
                "orthogonalize_order": [],
                "hysteresis_status": "stable",
            }
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _calc_nav(
        positions: dict[str, _VirtualPosition],
        prices: dict[str, float],
        cash: float,
        initial_capital: float,
    ) -> float:
        """净值 = (现金 + 持仓市值) / 初始资金。"""
        position_mv = 0.0
        for ts_code, pos in positions.items():
            price = prices.get(ts_code, pos.cost_price)
            position_mv += pos.shares * price
        return (cash + position_mv) / initial_capital if initial_capital > 0 else 1.0


# ---------------------------------------------------------------------------
# 信号执行（模拟成交）
# ---------------------------------------------------------------------------

def _execute_signals(
    signals: list,
    positions: dict[str, _VirtualPosition],
    quotes: pd.DataFrame,
    cash: float,
    config: BacktestConfig,
    trade_date: date,
    use_open_price: bool = False,
) -> tuple[list[dict], float, dict[str, _VirtualPosition]]:
    """
    按信号模拟成交，更新虚拟持仓和现金。

    V1.0 整改 Batch 3 — B3-2：use_open_price=True 时取 open 价（T+1 开盘撮合），否则 close。
    B3-1：涨停日 BUY 跳过、停牌日 BUY/SELL 全跳过（quotes 含 limit_up/is_suspended 字段）。

    返回：(trade_records, new_cash, new_positions)
    """
    trade_records: list[dict] = []
    new_positions = dict(positions)

    for sig in signals:
        ts_code = sig.ts_code
        if ts_code not in quotes.index:
            continue  # 无行情，跳过

        # B3-1：停牌日跳过（不论 BUY/SELL）
        if "is_suspended" in quotes.columns:
            try:
                if bool(quotes.loc[ts_code, "is_suspended"]):
                    continue
            except Exception:
                pass

        # B3-2：T+1 撮合用 open，CLOSE_T 兼容用 close
        price_col = "open" if use_open_price else "close"
        if price_col in quotes.columns:
            try:
                price_val = quotes.loc[ts_code, price_col]
                if pd.isna(price_val):
                    if "close" in quotes.columns:
                        price_val = quotes.loc[ts_code, "close"]
                    else:
                        continue
                price = float(price_val)
                if price <= 0:
                    continue
            except Exception:
                continue
        elif "close" in quotes.columns:
            price = float(quotes.loc[ts_code, "close"])
        else:
            continue

        # SDD-EXT-02s（V1.5-A A2）：涨停成交可行性精细化。原 B3-1 对 BUY 一律跳过
        # limit_up（过度保守）；改为仅「收盘涨停 AND 换手率 < 1%（无量一字板）」判定
        # 不可成交，涨停但有量（盘中打开过）→ 可成交。turnover_rate 入库为小数
        # （adapter `/100`），阈值 _LIMIT_UP_ILLIQUID_TURNOVER=0.01=1%。turnover 缺失
        # （NULL / 无列，旧 bundle 降级）→ 保守视为无量、跳过 BUY，并 logger.warning
        # 不静默。SELL 不受此约束（跌停无量 SELL 对称约束归 V2.0 SDD-EXT-02f）。
        if sig.signal_type == "BUY" and "limit_up" in quotes.columns:
            try:
                is_limit_up = bool(quotes.loc[ts_code, "limit_up"])
            except Exception:
                is_limit_up = False
            if is_limit_up:
                turnover: float | None = None
                if "turnover_rate" in quotes.columns:
                    try:
                        _tv = quotes.loc[ts_code, "turnover_rate"]
                        turnover = float(_tv) if pd.notna(_tv) else None
                    except Exception:
                        turnover = None
                if turnover is None:
                    logger.warning(
                        "backtest_limit_up_turnover_missing ts_code=%s date=%s "
                        "→ 保守视为无量一字板跳过 BUY",
                        ts_code, trade_date,
                    )
                    continue
                if turnover < _LIMIT_UP_ILLIQUID_TURNOVER:
                    continue  # 无量一字板 → 不可成交

        if sig.signal_type == "BUY":
            # 确定买入金额 = suggested_pct × initial_capital（或默认 10%）
            pct = sig.suggested_pct if sig.suggested_pct is not None else 0.10
            target_amount = config.initial_capital * pct
            cost_per_unit = _buy_cost_per_unit(price, config)
            if cost_per_unit <= 0 or cash < target_amount * 0.5:
                continue  # 现金不足，跳过

            actual_amount = min(target_amount, cash * 0.95)
            shares = int(actual_amount / cost_per_unit / 100) * 100  # 取整百股
            if shares <= 0:
                continue

            total_cost = shares * cost_per_unit
            if total_cost > cash:
                continue

            cash -= total_cost

            if ts_code in new_positions:
                # 加仓：WAC 更新
                old = new_positions[ts_code]
                total_shares = old.shares + shares
                wac = (old.shares * old.cost_price + shares * price) / total_shares
                new_positions[ts_code] = _VirtualPosition(
                    ts_code=ts_code,
                    shares=total_shares,
                    cost_price=wac,
                    open_date=old.open_date,
                )
            else:
                new_positions[ts_code] = _VirtualPosition(
                    ts_code=ts_code,
                    shares=shares,
                    cost_price=price,
                    open_date=trade_date,
                )

            trade_records.append({
                "ts_code": ts_code,
                "signal_type": "BUY",
                "trade_date": trade_date,
                "price": price,
                "shares": shares,
                "cost": total_cost,
                "proceeds": 0.0,
            })

        elif sig.signal_type == "SELL":
            if ts_code not in new_positions:
                continue
            pos = new_positions[ts_code]
            proceeds_per_unit = _sell_proceeds_per_unit(price, config)
            total_proceeds = pos.shares * proceeds_per_unit
            cash += total_proceeds
            del new_positions[ts_code]

            trade_records.append({
                "ts_code": ts_code,
                "signal_type": "SELL",
                "trade_date": trade_date,
                "price": price,
                "shares": pos.shares,
                "cost": pos.shares * pos.cost_price,
                "proceeds": total_proceeds,
            })

    return trade_records, cash, new_positions
