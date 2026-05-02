"""VAL-01~05: DataValidator 单元测试"""
from datetime import date

import pandas as pd
import pytest

from quantpilot.data.validators import DataValidator


@pytest.fixture
def validator() -> DataValidator:
    return DataValidator()


def _make_quotes_df(n: int = 10, inject_price_error: bool = False) -> pd.DataFrame:
    """构造日线测试 DataFrame，inject_price_error=True 时注入一行 low > close 异常"""
    lows = [9.0] * n
    if inject_price_error:
        lows[0] = 12.0  # low=12 > close=10.5 → 价格异常
    return pd.DataFrame(
        {
            "ts_code": [f"{i:06d}.SZ" for i in range(n)],
            "trade_date": [date(2026, 1, 2)] * n,
            "open": [10.0] * n,
            "high": [11.0] * n,
            "low": lows,
            "close": [10.5] * n,
            "vol": [100_000] * n,
            "adj_factor": [1.0] * n,
        }
    )


def test_val_01_normal_daily_quotes(validator: DataValidator) -> None:
    """VAL-01: 正常日线数据 → is_valid=True，无 errors"""
    df = _make_quotes_df(n=10)
    result = validator.validate_daily_quotes(df, prev_count=10)
    assert result.is_valid is True
    assert result.errors == []


def test_val_02_price_invalid_row_flagged(validator: DataValidator) -> None:
    """VAL-02: low > close 的行 → invalid_rows 包含该行，不产生 error（不阻断）"""
    df = _make_quotes_df(n=5, inject_price_error=True)
    result = validator.validate_daily_quotes(df, prev_count=5)
    assert 0 in result.invalid_rows
    assert result.errors == []


def test_val_03_completeness_error(validator: DataValidator) -> None:
    """VAL-03: 股票数 < prev_count × 0.95 → errors 非空（阻断性）"""
    df = _make_quotes_df(n=5)
    result = validator.validate_daily_quotes(df, prev_count=100)
    assert len(result.errors) > 0
    assert result.is_valid is False


def test_val_04_pit_violation(validator: DataValidator) -> None:
    """VAL-04: publish_date > as_of_date → PIT 违规行进入 invalid_rows。
    is_valid=True（行级过滤，不阻断）"""
    as_of = date(2026, 1, 2)
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "report_period": [date(2025, 9, 30)],
            "publish_date": [date(2026, 1, 3)],  # 晚于 as_of_date
            "pe_ttm": [20.0],
        }
    )
    result = validator.validate_financial_data(df, as_of_date=as_of)
    assert len(result.errors) > 0
    assert len(result.invalid_rows) > 0
    assert result.is_valid is True  # 行级过滤，不阻断整批入库


def test_val_05_adj_factor_series_warning(validator: DataValidator) -> None:
    """VAL-05: validate_adj_factor_series() — 多日序列相邻变化 30% → warnings 非空（不阻断）"""
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "trade_date": [date(2026, 1, 2), date(2026, 1, 5)],
            "adj_factor": [1.0, 0.7],  # 变化 30%，超过 20% 阈值
        }
    )
    result = validator.validate_adj_factor_series(df)
    assert len(result.warnings) > 0
    assert result.is_valid is True   # 告警不阻断
    assert result.errors == []


def test_val_05b_single_day_no_adj_factor_warning(validator: DataValidator) -> None:
    """VAL-05b: 单日全市场 DataFrame（每 ts_code 仅 1 行）→ adj_factor 连续性不触发"""
    df = _make_quotes_df(n=5)
    # validate_daily_quotes 不再包含 adj_factor 连续性检查
    result = validator.validate_daily_quotes(df, prev_count=5)
    assert result.warnings == []  # 单日场景无 adj_factor 告警
