"""PRV-01~04: AdjustedPriceProvider 纯函数层单元测试"""
import pandas as pd

from quantpilot.data.price_provider import AdjustedPriceProvider

# Day 1-2：除权前；Day 3 发生除权（adj_factor 降为 0.9）；Day 4-6：除权后
_IDX = pd.to_datetime(
    ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
)
CLOSE = pd.Series([10.0, 10.5, 9.0, 9.0, 9.5, 9.2], index=_IDX)
ADJ_FACTOR = pd.Series([1.0, 1.0, 0.9, 0.9, 0.9, 0.9], index=_IDX)


def test_prv_01_compute_backward() -> None:
    """PRV-01: 后复权 = close × adj_factor，精确到小数点后 6 位"""
    result = AdjustedPriceProvider._compute_backward(CLOSE, ADJ_FACTOR)
    expected = pd.Series([10.0, 10.5, 8.1, 8.1, 8.55, 8.28], index=_IDX)
    pd.testing.assert_series_equal(result, expected, atol=1e-6, check_names=False)


def test_prv_02_compute_forward_last_equals_close() -> None:
    """PRV-02: 前复权最新日 == 当前 close（以最新价为基准）"""
    result = AdjustedPriceProvider._compute_forward(CLOSE, ADJ_FACTOR)
    assert abs(result.iloc[-1] - CLOSE.iloc[-1]) < 1e-9


def test_prv_03_relative_return_consistent() -> None:
    """PRV-03: 不跨越除权日的连续段内，前/后复权相对涨跌幅一致"""
    bwd = AdjustedPriceProvider._compute_backward(CLOSE, ADJ_FACTOR)
    fwd = AdjustedPriceProvider._compute_forward(CLOSE, ADJ_FACTOR)

    # 除权前段 Day 1→2
    bwd_pre = (bwd.iloc[1] - bwd.iloc[0]) / bwd.iloc[0]
    fwd_pre = (fwd.iloc[1] - fwd.iloc[0]) / fwd.iloc[0]
    assert abs(bwd_pre - fwd_pre) < 1e-9

    # 除权后段 Day 4→6（下标 3→5）
    bwd_post = (bwd.iloc[5] - bwd.iloc[3]) / bwd.iloc[3]
    fwd_post = (fwd.iloc[5] - fwd.iloc[3]) / fwd.iloc[3]
    assert abs(bwd_post - fwd_post) < 1e-9


def test_prv_04_no_adj_factor_equals_close() -> None:
    """PRV-04: 无除权（adj_factor 全为 1.0），后复权 == 原始价格"""
    close = pd.Series([10.0, 10.5, 11.0])
    adj = pd.Series([1.0, 1.0, 1.0])
    result = AdjustedPriceProvider._compute_backward(close, adj)
    pd.testing.assert_series_equal(result, close, check_names=False)
