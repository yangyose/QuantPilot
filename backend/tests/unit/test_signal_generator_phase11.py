"""Phase 11 §5 SignalGenerator 5 trigger_reason 单元测试 SIG-P11-01~07。

覆盖：
- pct_below_buy（新分位 BUY 主路径，STRONG / MODERATE）
- pct_above_sell（持仓评分跌出阈值）
- hard_stop_loss（持仓硬止损，优先级低于 pct_above_sell）
- short_term_z_drop（核心策略 z 降幅 > 1.5σ）
- mid_term_icir_flip（核心策略 ICIR 月度由正转负）
- enable_absolute_threshold_override=True → 回 V1.0-r5 绝对阈值
- composite_pct_in_market 缺失 → 回 V1.0-r5 绝对阈值
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import pytest

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.signal import RiskParams, SignalGenerator


@dataclass
class _MockPosition:
    """简化版 Position 持仓（仅含 SignalGenerator 读取的字段）。"""

    ts_code: str
    pnl_pct: float = 0.0
    cost_price: float = 10.0


def _make_quotes(ts_codes: list[str], avg_amount: float = 1e8) -> pd.DataFrame:
    rows = []
    for tc in ts_codes:
        rows.append({
            "ts_code": tc,
            "close": 10.0,
            "avg_amount": avg_amount,
            "is_suspended": False,
            "limit_up": False,
            "sw_industry_l1": "TECH",
        })
    df = pd.DataFrame(rows).set_index("ts_code")
    return df


# ============================================================
# SIG-P11-01：pct_below_buy（新主路径）→ STRONG / MODERATE
# ============================================================
def test_sig_p11_01_pct_below_buy_strong_moderate() -> None:
    composite = pd.DataFrame(
        {
            "composite_score": [70.0, 60.0, 50.0],
            "composite_pct_in_market": [0.005, 0.03, 0.10],  # STRONG / MODERATE / 未触发
            "composite_z": [2.5, 1.8, 0.5],
            "weights_source": ["icir", "icir", "icir"],
        },
        index=pd.Index(["S1.SZ", "S2.SZ", "S3.SZ"], name="ts_code"),
    )
    quotes = _make_quotes(["S1.SZ", "S2.SZ", "S3.SZ"])
    gen = SignalGenerator()
    sigs = gen.generate(
        composite_scores=composite,
        current_positions=[],
        market_state=MarketStateEnum.UPTREND,
        snapshot_quotes=quotes,
        trade_date=date(2026, 1, 5),
        risk_params=RiskParams(buy_pct_threshold=0.05, strong_pct_threshold=0.01),
    )
    by_code = {s.ts_code: s for s in sigs}
    # S1: pct=0.005 ≤ 0.01 → STRONG
    assert "S1.SZ" in by_code
    assert by_code["S1.SZ"].signal_strength == "STRONG"
    assert by_code["S1.SZ"].trigger_reason == "pct_below_buy"
    assert by_code["S1.SZ"].composite_pct_in_market == pytest.approx(0.005)
    assert by_code["S1.SZ"].weights_source == "icir"
    # S2: pct=0.03 ≤ 0.05 → MODERATE
    assert "S2.SZ" in by_code
    assert by_code["S2.SZ"].signal_strength == "MODERATE"
    assert by_code["S2.SZ"].trigger_reason == "pct_below_buy"
    # S3: pct=0.10 > 0.05 → 未触发
    assert "S3.SZ" not in by_code


# ============================================================
# SIG-P11-02：pct_above_sell（持仓评分跌出）
# ============================================================
def test_sig_p11_02_pct_above_sell() -> None:
    composite = pd.DataFrame(
        {
            "composite_score": [30.0],
            "composite_pct_in_market": [0.80],  # ≥ 0.70 → SELL
            "composite_z": [-0.8],
            "weights_source": ["icir"],
        },
        index=pd.Index(["H1.SZ"], name="ts_code"),
    )
    quotes = _make_quotes(["H1.SZ"])
    gen = SignalGenerator()
    sigs = gen.generate(
        composite_scores=composite,
        current_positions=[_MockPosition(ts_code="H1.SZ", pnl_pct=0.02)],
        market_state=MarketStateEnum.OSCILLATION,
        snapshot_quotes=quotes,
        trade_date=date(2026, 1, 5),
        risk_params=RiskParams(sell_pct_threshold=0.70),
    )
    assert len(sigs) == 1
    assert sigs[0].signal_type == "SELL"
    assert sigs[0].trigger_reason == "pct_above_sell"


# ============================================================
# SIG-P11-03：hard_stop_loss 优先级（pnl ≤ -8% 时即便 pct 未跌出仍 SELL）
# ============================================================
def test_sig_p11_03_hard_stop_loss_priority() -> None:
    composite = pd.DataFrame(
        {
            "composite_score": [55.0],
            "composite_pct_in_market": [0.30],  # 未跌出 SELL 阈值
            "composite_z": [0.5],
            "weights_source": ["icir"],
        },
        index=pd.Index(["H2.SZ"], name="ts_code"),
    )
    quotes = _make_quotes(["H2.SZ"])
    gen = SignalGenerator()
    sigs = gen.generate(
        composite_scores=composite,
        current_positions=[_MockPosition(ts_code="H2.SZ", pnl_pct=-0.09)],  # 浮亏 9% > 8%
        market_state=MarketStateEnum.OSCILLATION,
        snapshot_quotes=quotes,
        trade_date=date(2026, 1, 5),
        risk_params=RiskParams(sell_pct_threshold=0.70, stop_loss_pct=0.08),
    )
    assert len(sigs) == 1
    assert sigs[0].signal_type == "SELL"
    assert sigs[0].trigger_reason == "hard_stop_loss"


# ============================================================
# SIG-P11-04：short_term_z_drop（核心策略 z 降幅 > 1.5σ）
# ============================================================
def test_sig_p11_04_short_term_z_drop() -> None:
    composite = pd.DataFrame(
        {
            "composite_score": [55.0],
            "composite_pct_in_market": [0.30],  # 未触发 pct_above_sell
            "composite_z": [0.5],
            "weights_source": ["icir"],
        },
        index=pd.Index(["H3.SZ"], name="ts_code"),
    )
    quotes = _make_quotes(["H3.SZ"])
    gen = SignalGenerator()
    sigs = gen.generate(
        composite_scores=composite,
        current_positions=[_MockPosition(ts_code="H3.SZ", pnl_pct=0.03)],  # 未硬止损
        market_state=MarketStateEnum.OSCILLATION,
        snapshot_quotes=quotes,
        trade_date=date(2026, 1, 5),
        risk_params=RiskParams(
            sell_pct_threshold=0.70, stop_loss_pct=0.08, short_term_failure_sigma=1.5,
        ),
        holding_signal_states={
            "H3.SZ": {"short_term_z_drop_value": 1.8},  # > 1.5σ
        },
    )
    assert len(sigs) == 1
    assert sigs[0].trigger_reason == "short_term_z_drop"


# ============================================================
# SIG-P11-05：mid_term_icir_flip（核心策略 ICIR 由正转负）
# ============================================================
def test_sig_p11_05_mid_term_icir_flip() -> None:
    composite = pd.DataFrame(
        {
            "composite_score": [55.0],
            "composite_pct_in_market": [0.30],
            "composite_z": [0.5],
            "weights_source": ["icir"],
        },
        index=pd.Index(["H4.SZ"], name="ts_code"),
    )
    quotes = _make_quotes(["H4.SZ"])
    gen = SignalGenerator()
    sigs = gen.generate(
        composite_scores=composite,
        current_positions=[_MockPosition(ts_code="H4.SZ", pnl_pct=0.03)],
        market_state=MarketStateEnum.OSCILLATION,
        snapshot_quotes=quotes,
        trade_date=date(2026, 1, 5),
        risk_params=RiskParams(
            sell_pct_threshold=0.70, stop_loss_pct=0.08, short_term_failure_sigma=1.5,
        ),
        holding_signal_states={
            "H4.SZ": {
                "short_term_z_drop_value": 0.3,  # 不触发
                "mid_term_icir_flipped": True,
            },
        },
    )
    assert len(sigs) == 1
    assert sigs[0].trigger_reason == "mid_term_icir_flip"


# ============================================================
# SIG-P11-06：enable_absolute_threshold_override=True → 回 V1.0-r5
# ============================================================
def test_sig_p11_06_absolute_threshold_override() -> None:
    composite = pd.DataFrame(
        {
            "composite_score": [85.0, 70.0],   # 85>80 → BUY; 70<80 → 不 BUY
            "composite_pct_in_market": [0.50, 0.50],  # 旧路径忽略 pct
            "composite_z": [0.0, 0.0],
            "weights_source": ["icir", "icir"],
        },
        index=pd.Index(["O1.SZ", "O2.SZ"], name="ts_code"),
    )
    quotes = _make_quotes(["O1.SZ", "O2.SZ"])
    gen = SignalGenerator()
    sigs = gen.generate(
        composite_scores=composite,
        current_positions=[],
        market_state=MarketStateEnum.OSCILLATION,
        snapshot_quotes=quotes,
        trade_date=date(2026, 1, 5),
        risk_params=RiskParams(enable_absolute_threshold_override=True, buy_threshold=80.0),
    )
    by_code = {s.ts_code: s for s in sigs}
    assert "O1.SZ" in by_code and by_code["O1.SZ"].signal_type == "BUY"
    assert "O2.SZ" not in by_code


# ============================================================
# SIG-P11-07：composite_pct_in_market 列缺失 → 自动 fallback V1.0-r5
# ============================================================
def test_sig_p11_07_pct_column_missing_falls_back() -> None:
    # 旧 candidate_pool 行：仅 composite_score 列
    composite = pd.DataFrame(
        {"composite_score": [85.0, 70.0]},
        index=pd.Index(["F1.SZ", "F2.SZ"], name="ts_code"),
    )
    quotes = _make_quotes(["F1.SZ", "F2.SZ"])
    gen = SignalGenerator()
    sigs = gen.generate(
        composite_scores=composite,
        current_positions=[],
        market_state=MarketStateEnum.OSCILLATION,
        snapshot_quotes=quotes,
        trade_date=date(2026, 1, 5),
        risk_params=RiskParams(buy_threshold=80.0),
    )
    by_code = {s.ts_code: s for s in sigs}
    # F1>80 → BUY；F2<80 → 无；composite_pct_in_market 字段为 None
    assert "F1.SZ" in by_code
    assert by_code["F1.SZ"].trigger_reason == "pct_below_buy"  # 仍标记新 trigger_reason
    assert by_code["F1.SZ"].composite_pct_in_market is None
    assert "F2.SZ" not in by_code
