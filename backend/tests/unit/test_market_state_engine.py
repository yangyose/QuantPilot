"""MSE-01~10: MarketStateEngine 单元测试（纯函数，无 DB）"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantpilot.engine.market_state import MarketStateEngine, MarketStateEnum, MarketStateRecord


def _make_ohlcv(n: int, close_values: list[float]) -> pd.DataFrame:
    """生成 n 行合成 OHLCV，high=close*1.01, low=close*0.99"""
    assert len(close_values) == n
    start = date(2024, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n)]
    return pd.DataFrame(
        {
            "high": [c * 1.01 for c in close_values],
            "low": [c * 0.99 for c in close_values],
            "close": close_values,
        },
        index=dates,
    )


@pytest.fixture
def engine() -> MarketStateEngine:
    return MarketStateEngine()


# ── MSE-01~04：determine_raw_state ────────────────────────────────────────────

def test_mse_01_determine_raw_state_uptrend(engine: MarketStateEngine) -> None:
    """ADX=30, MA20=3100, MA60=3000, close=3150（>MA20）→ UPTREND"""
    result = engine.determine_raw_state(adx=30.0, ma20=3100.0, ma60=3000.0, close=3150.0)
    assert result == MarketStateEnum.UPTREND


def test_mse_02_determine_raw_state_downtrend(engine: MarketStateEngine) -> None:
    """ADX=30, MA20=2900, MA60=3000, close=2880 → DOWNTREND"""
    result = engine.determine_raw_state(adx=30.0, ma20=2900.0, ma60=3000.0, close=2880.0)
    assert result == MarketStateEnum.DOWNTREND


def test_mse_03_determine_raw_state_oscillation_low_adx(engine: MarketStateEngine) -> None:
    """ADX=20（≤25），MA20>MA60 → OSCILLATION（ADX 不足优先）"""
    result = engine.determine_raw_state(adx=20.0, ma20=3100.0, ma60=3000.0, close=3050.0)
    assert result == MarketStateEnum.OSCILLATION


def test_mse_04_determine_raw_state_oscillation_mixed_signals(engine: MarketStateEngine) -> None:
    """ADX=30, MA20>MA60 but close<MA20 → OSCILLATION（均线方向混乱）"""
    result = engine.determine_raw_state(adx=30.0, ma20=3100.0, ma60=3000.0, close=3050.0)
    # close=3050 < MA20=3100，不满足上涨条件
    assert result == MarketStateEnum.OSCILLATION


# ── MSE-05~08：apply_debounce ─────────────────────────────────────────────────

def _make_raw_series(states: list[MarketStateEnum]) -> pd.Series:
    start = date(2024, 1, 1)
    dates = [start + timedelta(days=i) for i in range(len(states))]
    return pd.Series(states, index=dates)


def test_mse_05_debounce_one_day_no_switch(engine: MarketStateEngine) -> None:
    """prev=OSCILLATION, raw=[OSC, OSC, UP] → 最后 1 日仍为 OSCILLATION（只有 1 天 UP）"""
    raw = _make_raw_series([
        MarketStateEnum.OSCILLATION,
        MarketStateEnum.OSCILLATION,
        MarketStateEnum.UPTREND,
    ])
    confirmed = engine.apply_debounce(raw, prev_confirmed=MarketStateEnum.OSCILLATION)
    assert confirmed.iloc[-1] == MarketStateEnum.OSCILLATION


def test_mse_06_debounce_two_days_no_switch(engine: MarketStateEngine) -> None:
    """prev=OSCILLATION, raw=[UP, UP] → 最后仍为 OSCILLATION（连续 2 天不足 3）"""
    raw = _make_raw_series([MarketStateEnum.UPTREND, MarketStateEnum.UPTREND])
    confirmed = engine.apply_debounce(raw, prev_confirmed=MarketStateEnum.OSCILLATION)
    assert all(s == MarketStateEnum.OSCILLATION for s in confirmed)


def test_mse_07_debounce_three_days_switch(engine: MarketStateEngine) -> None:
    """prev=OSCILLATION, raw=[UP, UP, UP] → 第 3 日切换为 UPTREND"""
    raw = _make_raw_series([
        MarketStateEnum.UPTREND,
        MarketStateEnum.UPTREND,
        MarketStateEnum.UPTREND,
    ])
    confirmed = engine.apply_debounce(raw, prev_confirmed=MarketStateEnum.OSCILLATION)
    assert confirmed.iloc[0] == MarketStateEnum.OSCILLATION
    assert confirmed.iloc[1] == MarketStateEnum.OSCILLATION
    assert confirmed.iloc[2] == MarketStateEnum.UPTREND


def test_mse_08_debounce_interrupted_recount(engine: MarketStateEngine) -> None:
    """raw=[UP, DOWN, UP, UP, UP] → 最终确认 UPTREND（中断后从第3个UP起数）"""
    raw = _make_raw_series([
        MarketStateEnum.UPTREND,
        MarketStateEnum.DOWNTREND,
        MarketStateEnum.UPTREND,
        MarketStateEnum.UPTREND,
        MarketStateEnum.UPTREND,
    ])
    confirmed = engine.apply_debounce(raw, prev_confirmed=MarketStateEnum.OSCILLATION)
    # 前 4 天未满足连续 3 天 UP，最后一天才满足
    assert confirmed.iloc[-1] == MarketStateEnum.UPTREND
    # 第 1 天：只有 1 天 UP → 仍为 OSCILLATION
    assert confirmed.iloc[0] == MarketStateEnum.OSCILLATION


# ── MSE-09：compute_indicators ────────────────────────────────────────────────

def test_mse_09_compute_indicators_valid(engine: MarketStateEngine) -> None:
    """70 行合成数据，最后 1 行 ma20/ma60/adx 均非 NaN；ma20 值与手算一致"""
    close_values = [3000.0 + i * 5 for i in range(70)]
    ohlcv = _make_ohlcv(70, close_values)
    result = engine.compute_indicators(ohlcv)

    # 最后一行所有指标非 NaN
    last = result.iloc[-1]
    assert pd.notna(last["ma20"])
    assert pd.notna(last["ma60"])
    assert pd.notna(last["adx"])

    # ma20 手算（最后 20 行平均）
    expected_ma20 = sum(close_values[-20:]) / 20
    assert abs(last["ma20"] - expected_ma20) < 0.01

    # 前 59 行的 ma60 应为 NaN
    assert pd.isna(result.iloc[58]["ma60"])
    assert pd.notna(result.iloc[59]["ma60"])


# ── MSE-10：identify_latest state_changed ────────────────────────────────────

def test_mse_10_identify_latest_state_changed(engine: MarketStateEngine) -> None:
    """前日 confirmed=OSCILLATION，今日首次确认 UPTREND → state_changed=True"""
    # 构造 100 行稳定上涨数据（保证触发 UPTREND）
    # close 持续高于 MA20 > MA60，ADX 应足够高（趋势明显）
    # 使用指数级别的数据确保 MA20 > MA60 且 close > MA20
    n = 100
    # 平稳上涨：起始 3000，每日 +20（足够大使趋势明确）
    close_values = [3000.0 + i * 20 for i in range(n)]
    ohlcv = _make_ohlcv(n, close_values)

    records = engine.identify(ohlcv, prev_confirmed=MarketStateEnum.OSCILLATION)
    assert len(records) > 0

    # 找到第一个状态切换
    changed_records = [r for r in records if r.state_changed]
    assert len(changed_records) > 0, "应当存在至少一次状态切换"

    # 验证切换时 market_state 与 prev 不同
    first_change = changed_records[0]
    assert first_change.market_state != MarketStateEnum.OSCILLATION

    # identify_latest 返回最后一条
    latest = engine.identify_latest(ohlcv, prev_confirmed=MarketStateEnum.OSCILLATION)
    assert latest is not None
    assert isinstance(latest, MarketStateRecord)
    assert latest.trade_date == list(ohlcv.index)[-1]


# ── V1.5-A A3（SDD-EXT-07）：NH-NL 市场宽度 → breadth_weak ────────────────────


def test_mse_a3_compute_breadth_weak_pure() -> None:
    """纯函数 compute_breadth_weak：仅 UPTREND 且 NH-NL ≤ 0 时 True。"""
    from quantpilot.engine.market_state import MarketStateEngine as _E

    # UPTREND：NH-NL > 0 → False；≤ 0 → True
    assert _E.compute_breadth_weak(MarketStateEnum.UPTREND, 0.05) is False
    assert _E.compute_breadth_weak(MarketStateEnum.UPTREND, 0.0) is True
    assert _E.compute_breadth_weak(MarketStateEnum.UPTREND, -0.1) is True
    # 非 UPTREND：规则不适用，恒 False（即便 NH-NL ≤ 0）
    assert _E.compute_breadth_weak(MarketStateEnum.OSCILLATION, -0.5) is False
    assert _E.compute_breadth_weak(MarketStateEnum.DOWNTREND, -0.5) is False
    # NH-NL 缺失（None）→ False（数据不足不误降级）
    assert _E.compute_breadth_weak(MarketStateEnum.UPTREND, None) is False


def test_mse_a3_record_breadth_weak_defaults_false() -> None:
    """MarketStateRecord.breadth_weak 默认 False（既有构造不破）。"""
    rec = MarketStateRecord(
        trade_date=date(2026, 5, 20),
        market_state=MarketStateEnum.UPTREND,
        trend_strength=30.0, adx_value=30.0, ma20=10.0, ma60=9.0,
        state_changed=False, description="x",
    )
    assert rec.breadth_weak is False


def test_mse_a3_identify_sets_breadth_weak_from_nh_nl() -> None:
    """identify(nh_nl_series=)：UPTREND 日 NH-NL ≤ 0 → breadth_weak=True，> 0 → False。"""
    n = 100
    close_values = [3000.0 + i * 20 for i in range(n)]
    ohlcv = _make_ohlcv(n, close_values)
    engine = MarketStateEngine()

    # 无 nh_nl_series → 全 False（向后兼容）
    base = engine.identify(ohlcv, prev_confirmed=MarketStateEnum.OSCILLATION)
    up_records = [r for r in base if r.market_state == MarketStateEnum.UPTREND]
    assert len(up_records) > 0
    assert all(r.breadth_weak is False for r in base)

    # 全负 NH-NL → 所有 UPTREND 日 breadth_weak=True，非 UPTREND 日 False
    neg_series = pd.Series(-0.1, index=ohlcv.index)
    weak = engine.identify(
        ohlcv, prev_confirmed=MarketStateEnum.OSCILLATION, nh_nl_series=neg_series,
    )
    for r in weak:
        if r.market_state == MarketStateEnum.UPTREND:
            assert r.breadth_weak is True
        else:
            assert r.breadth_weak is False

    # 全正 NH-NL → 无 breadth_weak
    pos_series = pd.Series(0.2, index=ohlcv.index)
    healthy = engine.identify(
        ohlcv, prev_confirmed=MarketStateEnum.OSCILLATION, nh_nl_series=pos_series,
    )
    assert all(r.breadth_weak is False for r in healthy)


def test_mse_a3_compute_nh_nl_diff() -> None:
    """compute_nh_nl_diff：末日创新高/新低家数差 / 有效标的数。"""
    import numpy as np

    dates = pd.date_range("2024-01-01", periods=60, freq="D")
    # A: 单调上涨 → 末日创 60 日新高；B: 单调下跌 → 末日创新低；
    # C: 平盘后末日既非最高也非最低（中间值）。
    up = np.linspace(10, 20, 60)
    down = np.linspace(20, 10, 60)
    mid = np.concatenate([np.linspace(10, 30, 59), [20.0]])  # 末日 20 < max 30, > min 10
    close_wide = pd.DataFrame({"A": up, "B": down, "C": mid}, index=dates)

    diff = MarketStateEngine.compute_nh_nl_diff(close_wide)
    # NH=1(A), NL=1(B), C 既非高非低 → (1-1)/3 = 0.0
    assert diff == 0.0

    # 全体上涨 → 全部新高 → diff = 1.0
    all_up = pd.DataFrame(
        {c: np.linspace(10, 20, 60) for c in ("A", "B", "C")}, index=dates
    )
    assert MarketStateEngine.compute_nh_nl_diff(all_up) == 1.0


def test_mse_a3_compute_nh_nl_diff_insufficient() -> None:
    """行数 < lookback → None（数据不足不误判）。"""
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    close_wide = pd.DataFrame({"A": range(30)}, index=dates)
    assert MarketStateEngine.compute_nh_nl_diff(close_wide, lookback=60) is None
    assert MarketStateEngine.compute_nh_nl_diff(pd.DataFrame()) is None
