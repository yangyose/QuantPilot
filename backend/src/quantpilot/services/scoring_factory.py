"""ScoringService 默认组装工厂（V1.5-C C0）。

起因：`scripts/backfill_daily_ic.py`、`scripts/backfill_candidate_pool.py` 与生产
调度 Job 各自组装一份 `ScoringService`（策略列表 + Scorer + FactorPipeline 配置），
策略集合一旦变动（V1.5-C C3/C4 新增 low_volatility / money_flow）就必须同步改多处，
漏一处即"脚本与生产算的不是同一个东西"。本模块是该组装的**单一事实来源**。

Service 层（含 IO 依赖构造），不属 Engine 层无 IO 约束。
"""
from __future__ import annotations

from quantpilot.core.config_defaults import (
    DEFAULT_MEAN_REVERSION_STRATEGY,
    DEFAULT_MOMENTUM_STRATEGY,
    DEFAULT_SCORING_PIPELINE,
    DEFAULT_STRATEGY_WEIGHTS,
    DEFAULT_TREND_STRATEGY,
    DEFAULT_UNIVERSE,
    DEFAULT_VALUE_STRATEGY,
)
from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.factor_ic_repository import FactorICRepository
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.engine.factor_pipeline import FactorPipeline, FactorPipelineConfig
from quantpilot.engine.pool import CandidatePoolManager
from quantpilot.engine.scorer import Scorer
from quantpilot.engine.strategies.base import BaseStrategy
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.engine.strategies.trend import TrendStrategy
from quantpilot.engine.strategies.value import ValueStrategy
from quantpilot.engine.universe import UniverseFilter
from quantpilot.services.factor_monitor_service import FactorMonitorService
from quantpilot.services.strategy_service import ScoringService

__all__ = ["build_default_strategies", "build_default_scoring_service"]


def build_default_strategies() -> list[BaseStrategy]:
    """默认策略集合（default config）。

    V1.5-C C3/C4 新增策略时**只改这里**——脚本与生产 Job 自动同步。
    """
    return [
        TrendStrategy(DEFAULT_TREND_STRATEGY),
        MomentumStrategy(DEFAULT_MOMENTUM_STRATEGY),
        MeanReversionStrategy(DEFAULT_MEAN_REVERSION_STRATEGY),
        ValueStrategy(DEFAULT_VALUE_STRATEGY),
    ]


def build_default_scoring_service(
    session,
    calendar: TradingCalendar,
) -> ScoringService:
    """组装走 5 步管线的 ``ScoringService``（注入 ``FactorMonitorService``）。

    FactorPipeline 配置取 ``DEFAULT_SCORING_PIPELINE``（config_defaults 单一事实
    来源）而非就地字面量，避免默认值改动时此处静默脱节。
    """
    repo = MarketDataRepository(session)
    factor_monitor = FactorMonitorService(
        session, FactorMonitorEngine(), FactorICRepository(), calendar=calendar,
    )
    fp_cfg = FactorPipelineConfig(
        winsorize_lower_pct=DEFAULT_SCORING_PIPELINE.winsorize_lower_pct,
        winsorize_upper_pct=DEFAULT_SCORING_PIPELINE.winsorize_upper_pct,
        neutralize_industry=DEFAULT_SCORING_PIPELINE.neutralize_industry,
        neutralize_market_cap=DEFAULT_SCORING_PIPELINE.neutralize_market_cap,
        neutralize_beta=DEFAULT_SCORING_PIPELINE.neutralize_beta,
    )
    return ScoringService(
        repo=repo,
        universe_filter=UniverseFilter(DEFAULT_UNIVERSE),
        strategies=build_default_strategies(),
        scorer=Scorer(DEFAULT_STRATEGY_WEIGHTS, pipeline=FactorPipeline(fp_cfg)),
        pool_manager=CandidatePoolManager(DEFAULT_UNIVERSE),
        calendar=calendar,
        factor_monitor=factor_monitor,
    )
