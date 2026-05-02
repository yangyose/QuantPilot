"""PositionSizer：仓位计算引擎（Phase 5）。Engine 层纯函数，无 IO。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.signal import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class PositionConfig:
    """仓位控制参数（SDD §10.1）。"""

    single_pct: float = 0.10            # 单笔仓位比例
    max_single_stock_pct: float = 0.20  # 单股持仓上限
    max_total_pct: float = 0.80         # 总仓位上限（调节前）
    min_cash_pct: float = 0.20          # 最低现金保留

    # 市场状态调节系数（SDD §10.1）
    uptrend_multiplier: float = 1.00
    oscillation_multiplier: float = 0.75
    downtrend_multiplier: float = 0.50


class PositionSizer:
    """纯函数，无 IO。在 SignalGenerator 之后由 DailyPipeline CP3 调用。"""

    def suggest(
        self,
        signals: list[TradeSignal],
        account_total_assets: float,
        account_cash: float,
        current_positions: list,
        market_state: MarketStateEnum,
        config: PositionConfig | None = None,
    ) -> list[TradeSignal]:
        """
        为每个 BUY 信号填充 suggested_pct（SDD §10.1 固定比例法）。

        有效总仓位上限 = max_total_pct × market_multiplier
        当前已用仓位 = sum(position.market_value) / total_assets
        可用仓位 = max(0, 有效总仓位上限 - 当前已用仓位 - min_cash_pct)
        单笔仓位 = min(single_pct, 单股剩余额度, 可用仓位)
        若可用仓位 < single_pct × 0.5 → suggested_pct = None
        SELL 信号不填充 suggested_pct（保持 None）。
        """
        cfg = config or PositionConfig()

        # 市场状态系数
        multiplier = {
            MarketStateEnum.UPTREND: cfg.uptrend_multiplier,
            MarketStateEnum.OSCILLATION: cfg.oscillation_multiplier,
            MarketStateEnum.DOWNTREND: cfg.downtrend_multiplier,
        }.get(market_state, 1.0)

        effective_max = cfg.max_total_pct * multiplier

        # 当前已用仓位
        total_assets = account_total_assets if account_total_assets > 0 else 1.0
        current_used = sum(
            (float(p.market_value) if p.market_value is not None else 0.0)
            for p in current_positions
        ) / total_assets

        # 全局可用仓位（扣减最低现金保留）
        available = max(0.0, effective_max - current_used - cfg.min_cash_pct)

        # 每股持仓占比 lookup
        position_pct: dict[str, float] = {}
        for p in current_positions:
            mv = float(p.market_value) if p.market_value is not None else 0.0
            position_pct[p.ts_code] = mv / total_assets

        result: list[TradeSignal] = []
        allocated = 0.0  # 本次 suggest() 中已分配的 BUY 仓位累计

        for sig in signals:
            if sig.signal_type != "BUY":
                result.append(sig)
                continue

            # 扣减本轮已分配仓位后的剩余可用额度（多信号批量处理时互相扣减）
            remaining_available = max(0.0, available - allocated)

            # 单股剩余额度
            current_stock_pct = position_pct.get(sig.ts_code, 0.0)
            stock_remaining = max(0.0, cfg.max_single_stock_pct - current_stock_pct)

            # 单笔仓位
            single = min(cfg.single_pct, stock_remaining, remaining_available)

            # 资金不足判断
            if remaining_available < cfg.single_pct * 0.5:
                suggested_pct = None
            else:
                suggested_pct = single if single > 0 else None

            if suggested_pct is not None:
                allocated += suggested_pct

            result.append(replace(sig, suggested_pct=suggested_pct))

        return result
