"""unit/test_notification_service.py: Phase 10 NotificationService 真实实现单测。

INV-NTF-01：偏好开关过滤（notify_signal_buy=False → 不写库不推送）
INV-NTF-02：推送时段过滤（当前小时不在 [start, end) → 仅写库不推送）
INV-NTF-03：去重（24 小时内同类型同 payload 仅写一次）
INV-NTF-04：兜底写库始终发生 + WxPusher 失败标 wx_error
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from quantpilot.core.config_defaults import DEFAULT_NOTIFICATION, NotificationConfig
from quantpilot.models.business import InAppNotification
from quantpilot.services.notification_service import NotificationService

# 在 autouse fixture 替换前捕获原始静态方法，留给 TestPushWindow 直接调用
_ORIGINAL_IN_PUSH_WINDOW = NotificationService._in_push_window


# ───────────────────── Fakes ─────────────────────
class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """最小可用的 AsyncSession 替身。"""

    def __init__(self) -> None:
        self.added: list[InAppNotification] = []
        self.flush_count = 0
        # 预设 execute 返回值队列（None = 无重复行）
        self._execute_results: list[Any] = []
        self.executed_stmts: list[Any] = []

    def queue_execute(self, value: Any) -> None:
        self._execute_results.append(value)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed_stmts.append(stmt)
        if self._execute_results:
            return _FakeResult(self._execute_results.pop(0))
        return _FakeResult(None)


class _FakeConfigService:
    def __init__(self, prefs: NotificationConfig) -> None:
        self._prefs = prefs

    async def get_notification_prefs(self) -> NotificationConfig:
        return self._prefs


class _FakeWx:
    def __init__(self, ok: bool = True, uid: str = "UID_test") -> None:
        self.ok = ok
        self.uid = uid
        self.calls: list[tuple[str, str]] = []

    async def send(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        return self.ok


# ───────────────────── 助手 ─────────────────────
def _build(
    prefs: NotificationConfig | None = None,
    wx_ok: bool = True,
    with_wx: bool = True,
) -> tuple[NotificationService, _FakeSession, _FakeWx | None]:
    session = _FakeSession()
    wx: _FakeWx | None = _FakeWx(ok=wx_ok) if with_wx else None
    svc = NotificationService(
        session=session,  # type: ignore[arg-type]
        config_service=_FakeConfigService(prefs or DEFAULT_NOTIFICATION),  # type: ignore[arg-type]
        wxpusher=wx,
    )
    return svc, session, wx


@pytest.fixture(autouse=True)
def force_push_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让 _in_push_window 返回 True，避免本地时间影响断言。
    需要测试时段过滤的用例自行调用 monkeypatch 覆盖。"""
    monkeypatch.setattr(
        NotificationService,
        "_in_push_window",
        staticmethod(lambda prefs, now=None: True),
    )


# ───────────────────── INV-NTF-01：开关过滤 ─────────────────────
class TestSwitchFilter:
    async def test_signal_buy_disabled_skips_everything(self) -> None:
        prefs = replace(DEFAULT_NOTIFICATION, notify_signal_buy=False)
        svc, session, wx = _build(prefs=prefs)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is None
        assert session.added == []
        assert session.flush_count == 0
        assert wx is not None and wx.calls == []

    async def test_unknown_type_passes_default_true(self) -> None:
        """未在 _TYPE_PREF_MAP 登记的类型默认放行（如 PIPELINE_FAILURE）。"""
        svc, session, _ = _build()
        result = await svc.notify("PIPELINE_FAILURE", "t", "b", {"run_id": 1})
        assert result is not None
        assert len(session.added) == 1


# ───────────────────── INV-NTF-02：时段过滤 ─────────────────────
class TestPushWindow:
    async def test_outside_window_writes_db_but_skips_wx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 强制把 _in_push_window 改回 False（覆盖 autouse fixture）
        monkeypatch.setattr(
            NotificationService,
            "_in_push_window",
            staticmethod(lambda prefs, now=None: False),
        )
        svc, session, wx = _build()
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert len(session.added) == 1
        assert wx is not None and wx.calls == []
        assert result.wx_pushed is False

    def test_window_normal_range(self) -> None:
        prefs = replace(DEFAULT_NOTIFICATION, push_start_hour=15, push_end_hour=22)
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 15)) is True
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 21)) is True
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 22)) is False
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 14)) is False

    def test_window_overnight_range(self) -> None:
        # 跨日：22 → 06
        prefs = replace(DEFAULT_NOTIFICATION, push_start_hour=22, push_end_hour=6)
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 23)) is True
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 5)) is True
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 10)) is False


