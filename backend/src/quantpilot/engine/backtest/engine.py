"""BacktestEngine：回测主引擎（Phase 8，SDD §7.7）。Engine 层纯函数，无 IO。"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from quantpilot.engine.backtest.report import DISCLAIMER, BacktestReport
from quantpilot.engine.market_state import MarketStateEnum

logger = logging.getLogger(__name__)


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
    financials: pd.DataFrame       # MultiIndex(ts_code, report_period)，含 publish_date
    hs300_history: pd.DataFrame    # HS300 OHLCV 历史（index=trade_date 或含 trade_date 列）
    # B3-1：完整字段日线，index=(trade_date, ts_code)
    daily_quotes: pd.DataFrame = field(default_factory=pd.DataFrame)
    # B3-3：(ts_code, publish_date) 历史 PE/PB（ValueStrategy 真实分位数）
    pe_pb_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    # B3-3：HS300 后复权累计价（Momentum.rs_6m 真实计算；index=trade_date）
    index_adj_prices: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    # Phase 14 §14-3：5y 月末 rebalance active_weights 时序，键 (state_str, effective_date)。
    # 值含 weights / weights_source / orthogonalize_order / hysteresis_status；
    # 主循环用 max(effective_date) <= trade_date AND state 做 PIT 前向查找。
    # 空 dict → BacktestEngine 走 aggregate_legacy 降级（保留旧 mock 回测兼容）。
    active_weights_history: dict[tuple[str, date], dict] = field(default_factory=dict)


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
    ) -> BacktestResult:
        """
        回测主循环（同步；由 BacktestService 通过 asyncio.to_thread 包装）。

        参数：
          config      — 回测参数
          data        — BacktestService 预加载的历史数据
          progress_cb — 可选进度回调 (trade_date_str, progress_pct, current_nav)，每 100 日触发一次
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
            financials_t = self._get_financials_at(data.financials, trade_date)

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

            # ---------- d. Universe 过滤 ----------
            try:
                universe_idx = self._universe_filter.filter(
                    stock_info_t, financials_t, quotes_t, trade_date, self._calendar,
                )
            except Exception:
                logger.exception("backtest_universe_filter_error date=%s", trade_date)
                universe_idx = stock_info_t.index[:0]

            # 限制 universe 为有当日行情的股票（无价格则无法成交；也避免对无数据股票做无效循环）
            if not quotes_t.empty and len(universe_idx) > 0:
                universe_idx = universe_idx[universe_idx.isin(quotes_t.index)]

            # ---------- e. 市场状态识别 ----------
            market_state = self._get_market_state(data.hs300_history, trade_date)

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

                from quantpilot.engine.strategies.base import MarketSnapshot
                market_snap: MarketSnapshot = {
                    "trade_date": trade_date,
                    "adj_prices": adj_hist,
                    "daily_quotes": quotes_t,
                    "financials": financials_t,
                    "pe_pb_history": pe_pb_t,
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
            weights_record = self._lookup_active_weights(
                trade_date, market_state_str, data.active_weights_history,
            )

            composite_scores: list = []
            day_pipeline_mode = "legacy_fallback"

            if (len(universe_idx) < WINSORIZE_MIN_SAMPLES
                    or weights_record["weights"] is None
                    or not strategy_factors):
                # 降级路径：universe 不足 / active_weights 未就绪 / 因子矩阵全失败
                # → 走 Phase 4 aggregate_legacy（需 s.score 0-100 输出，临时再算一次）
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
            for ts_code, pos in virtual_positions.items():
                position_snapshots.append({
                    "trade_date": trade_date,
                    "ts_code": ts_code,
                    "shares": pos.shares,
                    "cost_price": pos.cost_price,
                    "market_value": pos.market_value,
                })

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
        """
        PIT 过滤：返回公告日 <= trade_date 的最近一期财务数据。
        financials 为 MultiIndex(ts_code, report_period) 或扁平 DataFrame。
        """
        if financials.empty:
            return financials
        # 若有 publish_date 列，按公告日过滤
        if "publish_date" in financials.columns:
            try:
                mask = financials["publish_date"].apply(
                    lambda d: d is not None and pd.notna(d) and pd.Timestamp(d).date() <= trade_date
                )
                pit = financials[mask]
                # 按 ts_code 取最新一期
                if isinstance(pit.index, pd.MultiIndex):
                    return pit.groupby(level=0).last()
                return pit
            except Exception:
                pass
        return financials

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
                # MultiIndex(ts_code, publish_date)
                pubs = pe_pb_history.index.get_level_values("publish_date")
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

    def _get_market_state(self, hs300_history: pd.DataFrame, trade_date: date) -> MarketStateEnum:
        """识别截至 trade_date 的市场状态。

        identify_latest 返回 MarketStateRecord | None；本方法抽出 .market_state 供 Scorer 使用。
        历史不足（暖启动期）时降级为 OSCILLATION。
        """
        try:
            if hs300_history is None or hs300_history.empty:
                return MarketStateEnum.OSCILLATION
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
                return MarketStateEnum.OSCILLATION
            record = self._market_state_engine.identify_latest(hist)
            if record is None:
                return MarketStateEnum.OSCILLATION
            return record.market_state
        except Exception:
            # B3-9：原 except 静默吞，改 logger.exception
            logger.exception("backtest_market_state_error date=%s", trade_date)
            return MarketStateEnum.OSCILLATION

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

        # B3-1：涨停日跳过 BUY（无法成交），SELL 仍允许
        if sig.signal_type == "BUY" and "limit_up" in quotes.columns:
            try:
                if bool(quotes.loc[ts_code, "limit_up"]):
                    continue
            except Exception:
                pass

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
