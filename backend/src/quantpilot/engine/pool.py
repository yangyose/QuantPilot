"""CandidatePoolManager：持仓保护 + 白名单机制（Phase 4）。Engine 层纯函数，无 IO。"""
from __future__ import annotations

from dataclasses import dataclass

from quantpilot.core.config_defaults import DEFAULT_UNIVERSE, UniverseConfig
from quantpilot.engine.scorer import CompositeScore


@dataclass(frozen=True)
class PoolEntry:
    ts_code: str
    composite_score: float | None
    trend_score: float | None
    momentum_score: float | None
    reversion_score: float | None
    value_score: float | None
    market_state: str | None
    in_pool: bool
    is_holding: bool


class CandidatePoolManager:
    """SDD §8.2：持仓保护 + 白名单机制。纯函数，DB I/O 由 ScoringService 承接。

    Phase 10：`config` 参数注入自 `config_service.get_universe_params()`，
    `pool_capacity` 兼容入参保留（旧调用点 `CandidatePoolManager(pool_capacity=N)` 继续工作）。
    """

    def __init__(
        self,
        config: UniverseConfig | None = None,
        *,
        pool_capacity: int | None = None,
    ) -> None:
        cfg = config or UniverseConfig(
            min_liquidity_amount=DEFAULT_UNIVERSE.min_liquidity_amount,
            new_stock_days=DEFAULT_UNIVERSE.new_stock_days,
            pool_capacity=(
                pool_capacity
                if pool_capacity is not None
                else DEFAULT_UNIVERSE.pool_capacity
            ),
            signal_expiry_days=DEFAULT_UNIVERSE.signal_expiry_days,
        )
        self._cfg = cfg
        self.pool_capacity = cfg.pool_capacity

    def compute_pool(
        self,
        composite_scores: list[CompositeScore],
        holding_codes: frozenset[str] | set[str],
        whitelist_codes: frozenset[str] | set[str],
    ) -> list[PoolEntry]:
        """
        入池规则（SDD §8.2）：
        1. composite_score 排名前 pool_capacity 只
        2. 持仓保护：holding_codes 强制入池，is_holding=True
        3. 白名单：WHITELIST 标的额外入池
        返回 list[PoolEntry]，不执行任何 DB 操作。
        """
        scores_map = {s.ts_code: s for s in composite_scores}

        # 按综合分降序，取前 N
        sorted_codes = sorted(
            scores_map,
            key=lambda c: scores_map[c].composite_score,
            reverse=True,
        )
        pool_codes: set[str] = set(sorted_codes[: self.pool_capacity])
        pool_codes |= set(holding_codes)
        pool_codes |= set(whitelist_codes)

        result = []
        for ts_code in pool_codes:
            s = scores_map.get(ts_code)
            result.append(PoolEntry(
                ts_code=ts_code,
                composite_score=s.composite_score if s else None,
                trend_score=s.trend_score if s else None,
                momentum_score=s.momentum_score if s else None,
                reversion_score=s.reversion_score if s else None,
                value_score=s.value_score if s else None,
                market_state=s.market_state.value if s else None,
                in_pool=True,
                is_holding=(ts_code in holding_codes),
            ))
        return result
