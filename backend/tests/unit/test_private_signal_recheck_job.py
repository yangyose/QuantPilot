"""持仓私有信号「收盘后复评」Job（硬止损一日延迟修复）。

## 缺陷（2026-09-03 生产实证）

`hard_stop_loss` 此前**唯一**的评估点是 15:05 的 `_stop_loss_warn_job`，而它读的
`position.current_price` 由 **17:30 管线的 step4 盯市**写入 → 15:05 看到的永远是
**前一交易日**收盘价，当日收盘价入库后再无任何环节复评。

实例 001258.SZ（cost 13.594）：

| 时刻 | 评估用价 | 浮亏 | 结果 |
|---|---|---|---|
| 9/3 15:05 | 9/2 收盘 12.520 | −7.90% | 不触发（按当时数据正确）|
| 9/3 18:43 盯市 | 9/3 收盘 11.610 | **−14.59%** | **无人评估** |
| 9/4 15:05 | 9/3 收盘 11.610 | −14.59% | 才触发 |

A 股 15:00 已收盘 → 通知发出时当日无法交易 → 用户最早次日开盘才能卖，
从「数据可知」到「可执行」约 **2 个交易日**。

旁证：`RISK_WARN` 共 71 条（自 2026-06-28）中 `hard_stop_loss` **0 条**——
机制上线以来一次都没发出过。

## 本文件钉什么

⚠️ **核心不变量是「时序」而不是「有没有这个 Job」**：加一个 Job 很容易，
但只要它排在管线之前，缺陷就原样存在且测试照样绿。故第一条用例断言的是
**复评 Job 必须严格晚于 daily_pipeline**——把它挪到 17:00 立刻红。
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from apscheduler.triggers.cron import CronTrigger

from quantpilot.pipeline.scheduler import create_scheduler

_JOB_ID = "private_signal_recheck"


def _mins(job) -> int:
    f = {x.name: str(x) for x in job.trigger.fields}
    return int(f["hour"]) * 60 + int(f["minute"])


def _scheduler():
    return create_scheduler(
        session_factory=MagicMock(), adapter=MagicMock(),
        validator=MagicMock(), calendar=MagicMock(),
    )


class TestScheduledAfterPipeline:
    """时序不变量——本修复的全部意义所在。"""

    def test_recheck_runs_strictly_after_daily_pipeline(self) -> None:
        """复评必须晚于管线盯市，否则它读到的仍是昨日价、等于没修。

        这条**不写死 18:30**：写死时刻只能挡住"改了时刻"，挡不住
        "把管线挪晚了"。断言两者的相对顺序才是真不变量。
        """
        sch = _scheduler()
        recheck = sch.get_job(_JOB_ID)
        pipeline = sch.get_job("daily_pipeline")
        assert recheck is not None, f"{_JOB_ID} Job 未注册"
        assert pipeline is not None
        assert _mins(recheck) > _mins(pipeline), (
            f"复评 Job（{_mins(recheck)} 分）必须晚于管线（{_mins(pipeline)} 分），"
            "否则读到的仍是前一交易日价格"
        )

    def test_recheck_also_after_the_1505_job_it_supplements(self) -> None:
        """它是 15:05 那条的补充而非替代，两者都要在。"""
        sch = _scheduler()
        assert sch.get_job("stop_loss_warn") is not None, "15:05 前瞻预警不应被删除"
        assert _mins(sch.get_job(_JOB_ID)) > _mins(sch.get_job("stop_loss_warn"))

    def test_registered_with_shanghai_tz_and_explicit_args(self) -> None:
        """APScheduler Job 无法访问 app.state，依赖必须显式经 args 注入。"""
        job = _scheduler().get_job(_JOB_ID)
        assert isinstance(job.trigger, CronTrigger)
        assert str(job.trigger.timezone) == "Asia/Shanghai"
        assert job.args, "依赖须显式经 args 注入"


class TestFreshnessGuard:
    """只有当日盯市确实跑过才评估——否则会用陈旧价占掉 24h 去重窗口。"""

    async def test_skips_when_todays_pipeline_not_successful(
        self, caplog
    ) -> None:
        """管线未成功 → 跳过且留 WARNING，**不静默**（C-4）。

        若此时照常评估，会用昨日价产出一条 payload 含今日日期的通知；
        次日那条正确的通知就会被 24h 去重挡掉——比不评估更糟。
        """
        from quantpilot.pipeline.scheduler import _private_signal_recheck_job

        cal = MagicMock()
        cal.is_trade_date.return_value = True
        evaluate = AsyncMock(return_value=[])
        with patch(
            "quantpilot.pipeline.scheduler._todays_pipeline_succeeded",
            AsyncMock(return_value=False),
        ), patch(
            "quantpilot.services.signal_service.SignalService.evaluate_private_signals",
            evaluate,
        ), caplog.at_level(logging.WARNING):
            await _private_signal_recheck_job(
                _DummySessionFactory(), cal, None, None
            )

        assert evaluate.await_count == 0, "盯市未跑时不应评估"
        assert "private_signal_recheck_skipped" in caplog.text

    async def test_skips_non_trade_date(self) -> None:
        from quantpilot.pipeline.scheduler import _private_signal_recheck_job

        cal = MagicMock()
        cal.is_trade_date.return_value = False
        guard = AsyncMock(return_value=True)
        with patch(
            "quantpilot.pipeline.scheduler._todays_pipeline_succeeded", guard
        ):
            await _private_signal_recheck_job(
                _DummySessionFactory(), cal, None, None
            )
        assert guard.await_count == 0


class _DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        return None


class _DummySessionFactory:
    def __call__(self):
        return _DummySession()


class TestReusesSingleImplementationSource:
    def test_does_not_reimplement_stop_loss_judgement(self) -> None:
        """复评必须复用 `evaluate_private_signals`，不得另写一份阈值判定。

        在 Job 里重写一遍 `pnl_pct <= -stop_loss_pct` 会产生第二个实现源，
        两处迟早漂移（`evaluate_private_signals` 的 docstring 明确要求单一实现源）。
        判据是**在源码上**查——构造 spy 再调用它是自证式的。
        """
        import ast
        import inspect

        from quantpilot.pipeline import scheduler as mod

        def _calls(fn) -> set[str]:
            tree = ast.parse(inspect.getsource(fn).lstrip())
            out: set[str] = set()
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                if isinstance(n.func, ast.Attribute):
                    out.add(n.func.attr)
                elif isinstance(n.func, ast.Name):
                    out.add(n.func.id)
            return out

        job_src = inspect.getsource(mod._private_signal_recheck_job)
        helper_src = inspect.getsource(mod._notify_private_signals)

        # 委托链：Job → 共用 helper → evaluate_private_signals。
        # 断言链条而非单个调用名——Job 直接调 evaluate_private_signals 也算对，
        # 但经由 15:05 那条同样使用的 helper 更好（推送格式也只有一份）。
        assert "_notify_private_signals" in _calls(mod._private_signal_recheck_job), (
            "复评 Job 必须委托给共用 helper，而不是自己拼一套推送"
        )
        assert "evaluate_private_signals" in _calls(mod._notify_private_signals), (
            "共用 helper 必须调用 evaluate_private_signals（单一实现源）"
        )
        # 两处都不得重写阈值判定
        assert "stop_loss_pct" not in job_src
        assert "stop_loss_pct" not in helper_src

    def test_the_1505_job_shares_the_same_helper(self) -> None:
        """15:05 那条也必须走同一个 helper，否则两处推送格式会漂移。"""
        import ast
        import inspect

        from quantpilot.pipeline import scheduler as mod

        tree = ast.parse(inspect.getsource(mod._stop_loss_warn_job).lstrip())
        names = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_notify_private_signals" in names
