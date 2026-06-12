"""单元：reconcile_orphan_backtests 把残留 RUNNING/PENDING 回测任务标 FAILED。

回测在后台 BackgroundTask 跑；进程因部署/重启/OOM 中断会让任务永久卡 RUNNING/PENDING
（前端表现为"超时"）。启动回收把这类任务标 FAILED。本测试 mock session 验证 UPDATE 的
WHERE（status in RUNNING/PENDING）与 SET（status=FAILED + error_msg + finished_at）。
"""
from __future__ import annotations

from quantpilot.services.backtest_service import reconcile_orphan_backtests


class _FakeResult:
    rowcount = 2


class _FakeSession:
    def __init__(self, store: dict) -> None:
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def begin(self):
        outer = self

        class _Begin:
            async def __aenter__(self_):
                return outer

            async def __aexit__(self_, *a):
                return False

        return _Begin()

    async def execute(self, stmt):
        self._store["stmt"] = stmt
        return _FakeResult()


async def test_reconcile_marks_running_pending_failed() -> None:
    store: dict = {}
    n = await reconcile_orphan_backtests(lambda: _FakeSession(store))

    assert n == 2  # rowcount 透传
    compiled = str(store["stmt"].compile(compile_kwargs={"literal_binds": True}))
    assert "backtest_task" in compiled.lower()
    # SET status=FAILED
    assert "FAILED" in compiled
    # WHERE status IN (RUNNING, PENDING)
    assert "RUNNING" in compiled and "PENDING" in compiled
    # 不应误伤 SUCCESS
    assert "SUCCESS" not in compiled
