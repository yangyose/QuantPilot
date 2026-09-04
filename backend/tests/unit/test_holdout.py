"""K-7：holdout 区间约定的**机制化**。V1.5-K §2.3。

## 为什么不是写一段约定就完事

§2.3 定的是纪律：开发集随便试、holdout 改造期不看、定案时解锁一次且解锁后冻结。
但纪律靠人记就等于没有——真实风险是**有人跑一次全窗口分析，不知不觉把 holdout
也算进去了**，而且事后完全看不出来（数字照样合理）。

按 C-6「修在源头，让错误不可能再犯，而不是靠下次记得」，本模块把它变成机制：

- 默认只给开发集；要 holdout 必须**显式解锁并写明理由**
- 解锁留 WARNING 日志——事后能查「谁在什么时候为什么看了 holdout」
- 边界是**写死的常量**，不随今天是几号变化

## ⚠️ 最要命的一条：边界绝不能随时间滚动

§2.3 原文：「滚动 holdout 会让『今天的 holdout』变成『明天的开发集』，
看过就无法假装没看过。」

若有人把 holdout 实现成「最近 12 个月」，代码今天跑和下个月跑会给出不同的划分，
**而且一次都不会报错**。`TestBoundariesAreFixed` 用「同一日期在不同 today 下
分类必须相同」来钉它。
"""
from __future__ import annotations

import logging
from datetime import date

import pytest

from quantpilot.engine.diagnostics.holdout import (
    DEV_END,
    HOLDOUT_END,
    HOLDOUT_START,
    Segment,
    classify,
    split_by_segment,
)


class TestBoundaries:
    @pytest.mark.parametrize(
        ("d", "expected"),
        [
            (date(2021, 5, 13), Segment.DEV),      # 面板窗口起点
            (date(2025, 7, 31), Segment.DEV),      # 开发集最后一天
            (date(2025, 8, 1), Segment.HOLDOUT),   # holdout 第一天
            (date(2026, 6, 30), Segment.HOLDOUT),  # 面板覆盖到此为止（h=40 上限）
            (date(2026, 7, 31), Segment.HOLDOUT),  # holdout 最后一天
            (date(2026, 8, 1), Segment.LIVE),      # 实盘期第一天
            (date(2026, 9, 4), Segment.LIVE),
        ],
    )
    def test_classify_exact_boundaries(self, d: date, expected: Segment) -> None:
        """逐日钉边界——差一天就会让 holdout 泄漏进开发集或反之。"""
        assert classify(d) is expected

    def test_constants_match_design(self) -> None:
        assert DEV_END == date(2025, 7, 31)
        assert HOLDOUT_START == date(2025, 8, 1)
        assert HOLDOUT_END == date(2026, 7, 31)

    def test_holdout_starts_the_day_after_dev_ends(self) -> None:
        """两段必须严丝合缝——留空隙会让那几天既不属开发集也不属 holdout。"""
        from datetime import timedelta

        assert HOLDOUT_START == DEV_END + timedelta(days=1)


class TestBoundariesAreFixed:
    """⚠️ 边界绝不能随「今天」滚动。"""

    def test_classification_independent_of_today(self, monkeypatch) -> None:
        """同一日期在任何「今天」下分类必须相同。

        若实现成「最近 12 个月」，本条立刻红——而那种实现一次都不会报错，
        只会让「今天的 holdout」悄悄变成「明天的开发集」。
        """
        import quantpilot.engine.diagnostics.holdout as mod

        probe = date(2025, 9, 15)
        first = classify(probe)

        class _FakeDate(date):
            @classmethod
            def today(cls):  # noqa: D102
                return date(2030, 1, 1)

        monkeypatch.setattr(mod, "date", _FakeDate, raising=False)
        assert classify(probe) is first is Segment.HOLDOUT

    def test_source_has_no_relative_date_arithmetic(self) -> None:
        """源码中不得出现 `today()` / `now()`——边界是常量，不是算出来的。"""
        import inspect

        import quantpilot.engine.diagnostics.holdout as mod

        src = inspect.getsource(mod)
        body = "\n".join(
            ln for ln in src.splitlines()
            if not ln.strip().startswith("#") and "「" not in ln
        )
        assert "today()" not in body
        assert "now()" not in body


class TestSplitDefaultsToDevOnly:
    _DATES = [
        date(2024, 3, 1),    # DEV
        date(2025, 7, 31),   # DEV
        date(2025, 8, 1),    # HOLDOUT
        date(2026, 6, 30),   # HOLDOUT
        date(2026, 9, 1),    # LIVE
    ]

    def test_default_returns_dev_only(self) -> None:
        """默认只给开发集——这是「不看 holdout」从纪律变成默认行为的那一步。"""
        got = split_by_segment(self._DATES)
        assert got == [date(2024, 3, 1), date(2025, 7, 31)]

    def test_holdout_requires_explicit_unlock(self) -> None:
        with pytest.raises(PermissionError) as exc:
            split_by_segment(self._DATES, segment=Segment.HOLDOUT)
        assert "解锁" in str(exc.value)

    def test_unlock_requires_nonempty_reason(self) -> None:
        """空理由不算解锁——否则这个机制退化成一个多余的参数。"""
        for bad in ("", "   "):
            with pytest.raises(ValueError):
                split_by_segment(
                    self._DATES, segment=Segment.HOLDOUT, unlock_reason=bad
                )

    def test_unlock_returns_holdout_and_logs_reason(self, caplog) -> None:
        """解锁必须留痕——事后要能查「谁在什么时候为什么看了 holdout」。"""
        with caplog.at_level(logging.WARNING):
            got = split_by_segment(
                self._DATES, segment=Segment.HOLDOUT,
                unlock_reason="momentum rs_6m 定案",
            )
        assert got == [date(2025, 8, 1), date(2026, 6, 30)]
        assert "holdout_unlocked" in caplog.text
        assert "momentum rs_6m 定案" in caplog.text

    def test_live_segment_also_requires_unlock(self) -> None:
        """实盘期同样不得用于因子选择（§2.3「永不用于因子选择」）。"""
        with pytest.raises(PermissionError):
            split_by_segment(self._DATES, segment=Segment.LIVE)

    def test_dev_never_logs_unlock(self, caplog) -> None:
        """取开发集是常规操作，不该刷 WARNING——否则日志噪声会让真解锁被淹没。"""
        with caplog.at_level(logging.WARNING):
            split_by_segment(self._DATES)
        assert "holdout_unlocked" not in caplog.text


class TestEmptyAndOrder:
    def test_empty_input(self) -> None:
        assert split_by_segment([]) == []

    def test_preserves_input_order(self) -> None:
        ds = [date(2025, 7, 1), date(2024, 1, 2), date(2025, 3, 3)]
        assert split_by_segment(ds) == ds
