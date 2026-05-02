"""unit/test_engine_config_injection.py: Phase 10 Engine dataclass 注入核查。

验证每个 Engine 类构造时都能正确接受/存储 config_defaults dataclass，
并暴露旧属性以兼容现有代码路径。
"""
from __future__ import annotations

from quantpilot.core.config_defaults import (
    DEFAULT_MARKET_STATE,
    DEFAULT_MEAN_REVERSION_STRATEGY,
    DEFAULT_MOMENTUM_STRATEGY,
    DEFAULT_RISK_LIMITS,
    DEFAULT_SIGNAL_CONFIG,
    DEFAULT_STRATEGY_WEIGHTS,
    DEFAULT_TREND_STRATEGY,
    DEFAULT_UNIVERSE,
    DEFAULT_VALUE_STRATEGY,
    MarketStateConfig,
    MeanReversionStrategyConfig,
    MomentumStrategyConfig,
    RiskLimitsConfig,
    SignalConfig,
    StrategyWeightsConfig,
    TrendStrategyConfig,
    UniverseConfig,
    ValueStrategyConfig,
)
from quantpilot.engine.market_state import MarketStateEngine
from quantpilot.engine.pool import CandidatePoolManager
from quantpilot.engine.risk import RiskChecker
from quantpilot.engine.scorer import Scorer
from quantpilot.engine.signal import SignalGenerator
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.engine.strategies.trend import TrendStrategy
from quantpilot.engine.strategies.value import ValueStrategy
from quantpilot.engine.universe import UniverseFilter


class TestDefaultConstruction:
    """无参构造：所有 Engine 类应使用 DEFAULT_* dataclass，等价于 Phase 10 前的硬编码行为。"""

    def test_scorer_default(self) -> None:
        s = Scorer()
        assert s._weights is DEFAULT_STRATEGY_WEIGHTS

    def test_market_state_default(self) -> None:
        e = MarketStateEngine()
        assert e.ma_short == DEFAULT_MARKET_STATE.ma_short
        assert e.ma_long == DEFAULT_MARKET_STATE.ma_long
        assert e.adx_period == DEFAULT_MARKET_STATE.adx_period
        assert e.adx_threshold == DEFAULT_MARKET_STATE.adx_threshold
        assert e.debounce_days == DEFAULT_MARKET_STATE.debounce_days

    def test_pool_default(self) -> None:
        p = CandidatePoolManager()
        assert p.pool_capacity == DEFAULT_UNIVERSE.pool_capacity

    def test_universe_default(self) -> None:
        u = UniverseFilter()
        assert u._cfg is DEFAULT_UNIVERSE

    def test_risk_default(self) -> None:
        r = RiskChecker()
        assert r._limits is DEFAULT_RISK_LIMITS

    def test_signal_default(self) -> None:
        sg = SignalGenerator()
        assert sg._signal_cfg is DEFAULT_SIGNAL_CONFIG
        assert sg._universe_cfg is DEFAULT_UNIVERSE

    def test_strategies_default(self) -> None:
        assert TrendStrategy()._cfg is DEFAULT_TREND_STRATEGY
        assert MomentumStrategy()._cfg is DEFAULT_MOMENTUM_STRATEGY
        assert MeanReversionStrategy()._cfg is DEFAULT_MEAN_REVERSION_STRATEGY
        assert ValueStrategy()._cfg is DEFAULT_VALUE_STRATEGY


class TestCustomInjection:
    """自定义注入：dataclass 非默认时，属性与行为应匹配注入值。"""

    def test_scorer_custom_weights(self) -> None:
        custom = StrategyWeightsConfig(
            uptrend={"trend": 0.50, "momentum": 0.20, "mean_reversion": 0.10, "value": 0.20},
            downtrend=DEFAULT_STRATEGY_WEIGHTS.downtrend,
            oscillation=DEFAULT_STRATEGY_WEIGHTS.oscillation,
        )
        s = Scorer(weights=custom)
        matrix = s._matrix()
        from quantpilot.engine.market_state import MarketStateEnum
        assert matrix[MarketStateEnum.UPTREND]["trend"] == 0.50

    def test_market_state_custom(self) -> None:
        cfg = MarketStateConfig(
            ma_short=5, ma_long=30, adx_period=10, adx_threshold=22.0, debounce_days=2
        )
        e = MarketStateEngine(config=cfg)
        assert e.ma_short == 5
        assert e.ma_long == 30
        assert e.debounce_days == 2

    def test_market_state_legacy_kwargs_compat(self) -> None:
        """旧版 ma_short= 关键字入参继续工作（兼容 Phase 3 测试）。"""
        e = MarketStateEngine(ma_short=10, ma_long=50)
        assert e.ma_short == 10
        assert e.ma_long == 50

    def test_pool_custom(self) -> None:
        cfg = UniverseConfig(
            min_liquidity_amount=1e6,
            new_stock_days=30,
            pool_capacity=50,
            signal_expiry_days=5,
        )
        p = CandidatePoolManager(config=cfg)
        assert p.pool_capacity == 50

    def test_pool_legacy_kwarg(self) -> None:
        """旧版 pool_capacity= 关键字入参继续工作（兼容 Phase 4 测试）。"""
        p = CandidatePoolManager(pool_capacity=7)
        assert p.pool_capacity == 7

    def test_universe_custom(self) -> None:
        cfg = UniverseConfig(
            min_liquidity_amount=1e7,
            new_stock_days=90,
            pool_capacity=30,
            signal_expiry_days=7,
        )
        u = UniverseFilter(config=cfg)
        assert u._cfg.min_liquidity_amount == 1e7

    def test_risk_custom(self) -> None:
        limits = RiskLimitsConfig(
            max_single_stock_pct=0.15,
            max_industry_pct=0.25,
            max_total_position_pct=0.70,
            single_trade_pct=0.05,
        )
        r = RiskChecker(risk_limits=limits)
        assert r._limits.max_single_stock_pct == 0.15

    def test_signal_custom(self) -> None:
        scfg = SignalConfig(buy_threshold=85.0, sell_threshold=35.0)
        ucfg = UniverseConfig(min_liquidity_amount=1e7)
        sg = SignalGenerator(signal_cfg=scfg, universe_cfg=ucfg)
        rp = sg._default_risk_params()
        assert rp.buy_threshold == 85.0
        assert rp.sell_threshold == 35.0
        assert rp.min_liquidity_amount == 1e7

    def test_strategies_custom(self) -> None:
        t = TrendStrategy(config=TrendStrategyConfig(ma_short=5))
        assert t._cfg.ma_short == 5

        m = MomentumStrategy(config=MomentumStrategyConfig(lookback_short=90))
        assert m._cfg.lookback_short == 90

        mr = MeanReversionStrategy(
            config=MeanReversionStrategyConfig(rsi_period=7)
        )
        assert mr._cfg.rsi_period == 7

        v = ValueStrategy(config=ValueStrategyConfig(pe_pb_history_years=3))
        assert v._cfg.pe_pb_history_years == 3
