"""RiskChecker：风险检查引擎（Phase 5）。Engine 层纯函数，无 IO。"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from quantpilot.core.config_defaults import DEFAULT_RISK_LIMITS, RiskLimitsConfig
from quantpilot.engine.signal import TradeSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskWarning:
    """风险告警。severity='BLOCK' 时对应信号由 SignalService.save() 移除。"""

    ts_code: str
    warning_type: str  # 'CONCENTRATION_STOCK' | 'CONCENTRATION_INDUSTRY' | 'DRAWDOWN'
    message: str
    severity: str      # 'WARN'（附加到 signal.reason，不阻断）| 'BLOCK'（移除对应 BUY 信号）


class RiskChecker:
    """纯函数，无 IO。检查集中度风险和账户回撤风险（SDD §10.2）。

    Phase 10：`risk_limits` 注入自 `config_service.get_risk_limits()`。
    `check()` 的阈值入参 `None` 时自动回退 `self._limits.*`。
    """

    def __init__(self, risk_limits: RiskLimitsConfig | None = None) -> None:
        self._limits = risk_limits or DEFAULT_RISK_LIMITS

    def check(
        self,
        signals: list[TradeSignal],
        current_positions: list,
        account_total_assets: float,
        stock_industry: pd.DataFrame,
        # index=ts_code，columns 含 sw_industry_l1
        max_single_stock_pct: float | None = None,
        max_industry_pct: float | None = None,
        account_max_drawdown_pct: float | None = None,
        # 【降级说明】Phase 5 无 AccountService，调用方传 None 跳过回撤检查；
        # Phase 7 DailyPipeline CP3 集成时从 Account 对象读取实际最大回撤并传入。
        max_drawdown_pct: float = 0.20,
    ) -> list[RiskWarning]:
        """
        检查买入信号执行后的集中度风险 + 账户回撤风险（SDD §10.2）：

        单股集中度（BLOCK）：执行该信号后该标的占比 > max_single_stock_pct
        行业集中度（BLOCK）：执行该信号后同行业合计占比 > max_industry_pct
        账户回撤（WARN）：account_max_drawdown_pct 非 None 且 > max_drawdown_pct
        """
        if max_single_stock_pct is None:
            max_single_stock_pct = self._limits.max_single_stock_pct
        if max_industry_pct is None:
            max_industry_pct = self._limits.max_industry_pct
        warnings: list[RiskWarning] = []
        total = account_total_assets if account_total_assets > 0 else 1.0

        # 构建当前持仓占比 map
        position_pct: dict[str, float] = {}
        for p in current_positions:
            mv = float(p.market_value) if p.market_value is not None else 0.0
            position_pct[p.ts_code] = mv / total

        # 构建行业持仓占比 map（行业 → 合计占比）
        industry_map: dict[str, str] = {}
        if not stock_industry.empty and "sw_industry_l1" in stock_industry.columns:
            for ts_code in stock_industry.index:
                industry_map[ts_code] = stock_industry.loc[ts_code, "sw_industry_l1"]

        industry_pct: dict[str, float] = {}
        for ts_code, pct in position_pct.items():
            ind = industry_map.get(ts_code)
            if ind:
                industry_pct[ind] = industry_pct.get(ind, 0.0) + pct

        # 逐信号检查集中度风险（仅 BUY 信号）
        for sig in signals:
            if sig.signal_type != "BUY":
                continue

            # suggested_pct=None 表示"建议买入但资金不足，不可执行"，跳过集中度检查
            # 避免对不可执行信号产生误报 BLOCK，导致信号从列表中被移除
            suggested = sig.suggested_pct
            if not suggested:
                continue

            # 单股集中度
            new_stock_pct = position_pct.get(sig.ts_code, 0.0) + suggested
            if new_stock_pct > max_single_stock_pct:
                warnings.append(RiskWarning(
                    ts_code=sig.ts_code,
                    warning_type="CONCENTRATION_STOCK",
                    message=(
                        f"买入后 {sig.ts_code} 占总资产 {new_stock_pct:.1%}，"
                        f"超过单股上限 {max_single_stock_pct:.1%}"
                    ),
                    severity="BLOCK",
                ))

            # 行业集中度
            ind = industry_map.get(sig.ts_code)
            if ind:
                new_ind_pct = industry_pct.get(ind, 0.0) + suggested
                if new_ind_pct > max_industry_pct:
                    warnings.append(RiskWarning(
                        ts_code=sig.ts_code,
                        warning_type="CONCENTRATION_INDUSTRY",
                        message=(
                            f"买入后 {ind} 行业占比 {new_ind_pct:.1%}，"
                            f"超过行业上限 {max_industry_pct:.1%}"
                        ),
                        severity="BLOCK",
                    ))

        # 账户回撤检查（WARN）
        if account_max_drawdown_pct is not None and account_max_drawdown_pct > max_drawdown_pct:
            warnings.append(RiskWarning(
                ts_code="ACCOUNT",
                warning_type="DRAWDOWN",
                message=(
                    f"账户最大回撤 {account_max_drawdown_pct:.1%} 超过警戒线 {max_drawdown_pct:.1%}"
                ),
                severity="WARN",
            ))

        return warnings