# ───────────────────── INV-NTF-03：去重 ─────────────────────
class TestDedup:
    async def test_dedup_skips_when_recent_exists(self) -> None:
        svc, session, wx = _build()
        # 模拟 _is_duplicate 查库返回已有 id
        session.queue_execute(123)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is None
        assert session.added == []
        assert wx is not None and wx.calls == []

    async def test_no_dedup_when_payload_is_none(self) -> None:
        svc, session, _ = _build()
        result = await svc.notify("SIGNAL_BUY", "t", "b", payload=None)
        assert result is not None
        # payload=None 时跳过去重查询
        assert session.executed_stmts == []

    async def test_no_dedup_for_different_payload(self) -> None:
        svc, session, _ = _build()
        # 第一次：dedup 查询返回 None；第二次：返回 None
        session.queue_execute(None)
        session.queue_execute(None)
        await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "600519.SH"})
        assert len(session.added) == 2


# ───────────────────── INV-NTF-04：兜底写库 + WxPusher 失败标记 ─────────────────────
class TestFallback:
    async def test_wx_failure_marks_in_app(self) -> None:
        svc, session, wx = _build(wx_ok=False)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert result.wx_pushed is False
        assert result.wx_error is not None
        assert "重试 3 次均失败" in result.wx_error
        assert wx is not None and len(wx.calls) == 1

    async def test_wx_success_marks_pushed(self) -> None:
        svc, session, wx = _build(wx_ok=True)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert result.wx_pushed is True
        assert result.wx_error is None

    async def test_no_wx_adapter_only_writes_in_app(self) -> None:
        svc, session, wx = _build(with_wx=False)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert result.wx_pushed is False
        assert wx is None
        assert len(session.added) == 1

    async def test_wx_disabled_in_prefs_skips_push(self) -> None:
        prefs = replace(DEFAULT_NOTIFICATION, wx_enabled=False)
        svc, session, wx = _build(prefs=prefs)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert wx is not None and wx.calls == []
        assert result.wx_pushed is False


# ───────────────────── 模板渲染（轻量验证） ─────────────────────
class TestTemplates:
    async def test_market_state_change_template(self) -> None:
        svc, session, _ = _build()
        result = await svc.notify_market_state_change("UPTREND", "DOWNTREND", "2026-04-21")
        assert result is not None
        assert "UPTREND" in result.title and "DOWNTREND" in result.title
        assert "2026-04-21" in result.body

    async def test_stop_loss_warn_template(self) -> None:
        svc, _, _ = _build()
        result = await svc.notify_stop_loss_warn(
            ts_code="000001.SZ",
            name="平安银行",
            current_price=10.50,
            stop_loss_price=10.40,
            distance_pct=0.0095,
        )
        assert result is not None
        assert "平安银行" in result.title
        assert "10.50" in result.body and "10.40" in result.body
        assert "0.95%" in result.body

    async def test_factor_alert_template_with_ic(self) -> None:
        svc, _, _ = _build()
        result = await svc.notify_factor_alert("IC_NEGATIVE", "trend", "ma_alignment", -0.0123)
        assert result is not None
        assert "trend.ma_alignment" in result.title
        assert "-0.0123" in result.body

    async def test_factor_alert_legacy_signature_compat(self) -> None:
        """Phase 7 调用方仍用 (alert_type, strategy, factor) 三参签名。"""
        svc, session, _ = _build()
        result = await svc.notify_factor_alert("IC_NEGATIVE", "trend", "ma_alignment")
        assert result is not None
        assert len(session.added) == 1


# ───────────────────── 异常路径：flush 失败 ─────────────────────
class TestFlushFailure:
    async def test_flush_exception_re_raises(self) -> None:
        svc, session, _ = _build()

        async def _raising_flush() -> None:
            raise RuntimeError("DB down")

        session.flush = _raising_flush  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="DB down"):
            await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})


# ───────────────────── 信号模板 ─────────────────────
class TestSignalTemplate:
    async def test_buy_signal_renders_full_template(self) -> None:
        from quantpilot.models.business import Signal
        svc, _, _ = _build()
        sig = Signal(
            id=1,
            ts_code="000001.SZ",
            signal_type="BUY",
            trade_date=datetime(2026, 4, 21).date(),
            score=85.0,
            suggested_pct=0.10,
            suggested_price_low=10.0,
            suggested_price_high=10.20,
            stop_loss_price=9.20,
            signal_strength="STRONG",
            reason="均线多头排列+MACD金叉",
            status="NEW",
        )
        result = await svc.notify_signal(sig, name="平安银行", amount=10000)
        assert result is not None
        assert "平安银行" in result.title
        assert "85.00/100" in result.body
        assert "STRONG" in result.body
        assert "10.00-10.20" in result.body
        assert "10.0%" in result.body
        assert "10000" in result.body
        # stop_loss_pct = (1 - 9.20/10.00) * 100 = 8.0
        assert "8.0%" in result.body
        assert "T+1" in result.body
