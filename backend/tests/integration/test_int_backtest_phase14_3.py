"""Phase 14 §14-3 INT-P14-3-01：BacktestService._load_data_bundle 真 DB 集成测试。

依据 docs/design/phases/phase14_account_integrity.md §5.3 DoD 量化阈值：
- 设计文档原 DoD：30 trade_date × ~2400 universe 回测，composite_z ±3.5σ + pipeline_mode
  real_5step 占比 ≥ 90%。该规模是真机验收，需 §14-2 5y 回填完成后跑。
- 本 INT 层验证关键 IO 契约：
  - INT-P14-3-01a：strategy_weights_history 多策略多 state 多 trade_date 行 → 经
    _load_data_bundle 正确组装为 active_weights_history dict[(state, trade_date)]，
    含 weights/weights_source/orthogonalize_order (weight 降序)/hysteresis_status；
  - INT-P14-3-01b：daily_quote.float_mkt_cap 列在 _load_data_bundle 中被加载到
    bundle.daily_quotes 列，且非 None。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.engine.backtest.engine import BacktestConfig
from quantpilot.models.business import StrategyWeightsHistory
from quantpilot.models.market import DailyQuote, StockInfo
from quantpilot.services.backtest_service import BacktestService

_TS = ["000001.SZ", "000002.SZ", "000003.SZ"]


async def _seed_minimal(session: AsyncSession) -> None:
    """Seed 3 stocks + daily_quote 2 trade_dates + strategy_weights_history
    (3 state × 4 strategy × 1 trade_date = 12 行)。"""
    for c in _TS:
        session.add(StockInfo(
            ts_code=c, name=f"name_{c}", list_date=date(2015, 1, 1),
            delist_date=None, sw_industry_l1="银行", is_active=True,
        ))
    for d in (date(2024, 6, 3), date(2024, 6, 4)):
        for i, c in enumerate(_TS):
            session.add(DailyQuote(
                ts_code=c, trade_date=d,
                open=Decimal("10.0"), high=Decimal("11.0"),
                low=Decimal("9.5"), close=Decimal("10.5"),
                vol=Decimal("100000"), amount=Decimal("1050000.0"),
                adj_factor=Decimal("1.0"),
                is_suspended=False, is_st=False,
                limit_up=False, limit_down=False,
                float_mkt_cap=Decimal(f"{(i + 1) * 1.0e9}"),
            ))
    # strategy_weights_history：3 state × 4 strategy × 1 trade_date = 12 行
    weights_by_state = {
        "UPTREND":     {"trend": 0.40, "momentum": 0.30, "mean_reversion": 0.20, "value": 0.10},
        "DOWNTREND":   {"trend": 0.10, "momentum": 0.20, "mean_reversion": 0.30, "value": 0.40},
        "OSCILLATION": {"trend": 0.25, "momentum": 0.25, "mean_reversion": 0.25, "value": 0.25},
    }
    for state, w_map in weights_by_state.items():
        for strategy, weight in w_map.items():
            session.add(StrategyWeightsHistory(
                state=state, strategy=strategy, trade_date=date(2024, 6, 1),
                weight_used=Decimal(f"{weight:.4f}"),
                weights_source="icir",
                icir_inputs=None,
                hysteresis_status="stable",
            ))
    await session.flush()


async def test_int_p14_3_01a_load_data_bundle_assembles_active_weights_history(
    db_session: AsyncSession,
) -> None:
    """INT-P14-3-01a：_load_data_bundle 把 12 行 StrategyWeightsHistory 组装为 3 entries
    dict[(state, trade_date)]，每 entry 含 4 strategy weights + 同步 source/order/status。"""
    await _seed_minimal(db_session)

    service = BacktestService(session=db_session, engine=None)
    cfg = BacktestConfig(
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 4),
        initial_capital=1_000_000.0,
        strategy_config={},
        account_config={},
    )
    bundle = await service._load_data_bundle(cfg)

    # 3 entries（3 state × 1 trade_date）
    assert len(bundle.active_weights_history) == 3
    assert ("UPTREND", date(2024, 6, 1)) in bundle.active_weights_history
    assert ("DOWNTREND", date(2024, 6, 1)) in bundle.active_weights_history
    assert ("OSCILLATION", date(2024, 6, 1)) in bundle.active_weights_history

    up = bundle.active_weights_history[("UPTREND", date(2024, 6, 1))]
    assert set(up["weights"].keys()) == {"trend", "momentum", "mean_reversion", "value"}
    assert abs(up["weights"]["trend"] - 0.40) < 1e-9
    assert up["weights_source"] == "icir"
    assert up["hysteresis_status"] == "stable"
    # orthogonalize_order：按 weight 降序（UPTREND: trend > momentum > mean_reversion > value）
    assert up["orthogonalize_order"] == [
        "trend", "momentum", "mean_reversion", "value",
    ]

    # DOWNTREND 顺序反过来
    dn = bundle.active_weights_history[("DOWNTREND", date(2024, 6, 1))]
    assert dn["orthogonalize_order"] == [
        "value", "mean_reversion", "momentum", "trend",
    ]


async def test_int_p14_3_01b_load_data_bundle_includes_float_mkt_cap(
    db_session: AsyncSession,
) -> None:
    """INT-P14-3-01b：daily_quote.float_mkt_cap 列被加载到 bundle.daily_quotes，
    且每条数据非 None。"""
    await _seed_minimal(db_session)

    service = BacktestService(session=db_session, engine=None)
    cfg = BacktestConfig(
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 4),
        initial_capital=1_000_000.0,
        strategy_config={},
        account_config={},
    )
    bundle = await service._load_data_bundle(cfg)

    assert "float_mkt_cap" in bundle.daily_quotes.columns
    # 全部行非 None（seed 时每行都赋值）
    assert bundle.daily_quotes["float_mkt_cap"].notna().all()
    # 数值合理（10 亿量级）
    assert bundle.daily_quotes["float_mkt_cap"].min() >= 1e9 - 1
    assert bundle.daily_quotes["float_mkt_cap"].max() <= 3e9 + 1
