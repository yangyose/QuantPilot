"""持仓私有信号派发：计数口径 + 管线末尾触发。V1.5-K 后续修正（2026-09-04）。

## 修两件事

### 1. `sent` 计数把被去重的空操作算成了「已发送」

2026-09-04 生产首跑实测：`private_signal_recheck_done ... sent=3`，
而 `in_app_notification` 当次只落了 **1 行**。成因是原实现在
`await notifier.notify(...)` 不抛异常时就 `sent += 1`，
而 `NotificationService.notify` **去重命中时返回 `None` 且不抛**。

不影响正确性（去重本身是对的），但计数器与其名字不符——排障时会让人以为发了 3 条。
判据：`notify` 返回 `None` 时**不得计数**。

### 2. 定时 18:30 是把因果依赖编码成了时间偏移

复评需要的是「盯市已完成」，不是「到 18:30 了」。代价有实证：
**2026-09-03 管线跑到 18:11 才被 OOM 杀死**——若复评 Job 当时已存在，
18:30 触发会看到 `status=RUNNING` → 守卫跳过 → **那天根本没做止损检查**，
而那天正是 001258.SZ 跌到 −14.59% 的日子。守卫防住了「用错数据」，
没防住「根本没查」。

故改为**管线末尾直接触发（主）+ 保留 18:30（兜底）**。两者叠加不会重复通知：
`payload` 含日期，NotificationService 按 24h 窗口去重，所以兜底是免费的。

⚠️ 本文件最关键的一条是**顺序**：触发必须排在 `_step4_mark_to_market` **之后**。
排在之前读到的仍是前一交易日的价，等于没修——`TestPipelineOrdering` 钉死它。
"""
from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock, MagicMock

from quantpilot.services.private_signal_dispatch import notify_private_signals


class _Sig:
    def __init__(self, ts_code: str, signal_type: str = "SELL", trigger: str = "hard_stop_loss"):
        self.ts_code = ts_code
        self.signal_type = signal_type
        self.trigger_reason = trigger
        self.reason = "硬止损"


def _svc(signals: list) -> MagicMock:
    m = MagicMock()
    m.evaluate_private_signals = AsyncMock(return_value=signals)
    return m


class TestSentCountsOnlyRealInsertions:
    async def test_deduped_none_is_not_counted(self) -> None:
        """去重命中 → `notify_risk_warn` 返回 None → **不得计数**。

        这正是 2026-09-04 生产首跑 `sent=3` 而只落 1 行的成因。
        """
        notifier = MagicMock()
        notifier.notify_risk_warn = AsyncMock(return_value=None)
        notifier.notify = AsyncMock(return_value=None)
        n = await notify_private_signals(
            _svc([_Sig("A.SZ"), _Sig("B.SZ")]), notifier,
            MagicMock(id=1), [MagicMock()], "2026-09-04",
        )
        assert n == 0, "被去重的空操作不算已发送"

    async def test_real_insertion_is_counted(self) -> None:
        notifier = MagicMock()
        notifier.notify_risk_warn = AsyncMock(return_value=object())
        notifier.notify = AsyncMock(return_value=object())
        n = await notify_private_signals(
            _svc([_Sig("A.SZ"), _Sig("B.SZ")]), notifier,
            MagicMock(id=1), [MagicMock()], "2026-09-04",
        )
        assert n == 2

    async def test_mixed_counts_only_inserted(self) -> None:
        notifier = MagicMock()
        notifier.notify_risk_warn = AsyncMock(side_effect=[object(), None, object()])
        notifier.notify = AsyncMock(return_value=object())
        n = await notify_private_signals(
            _svc([_Sig("A.SZ"), _Sig("B.SZ"), _Sig("C.SZ")]), notifier,
            MagicMock(id=1), [MagicMock()], "2026-09-04",
        )
        assert n == 2

    async def test_buy_path_also_respects_none(self) -> None:
        notifier = MagicMock()
        notifier.notify = AsyncMock(return_value=None)
        notifier.notify_risk_warn = AsyncMock(return_value=object())
        n = await notify_private_signals(
            _svc([_Sig("A.SZ", signal_type="BUY", trigger="")]), notifier,
            MagicMock(id=1), [MagicMock()], "2026-09-04",
        )
        assert n == 0

    async def test_empty_positions_short_circuits(self) -> None:
        svc = _svc([])
        n = await notify_private_signals(
            svc, MagicMock(), MagicMock(id=1), [], "2026-09-04"
        )
        assert n == 0
        assert svc.evaluate_private_signals.await_count == 0


class TestFailureIsolation:
    async def test_eval_failure_returns_zero_not_raise(self) -> None:
        """评估失败不得把调用方（管线 / Job）带崩。"""
        svc = MagicMock()
        svc.evaluate_private_signals = AsyncMock(side_effect=RuntimeError("boom"))
        assert await notify_private_signals(
            svc, MagicMock(), MagicMock(id=1), [MagicMock()], "2026-09-04"
        ) == 0

    async def test_one_notify_failure_does_not_stop_others(self) -> None:
        notifier = MagicMock()
        notifier.notify_risk_warn = AsyncMock(
            side_effect=[RuntimeError("x"), object()]
        )
        notifier.notify = AsyncMock(return_value=object())
        n = await notify_private_signals(
            _svc([_Sig("A.SZ"), _Sig("B.SZ")]), notifier,
            MagicMock(id=1), [MagicMock()], "2026-09-04",
        )
        assert n == 1


class TestPipelineOrdering:
    """⚠️ 核心不变量：触发必须在盯市**之后**。排在之前读到的仍是昨日价，等于没修。"""

    @staticmethod
    def _run_body_calls() -> list[str]:
        from quantpilot.pipeline.daily_pipeline import DailyPipeline

        tree = ast.parse(inspect.getsource(DailyPipeline.run).lstrip())
        return [
            n.func.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]

    def test_dispatch_called_after_mark_to_market(self) -> None:
        calls = self._run_body_calls()
        assert "_step4_mark_to_market" in calls
        assert "_step7_private_signals" in calls, "管线末尾未触发私有信号复评"
        assert calls.index("_step7_private_signals") > calls.index("_step4_mark_to_market"), (
            "复评必须排在盯市之后——排在之前读到的仍是前一交易日的价"
        )

    def test_step7_delegates_to_shared_dispatch(self) -> None:
        """管线那一步必须复用共用派发，不得自己再写一份推送。"""
        from quantpilot.pipeline.daily_pipeline import DailyPipeline

        tree = ast.parse(
            inspect.getsource(DailyPipeline._step7_private_signals).lstrip()
        )
        names = {
            (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
            for n in ast.walk(tree) if isinstance(n, ast.Call)
        }
        assert "notify_private_signals" in names

    def test_scheduler_jobs_share_the_same_dispatch(self) -> None:
        """15:05 与 18:30 两个 Job 也必须走同一份，否则三处推送格式会漂移。

        ⚠️ 断言的是**对象同一性**而非源码子串：`_notify_private_signals` 这个别名
        恰好包含 `notify_private_signals`，靠子串匹配的话，谁在 scheduler 里另写一份
        同名函数照样能过（§5.3 记过同类教训：规则写宽了没人发现）。
        """
        from quantpilot.pipeline import scheduler as mod
        from quantpilot.services.private_signal_dispatch import (
            notify_private_signals as shared,
        )

        assert mod._notify_private_signals is shared, "scheduler 的别名不是共用实现"
        for fn in (mod._stop_loss_warn_job, mod._private_signal_recheck_job):
            assert "_notify_private_signals" in inspect.getsource(fn), (
                f"{fn.__name__} 未调用共用派发"
            )
