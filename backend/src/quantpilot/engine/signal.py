"""SignalGenerator：信号生成引擎（Phase 5，Phase 10 接入 UserConfig）。Engine 层纯函数，无 IO。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from quantpilot.core.config_defaults import (
    DEFAULT_SIGNAL_CONFIG,
    DEFAULT_UNIVERSE,
    SignalConfig,
    UniverseConfig,
)
from quantpilot.engine.market_state import MarketStateEnum

logger = logging.getLogger(__name__)


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
    signal_type: str                    # 'BUY' / 'SELL'
    trade_date: date
    score: float                        # 综合评分 0-100
    suggested_price_low: float | None = None
    suggested_price_high: float | None = None
    stop_loss_price: float | None = None
    suggested_pct: float | None = None   # PositionSizer 填充：建议买入占总资产比例
    signal_strength: str | None = None   # 'STRONG' / 'MODERATE'（仅买入信号）
    liquidity_note: str | None = None
    t1_warning: str = "A股T+1制度：买入当日不可卖出"
    reason: str = ""
    # 数据血缘（SignalService.save 写入 SignalScoreSnapshot 时使用）
    score_breakdown: dict | None = None  # {strategy: {score, weight, contribution}}
    raw_factors: dict | None = None      # {factor_name: value}


class SignalGenerator:
    """纯函数，无 IO。由 DailyPipeline CP3 调用（Phase 7），或测试直接调用。

    Phase 10：`signal_cfg` / `universe_cfg` 注入自 ConfigService；
    `generate()` 的 `risk_params` 显式覆盖入参保留（用于单元测试场景），
    未传入时按 `self._default_risk_params()` 从 dataclass 组装。
    """

    def __init__(
        self,
        signal_cfg: SignalConfig | None = None,
        universe_cfg: UniverseConfig | None = None,
    ) -> None:
        self._signal_cfg = signal_cfg or DEFAULT_SIGNAL_CONFIG
        self._universe_cfg = universe_cfg or DEFAULT_UNIVERSE

    def _default_risk_params(self) -> RiskParams:
        """从注入的 dataclass 组装 RiskParams（保留 RiskParams 以兼容现有 generate 逻辑）。"""
        s = self._signal_cfg
        u = self._universe_cfg
        return RiskParams(
            buy_threshold=s.buy_threshold,
            sell_threshold=s.sell_threshold,
            stop_loss_pct=s.stop_loss_pct,
            add_cost_deviation_pct=s.add_cost_deviation_pct,
            min_liquidity_amount=u.min_liquidity_amount,
            price_low_mult=s.price_low_mult,
            price_high_mult=s.price_high_mult,
            stop_loss_from_entry_pct=s.stop_loss_pct,
            signal_strong_threshold=s.strong_threshold,
        )

    def generate(
        self,
        composite_scores: pd.DataFrame,
        current_positions: list,
        market_state: MarketStateEnum,
        snapshot_quotes: pd.DataFrame,
        trade_date: date,
        risk_params: RiskParams | None = None,
    ) -> list[TradeSignal]:
        """
        买入信号逻辑（SDD §9.1，全部条件须同时满足）：
        1. composite_score > buy_threshold（默认80）
        2. is_suspended=False 且 limit_up=False（非停牌、非涨停）
        3. avg_amount >= min_liquidity_amount（流动性检查）
        4. 当前无持仓，或符合加仓规则

        加仓规则（SDD §10.1，任一满足）：
        - 持仓浮盈 > 0（pnl_pct > 0）
        - 当前价偏离成本价 ≤ ±10% 且市场状态非下跌趋势

        卖出信号逻辑（SDD §9.2，任一满足）：
        1. 持仓股 composite_score < sell_threshold（默认40）
        2. 硬止损：pnl_pct <= -stop_loss_pct（-8%）

        同一标的同日不同时产生买入和卖出信号（SDD §9.5）：
        持仓标的评分处于 [sell_threshold, buy_threshold] 区间时不产生任何信号。
        """
        params = risk_params or self._default_risk_params()
        signals: list[TradeSignal] = []

        # 构建持仓查找表：ts_code → position
        position_map = {p.ts_code: p for p in current_positions}
        holding_codes = set(position_map.keys())

        for ts_code, row in composite_scores.iterrows():
            score = float(row["composite_score"])

            # 获取行情快照
            if ts_code not in snapshot_quotes.index:
                continue
            q = snapshot_quotes.loc[ts_code]
            is_suspended = bool(q.get("is_suspended", False))
            limit_up = bool(q.get("limit_up", False))
            avg_amount = float(q.get("avg_amount", float("nan")))
            close = float(q.get("close", float("nan")))

            if ts_code in holding_codes:
                pos = position_map[ts_code]
                pnl_pct = float(pos.pnl_pct) if pos.pnl_pct is not None else 0.0
                cost_price = float(pos.cost_price) if pos.cost_price is not None else close

                # 卖出条件（优先于买入判断）
                stop_loss_triggered = pnl_pct <= -params.stop_loss_pct
                sell_by_score = score < params.sell_threshold

                if stop_loss_triggered or sell_by_score:
                    reason = (
                        f"止损触发（浮亏 {pnl_pct:.1%}，阈值 -{params.stop_loss_pct:.1%}）"
                        if stop_loss_triggered
                        else f"评分 {score:.1f} 低于卖出阈值 {params.sell_threshold}"
                    )
                    signals.append(TradeSignal(
                        ts_code=ts_code,
                        signal_type="SELL",
                        trade_date=trade_date,
                        score=score,
                        reason=reason,
                    ))
                    continue

                # 持有区间 [sell_threshold, buy_threshold] → 无信号
                if score <= params.buy_threshold:
                    continue

                # 加仓条件（SDD §10.1）：任一满足即可
                can_add = False
                if pnl_pct > 0:
                    can_add = True
                else:
                    # 价格偏离成本 ≤ ±10% 且非下跌趋势
                    price_deviation = (
                        abs(close - cost_price) / cost_price if cost_price > 0 else 1.0
                    )
                    if (
                        price_deviation <= params.add_cost_deviation_pct
                        and market_state != MarketStateEnum.DOWNTREND
                    ):
                        can_add = True

                if not can_add:
                    continue

            else:
                # 无持仓：检查买入条件
                if score <= params.buy_threshold:
                    continue

            # 通用买入条件检查
            if is_suspended or limit_up:
                continue
            if not pd.isna(avg_amount) and avg_amount < params.min_liquidity_amount:
                continue
            if pd.isna(close):
                continue

            # 构造 BUY 信号
            price_low = round(close * params.price_low_mult, 3)
            price_high = round(close * params.price_high_mult, 3)
            mid = (price_low + price_high) / 2
            stop_loss = round(mid * (1 - params.stop_loss_from_entry_pct), 3)
            strength = (
                "STRONG" if score >= params.signal_strong_threshold else "MODERATE"
            )

            # 提取数据血缘字段（SignalService.save() 写入 SignalScoreSnapshot 时使用）
            # 若 composite_scores 含 score_breakdown/raw_factors 列则提取，否则为 None
            has_breakdown = "score_breakdown" in composite_scores.columns
            breakdown = row.get("score_breakdown") if has_breakdown else None
            raw = row.get("raw_factors") if "raw_factors" in composite_scores.columns else None
            score_breakdown = breakdown if isinstance(breakdown, dict) else None
            raw_factors = raw if isinstance(raw, dict) else None

            signals.append(TradeSignal(
                ts_code=ts_code,
                signal_type="BUY",
                trade_date=trade_date,
                score=score,
                suggested_price_low=price_low,
                suggested_price_high=price_high,
                stop_loss_price=stop_loss,
                signal_strength=strength,
                t1_warning="A股T+1制度：买入当日不可卖出",
                reason=f"综合评分 {score:.1f}",
                score_breakdown=score_breakdown,
                raw_factors=raw_factors,
            ))

        return signals
