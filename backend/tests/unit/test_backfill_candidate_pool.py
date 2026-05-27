"""Phase 14 §14-2：UT-P14-2-01 / UT-P14-2-03 单元测试。

依据 docs/design/phases/phase14_account_integrity.md §4.3：
- UT-P14-2-01：`_compute_plan` 跳过已存在日（force=False）/ 全跑（force=True）
- UT-P14-2-03：`_GracefulInterrupt` 捕获信号后 .stop=True；install 不抛
"""
from __future__ import annotations

import signal
import sys
from datetime import date
from pathlib import Path

# 把 backend/scripts/ 加到 path 让 backfill_candidate_pool 可 import
_BACKEND_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_BACKEND_SCRIPTS))

from backfill_candidate_pool import _compute_plan, _GracefulInterrupt  # noqa: E402


# ============================================================
# UT-P14-2-01：_compute_plan 跳过逻辑
# ============================================================
def test_ut_p14_2_01_compute_plan_skips_existing_when_not_force() -> None:
    """existing 中的 trade_date 进 to_skip（force=False 默认）。"""
    trade_dates = [date(2025, 1, d) for d in (2, 3, 4, 5, 6)]
    existing = {date(2025, 1, 3), date(2025, 1, 4)}
    to_process, to_skip = _compute_plan(trade_dates, existing, force=False)

    assert to_process == [date(2025, 1, 2), date(2025, 1, 5), date(2025, 1, 6)]
    assert to_skip == [date(2025, 1, 3), date(2025, 1, 4)]


def test_ut_p14_2_01b_compute_plan_force_includes_all() -> None:
    """force=True → 全部 trade_date 进 to_process（即便已存在）。"""
    trade_dates = [date(2025, 1, d) for d in (2, 3, 4)]
    existing = {date(2025, 1, 3)}
    to_process, to_skip = _compute_plan(trade_dates, existing, force=True)

    assert to_process == trade_dates
    assert to_skip == []


def test_ut_p14_2_01c_compute_plan_empty_existing() -> None:
    """existing 空（首次回填）→ 全部进 to_process。"""
    trade_dates = [date(2025, 1, d) for d in (2, 3, 4)]
    to_process, to_skip = _compute_plan(trade_dates, set(), force=False)

    assert to_process == trade_dates
    assert to_skip == []


def test_ut_p14_2_01d_compute_plan_all_existing() -> None:
    """existing 全覆盖（断点续传完成态）→ 全部进 to_skip。"""
    trade_dates = [date(2025, 1, d) for d in (2, 3, 4)]
    existing = set(trade_dates)
    to_process, to_skip = _compute_plan(trade_dates, existing, force=False)

    assert to_process == []
    assert to_skip == trade_dates


# ============================================================
# UT-P14-2-03：_GracefulInterrupt 契约
# ============================================================
def test_ut_p14_2_03_graceful_interrupt_initial_state() -> None:
    """构造后 stop=False，未安装 handler。"""
    g = _GracefulInterrupt()
    assert g.stop is False


def test_ut_p14_2_03b_graceful_interrupt_handler_sets_stop_true() -> None:
    """信号 handler 被调用后 stop=True（不实际发信号，直接调内部 handler）。"""
    g = _GracefulInterrupt()
    assert g.stop is False
    g._handler(signal.SIGINT, None)
    assert g.stop is True


def test_ut_p14_2_03c_graceful_interrupt_install_does_not_raise() -> None:
    """install 在主线程不抛；测试结束恢复默认 handler，避免污染其他测试。"""
    import signal as _signal  # 避免与上方 signal 冲突
    original_sigint = _signal.getsignal(_signal.SIGINT)
    original_sigterm = (
        _signal.getsignal(_signal.SIGTERM)
        if hasattr(_signal, "SIGTERM") else None
    )
    try:
        g = _GracefulInterrupt()
        g.install()  # 不应抛 ValueError / RuntimeError
        # install 后 SIGINT handler 应是 g._handler（bound method）
        # 用 == 比对 bound method 在 Python 3 会比较实例 + 函数，一致即成功
        assert _signal.getsignal(_signal.SIGINT) == g._handler
    finally:
        _signal.signal(_signal.SIGINT, original_sigint)
        if original_sigterm is not None:
            _signal.signal(_signal.SIGTERM, original_sigterm)
