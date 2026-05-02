"""单元测试：FactorMonitorEngine（engine/factor_monitor.py）。

覆盖：
- calc_ic：正相关/负相关/NaN 处理/样本不足返回 None
- calc_ic_ir：正常计算/数据不足返回 None
- calc_half_life：AR(1) 估计/数据不足/非平稳
- detect_alert：DECAY/INEFFICIENT/FAST_DECAY/优先级/无告警
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from quantpilot.engine.factor_monitor import FactorMonitorEngine


@pytest.fixture
def engine() -> FactorMonitorEngine:
    return FactorMonitorEngine()


# ---------------------------------------------------------------------------
# calc_ic
# ---------------------------------------------------------------------------

class TestCalcIc:
    def test_positive_correlation(self, engine: FactorMonitorEngine) -> None:
        """完全正相关 → IC ≈ 1.0。"""
        factor = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=list("ABCDE"))
        returns = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05], index=list("ABCDE"))
        ic = engine.calc_ic(factor, returns)
        assert ic is not None
        assert ic == pytest.approx(1.0, abs=1e-9)

    def test_negative_correlation(self, engine: FactorMonitorEngine) -> None:
        """完全负相关 → IC ≈ -1.0。"""
        factor = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0], index=list("ABCDE"))
        returns = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05], index=list("ABCDE"))
        ic = engine.calc_ic(factor, returns)
        assert ic is not None
        assert ic == pytest.approx(-1.0, abs=1e-9)

    def test_nan_omitted(self, engine: FactorMonitorEngine) -> None:
        """NaN 值被忽略，非 NaN 对仍能计算出有效 IC。"""
        # 6 个值含 1 个 NaN，dropna 后剩余 5 对（A/C/D/E/F），满足 >= 5 的样本要求
        factor = pd.Series([1.0, float("nan"), 3.0, 4.0, 5.0, 6.0], index=list("ABCDEF"))
        returns = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05, 0.06], index=list("ABCDEF"))
        ic = engine.calc_ic(factor, returns)
        # 剩余 5 对（A/C/D/E/F）仍完全正相关
        assert ic is not None
        assert ic == pytest.approx(1.0, abs=1e-9)

    def test_fewer_than_5_samples_returns_none(self, engine: FactorMonitorEngine) -> None:
        """有效样本 < 5 时返回 None。"""
        factor = pd.Series([1.0, 2.0, 3.0, 4.0], index=list("ABCD"))
        returns = pd.Series([0.01, 0.02, 0.03, 0.04], index=list("ABCD"))
        assert engine.calc_ic(factor, returns) is None

    def test_all_nan_returns_none(self, engine: FactorMonitorEngine) -> None:
        """全为 NaN → 有效样本 0 → 返回 None。"""
        factor = pd.Series([float("nan")] * 6, index=list("ABCDEF"))
        returns = pd.Series([0.01] * 6, index=list("ABCDEF"))
        assert engine.calc_ic(factor, returns) is None

    def test_exact_5_samples_valid(self, engine: FactorMonitorEngine) -> None:
        """恰好 5 个有效样本时应能正常返回（边界值）。"""
        factor = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=list("ABCDE"))
        returns = pd.Series([0.05, 0.04, 0.03, 0.02, 0.01], index=list("ABCDE"))
        ic = engine.calc_ic(factor, returns)
        assert ic is not None
        assert ic == pytest.approx(-1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# calc_ic_ir
# ---------------------------------------------------------------------------

class TestCalcIcIr:
    def test_normal_calculation(self, engine: FactorMonitorEngine) -> None:
        """正常 IC 序列：ic_mean/ic_std/ir 均有值。"""
        ic_series = [0.05, 0.08, 0.06, 0.07, 0.09]
        ic_mean, ic_std, ir = engine.calc_ic_ir(ic_series, window=3)
        assert ic_mean is not None
        assert ic_std is not None
        assert ir is not None
        # ir = ic_mean / ic_std * sqrt(window)（信息比率公式）
        assert ir == pytest.approx(ic_mean / ic_std * math.sqrt(3), rel=1e-6)

    def test_window_longer_than_series_returns_none(self, engine: FactorMonitorEngine) -> None:
        """序列长度 < window 时返回 (None, None, None)。"""
        ic_series = [0.05, 0.06]
        result = engine.calc_ic_ir(ic_series, window=3)
        assert result == (None, None, None)

    def test_empty_series_returns_none(self, engine: FactorMonitorEngine) -> None:
        """空序列 → (None, None, None)。"""
        result = engine.calc_ic_ir([], window=3)
        assert result == (None, None, None)

    def test_zero_std_ir_none(self, engine: FactorMonitorEngine) -> None:
        """全相同 IC → std=0 → IR 无定义 → ir=None，ic_mean/ic_std 正常返回。"""
        ic_series = [0.05, 0.05, 0.05]
        ic_mean, ic_std, ir = engine.calc_ic_ir(ic_series, window=3)
        assert ic_mean == pytest.approx(0.05)
        assert ic_std == pytest.approx(0.0, abs=1e-9)
        assert ir is None

    def test_uses_last_window_values(self, engine: FactorMonitorEngine) -> None:
        """使用序列末尾 window 个值（不是全部）。"""
        # 前两个值故意偏离，只取最后 3 个
        ic_series = [100.0, 200.0, 0.04, 0.06, 0.05]
        ic_mean, _, _ = engine.calc_ic_ir(ic_series, window=3)
        assert ic_mean == pytest.approx((0.04 + 0.06 + 0.05) / 3, rel=1e-6)


# ---------------------------------------------------------------------------
# calc_half_life
# ---------------------------------------------------------------------------

class TestCalcHalfLife:
    def test_fewer_than_6_returns_none(self, engine: FactorMonitorEngine) -> None:
        """数据点 < 6 → 返回 None。"""
        assert engine.calc_half_life([0.1, 0.08, 0.06, 0.04, 0.02]) is None

    def test_exactly_6_valid(self, engine: FactorMonitorEngine) -> None:
        """恰好 6 个点时应能计算（边界值）。"""
        ic_series = [0.10, 0.08, 0.064, 0.051, 0.041, 0.033]
        result = engine.calc_half_life(ic_series)
        # 几何衰减序列，半衰期应为正值
        assert result is not None
        assert result > 0

    def test_positive_half_life_for_decaying_series(self, engine: FactorMonitorEngine) -> None:
        """持续衰减序列 → 正的半衰期值。"""
        # IC 序列大约以 0.8 的自相关系数衰减
        ic_series = [0.10, 0.08, 0.064, 0.051, 0.041, 0.033, 0.026, 0.021]
        result = engine.calc_half_life(ic_series)
        assert result is not None
        assert result > 0

    def test_nonstationary_returns_none(self, engine: FactorMonitorEngine) -> None:
        """非平稳序列（|b| >= 1）→ 返回 None。"""
        # 单调递增序列，自相关系数接近或超过 1
        ic_series = [float(i) for i in range(1, 10)]
        result = engine.calc_half_life(ic_series)
        assert result is None

    def test_empty_returns_none(self, engine: FactorMonitorEngine) -> None:
        """空列表 → None。"""
        assert engine.calc_half_life([]) is None


# ---------------------------------------------------------------------------
# detect_alert
# ---------------------------------------------------------------------------

class TestDetectAlert:
    def test_no_alert(self, engine: FactorMonitorEngine) -> None:
        """正常因子 → 无告警。"""
        result = engine.detect_alert(
            ic_mean=0.08, ir=0.5, half_life_days=15.0,
            recent_ic_signs=[0.05, 0.08, 0.06],
        )
        assert result is None

    def test_decay_all_negative(self, engine: FactorMonitorEngine) -> None:
        """最近 3 个月 IC 均为负 → DECAY。"""
        result = engine.detect_alert(
            ic_mean=-0.03, ir=0.1, half_life_days=20.0,
            recent_ic_signs=[-0.02, -0.04, -0.01],
        )
        assert result == "DECAY"

    def test_inefficient_low_ir(self, engine: FactorMonitorEngine) -> None:
        """IR < 0.3 → INEFFICIENT。"""
        result = engine.detect_alert(
            ic_mean=0.02, ir=0.2, half_life_days=20.0,
            recent_ic_signs=[0.01, 0.02, 0.03],
        )
        assert result == "INEFFICIENT"

    def test_fast_decay_short_half_life(self, engine: FactorMonitorEngine) -> None:
        """half_life_days < 5 → FAST_DECAY。"""
        result = engine.detect_alert(
            ic_mean=0.05, ir=0.4, half_life_days=3.0,
            recent_ic_signs=[0.04, 0.05, 0.06],
        )
        assert result == "FAST_DECAY"

    def test_priority_decay_over_fast_decay(self, engine: FactorMonitorEngine) -> None:
        """DECAY 优先于 FAST_DECAY。"""
        result = engine.detect_alert(
            ic_mean=-0.05, ir=0.1, half_life_days=3.0,
            recent_ic_signs=[-0.04, -0.06, -0.05],
        )
        assert result == "DECAY"

    def test_priority_fast_decay_over_inefficient(self, engine: FactorMonitorEngine) -> None:
        """FAST_DECAY 优先于 INEFFICIENT。"""
        result = engine.detect_alert(
            ic_mean=0.02, ir=0.2, half_life_days=3.0,
            recent_ic_signs=[0.01, 0.02, 0.03],
        )
        assert result == "FAST_DECAY"

    def test_none_values_skip_check(self, engine: FactorMonitorEngine) -> None:
        """ir=None/half_life_days=None 时跳过对应检查，不误报告警。"""
        result = engine.detect_alert(
            ic_mean=0.05, ir=None, half_life_days=None,
            recent_ic_signs=[0.04, 0.05, 0.06],
        )
        assert result is None

    def test_partial_negative_not_decay(self, engine: FactorMonitorEngine) -> None:
        """最近 3 个月 IC 不全为负 → 不触发 DECAY。"""
        result = engine.detect_alert(
            ic_mean=0.01, ir=0.4, half_life_days=15.0,
            recent_ic_signs=[-0.02, 0.01, -0.01],
        )
        assert result is None

    def test_fewer_than_3_recent_signs_no_decay(self, engine: FactorMonitorEngine) -> None:
        """recent_ic_signs 不足 3 个 → 不触发 DECAY。"""
        result = engine.detect_alert(
            ic_mean=-0.03, ir=0.4, half_life_days=15.0,
            recent_ic_signs=[-0.02, -0.04],
        )
        assert result is None
