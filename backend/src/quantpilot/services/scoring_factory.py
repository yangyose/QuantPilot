"""ScoringService 默认组装工厂（V1.5-C C0）。

起因：`scripts/backfill_daily_ic.py`、`scripts/backfill_candidate_pool.py` 与生产
调度 Job 各自组装一份 `ScoringService`（策略列表 + Scorer + FactorPipeline 配置），
策略集合一旦变动（V1.5-C C3/C4 新增 low_volatility / money_flow）就必须同步改多处，
漏一处即"脚本与生产算的不是同一个东西"。本模块是该组装的**单一事实来源**。

Service 层（含 IO 依赖构造），不属 Engine 层无 IO 约束。
"""
from __future__ import annotations

from dataclasses import replace

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


def build_default_strategies(
    momentum_risk_adjusted: bool | None = None,
) -> list[BaseStrategy]:
    """默认策略集合（default config）。

    V1.5-C C3/C4 新增策略时**只改这里**——脚本与生产 Job 自动同步。

    ``momentum_risk_adjusted``（V1.5-C C1-2）：仅供**离线对照跑**覆写
    ``MomentumStrategyConfig.risk_adjusted``，``None`` = 不覆写（生产路径）。
    C1 DoD 的 5y 面板对比要在同一份数据、同一条管线上跑两遍，只差这一个开关；
    做成入参而非手改 ``config_defaults.py``，是为了让命令行本身成为「这批 IC 行
    出自哪个配置」的出处记录——手改默认值忘了改回来会把对照组配置带进生产。
    用 ``dataclasses.replace`` 生成副本，不就地改模块级单例。
    """
    momentum_cfg = DEFAULT_MOMENTUM_STRATEGY
    if momentum_risk_adjusted is not None:
        momentum_cfg = replace(momentum_cfg, risk_adjusted=momentum_risk_adjusted)
    return [
        TrendStrategy(DEFAULT_TREND_STRATEGY),
        MomentumStrategy(momentum_cfg),
        MeanReversionStrategy(DEFAULT_MEAN_REVERSION_STRATEGY),
        ValueStrategy(DEFAULT_VALUE_STRATEGY),
    ]


def build_default_scoring_service(
    session,
    calendar: TradingCalendar,
    strategies: list[BaseStrategy] | None = None,
) -> ScoringService:
    """组装走 5 步管线的 ``ScoringService``（注入 ``FactorMonitorService``）。

    FactorPipeline 配置取 ``DEFAULT_SCORING_PIPELINE``（config_defaults 单一事实
    来源）而非就地字面量，避免默认值改动时此处静默脱节。

    ``strategies``（V1.5-C C1-2）：离线对照跑传入 ``build_default_strategies(...)``
    的覆写结果；``None`` = 默认集合（生产路径）。
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
        strategies=strategies if strategies is not None else build_default_strategies(),
        scorer=Scorer(DEFAULT_STRATEGY_WEIGHTS, pipeline=FactorPipeline(fp_cfg)),
        pool_manager=CandidatePoolManager(DEFAULT_UNIVERSE),
        calendar=calendar,
        factor_monitor=factor_monitor,
    )
